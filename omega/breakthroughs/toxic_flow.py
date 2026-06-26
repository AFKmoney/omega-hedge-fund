"""B6 — ToxicFlowDetector: detects toxic order flow (informed flow).

When large trades consistently execute against the bid (market sells) or ask
(market buys), it signals 'informed' flow — someone knows something. We track
the Cumulative Volume Delta (CVD) and its acceleration. A sudden CVD spike with
rising volume = toxic flow = trend about to accelerate.
"""
from __future__ import annotations
from collections import deque
from typing import Deque
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.toxic_flow")

class ToxicFlowDetector:
    """Detects toxic (informed) order flow from CVD acceleration."""
    def __init__(self, window: int = 100) -> None:
        self._deltas: Deque[float] = deque(maxlen=window)
        self._cvd: float = 0.0
        self._toxicity: float = 0.0

    def on_trade(self, side: str, size: float) -> None:
        delta = size if side.lower() == "buy" else -size
        self._cvd += delta
        self._deltas.append(delta)
        if len(self._deltas) >= 20:
            recent = list(self._deltas[-20:])
            mean = sum(recent) / len(recent)
            # Toxicity = |mean delta| relative to total volume (all positive)
            total = sum(abs(d) for d in recent)
            self._toxicity = abs(mean) / total if total > 0 else 0.0

    @property
    def cvd(self) -> float:
        return self._cvd

    @property
    def toxicity(self) -> float:
        return self._toxicity

    def stats(self) -> dict:
        return {"name": "toxic_flow", "cvd": round(self._cvd, 2),
                "toxicity": round(self._toxicity, 3), "samples": len(self._deltas)}
