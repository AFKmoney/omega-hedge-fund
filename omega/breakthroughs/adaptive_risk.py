"""B22 — AdaptiveRiskManager: dynamically adjusts risk based on market regime.

Instead of fixed Kelly fraction + fixed drawdown limit, this module reads the
StressIndex and VolatilityForecast and dynamically scales:
    - High stress → cut position size by 50%, tighten stops
    - Low stress + low vol → allow 1.5x normal size
    - Post-loss streak → automatic cooldown period

This is the 'self-preservation' layer that separates pros from gamblers.
"""
from __future__ import annotations
from collections import deque
from typing import Deque
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.adaptive_risk")

class AdaptiveRiskManager:
    """Dynamically scales risk parameters from market conditions."""
    def __init__(self, base_kelly_fraction: float = 0.25) -> None:
        self.base_kelly = base_kelly_fraction
        self._stress: float = 0.0
        self._vol_regime: str = "normal"
        self._recent_results: Deque[float] = deque(maxlen=10)
        self._size_multiplier: float = 1.0

    def update_stress(self, stress_score: float) -> None:
        self._stress = stress_score

    def update_vol_regime(self, regime: str) -> None:
        self._vol_regime = regime

    def record_trade(self, pnl_bps: float) -> None:
        self._recent_results.append(pnl_bps)
        self._recompute()

    def _recompute(self) -> None:
        mult = 1.0
        # Stress scaling: >50 stress cuts size
        if self._stress > 70:
            mult *= 0.3
        elif self._stress > 50:
            mult *= 0.6
        elif self._stress < 25:
            mult *= 1.3
        # Vol regime scaling
        if self._vol_regime == "extreme":
            mult *= 0.4
        elif self._vol_regime == "high":
            mult *= 0.7
        elif self._vol_regime == "low":
            mult *= 1.2
        # Loss streak penalty
        if len(self._recent_results) >= 3:
            recent = list(self._recent_results[-3:])
            if all(r < 0 for r in recent):
                mult *= 0.5  # 3 losses in a row → halve size
                logger.warning("3 consecutive losses → risk halved (cooldown)")
        self._size_multiplier = max(0.1, min(2.0, mult))

    @property
    def effective_kelly_fraction(self) -> float:
        return self.base_kelly * self._size_multiplier

    @property
    def size_multiplier(self) -> float:
        return self._size_multiplier

    def stats(self) -> dict:
        return {"name": "adaptive_risk", "multiplier": round(self._size_multiplier, 2),
                "effective_kelly": round(self.effective_kelly_fraction, 3),
                "stress": round(self._stress, 1), "vol_regime": self._vol_regime,
                "recent_trades": list(self._recent_results)}
