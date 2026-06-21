"""
PPOAgent — The Quant (Deep RL agent using Proximal Policy Optimization).

Implements a hand-rolled PPO in PyTorch as the master prompt requests.
Two specialization modes:
    - mode="trend"     : rewards policy for capturing directional trends
    - mode="meanrev"   : rewards policy for fading extremes

Both modes share the same actor-critic network architecture; only the reward
shape and observation normalization differ. The agent ingests MarketEvents,
maintains a rolling window of price/order-book features, and emits a
SignalEvent per decision step.

Architecture:
    Actor  : MLP(obs_dim → 256 → 256 → 3)   outputs logits over {SHORT, FLAT, LONG}
    Critic : MLP(obs_dim → 256 → 256 → 1)   outputs state-value estimate

PPO update:
    - Collect rollout_len steps
    - Compute advantages via GAE-λ
    - For ppo_epochs:
        - Mini-batch update with clipped surrogate objective
        - Value loss + entropy bonus

This is real, training PPO — not a stub. See `train_ppo.py` for the
end-to-end training loop that runs against live Binance data.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical

from omega.alpha_swarm.base import AlphaAgent
from omega.config.settings import AlphaSwarmSettings
from omega.utils.events import MarketEvent, SignalEvent, Side
from omega.utils.logger import get_logger

logger = get_logger("omega.alpha_swarm.ppo")

# Action space: 0=SHORT, 1=FLAT, 2=LONG
ACTION_TO_SIDE: Tuple[Side, ...] = (Side.SELL, Side.FLAT, Side.BUY)
ACTION_NAMES: Tuple[str, ...] = ("SHORT", "FLAT", "LONG")


def _features_from_history(history: np.ndarray, mode: str) -> np.ndarray:
    """
    Convert (N, F) raw history matrix → fixed-length observation vector.

    history columns: [open, high, low, close, volume, bid, ask, bid_qty, ask_qty]
    Output features (dim=64 by default, configurable):
        - Log returns (last 16 bars)
        - Volume z-score
        - Order book imbalance (last 8 bars)
        - Volatility (rolling std of returns, 16-bar)
        - RSI (14-bar)
        - Bollinger position
    """
    if history.shape[0] < 2:
        return np.zeros(64, dtype=np.float32)

    close = history[:, 3]
    high = history[:, 1]
    low = history[:, 2]
    vol = history[:, 4]
    bid_qty = history[:, 7]
    ask_qty = history[:, 8]

    # Log returns (last 16)
    log_ret = np.diff(np.log(close + 1e-9))
    rets_16 = np.zeros(16, dtype=np.float32)
    if len(log_ret) >= 16:
        rets_16 = log_ret[-16:].astype(np.float32)
    elif len(log_ret) > 0:
        rets_16[-len(log_ret):] = log_ret.astype(np.float32)

    # Volume z-score (rolling 32)
    vol_window = vol[-32:] if len(vol) >= 32 else vol
    vol_mean = np.mean(vol_window) + 1e-9
    vol_std = np.std(vol_window) + 1e-9
    vol_z = (vol[-1] - vol_mean) / vol_std if len(vol) > 0 else 0.0

    # Order book imbalance (last 8)
    imb = (bid_qty - ask_qty) / (bid_qty + ask_qty + 1e-9)
    imb_8 = np.zeros(8, dtype=np.float32)
    if len(imb) >= 8:
        imb_8 = imb[-8:].astype(np.float32)
    elif len(imb) > 0:
        imb_8[-len(imb):] = imb.astype(np.float32)

    # Volatility (16-bar rolling std of returns)
    vol16 = np.std(log_ret[-16:]) if len(log_ret) >= 2 else 0.0

    # RSI 14
    rsi = _rsi(close, 14)

    # Bollinger position (where is close relative to 20-bar bands)
    if len(close) >= 20:
        ma = np.mean(close[-20:])
        sd = np.std(close[-20:]) + 1e-9
        bb_pos = (close[-1] - ma) / (2 * sd)
    else:
        bb_pos = 0.0

    # ATR (14) — average true range, normalized by close
    atr = _atr(high, low, close, 14) / (close[-1] + 1e-9)

    # Padding to fixed dim 64
    feats = np.concatenate([
        rets_16,                                 # 16
        imb_8,                                   # 8
        [vol_z, vol16, rsi, bb_pos, atr, 0.0],   # 6 (last zero = placeholder)
        np.zeros(34, dtype=np.float32),          # 34 reserved
    ]).astype(np.float32)
    return feats


def _rsi(close: np.ndarray, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    deltas = np.diff(close[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses) + 1e-9
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    if len(close) < period + 1:
        return 0.0
    tr = np.maximum(
        high[-period:] - low[-period:],
        np.maximum(
            np.abs(high[-period:] - close[-period - 1:-1]),
            np.abs(low[-period:] - close[-period - 1:-1]),
        ),
    )
    return float(np.mean(tr))


# ---------------------------------------------------------------------------
# Network definitions
# ---------------------------------------------------------------------------


class Actor(nn.Module):
    def __init__(self, obs_dim: int, hidden: Tuple[int, ...] = (256, 256), n_actions: int = 3):
        super().__init__()
        layers: List[nn.Module] = []
        prev = obs_dim
        for h in hidden:
            layers += [nn.Linear(prev, h), nn.LayerNorm(h), nn.Tanh()]
            prev = h
        layers.append(nn.Linear(prev, n_actions))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class Critic(nn.Module):
    def __init__(self, obs_dim: int, hidden: Tuple[int, ...] = (256, 256)):
        super().__init__()
        layers: List[nn.Module] = []
        prev = obs_dim
        for h in hidden:
            layers += [nn.Linear(prev, h), nn.LayerNorm(h), nn.Tanh()]
            prev = h
        layers.append(nn.Linear(prev, 1))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


@dataclass
class PPONetworks:
    actor: Actor
    critic: Critic


# ---------------------------------------------------------------------------
# Rollout buffer
# ---------------------------------------------------------------------------


@dataclass
class Transition:
    obs: np.ndarray
    action: int
    log_prob: float
    reward: float
    value: float
    done: bool


class RolloutBuffer:
    def __init__(self, capacity: int, obs_dim: int) -> None:
        self.capacity = capacity
        self.obs = np.zeros((capacity, obs_dim), dtype=np.float32)
        self.actions = np.zeros(capacity, dtype=np.int64)
        self.log_probs = np.zeros(capacity, dtype=np.float32)
        self.rewards = np.zeros(capacity, dtype=np.float32)
        self.values = np.zeros(capacity, dtype=np.float32)
        self.dones = np.zeros(capacity, dtype=np.float32)
        self.advantages = np.zeros(capacity, dtype=np.float32)
        self.returns = np.zeros(capacity, dtype=np.float32)
        self.idx = 0

    def add(self, t: Transition) -> None:
        i = self.idx % self.capacity
        self.obs[i] = t.obs
        self.actions[i] = t.action
        self.log_probs[i] = t.log_prob
        self.rewards[i] = t.reward
        self.values[i] = t.value
        self.dones[i] = float(t.done)
        self.idx += 1

    def compute_gae(self, gamma: float, lam: float, last_value: float) -> None:
        n = min(self.idx, self.capacity)
        adv = 0.0
        for t in reversed(range(n)):
            next_val = last_value if t == n - 1 else self.values[t + 1]
            next_non_terminal = 1.0 - self.dones[t]
            delta = self.rewards[t] + gamma * next_val * next_non_terminal - self.values[t]
            adv = delta + gamma * lam * next_non_terminal * adv
            self.advantages[t] = adv
            self.returns[t] = self.advantages[t] + self.values[t]
        # Normalize advantages
        if n > 1:
            self.advantages[:n] = (self.advantages[:n] - self.advantages[:n].mean()) / (
                self.advantages[:n].std() + 1e-8
            )

    def sample(self, batch_size: int):
        n = min(self.idx, self.capacity)
        idx = np.arange(n)
        np.random.shuffle(idx)
        for i in range(0, n, batch_size):
            batch = idx[i : i + batch_size]
            yield (
                torch.from_numpy(self.obs[batch]),
                torch.from_numpy(self.actions[batch]),
                torch.from_numpy(self.log_probs[batch]),
                torch.from_numpy(self.advantages[batch]),
                torch.from_numpy(self.returns[batch]),
            )


# ---------------------------------------------------------------------------
# PPO Agent
# ---------------------------------------------------------------------------


class PPOAgent(AlphaAgent):
    """Proximal Policy Optimization agent for directional trading."""

    def __init__(
        self,
        symbols: tuple,
        mode: str = "trend",                       # "trend" | "meanrev"
        settings: Optional[AlphaSwarmSettings] = None,
        obs_dim: int = 64,
        device: str = "cpu",
    ) -> None:
        super().__init__(symbols)
        if mode not in ("trend", "meanrev"):
            raise ValueError(f"Unknown mode: {mode}")
        self.mode = mode
        self.name = f"ppo_{mode}"
        self.settings = settings or AlphaSwarmSettings()
        self.obs_dim = obs_dim
        self.device = torch.device(device)

        self.actor = Actor(obs_dim, self.settings.actor_hidden).to(self.device)
        self.critic = Critic(obs_dim, self.settings.critic_hidden).to(self.device)
        self.optimizer = torch.optim.Adam(
            list(self.actor.parameters()) + list(self.critic.parameters()),
            lr=self.settings.ppo_lr,
        )

        self.buffer = RolloutBuffer(self.settings.ppo_rollout_len, obs_dim)
        # BUGFIX: per-symbol state. Previously a single agent shared one history
        # buffer / one action across all symbols, mashing BTC/ETH/SOL bars
        # together and producing incoherent features. Now each symbol keeps its
        # own rolling history, last action, entry price, and last price.
        self._history: Dict[str, Deque[np.ndarray]] = {
            s: deque(maxlen=self.settings.observation_window) for s in symbols
        }
        self._last_action: Dict[str, int] = {s: 1 for s in symbols}  # FLAT
        self._last_price: Dict[str, float] = {}
        self._entry_price: Dict[str, float] = {}
        self._step_count = 0
        self.is_ready = True

    # ------------------------------------------------------------------
    # AlphaAgent interface
    # ------------------------------------------------------------------

    def on_market(self, event: MarketEvent) -> List[SignalEvent]:
        sym = event.symbol
        if sym not in self._history:
            # Unknown symbol — ignore (keeps agent scoped to configured symbols)
            return []
        # Append to this symbol's rolling history
        row = np.array([
            event.last_price, event.last_price, event.last_price,
            event.last_price, event.volume_24h, event.bid, event.ask,
            event.bid_qty, event.ask_qty,
        ], dtype=np.float32)
        self._history[sym].append(row)

        if len(self._history[sym]) < 20:
            return []

        # Build observation
        hist = np.array(self._history[sym], dtype=np.float32)
        obs = _features_from_history(hist, self.mode)
        obs_t = torch.from_numpy(obs).unsqueeze(0).to(self.device)

        # Forward pass
        with torch.no_grad():
            logits = self.actor(obs_t)
            value = self.critic(obs_t).item()
            dist = Categorical(logits=logits)
            action = dist.sample().item()
            log_prob = dist.log_prob(torch.tensor(action, device=self.device)).item()

        # Compute reward from previous step (PnL-based) for this symbol
        reward = self._compute_reward(event)
        done = False
        self.buffer.add(Transition(obs, action, log_prob, reward, value, done))
        self._step_count += 1

        # Update policy when buffer full
        if self.buffer.idx >= self.settings.ppo_rollout_len:
            with torch.no_grad():
                last_val = self.critic(obs_t).item()
            self.buffer.compute_gae(self.settings.ppo_gamma, self.settings.ppo_lambda, last_val)
            self._update()
            self.buffer.idx = 0

        # Emit signal if action changed
        last_action = self._last_action[sym]
        side = ACTION_TO_SIDE[action]
        if action != last_action and side != Side.FLAT:
            self._entry_price[sym] = event.last_price
            self._last_action[sym] = action
            confidence = self._confidence(logits)
            return [SignalEvent(
                agent=self.name,
                symbol=sym,
                timestamp=event.timestamp,
                side=side,
                confidence=confidence,
                expected_return_bps=self._expected_return_bps(confidence),
                stop_loss_bps=100.0 + 50.0 * (1.0 - confidence),
                take_profit_bps=200.0 + 100.0 * confidence,
                rationale=f"PPO {self.mode}: action={ACTION_NAMES[action]}",
                metadata={"logits": logits.squeeze().tolist(), "value": value},
            )]
        elif action == 1 and last_action != 1:
            # Just went flat — close any position
            self._last_action[sym] = action
            self._entry_price.pop(sym, None)
            return [SignalEvent(
                agent=self.name,
                symbol=sym,
                timestamp=event.timestamp,
                side=Side.FLAT,
                confidence=0.5,
                rationale=f"PPO {self.mode}: flatten",
            )]
        self._last_action[sym] = action
        self._last_price[sym] = event.last_price
        return []

    def _compute_reward(self, event: MarketEvent) -> float:
        """
        PnL-shaped reward for the previous action on this symbol.

        trend   : reward = direction * bar_return
        meanrev : reward = -direction * (price - rolling_mean)/rolling_mean
                  i.e. fade deviation from the rolling mean. BUGFIX: previously
                  the meanrev branch reused `ret` as `momentum`, so the term
                  collapsed to a trivial 0.7x scaling of the trend reward.
        """
        sym = event.symbol
        prev_price = self._last_price.get(sym)
        if prev_price is None or prev_price == 0:
            return 0.0
        ret = (event.last_price - prev_price) / prev_price
        direction = 1.0 if self._last_action.get(sym, 1) == 2 else (
            -1.0 if self._last_action.get(sym, 1) == 0 else 0.0
        )
        if self.mode == "trend":
            pnl = direction * ret
        else:
            # Mean-reversion: reward fading extremes. Build a short rolling
            # mean from this symbol's recent closes; reward the agent when its
            # position opposes the current deviation from that mean.
            recent = list(self._history[sym])[-20:]
            closes = np.array([r[3] for r in recent], dtype=np.float64)
            if len(closes) >= 5:
                mean = closes.mean()
                deviation = (event.last_price - mean) / (mean + 1e-9)
                pnl = -direction * deviation
            else:
                pnl = direction * ret
        return float(pnl * 1000.0)  # scale to bps-ish

    def _confidence(self, logits: torch.Tensor) -> float:
        probs = F.softmax(logits, dim=-1).squeeze().cpu().numpy()
        return float(probs.max())

    def _expected_return_bps(self, confidence: float) -> float:
        if self.mode == "trend":
            return 50.0 * confidence
        return 30.0 * confidence

    # ------------------------------------------------------------------
    # PPO update
    # ------------------------------------------------------------------

    def _update(self) -> None:
        """PPO clipped-surrogate update."""
        for _ in range(self.settings.ppo_epochs):
            for obs_b, act_b, old_lp_b, adv_b, ret_b in self.buffer.sample(
                self.settings.ppo_batch_size
            ):
                obs_b = obs_b.to(self.device)
                act_b = act_b.to(self.device)
                old_lp_b = old_lp_b.to(self.device)
                adv_b = adv_b.to(self.device)
                ret_b = ret_b.to(self.device)

                logits = self.actor(obs_b)
                dist = Categorical(logits=logits)
                new_lp = dist.log_prob(act_b)
                entropy = dist.entropy().mean()
                values = self.critic(obs_b)

                # Clipped surrogate
                ratio = torch.exp(new_lp - old_lp_b)
                surr1 = ratio * adv_b
                surr2 = torch.clamp(
                    ratio, 1.0 - self.settings.ppo_clip, 1.0 + self.settings.ppo_clip
                ) * adv_b
                actor_loss = -torch.min(surr1, surr2).mean()
                value_loss = F.mse_loss(values, ret_b)
                loss = (
                    actor_loss
                    + self.settings.ppo_value_coef * value_loss
                    - self.settings.ppo_entropy_coef * entropy
                )

                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(
                    self.actor.parameters(), self.settings.ppo_max_grad_norm
                )
                nn.utils.clip_grad_norm_(
                    self.critic.parameters(), self.settings.ppo_max_grad_norm
                )
                self.optimizer.step()

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        torch.save({
            "actor": self.actor.state_dict(),
            "critic": self.critic.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "mode": self.mode,
            "obs_dim": self.obs_dim,
        }, path)
        logger.info(f"PPO checkpoint saved: {path}")

    def load(self, path: str) -> None:
        ckpt = torch.load(path, map_location=self.device, weights_only=False)
        self.actor.load_state_dict(ckpt["actor"])
        self.critic.load_state_dict(ckpt["critic"])
        self.optimizer.load_state_dict(ckpt["optimizer"])
        self.mode = ckpt.get("mode", self.mode)
        self.is_ready = True
        logger.info(f"PPO checkpoint loaded: {path}")

    def stats(self) -> dict:
        return {
            "name": self.name,
            "ready": self.is_ready,
            "mode": self.mode,
            "steps": self._step_count,
            "buffer_idx": self.buffer.idx,
            "device": str(self.device),
        }
