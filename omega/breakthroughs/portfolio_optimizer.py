"""B25 — PortfolioOptimizer: Markowitz mean-variance optimization for allocations.

Given expected returns + covariance matrix for N assets, computes the
optimal portfolio weights that maximize Sharpe ratio. Used to allocate capital
across BTC/ETH/SOL based on their risk-adjusted expected returns, rather than
equal-weighting or gut-feel.

Uses a simplified closed-form solution (tangent portfolio) via NumPy.
"""
from __future__ import annotations
from typing import Dict, List
import numpy as np
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.portfolio_opt")

class PortfolioOptimizer:
    """Markowitz mean-variance portfolio optimizer."""
    def __init__(self, min_weight: float = 0.0, max_weight: float = 0.60,
                 risk_free_rate: float = 0.0) -> None:
        self.min_weight = min_weight
        self.max_weight = max_weight
        self.rf = risk_free_rate
        self._weights: Dict[str, float] = {}

    def optimize(self, expected_returns: Dict[str, float],
                 covariance_matrix: np.ndarray) -> Dict[str, float]:
        """Compute optimal weights maximizing Sharpe ratio.

        Args:
            expected_returns: {symbol: expected_return}
            covariance_matrix: NxN numpy array (same order as dict keys)
        Returns:
            {symbol: optimal_weight}
        """
        symbols = list(expected_returns.keys())
        n = len(symbols)
        if n < 2 or covariance_matrix.shape != (n, n):
            # Can't optimize — equal weight
            w = 1.0 / n
            self._weights = {s: w for s in symbols}
            return self._weights
        mu = np.array([expected_returns[s] for s in symbols])
        cov = covariance_matrix
        try:
            # Tangent portfolio: w ∝ cov^{-1} (mu - rf)
            excess = mu - self.rf
            inv_cov = np.linalg.inv(cov + np.eye(n) * 1e-8)  # regularize
            raw_weights = inv_cov @ excess
            # Normalize to sum=1
            if raw_weights.sum() <= 0:
                raw_weights = np.ones(n)
            raw_weights = np.maximum(raw_weights, 0)  # long-only
            raw_weights /= raw_weights.sum()
            # Clamp individual weights
            raw_weights = np.clip(raw_weights, self.min_weight, self.max_weight)
            raw_weights /= raw_weights.sum()  # renormalize after clamping
            self._weights = {symbols[i]: float(raw_weights[i]) for i in range(n)}
        except Exception as exc:
            logger.warning(f"Portfolio optimization failed: {exc}")
            w = 1.0 / n
            self._weights = {s: w for s in symbols}
        return self._weights

    @property
    def weights(self) -> Dict[str, float]:
        return self._weights

    def expected_sharpe(self, expected_returns: Dict[str, float],
                        covariance_matrix: np.ndarray) -> float:
        """Compute the expected Sharpe ratio of the optimal portfolio."""
        if not self._weights:
            return 0.0
        symbols = list(expected_returns.keys())
        w = np.array([self._weights.get(s, 0) for s in symbols])
        mu = np.array([expected_returns[s] for s in symbols])
        port_return = float(w @ mu)
        port_vol = float(np.sqrt(w @ covariance_matrix @ w))
        return (port_return - self.rf) / (port_vol + 1e-9)

    def stats(self) -> dict:
        return {"name": "portfolio_optimizer",
                "weights": {k: round(v, 3) for k, v in self._weights.items()}}
