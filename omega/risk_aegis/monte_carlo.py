"""
MonteCarloEngine — real-time drawdown probability estimation.

Every `monte_carlo_refresh_sec` seconds, runs N=10,000 simulations of the
next H bars using a bootstrap of recent returns. If the probability of a
>2% drawdown within the horizon exceeds a threshold, returns a position
size multiplier < 1.0 (de-risking).

Uses vectorized NumPy — 10K paths × 30 bars = 300K samples in <50ms.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Optional

import numpy as np

from omega.config.settings import RiskAegisSettings
from omega.utils.logger import get_logger

logger = get_logger("omega.risk_aegis.monte_carlo")


class MonteCarloEngine:
    """Vectorized bootstrap Monte Carlo for drawdown estimation."""

    def __init__(self, settings: Optional[RiskAegisSettings] = None) -> None:
        self.settings = settings or RiskAegisSettings()
        self._returns: Deque[float] = deque(maxlen=500)
        self._last_run: float = 0.0
        self._last_pnl_prob: float = 0.0
        self._last_dd_prob: float = 0.0
        self._last_multiplier: float = 1.0
        self._rng = np.random.default_rng(seed=42)

    def on_return(self, ret: float) -> None:
        """Add one realized return to the bootstrap pool."""
        self._returns.append(ret)

    def should_rerun(self) -> bool:
        return (time.time() - self._last_run) >= self.settings.monte_carlo_refresh_sec

    def run(self, current_equity: float, current_position_value: float) -> float:
        """
        Run Monte Carlo. Returns a position-size multiplier in [0.0, 1.0].
        1.0 = no de-risking. 0.0 = full liquidation recommended.
        """
        if len(self._returns) < 50:
            return 1.0
        returns = np.array(self._returns, dtype=np.float64)
        # Filter extreme outliers for bootstrap stability
        returns = returns[np.abs(returns) < np.std(returns) * 5 + 1e-9]
        if len(returns) < 30:
            return 1.0
        n_paths = self.settings.monte_carlo_paths
        horizon = self.settings.monte_carlo_horizon_bars
        # Bootstrap sample: (n_paths, horizon)
        idx = self._rng.integers(0, len(returns), size=(n_paths, horizon))
        sampled = returns[idx]
        # Cumulative returns
        cum = np.cumprod(1.0 + sampled, axis=1)
        # Position P&L paths (assuming current_position_value stays constant)
        pnl_paths = current_position_value * (cum - 1.0)
        # Drawdown from peak along each path
        peaks = np.maximum.accumulate(pnl_paths, axis=1)
        drawdowns = peaks - pnl_paths
        # Worst drawdown per path
        max_dd_per_path = drawdowns.max(axis=1)
        # Probability of >X% drawdown on the position
        threshold = current_position_value * (self.settings.monte_carlo_max_drawdown_pct / 100.0)
        if threshold <= 0:
            dd_prob = 0.0
        else:
            dd_prob = float((max_dd_per_path > threshold).mean())
        # Probability of positive PnL (sanity check)
        pnl_prob = float((pnl_paths[:, -1] > 0).mean())
        # Position-size multiplier: linearly scale down as dd_prob goes from 0.3 to 0.8
        # 0.0–0.3 → 1.0 (no reduction), 0.8+ → 0.2 (heavy reduction)
        if dd_prob < 0.3:
            multiplier = 1.0
        elif dd_prob > 0.8:
            multiplier = 0.2
        else:
            # Linear: 1.0 at p=0.3, 0.2 at p=0.8
            multiplier = 1.0 - ((dd_prob - 0.3) / 0.5) * 0.8
        self._last_run = time.time()
        self._last_pnl_prob = pnl_prob
        self._last_dd_prob = dd_prob
        self._last_multiplier = float(multiplier)
        if multiplier < 0.8:
            logger.warning(
                f"Monte Carlo de-risking: dd_prob={dd_prob:.2f} "
                f"pnl_prob={pnl_prob:.2f} multiplier={multiplier:.2f}",
                extra={"component": "risk_aegis.monte_carlo"},
            )
        return self._last_multiplier

    def stats(self) -> dict:
        return {
            "last_run_age_sec": time.time() - self._last_run,
            "last_dd_prob": self._last_dd_prob,
            "last_pnl_prob": self._last_pnl_prob,
            "last_multiplier": self._last_multiplier,
            "returns_buffered": len(self._returns),
        }
