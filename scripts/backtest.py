#!/usr/bin/env python3
"""
OMEGA Vectorized Backtest Script
================================

Runs a fast vectorized backtest using NumPy/Pandas. Loads historical OHLCV
data, runs the PPO agent + Risk Aegis + simulated Execution Blade, and
reports performance metrics: total return, Sharpe, max drawdown, win rate.

Usage:
    python scripts/backtest.py --data data/btcusd_1min.parquet
    python scripts/backtest.py --data data/btcusd_1min.csv --initial-equity 50000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omega.alpha_swarm.ppo_agent import PPOAgent
from omega.config.settings import AlphaSwarmSettings, RiskAegisSettings
from omega.risk_aegis.aegis import RiskAegis
from omega.risk_aegis.kelly import KellyPositionSizer
from omega.utils.events import SignalEvent, Side
from omega.utils.logger import get_logger

logger = get_logger("omega.scripts.backtest")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OMEGA vectorized backtest")
    p.add_argument("--data", type=str, required=True, help="Path to OHLCV parquet/csv")
    p.add_argument("--initial-equity", type=float, default=100_000.0)
    p.add_argument("--checkpoint", type=str, default=None,
                   help="PPO checkpoint to load (otherwise random policy)")
    p.add_argument("--output", type=str, default=None,
                   help="Path to save results JSON")
    return p.parse_args()


def load_data(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp")
    df.columns = [c.lower() for c in df.columns]
    return df.astype(np.float32)


def backtest(df: pd.DataFrame, initial_equity: float, checkpoint: str | None = None) -> dict:
    """Vectorized backtest of PPO agent + Risk Aegis."""
    settings = AlphaSwarmSettings()
    risk_settings = RiskAegisSettings()
    agent = PPOAgent(symbols=("BTCUSDT",), mode="trend", settings=settings)
    if checkpoint and Path(checkpoint).exists():
        agent.load(checkpoint)
    risk = RiskAegis(risk_settings, initial_equity=initial_equity)
    # State
    equity = initial_equity
    position = 0  # 0=flat, 1=long, -1=short
    entry_price = 0.0
    entry_t = 0
    equity_curve = []
    trades = []
    window = settings.observation_window
    # Iterate
    for t in range(window, len(df)):
        row = df.iloc[t]
        price = float(row["close"])
        # Build observation
        window_df = df.iloc[t - window : t + 1]
        # Update risk aegis with market data
        from omega.utils.events import MarketEvent
        me = MarketEvent(
            symbol="BTCUSDT",
            timestamp=str(row.name),
            last_price=price,
            volume_24h=float(row["volume"]),
            bid=price,
            ask=price,
            bid_qty=1.0,
            ask_qty=1.0,
        )
        risk.on_market(me)
        # Agent action
        import torch
        from omega.alpha_swarm.ppo_agent import _features_from_history
        hist = window_df[["open", "high", "low", "close", "volume"]].values.astype(np.float32)
        # Pad with bid/ask/qty columns
        hist = np.hstack([
            hist,
            hist[:, 3:4],  # bid
            hist[:, 3:4],  # ask
            np.ones((len(hist), 2), dtype=np.float32),
        ])
        obs = _features_from_history(hist, mode="trend")
        with torch.no_grad():
            obs_tensor = torch.from_numpy(obs).unsqueeze(0).to(agent.device)
            logits = agent.actor(obs_tensor)
            from torch.distributions import Categorical
            dist = Categorical(logits=logits)
            action = dist.sample().item()
        # Map action: 0=SHORT, 1=FLAT, 2=LONG
        new_position = -1 if action == 0 else (1 if action == 2 else 0)
        # Position change → execute
        if new_position != position:
            # Close existing position
            if position != 0:
                pnl = position * (price - entry_price) / entry_price * equity * 0.10
                equity += pnl
                trades.append({
                    "entry_t": entry_t,
                    "exit_t": t,
                    "side": "long" if position == 1 else "short",
                    "entry_price": entry_price,
                    "exit_price": price,
                    "pnl": pnl,
                    "holding_bars": t - entry_t,
                })
            # Open new position
            if new_position != 0:
                entry_price = price
                entry_t = t
            position = new_position
        # Mark-to-market equity
        if position != 0 and t > 0:
            prev_price = float(df.iloc[t - 1]["close"])
            ret = (price - prev_price) / prev_price
            equity += position * ret * equity * 0.10
        equity_curve.append(equity)
    # Close any open position at the end
    if position != 0:
        # BUGFIX: the old expression `df.iloc["close" ...]` mixed label and
        # positional indexing and raised TypeError. Use .loc for label access.
        close_col = "close" if "close" in df.columns else df.columns[-1]
        final_price = float(df[close_col].iloc[-1])
        pnl = position * (final_price - entry_price) / entry_price * equity * 0.10
        equity += pnl
        trades.append({
            "entry_t": entry_t,
            "exit_t": len(df) - 1,
            "side": "long" if position == 1 else "short",
            "entry_price": entry_price,
            "exit_price": final_price,
            "pnl": pnl,
            "holding_bars": len(df) - 1 - entry_t,
        })
    # Compute metrics
    equity_arr = np.array(equity_curve)
    returns = np.diff(equity_arr) / equity_arr[:-1] if len(equity_arr) > 1 else np.array([0])
    sharpe = float(np.mean(returns) / (np.std(returns) + 1e-9) * (252 * 1440) ** 0.5)  # 1-min bars
    peak = np.maximum.accumulate(equity_arr)
    max_dd = float(np.max((peak - equity_arr) / peak)) if len(equity_arr) > 0 else 0.0
    winning = [t for t in trades if t["pnl"] > 0]
    win_rate = len(winning) / max(len(trades), 1)
    return {
        "initial_equity": initial_equity,
        "final_equity": equity,
        "total_return_pct": (equity - initial_equity) / initial_equity * 100.0,
        "sharpe": sharpe,
        "max_drawdown_pct": max_dd * 100.0,
        "n_trades": len(trades),
        "win_rate": win_rate,
        "avg_holding_bars": float(np.mean([t["holding_bars"] for t in trades])) if trades else 0.0,
        "risk_aegis_stats": risk.stats(),
    }


def main() -> None:
    args = parse_args()
    df = load_data(args.data)
    logger.info(f"Loaded {len(df)} bars")
    results = backtest(df, args.initial_equity, args.checkpoint)
    print("\n" + "=" * 50)
    print("OMEGA Backtest Results")
    print("=" * 50)
    for k, v in results.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for kk, vv in v.items():
                print(f"    {kk}: {vv}")
        else:
            print(f"  {k}: {v}")
    print("=" * 50)
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        logger.info(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
