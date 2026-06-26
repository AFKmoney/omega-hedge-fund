"""B7 — SmartMoneyDivergence: spots when 'smart money' diverges from retail.

Retail buys the top (high volume on green candles at peaks). Smart money
distributes into that buying (selling into the rally). We detect this by
comparing candle direction vs CVD: if price is rising but CVD is falling
(distribution), smart money is selling into retail FOMO.
"""
from __future__ import annotations
from collections import deque
from typing import Deque
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.smart_money")

class SmartMoneyDivergence:
    """Detects bullish/bearish divergence between price and CVD."""
    def __init__(self, window: int = 20) -> None:
        self._prices: Deque[float] = deque(maxlen=window)
        self._cvds: Deque[float] = deque(maxlen=window)
        self._divergence: float = 0.0

    def update(self, price: float, cvd: float) -> float:
        self._prices.append(price)
        self._cvds.append(cvd)
        if len(self._prices) < 10:
            return 0.0
        # Compute slopes
        p = list(self._prices)
        c = list(self._cvds)
        n = len(p)
        p_slope = (p[-1] - p[-n//2]) / (p[-n//2] + 1e-9)
        c_slope = (c[-1] - c[-n//2]) / (abs(c[-n//2]) + 1e-9)
        # Divergence: price up + CVD down = bearish (distribution)
        # price down + CVD up = bullish (accumulation)
        self._divergence = c_slope - p_slope  # positive = CVD leads price up
        return self._divergence

    @property
    def divergence(self) -> float:
        return self._divergence

    def stats(self) -> dict:
        direction = "accumulation" if self._divergence > 0.01 else (
            "distribution" if self._divergence < -0.01 else "neutral")
        return {"name": "smart_money_div", "divergence": round(self._divergence, 4),
                "interpretation": direction, "samples": len(self._prices)}
