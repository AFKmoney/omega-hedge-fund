"""B2 — FundingForecast: predicts the next funding rate from basis + trend.

The funding rate is partially predictable: when the perp price trades at a
premium to spot (positive basis), the next funding will likely be positive.
By tracking the basis (perp - spot) and its rate of change, we forecast whether
the next funding event will squeeze longs or shorts — positioning ahead of it.
"""
from __future__ import annotations
from collections import deque
from typing import Deque
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.funding_forecast")

class FundingForecast:
    """Forecasts next funding rate from perp-spot basis momentum."""
    def __init__(self, window: int = 60) -> None:
        self._basis: Deque[float] = deque(maxlen=window)
        self._forecast: float = 0.0

    def update(self, perp_price: float, spot_price: float) -> float:
        if spot_price <= 0:
            return 0.0
        basis = (perp_price - spot_price) / spot_price
        self._basis.append(basis)
        if len(self._basis) < 10:
            return 0.0
        # Basis momentum = recent average + trend
        recent = list(self._basis)
        avg = sum(recent[-20:]) / min(20, len(recent))
        # Trend = slope of last 10 vs previous 10
        if len(recent) >= 20:
            slope = sum(recent[-10:]) / 10 - sum(recent[-20:-10]) / 10
        else:
            slope = 0.0
        self._forecast = (avg + slope) * 8  # scale to 8h funding equivalent
        return self._forecast

    @property
    def forecast(self) -> float:
        return self._forecast

    def stats(self) -> dict:
        return {"name": "funding_forecast", "next_funding_est": round(self._forecast, 6),
                "samples": len(self._basis)}
