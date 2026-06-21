"""
SmartOrderRouter — chooses the best execution venue + algorithm per order.

Currently supports a single venue (Binance) but designed for multi-venue:
when more executors are added (Coinbase, Kraken, NYSE), the SOR queries each
for the current best bid/ask and routes the order to whichever offers the
lowest expected slippage (accounting for fee tiers and latency).

Algorithm selection logic:
    - Small order (< $1k): single market order, no slicing
    - Medium ($1k–$50k): TWAP with 5 slices
    - Large ($50k–$500k): VWAP with participation_rate=0.10
    - XLarge (> $500k): Iceberg with 5% display qty
    - RL agent override: if ExecutionRLAgent is trained, it can override
      the heuristic choice per order based on order-book features
"""

from __future__ import annotations

from typing import Dict, List, Optional

from omega.execution.base import Executor
from omega.execution.algorithms import Iceberg, TWAP, VWAP
from omega.execution.binance_executor import BinanceExecutor
from omega.utils.events import OrderEvent, OrderType
from omega.utils.logger import get_logger

logger = get_logger("omega.execution.sor")


class SmartOrderRouter:
    """Multi-venue order router with algorithm selection."""

    def __init__(self, executors: Optional[List[Executor]] = None) -> None:
        self.executors: Dict[str, Executor] = {}
        for ex in executors or [BinanceExecutor()]:
            self.executors[ex.venue] = ex

    def get_venue(self, name: str = "binance") -> Executor:
        if name not in self.executors:
            raise KeyError(f"Venue '{name}' not registered. Available: {list(self.executors)}")
        return self.executors[name]

    def select_algorithm(
        self, order: OrderEvent, reference_price: Optional[float] = None
    ):
        """
        Pick the execution algorithm based on order notional and book conditions.
        Returns one of: None (single market order), TWAP, VWAP, Iceberg.

        `reference_price` is the current mid/last price for the symbol; it is
        used to estimate notional for MARKET orders (which have no limit_price).
        BUGFIX: previously this used order.limit_price, which is None for MARKET
        orders, so notional was always 0 and TWAP was chosen for every order
        regardless of size.
        """
        price = order.limit_price if order.limit_price else reference_price
        notional = order.qty * (price or 0.0)
        # If we don't know notional, assume medium
        if notional == 0:
            notional = 10_000.0
        if notional < 1_000:
            return None
        elif notional < 50_000:
            return TWAP(slices=5, interval_sec=3)
        elif notional < 500_000:
            return VWAP(participation_rate=0.10, interval_sec=5)
        else:
            return Iceberg(display_qty_pct=0.05, refresh_sec=2)

    async def route(
        self, order: OrderEvent, venue: str = "binance",
        reference_price: Optional[float] = None,
    ) -> str:
        """Route an order to the chosen venue. Returns exchange order ID."""
        executor = self.get_venue(venue)
        algo = self.select_algorithm(order, reference_price=reference_price)
        if algo is None:
            return await executor.submit(order)
        # Use the algorithm to slice the parent order
        from omega.execution.algorithms import TWAP, VWAP, Iceberg
        exchange_ids: List[str] = []
        async for child in algo.slice(order):
            # Build a child OrderEvent
            child_order = OrderEvent(
                symbol=child.symbol,
                side=order.side,
                qty=child.qty,
                order_type=child.order_type,
                limit_price=child.limit_price,
                time_in_force=order.time_in_force,
                strategy=order.strategy,
                risk_score=order.risk_score,
                metadata={**order.metadata, "child_slice": child.slice_index},
            )
            ex_id = await executor.submit(child_order)
            if ex_id:
                exchange_ids.append(ex_id)
        logger.info(
            f"SOR routed {order.symbol} via {type(algo).__name__}: "
            f"{len(exchange_ids)} child fills",
            extra={"component": "execution.sor", "symbol": order.symbol},
        )
        return ",".join(exchange_ids)

    async def cancel_all(self) -> int:
        """Cancel all open orders across all venues. Used by Kill Switch."""
        total = 0
        for ex in self.executors.values():
            total += await ex.cancel_all()
        return total

    async def close(self) -> None:
        for ex in self.executors.values():
            if hasattr(ex, "close"):
                await ex.close()
