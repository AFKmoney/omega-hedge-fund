#!/usr/bin/env python3
"""
Generate multi-regime synthetic OHLCV data for PPO training.

Real markets cycle through regimes: trending bull, choppy sideways, sharp
crashes, and recovery. A PPO agent trained only on random-walk (GBM) data
learns nothing useful. This script stitches together realistic regime
segments so the agents learn to:
    - ride trends (trend mode)
    - fade extremes (meanrev mode)
    - go flat in choppy / crash conditions

Output: tests/_train_data.csv with columns open/high/low/close/volume +
a timestamp index.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _trending_bull(n: int, start: float, rng) -> np.ndarray:
    """Steady uptrend with normal pullbacks."""
    drift = rng.uniform(0.0001, 0.0003)  # mild positive drift
    vol = rng.uniform(0.002, 0.004)
    rets = rng.normal(drift, vol, n)
    return start * np.exp(np.cumsum(rets))


def _trending_bear(n: int, start: float, rng) -> np.ndarray:
    """Steady downtrend."""
    drift = rng.uniform(-0.0003, -0.0001)
    vol = rng.uniform(0.002, 0.004)
    rets = rng.normal(drift, vol, n)
    return start * np.exp(np.cumsum(rets))


def _choppy(n: int, start: float, rng) -> np.ndarray:
    """Mean-reverting sideways: oscillate around a level."""
    vol = rng.uniform(0.003, 0.005)
    prices = np.empty(n)
    p = start
    half_life = rng.integers(15, 40)
    mean = start
    for i in range(n):
        shock = rng.normal(0, vol)
        pull = (mean - p) / half_life
        p = p * (1 + shock + pull)
        prices[i] = p
    return prices


def _crash(n: int, start: float, rng) -> np.ndarray:
    """Sharp drawdown then stabilization."""
    crash_len = max(10, n // 4)
    crash_rets = rng.normal(-0.003, 0.008, crash_len)
    flat_rets = rng.normal(0.0, 0.003, n - crash_len)
    rets = np.concatenate([crash_rets, flat_rets])
    return start * np.exp(np.cumsum(rets))


def _recovery(n: int, start: float, rng) -> np.ndarray:
    """V-shaped recovery."""
    rets = rng.normal(0.0002, 0.004, n)
    return start * np.exp(np.cumsum(rets))


def generate(seed: int = 42, total_bars: int = 40_000) -> pd.DataFrame:
    """Stitch regime segments into one long series."""
    rng = np.random.default_rng(seed)
    segments = []
    base_price = 50000.0
    price = base_price
    regime_fns = [_trending_bull, _choppy, _trending_bear, _crash, _recovery, _choppy, _trending_bull]
    remaining = total_bars
    while remaining > 0:
        fn = rng.choice(regime_fns)
        seg_len = min(remaining, rng.integers(2000, 6000))
        prices = fn(seg_len, price, rng)
        segments.append(prices)
        price = prices[-1]
        # Re-center toward base if price wanders too far, so regimes don't compound
        if price > base_price * 2.5 or price < base_price * 0.25:
            price = base_price * rng.uniform(0.6, 1.6)
        remaining -= seg_len
    close = np.concatenate(segments)[:total_bars]
    # Hard clamp any pathological spikes (defensive — should be rare after re-centering)
    close = np.clip(close, base_price * 0.05, base_price * 5.0)
    n = len(close)
    # Build OHLCV around close
    intrabar = np.abs(rng.normal(0, 0.0015, n))
    high = close * (1 + intrabar)
    low = close * (1 - intrabar)
    openc = close * (1 + rng.normal(0, 0.0008, n))
    volume = rng.uniform(100, 2000, n).astype(np.float32)
    df = pd.DataFrame({
        "timestamp": pd.date_range("2023-01-01", periods=n, freq="1min"),
        "open": openc.astype(np.float32),
        "high": high.astype(np.float32),
        "low": low.astype(np.float32),
        "close": close.astype(np.float32),
        "volume": volume,
    }).set_index("timestamp")
    return df


def main() -> None:
    out = Path("tests/_train_data.csv")
    df = generate()
    df.to_csv(out)
    # Quick stats
    rets = np.diff(np.log(df["close"].values))
    print(f"Wrote {len(df)} bars to {out}")
    print(f"  price range: ${df['close'].min():,.0f} - ${df['close'].max():,.0f}")
    print(f"  return mean: {rets.mean()*100:.4f}%  std: {rets.std()*100:.4f}%")
    print(f"  final/initial: {df['close'].iloc[-1]/df['close'].iloc[0]:.2f}x")
    # Count regime changes (rough)
    windows = np.array_split(rets, 20)
    means = [w.mean() for w in windows]
    print(f"  segment drifts (sign of regime): {[f'{m*100:.3f}%' for m in means[:10]]} ...")


if __name__ == "__main__":
    main()
