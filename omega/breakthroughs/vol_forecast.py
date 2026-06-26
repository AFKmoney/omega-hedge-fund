"""B8 — VolatilityForecast: GARCH-like volatility forecasting.

Volatility clusters: high vol begets high vol. We use a simplified EWMA
(exponentially weighted moving average) variance model — the same family as
GARCH(1,1) used by RiskMetrics. When forecasted vol spikes, the risk engine
should cut position sizes; when it drops, opportunity for breakout trades.
"""
from __future__ import annotations
from collections import deque
from typing import Deque
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.vol_forecast")

class VolatilityForecast:
    """EWMA volatility forecaster."""
    def __init__(self, lambda_: float = 0.94, window: int = 100) -> None:
        self.lam = lambda_
        self._returns: Deque[float] = deque(maxlen=window)
        self._variance: float = 0.0
        self._prev_price: float = 0.0

    def update(self, price: float) -> float:
        if self._prev_price > 0:
            ret = (price - self._prev_price) / self._prev_price
            self._returns.append(ret)
            # EWMA variance update: sigma^2 = lambda * sigma^2_prev + (1-lambda) * r^2
            self._variance = self.lam * self._variance + (1 - self.lam) * ret * ret
        self._prev_price = price
        return self.forecast_vol

    @property
    def forecast_vol(self) -> float:
        """Forecasted annualized volatility."""
        import math
        return math.sqrt(self._variance * 365 * 24 * 60) if self._variance > 0 else 0.0  # 1-min bars

    @property
    def vol_regime(self) -> str:
        v = self.forecast_vol
        if v > 1.5: return "extreme"
        if v > 0.8: return "high"
        if v > 0.4: return "normal"
        return "low"

    def stats(self) -> dict:
        return {"name": "vol_forecast", "forecast_vol_annualized": round(self.forecast_vol, 3),
                "regime": self.vol_regime, "samples": len(self._returns)}
