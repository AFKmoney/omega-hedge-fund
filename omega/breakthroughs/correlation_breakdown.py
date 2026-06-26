"""B9 — CorrelationBreakdown: detects when asset correlations break down.

Normally BTC and ETH are 0.8+ correlated. When their correlation suddenly drops,
it signals a regime shift — usually a rotation (capital flowing from one to the
other). A correlation breakdown often precedes a volatility spike.
"""
from __future__ import annotations
from collections import deque
from typing import Deque, Dict
import numpy as np
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.correlation")

class CorrelationBreakdown:
    """Detects correlation breakdowns between asset pairs."""
    def __init__(self, window: int = 60) -> None:
        self.window = window
        self._prices: Dict[str, Deque[float]] = {}
        self._correlations: Dict[str, float] = {}
        self._breakdowns: list = []

    def update(self, symbol: str, price: float) -> None:
        if symbol not in self._prices:
            self._prices[symbol] = deque(maxlen=self.window)
        self._prices[symbol].append(price)
        self._recompute()

    def _recompute(self) -> None:
        symbols = list(self._prices.keys())
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                a, b = symbols[i], symbols[j]
                pa = list(self._prices[a])
                pb = list(self._prices[b])
                n = min(len(pa), len(pb))
                if n < 20:
                    continue
                ra = np.diff(np.log(np.array(pa[-n:]) + 1e-9))
                rb = np.diff(np.log(np.array(pb[-n:]) + 1e-9))
                if len(ra) > 5 and np.std(ra) > 0 and np.std(rb) > 0:
                    corr = float(np.corrcoef(ra, rb)[0, 1])
                    pair = f"{a}/{b}"
                    prev = self._correlations.get(pair, corr)
                    self._correlations[pair] = corr
                    if abs(corr - prev) > 0.2 and abs(prev) > 0.5:
                        msg = f"CORRELATION BREAKDOWN: {pair} {prev:.2f}→{corr:.2f}"
                        self._breakdowns.append(msg)
                        logger.warning(msg)

    def stats(self) -> dict:
        return {"name": "correlation_breakdown", "correlations": 
                {k: round(v, 3) for k, v in self._correlations.items()},
                "breakdowns": self._breakdowns[-3:]}
