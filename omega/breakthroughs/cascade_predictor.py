"""B1 — CascadePredictor: predicts liquidation cascades from OI + funding extremes.

When OI is at a high AND funding is extreme, the leverage fuel for a cascade is
maximum. A small price move triggers cascading liquidations. This module scores
the cascade RISK (not direction) by combining OI percentile + funding extremity.
"""
from __future__ import annotations
import math, time
from typing import Optional
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.cascade")

class CascadePredictor:
    """Scores liquidation cascade risk [0,1] from OI + funding extremes."""
    def __init__(self) -> None:
        self._oi_history: list = []
        self._funding: float = 0.0
        self._last_score: float = 0.0

    def update(self, open_interest: float, funding_rate: float) -> float:
        self._funding = funding_rate
        if open_interest > 0:
            self._oi_history.append(open_interest)
            if len(self._oi_history) > 500:
                self._oi_history.pop(0)
        if len(self._oi_history) < 50:
            return 0.0
        # OI percentile: how high is current OI vs recent history?
        sorted_oi = sorted(self._oi_history)
        rank = sum(1 for x in sorted_oi if x <= open_interest) / len(sorted_oi)
        oi_score = max(0.0, (rank - 0.7) / 0.3)  # only top 30% counts
        # Funding extremity
        fund_score = min(1.0, abs(funding_rate) / 0.001)  # 0.1% = max
        # Cascade risk = geometric mean (both must be high)
        risk = math.sqrt(oi_score * fund_score)
        self._last_score = risk
        if risk > 0.6:
            logger.warning(f"Cascade risk HIGH: {risk:.2f} (OI pct={rank:.0%} funding={funding_rate:.6f})")
        return risk

    @property
    def risk(self) -> float:
        return self._last_score

    def stats(self) -> dict:
        return {"name": "cascade_predictor", "risk": round(self._last_score, 3),
                "oi_samples": len(self._oi_history), "funding": self._funding}
