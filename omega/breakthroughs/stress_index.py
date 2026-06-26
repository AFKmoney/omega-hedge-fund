"""B20 — StressIndex: composite market stress indicator.

Combines multiple stress signals (volatility, funding, depeg, spread, correlation)
into a single 0-100 stress index — the crypto equivalent of the VIX. When stress
is high (>70), the bot should be defensive (small positions, tight stops).
When stress is low (<30), the bot can be aggressive.
"""
from __future__ import annotations
from typing import Optional
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.stress")

class StressIndex:
    """Composite market stress score [0-100]."""
    def __init__(self) -> None:
        self._vol_score: float = 0.0
        self._funding_score: float = 0.0
        self._depeg_score: float = 0.0
        self._spread_score: float = 0.0
        self._stress: float = 0.0

    def update_volatility(self, vol_annualized: float) -> None:
        # Map 0-3.0 annualized vol to 0-100
        self._vol_score = min(100.0, vol_annualized / 3.0 * 100)

    def update_funding(self, funding_rate: float) -> None:
        # Extreme funding = stressed
        self._funding_score = min(100.0, abs(funding_rate) / 0.002 * 100)

    def update_depeg(self, max_deviation: float) -> None:
        self._depeg_score = min(100.0, max_deviation / 0.02 * 100)

    def update_spread(self, spread_bps: float, normal_spread_bps: float = 5.0) -> None:
        ratio = spread_bps / (normal_spread_bps + 1e-9)
        self._spread_score = min(100.0, (ratio - 1.0) * 50)

    def compute(self) -> float:
        """Weighted average of all stress components."""
        self._stress = (
            self._vol_score * 0.30 +
            self._funding_score * 0.25 +
            self._depeg_score * 0.25 +
            self._spread_score * 0.20
        )
        if self._stress > 70:
            logger.warning(f"Market STRESS HIGH: {self._stress:.0f}/100")
        return self._stress

    @property
    def stress(self) -> float:
        return self._stress

    @property
    def regime(self) -> str:
        if self._stress > 70: return "crisis"
        if self._stress > 50: return "stressed"
        if self._stress > 30: return "normal"
        return "calm"

    def stats(self) -> dict:
        return {"name": "stress_index", "stress": round(self._stress, 1),
                "regime": self.regime,
                "components": {"vol": round(self._vol_score, 1),
                               "funding": round(self._funding_score, 1),
                               "depeg": round(self._depeg_score, 1),
                               "spread": round(self._spread_score, 1)}}
