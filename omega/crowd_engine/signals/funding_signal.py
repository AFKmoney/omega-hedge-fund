"""
FundingRateSignal — perpetual futures funding rate as a crowd-positioning proxy.

The funding rate is the periodic payment longs pay to shorts (or vice versa)
to keep the perp price tethered to spot. It is the most direct measure of
crowd leverage on the long or short side:

    funding very positive → longs are overcrowded, paying a premium to hold →
                            cascade risk on the long side → we fade by SHORTING
    funding very negative → shorts overcrowded → we fade by going LONG

We ingest the @markPrice stream (already wired in BinanceWebSocketFeed) and
normalize via tanh so the score saturates at extreme funding.

Normalization:
    score = tanh(funding_rate / threshold)
    threshold = 0.0005 (0.05% per 8h funding = ~55% APR — a historically
    extreme level). tanh gives a smooth curve that reaches ~0.76 at threshold
    and ~0.99 at 2×threshold.
"""

from __future__ import annotations

from typing import Dict, Optional

from omega.crowd_engine.signals.base import PositioningSignal, SignalReading
from omega.utils.logger import get_logger

logger = get_logger("omega.crowd_engine.funding")


class FundingRateSignal(PositioningSignal):
    """Crowd positioning from perpetual funding rate."""

    name = "funding"

    def __init__(
        self,
        threshold: float = 0.0005,  # 0.05% per 8h
        weight: float = 0.40,
        horizon: str = "hours",
    ) -> None:
        self.threshold = threshold
        self.weight = weight
        self.horizon = horizon
        # Per-symbol latest funding: symbol -> funding_rate (fraction, e.g. 0.0001)
        self._latest: Dict[str, float] = {}

    def update(self, symbol: str, funding_rate: Optional[float]) -> None:
        """Called by the engine when a new funding rate arrives (from the
        BinanceWebSocketFeed @markPrice stream, already parsed into
        MarketEvent.funding_rate)."""
        if funding_rate is None:
            return
        self._latest[symbol] = float(funding_rate)

    def reading_for(self, symbol: str) -> Optional[SignalReading]:
        rate = self._latest.get(symbol)
        if rate is None:
            return None
        # tanh normalization: positive funding -> crowd long overcrowded -> +score
        import math
        score = math.tanh(rate / self.threshold) if self.threshold > 0 else 0.0
        # Clamp to [-1, 1] defensively
        score = max(-1.0, min(1.0, score))
        return SignalReading(
            score=score,
            horizon=self.horizon,
            weight=self.weight,
            raw={"funding_rate": rate, "threshold": self.threshold},
        )

    # Default no-arg reading() returns the aggregate (mean across symbols);
    # the engine uses reading_for(symbol) per-symbol.
    def reading(self) -> Optional[SignalReading]:
        if not self._latest:
            return None
        import statistics
        vals = list(self._latest.values())
        mean = statistics.fmean(vals)
        import math
        score = max(-1.0, min(1.0, math.tanh(mean / self.threshold)))
        return SignalReading(score=score, horizon=self.horizon, weight=self.weight,
                             raw={"mean_funding": mean, "symbols": len(vals)})

    def stats(self) -> dict:
        return {"name": self.name, "symbols": len(self._latest),
                "latest": dict(self._latest)}
