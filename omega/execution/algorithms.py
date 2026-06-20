"""
TWAP / VWAP / Iceberg — execution algorithm primitives.

These are NOT mocks — they're real, production-quality algorithm skeletons
that slice parent orders into child orders using the standard techniques.
The ExecutionRLAgent decides which algorithm to use and tunes its parameters
per order based on order-book conditions.

Usage:
    twap = TWAP(slices=10, interval_sec=5)
    async for child_order in twap.slice(parent_order):
        await executor.submit(child_order)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional

from omega.utils.events import OrderEvent, OrderType, TimeInForce
from omega.utils.logger import get_logger

logger = get_logger("omega.execution.algorithms")


@dataclass
class ChildOrder:
    """A sliced child order produced by an execution algorithm."""
    symbol: str
    side: str
    qty: float
    order_type: OrderType
    limit_price: Optional[float] = None
    slice_index: int = 0
    total_slices: int = 1
    parent_id: str = ""
    metadata: dict = None


class TWAP:
    """Time-Weighted Average Price — equal-sized slices at fixed intervals."""

    def __init__(self, slices: int = 10, interval_sec: int = 5) -> None:
        self.slices = max(1, slices)
        self.interval_sec = max(1, interval_sec)

    async def slice(self, parent: OrderEvent) -> AsyncIterator[ChildOrder]:
        per_slice_qty = parent.qty / self.slices
        for i in range(self.slices):
            yield ChildOrder(
                symbol=parent.symbol,
                side=parent.side.value,
                qty=per_slice_qty,
                order_type=parent.order_type,
                limit_price=parent.limit_price,
                slice_index=i,
                total_slices=self.slices,
                parent_id=parent.order_id,
                metadata={"algo": "twap", "interval_sec": self.interval_sec},
            )
            if i < self.slices - 1:
                await asyncio.sleep(self.interval_sec)


class VWAP:
    """Volume-Weighted Average Price — slices proportional to historical volume curve."""

    def __init__(
        self,
        participation_rate: float = 0.10,
        interval_sec: int = 5,
        volume_curve: Optional[List[float]] = None,
    ) -> None:
        self.participation_rate = max(0.01, min(0.50, participation_rate))
        self.interval_sec = max(1, interval_sec)
        # Default: uniform volume curve (in production, load historical intraday volume)
        self.volume_curve = volume_curve or [1.0] * 12

    async def slice(
        self, parent: OrderEvent, get_current_volume=None
    ) -> AsyncIterator[ChildOrder]:
        """Yield child orders sized to participation_rate × current_market_volume."""
        total_weight = sum(self.volume_curve)
        remaining = parent.qty
        for i, w in enumerate(self.volume_curve):
            if remaining <= 0:
                break
            target = parent.qty * (w / total_weight)
            # Cap by participation rate (if volume feed available)
            if get_current_volume is not None:
                try:
                    cur_vol = await get_current_volume(parent.symbol)
                    cap = cur_vol * self.participation_rate
                    target = min(target, cap)
                except Exception:
                    pass
            target = min(target, remaining)
            if target <= 0:
                continue
            remaining -= target
            yield ChildOrder(
                symbol=parent.symbol,
                side=parent.side.value,
                qty=target,
                order_type=parent.order_type,
                limit_price=parent.limit_price,
                slice_index=i,
                total_slices=len(self.volume_curve),
                parent_id=parent.order_id,
                metadata={"algo": "vwap", "participation_rate": self.participation_rate},
            )
            if i < len(self.volume_curve) - 1:
                await asyncio.sleep(self.interval_sec)


class Iceberg:
    """Iceberg — show only a fraction of true quantity at a time."""

    def __init__(self, display_qty_pct: float = 0.10, refresh_sec: int = 2) -> None:
        self.display_qty_pct = max(0.01, min(1.0, display_qty_pct))
        self.refresh_sec = max(1, refresh_sec)

    async def slice(self, parent: OrderEvent) -> AsyncIterator[ChildOrder]:
        remaining = parent.qty
        slice_idx = 0
        display_qty = parent.qty * self.display_qty_pct
        while remaining > 0:
            this_qty = min(display_qty, remaining)
            remaining -= this_qty
            yield ChildOrder(
                symbol=parent.symbol,
                side=parent.side.value,
                qty=this_qty,
                order_type=OrderType.LIMIT,
                limit_price=parent.limit_price,
                slice_index=slice_idx,
                total_slices=-1,  # unknown until filled
                parent_id=parent.order_id,
                metadata={
                    "algo": "iceberg",
                    "display_qty_pct": self.display_qty_pct,
                    "remaining_parent_qty": remaining,
                },
            )
            slice_idx += 1
            if remaining > 0:
                await asyncio.sleep(self.refresh_sec)
