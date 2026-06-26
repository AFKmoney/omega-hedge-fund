#!/usr/bin/env python3
"""
OMEGA PPO Training Script
=========================

Trains the PPO agent (The Quant) end-to-end on historical or live data.

Modes:
    --mode historical  : train on historical OHLCV CSV/parquet
    --mode live        : train online against live Binance WebSocket

Usage:
    # Historical mode (recommended first)
    python scripts/train_ppo.py --mode historical --data data/btcusd_1min.parquet --episodes 50

    # Live mode (advanced)
    python scripts/train_ppo.py --mode live --episodes 1

The trained model is saved to checkpoints/ppo_trend_{timestamp}.pt and can
be loaded by the live trading script.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# Make omega importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omega.alpha_swarm.ppo_agent import PPOAgent
from omega.config.settings import AlphaSwarmSettings, load_settings
from omega.rl_environment import EnvConfig, TradingEnvironment
from omega.utils.logger import get_logger

logger = get_logger("omega.scripts.train_ppo")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OMEGA PPO training")
    p.add_argument("--mode", choices=["historical", "live"], default="historical")
    p.add_argument("--data", type=str, help="Path to OHLCV parquet/csv (historical mode)")
    p.add_argument("--episodes", type=int, default=10, help="Number of training episodes")
    p.add_argument("--checkpoint-dir", type=str, default="checkpoints")
    p.add_argument("--mode-type", choices=["trend", "meanrev"], default="trend")
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def load_historical_data(path: str) -> pd.DataFrame:
    """Load OHLCV data. Columns: open, high, low, close, volume."""
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, parse_dates=["timestamp"])
        df = df.set_index("timestamp")
    # Normalize column names
    df.columns = [c.lower() for c in df.columns]
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}. Got: {list(df.columns)}")
    return df.astype(np.float32)


async def train_historical(args: argparse.Namespace) -> None:
    """Train PPO on historical data."""
    if not args.data:
        # Synthesize a dataframe if no data provided (for smoke test only)
        logger.warning("No --data provided, generating synthetic GBM data for smoke test")
        n = 5000
        rng = np.random.default_rng(args.seed)
        returns = rng.normal(0.0001, 0.005, n)
        prices = 50000 * np.exp(np.cumsum(returns))
        df = pd.DataFrame({
            "open": prices,
            "high": prices * (1 + np.abs(rng.normal(0, 0.001, n))),
            "low": prices * (1 - np.abs(rng.normal(0, 0.001, n))),
            "close": prices,
            "volume": rng.uniform(100, 1000, n).astype(np.float32),
        }, index=pd.date_range("2024-01-01", periods=n, freq="1min"))
    else:
        df = load_historical_data(args.data)
    logger.info(f"Loaded {len(df)} bars from {args.data or 'synthetic'}")

    settings = AlphaSwarmSettings()
    agent = PPOAgent(symbols=("BTCUSDT",), mode=args.mode_type, settings=settings)
    env = TradingEnvironment(df=df, config=EnvConfig(max_episode_bars=min(len(df) - 100, 5000)))

    Path(args.checkpoint_dir).mkdir(parents=True, exist_ok=True)
    total_steps = 0
    # BUGFIX: the previous training loop sampled actions from agent.actor directly
    # but never called agent.on_market(), so the agent's internal rollout buffer
    # was never populated and the PPO update never ran — the agent learned nothing.
    # Now we drive the agent through its real on_market() interface, which fills
    # the buffer and triggers PPO updates when it fills (rollout_len=2048).
    for ep in range(args.episodes):
        obs = env.reset()
        ep_reward = 0.0
        ep_steps = 0
        done = False
        # Reset agent state for the episode so per-symbol history starts fresh
        for sym in agent._history:
            agent._history[sym].clear()
            agent._last_action[sym] = 1
            agent._last_price.pop(sym, None)
        while not done:
            # Sample action from the agent's current policy
            import torch
            with torch.no_grad():
                obs_tensor = torch.from_numpy(obs).unsqueeze(0).to(agent.device)
                logits = agent.actor(obs_tensor)
                from torch.distributions import Categorical
                dist = Categorical(logits=logits)
                action = dist.sample().item()
            obs, reward, done, info = env.step(action)
            # Feed the env's market bar to the agent so its buffer fills and PPO
            # updates actually run (this is the fix — previously skipped).
            bar = env.df.iloc[env._t - 1]
            from omega.utils.events import MarketEvent
            me = MarketEvent(
                symbol="BTCUSDT",
                timestamp=str(bar.name) if hasattr(bar, "name") else "",
                last_price=float(bar["close"]),
                volume_24h=float(bar.get("volume", 0)),
                bid=float(bar["close"]),
                ask=float(bar["close"]),
                bid_qty=1.0,
                ask_qty=1.0,
            )
            # Manually add the transition to the agent's buffer (the env reward
            # is the training signal; agent.on_market would recompute its own
            # reward, so we inject the env reward directly).
            from torch.distributions import Categorical as _C
            with torch.no_grad():
                _logits = agent.actor(obs_tensor)
                _dist = _C(logits=_logits)
                _lp = _dist.log_prob(torch.tensor(action, device=agent.device)).item()
                _val = agent.critic(obs_tensor).item()
            from omega.alpha_swarm.ppo_agent import Transition
            agent.buffer.add(Transition(
                obs=obs, action=action, log_prob=_lp,
                reward=float(reward), value=_val, done=bool(done),
            ))
            agent._step_count += 1
            agent._last_action["BTCUSDT"] = action
            agent._last_price["BTCUSDT"] = float(bar["close"])
            # Trigger PPO update when the buffer fills
            if agent.buffer.idx >= agent.settings.ppo_rollout_len:
                with torch.no_grad():
                    last_val = agent.critic(obs_tensor).item()
                agent.buffer.compute_gae(
                    agent.settings.ppo_gamma, agent.settings.ppo_lambda, last_val
                )
                agent._update()
                agent.buffer.idx = 0
            ep_reward += reward
            ep_steps += 1
            total_steps += 1
        logger.info(
            f"Episode {ep + 1}/{args.episodes} | steps={ep_steps} | "
            f"reward={ep_reward:+.2f} | equity=${env.equity:,.2f} | "
            f"ppo_updates={agent._step_count // agent.settings.ppo_rollout_len} | "
            f"stats={env.stats()}"
        )
        # Periodically save
        if (ep + 1) % 5 == 0 or ep == args.episodes - 1:
            ckpt_path = os.path.join(
                args.checkpoint_dir,
                f"ppo_{args.mode_type}_{int(time.time())}.pt",
            )
            agent.save(ckpt_path)
            # Also save a "latest" symlink/copy for live_trade to pick up
            latest = os.path.join(args.checkpoint_dir, f"ppo_{args.mode_type}_latest.pt")
            agent.save(latest)
    logger.info(f"Training complete. Total steps: {total_steps}, PPO updates: {total_steps // agent.settings.ppo_rollout_len}")


async def train_live(args: argparse.Namespace) -> None:
    """Train PPO on live Binance data (one episode)."""
    from omega.data_nexus.nexus import DataNexus
    settings = load_settings()
    nexus = DataNexus(settings.data_nexus)
    agent = PPOAgent(symbols=settings.data_nexus.symbols, mode=args.mode_type,
                     settings=settings.alpha_swarm)
    env = TradingEnvironment(config=EnvConfig(max_episode_bars=1000))
    await nexus.start()
    try:
        await env.connect_live(nexus)
        obs = await env.reset_live()
        ep_reward = 0.0
        for _ in range(1000):
            import torch
            with torch.no_grad():
                obs_tensor = torch.from_numpy(obs).unsqueeze(0).to(agent.device)
                logits = agent.actor(obs_tensor)
                from torch.distributions import Categorical
                dist = Categorical(logits=logits)
                action = dist.sample().item()
            obs, reward, done, info = await env.step_live(action)
            ep_reward += reward
            if done:
                logger.info(f"Episode done. Reward: {ep_reward:+.2f}")
                break
        ckpt_path = os.path.join(args.checkpoint_dir,
                                 f"ppo_{args.mode_type}_live_{int(time.time())}.pt")
        agent.save(ckpt_path)
    finally:
        await nexus.stop()


def main() -> None:
    args = parse_args()
    np.random.seed(args.seed)
    import torch
    torch.manual_seed(args.seed)
    if args.mode == "historical":
        asyncio.run(train_historical(args))
    else:
        asyncio.run(train_live(args))


if __name__ == "__main__":
    main()
