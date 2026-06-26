"""
IcebergDetectionSignal — passive iceberg-order detection from public depth.

Whales hide their true order size using "iceberg" orders: a 50M order displays
only a 100k slice, and refills the visible slice the instant it is consumed.

We detect this PASSIVELY from the depth feed — we do NOT send probing orders
(which would cost spread + tip off the whale + risk our own fills). Instead we
watch the depth_bids/depth_asks snapshots that the feed already publishes:

    If a price level's visible quantity is repeatedly *replenished* shortly
    after trades consume it, while the price itself barely moves, that level is
    the visible tip of an iceberg. The size of the hidden reserve is inferred
    from how aggressively it absorbs incoming flow without yielding price.

Score:
    + if a large BID iceberg is detected → hidden buy demand → crowd/buyers
      building a wall → bullish pressure BUT also a magnet for stops below
      it. We score it as "crowd long leaning on a wall" (positive), which the
      contrarian fades by selling into the wall's eventual failure.
    - if a large ASK iceberg → crowd short leaning → negative.

This is a microstructure proxy, horizon "minutes".
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

from omega.crowd_engine.signals.base import PositioningSignal, SignalReading
from omega.utils.events import MarketEvent
from omega.utils.logger import get_logger

logger = get_logger("omega.crowd_engine.iceberg")

# How many recent depth snapshots to keep per symbol
_HISTORY = 30
# Min qty (in base currency) at a level to be considered "wall-like"
_WALL_QTY_THRESHOLD = 5.0
# How many times a level must refill within the window to count as iceberg
_REFILL_COUNT_THRESHOLD = 3


class IcebergDetectionSignal(PositioningSignal):
    """Passive iceberg detection from public depth snapshots."""

    name = "iceberg"

    def __init__(
        self,
        weight: float = 0.25,
        horizon: str = "minutes",
        wall_qty_threshold: float = _WALL_QTY_THRESHOLD,
        refill_threshold: int = _REFILL_COUNT_THRESHOLD,
    ) -> None:
        self.weight = weight
        self.horizon = horizon
        self.wall_qty_threshold = wall_qty_threshold
        self.refill_threshold = refill_threshold
        # symbol -> deque of (ts, best_bid_px, best_bid_qty, best_ask_px, best_ask_qty)
        self._snapshots: Dict[str, Deque[Tuple[float, float, float, float, float]]] = {}

    def update_from_market(self, event: MarketEvent) -> None:
        """Called by the engine on each market event; we only care about depth."""
        if not event.depth_bids and not event.depth_asks:
            # Still track top-of-book for refill detection
            sym = event.symbol
            snap = (time.time(), event.bid, event.bid_qty, event.ask, event.ask_qty)
            self._snapshots.setdefault(sym, deque(maxlen=_HISTORY)).append(snap)
            return
        sym = event.symbol
        # Use the top-of-book from the depth snapshot
        bid_px = event.depth_bids[0][0] if event.depth_bids else event.bid
        bid_q = event.depth_bids[0][1] if event.depth_bids else event.bid_qty
        ask_px = event.depth_asks[0][0] if event.depth_asks else event.ask
        ask_q = event.depth_asks[0][1] if event.depth_asks else event.ask_qty
        self._snapshots.setdefault(sym, deque(maxlen=_HISTORY)).append(
            (time.time(), bid_px, bid_q, ask_px, ask_q)
        )

    def reading_for(self, symbol: str) -> Optional[SignalReading]:
        snaps = self._snapshots.get(symbol)
        if not snaps or len(snaps) < 5:
            return None
        bid_refills = self._count_refills(snaps, side="bid")
        ask_refills = self._count_refills(snaps, side="ask")
        # Net iceberg pressure: more bid icebergs = crowd leaning long on walls
        net = bid_refills - ask_refills
        score = max(-1.0, min(1.0, net / (self.refill_threshold * 2.0)))
        return SignalReading(
            score=score,
            horizon=self.horizon,
            weight=self.weight,
            raw={"bid_iceberg_refills": bid_refills, "ask_iceberg_refills": ask_refills},
        )

    def reading(self) -> Optional[SignalReading]:
        return None

    def _count_refills(self, snaps: Deque, side: str) -> int:
        """Count how many times the top-of-book qty on one side dipped then
        returned to a 'wall' level without the price moving much."""
        idx = 1 if side == "bid" else 3  # qty index
        px_idx = 0 if side == "bid" else 2
        refills = 0
        was_consumed = False
        prev_qty = 0.0
        prev_px = 0.0
        for _ts, bpx, bqty, apx, aqty in snaps:
            qty = bqty if side == "bid" else aqty
            px = bpx if side == "bid" else apx
            if prev_px > 0 and abs(px - prev_px) / (prev_px + 1e-9) < 0.0005:
                # Price stable — check for consume-then-refill pattern
                if prev_qty >= self.wall_qty_threshold and qty < prev_qty * 0.3:
                    was_consumed = True
                elif was_consumed and qty >= self.wall_qty_threshold:
                    refills += 1
                    was_consumed = False
            else:
                was_consumed = False
            prev_qty = qty
            prev_px = px
        return refills

    def stats(self) -> dict:
        return {
            "name": self.name,
            "symbols_tracked": list(self._snapshots.keys()),
        }
