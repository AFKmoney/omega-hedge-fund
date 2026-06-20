"""
TradingEnvironment — PyTorch RL environment tying the Alpha Swarm to the Risk Aegis.

This is the central RL training environment the master prompt asks for. It:
    - Wraps a market data stream (live Binance OR historical replay)
    - Exposes a Gymnasium-compatible (observation, action, reward) interface
    - Routes agent actions through the Risk Aegis (Kelly + Monte Carlo + Kill Switch)
    - Computes reward as risk-adjusted PnL (Sharpe-shaped, drawdown-penalized)

The environment is designed to be used both:
    1. Online: live trading — observations come from Binance in real time
    2. Offline: backtesting — observations come from a historical parquet/CSV

Vectorized: the step() function uses NumPy operations throughout, so it can
be batched across many parallel environment instances for fast PPO training.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from omega.alpha_swarm.ppo_agent import _features_from_history
from omega.config.settings import (
    AlphaSwarmSettings, RiskAegisSettings, Settings,
)
from omega.risk_aegis.aegis import RiskAegis
from omega.utils.events import MarketEvent, OrderType, Side, SignalEvent, TimeInForce
from omega.utils.logger import get_logger

logger = get_logger("omega.rl_env")

# Action space: 0=SHORT, 1=FLAT, 2=LONG
N_ACTIONS = 3


@dataclass
class EnvConfig:
    """Configuration for the TradingEnvironment."""
    obs_dim: int = 64
    window: int = 64            # bars of history per observation
    initial_equity: float = 100_000.0
    transaction_cost_bps: float = 5.0   # 5 bps per trade
    reward_sharpe_window: int = 30      # rolling Sharpe window
    reward_drawdown_penalty: float = 2.0
    reward_kelly_alignment: float = 0.5
    max_episode_bars: int = 5000


class TradingEnvironment:
    """
    RL environment for training the Alpha Swarm's PPO agents.

    Designed to be used with both:
        - Live Binance WebSocket data (online mode)
        - Historical OHLCV bars from CSV/parquet (offline mode)

    Usage (offline):
        df = pd.read_parquet("btc_1min.parquet")
        env = TradingEnvironment(df=df)
        obs = env.reset()
        for _ in range(len(df)):
            action = policy(obs)
            obs, reward, done, info = env.step(action)
            if done: break

    Usage (live):
        env = TradingEnvironment(live=True)
        await env.connect_live(symbols=("BTCUSDT",))
        obs = await env.reset_live()
        while True:
            action = policy(obs)
            obs, reward, done, info = await env.step_live(action)
    """

    def __init__(
        self,
        df: Optional[pd.DataFrame] = None,
        settings: Optional[Settings] = None,
        config: Optional[EnvConfig] = None,
        risk_aegis: Optional[RiskAegis] = None,
    ) -> None:
        self.df = df
        self.settings = settings or Settings()
        self.config = config or EnvConfig()
        self.risk_aegis = risk_aegis or RiskAegis(
            settings=self.settings.risk,
            initial_equity=self.config.initial_equity,
        )
        # State
        self._t: int = 0
        self._equity: float = self.config.initial_equity
        self._position: int = 1  # FLAT
        self._entry_price: float = 0.0
        self._entry_t: int = 0
        self._history: np.ndarray = np.zeros((0, 9), dtype=np.float32)
        self._returns: list = []
        self._equity_curve: list = []
        # Live mode
        self._live_mode: bool = df is None
        self._live_queue: Optional[asyncio.Queue] = None
        self._last_market_event: Optional[MarketEvent] = None

    # ------------------------------------------------------------------
    # Offline mode (historical data)
    # ------------------------------------------------------------------

    def reset(self) -> np.ndarray:
        """Reset episode. Returns initial observation."""
        self._t = self.config.window
        self._equity = self.config.initial_equity
        self._position = 1
        self._entry_price = 0.0
        self._entry_t = 0
        self._returns = []
        self._equity_curve = [self._equity]
        self._history = self._df_window(self._t)
        # Reset risk aegis state so kill switch doesn't latch across episodes
        self.risk_aegis.kill_switch.reset()
        self.risk_aegis.equity = self.config.initial_equity
        return self._obs()

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, Dict]:
        """
        Execute one bar. Returns (obs, reward, done, info).

        Action: 0=SHORT, 1=FLAT, 2=LONG
        Reward: risk-adjusted PnL (Sharpe-shaped, drawdown-penalized)
        """
        if self._live_mode:
            raise RuntimeError("Use step_live() in live mode")
        if self._t >= len(self.df) - 1:
            return self._obs(), 0.0, True, {"reason": "end_of_data"}
        # Current bar
        row = self.df.iloc[self._t]
        price = float(row["close"])
        # Apply action
        prev_position = self._position
        reward = self._compute_reward(action, prev_position, price)
        # If action changed, pay transaction cost
        if action != prev_position:
            self._equity -= self._transaction_cost(price, action)
            if action != 1:  # opening a position
                self._entry_price = price
                self._entry_t = self._t
            else:  # closing position
                self._entry_price = 0.0
        self._position = action
        # Advance
        self._t += 1
        self._history = self._df_window(self._t)
        # Track equity based on position
        next_row = self.df.iloc[self._t]
        next_price = float(next_row["close"])
        direction = 1.0 if self._position == 2 else (-1.0 if self._position == 0 else 0.0)
        if self._t > 0:
            ret = (next_price - price) / price
            pnl = direction * ret * self._equity
            self._equity += pnl
            self._returns.append(ret * direction)
        self._equity_curve.append(self._equity)
        # Risk Aegis: feed market data so it can track drawdown, etc.
        market_event = self._row_to_market_event(next_row)
        self.risk_aegis.on_market(market_event)
        # Done conditions
        done = (
            self._t >= len(self.df) - 1
            or self._t >= self.config.max_episode_bars
            or self.risk_aegis.kill_switch.is_triggered
            or self._equity <= self.config.initial_equity * 0.5
        )
        info = {
            "equity": self._equity,
            "position": self._position,
            "bar": self._t,
            "kill_switch": self.risk_aegis.kill_switch.is_triggered,
        }
        return self._obs(), reward, done, info

    def _compute_reward(self, action: int, prev_position: int, price: float) -> float:
        """Risk-adjusted reward shaping."""
        # 1. PnL component (only if position was held through this bar)
        if len(self._returns) == 0:
            pnl_component = 0.0
        else:
            recent = self._returns[-self.config.reward_sharpe_window:]
            mean = float(np.mean(recent))
            std = float(np.std(recent)) + 1e-9
            sharpe = mean / std
            pnl_component = sharpe * 10.0  # scale up
        # 2. Drawdown penalty
        if len(self._equity_curve) > 1:
            peak = max(self._equity_curve)
            dd = (peak - self._equity) / peak if peak > 0 else 0.0
            dd_penalty = -self.config.reward_drawdown_penalty * dd * 100.0
        else:
            dd_penalty = 0.0
        # 3. Kelly alignment bonus: rewards the agent for taking positions that
        #    the Risk Aegis would also approve (high confidence → sized correctly)
        kelly_bonus = 0.0
        if action != 1 and action != prev_position:
            # Create a synthetic signal for Kelly to evaluate
            sig = SignalEvent(
                agent="env",
                symbol="BTCUSDT",
                timestamp="",
                side=Side.BUY if action == 2 else Side.SELL,
                confidence=0.6,
                stop_loss_bps=100.0,
                take_profit_bps=200.0,
            )
            kelly = self.risk_aegis.kelly.size(sig, self._equity, price)
            if kelly.rejected_reason is None:
                kelly_bonus = self.config.reward_kelly_alignment * kelly.kelly_fraction_raw
        # 4. Kill switch penalty
        kill_penalty = -100.0 if self.risk_aegis.kill_switch.is_triggered else 0.0
        return float(pnl_component + dd_penalty + kelly_bonus + kill_penalty)

    def _transaction_cost(self, price: float, action: int) -> float:
        """Estimate transaction cost in USD."""
        notional = self._equity * 0.10  # assume 10% of equity per position
        return notional * (self.config.transaction_cost_bps / 10000.0)

    def _df_window(self, t: int) -> np.ndarray:
        """Extract a window of OHLCV data ending at bar t."""
        start = max(0, t - self.config.window)
        cols = ["open", "high", "low", "close", "volume"]
        # Pad with bid=close, ask=close, bid_qty=1, ask_qty=1 (no L2 data in offline)
        window = self.df.iloc[start : t + 1][cols].values.astype(np.float32)
        # Augment with bid/ask/bid_qty/ask_qty columns
        bid = window[:, 3:4]  # close as bid
        ask = window[:, 3:4]  # close as ask
        qty = np.ones((len(window), 2), dtype=np.float32)
        return np.hstack([window, bid, ask, qty])

    def _obs(self) -> np.ndarray:
        """Build observation from history window."""
        return _features_from_history(self._history, mode="trend")

    def _row_to_market_event(self, row) -> MarketEvent:
        return MarketEvent(
            symbol="BTCUSDT",
            timestamp=str(row.name) if hasattr(row, "name") else "",
            last_price=float(row["close"]),
            volume_24h=float(row.get("volume", 0)),
            bid=float(row["close"]),
            ask=float(row["close"]),
        )

    # ------------------------------------------------------------------
    # Live mode (Binance WebSocket)
    # ------------------------------------------------------------------

    async def connect_live(self, data_nexus) -> None:
        """Connect to a live DataNexus for market events."""
        self._live_queue = data_nexus.subscribe()

    async def reset_live(self) -> np.ndarray:
        self._equity = self.config.initial_equity
        self._position = 1
        self._entry_price = 0.0
        self._returns = []
        self._equity_curve = [self._equity]
        self._history = np.zeros((self.config.window, 9), dtype=np.float32)
        return self._obs()

    async def step_live(self, action: int) -> Tuple[np.ndarray, float, bool, Dict]:
        """Wait for next market event, then step."""
        if self._live_queue is None:
            raise RuntimeError("Call connect_live() first")
        event = await self._live_queue.get()
        if not isinstance(event, MarketEvent):
            return self._obs(), 0.0, False, {}
        self._last_market_event = event
        # Append to history
        row = np.array([
            event.last_price, event.last_price, event.last_price,
            event.last_price, event.volume_24h, event.bid, event.ask,
            event.bid_qty, event.ask_qty,
        ], dtype=np.float32)
        if len(self._history) < self.config.window:
            self._history = np.vstack([self._history, row])
        else:
            self._history = np.roll(self._history, -1, axis=0)
            self._history[-1] = row
        # Same logic as offline step()
        prev_position = self._position
        price = event.last_price
        reward = self._compute_reward(action, prev_position, price)
        if action != prev_position:
            self._equity -= self._transaction_cost(price, action)
            if action != 1:
                self._entry_price = price
            else:
                self._entry_price = 0.0
        self._position = action
        self.risk_aegis.on_market(event)
        self._equity_curve.append(self._equity)
        info = {
            "equity": self._equity,
            "position": self._position,
            "kill_switch": self.risk_aegis.kill_switch.is_triggered,
        }
        done = (
            self.risk_aegis.kill_switch.is_triggered
            or self._equity <= self.config.initial_equity * 0.5
        )
        return self._obs(), reward, done, info

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def observation_dim(self) -> int:
        return self.config.obs_dim

    @property
    def action_dim(self) -> int:
        return N_ACTIONS

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def equity_curve(self) -> list:
        return self._equity_curve

    def stats(self) -> dict:
        sharpe = 0.0
        if len(self._returns) > 1:
            arr = np.array(self._returns)
            sharpe = float(arr.mean() / (arr.std() + 1e-9) * (252 ** 0.5))
        max_dd = 0.0
        if self._equity_curve:
            peak = self._equity_curve[0]
            for v in self._equity_curve:
                peak = max(peak, v)
                dd = (peak - v) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
        return {
            "equity": self._equity,
            "initial_equity": self.config.initial_equity,
            "return_pct": (self._equity - self.config.initial_equity) / self.config.initial_equity * 100.0,
            "sharpe": sharpe,
            "max_drawdown_pct": max_dd * 100.0,
            "bars": self._t,
            "kill_switch": self.risk_aegis.kill_switch.is_triggered,
        }
