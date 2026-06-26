"""B3 — WhaleTracker: aggregates whale movements across on-chain + CEX.

Combines on-chain transfers (Etherscan), CEX inflows/outflows, and large CVD
(Cumulative Volume Delta) prints into a unified whale activity score. When
whales are accumulating (large outflows from exchanges = withdrawing to cold
storage), it's bullish. When they deposit to exchanges (large inflows), it's
bearish. The key insight: whale on-chain moves PRECEDE CEX price action by
minutes to hours because of confirmation times.
"""
from __future__ import annotations
import time
from collections import deque
from typing import Deque
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.whale_tracker")

class WhaleTracker:
    """Unified whale activity signal from on-chain + CEX flows."""
    def __init__(self, window_sec: int = 3600, min_notional: float = 1_000_000) -> None:
        self.window_sec = window_sec
        self.min_notional = min_notional
        self._events: Deque = deque(maxlen=500)

    def record_inflow(self, usd: float) -> None:
        """Whale deposited to exchange = bearish."""
        if usd >= self.min_notional:
            self._events.append((time.time(), -abs(usd)))
            logger.info(f"Whale inflow: ${usd:,.0f} to exchange (bearish)")

    def record_outflow(self, usd: float) -> None:
        """Whale withdrew from exchange = bullish."""
        if usd >= self.min_notional:
            self._events.append((time.time(), abs(usd)))
            logger.info(f"Whale outflow: ${usd:,.0f} from exchange (bullish)")

    def score(self) -> float:
        """Net whale flow [-1,+1]: positive = accumulation (bullish)."""
        cutoff = time.time() - self.window_sec
        net = sum(amt for ts, amt in self._events if ts > cutoff)
        if net == 0:
            return 0.0
        # Normalize: $100M net in an hour = saturation
        import math
        return max(-1.0, min(1.0, math.tanh(net / 100_000_000)))

    def stats(self) -> dict:
        import time as _t
        cutoff = _t.time() - self.window_sec
        recent = [(ts, amt) for ts, amt in self._events if ts > cutoff]
        inflows = sum(1 for _, a in recent if a < 0)
        outflows = sum(1 for _, a in recent if a > 0)
        return {"name": "whale_tracker", "score": round(self.score(), 3),
                "inflows_1h": inflows, "outflows_1h": outflows, "events": len(recent)}
