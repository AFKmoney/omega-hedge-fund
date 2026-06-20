"""
Executor — abstract base class for order execution venues.

One concrete Executor per exchange (BinanceExecutor, CoinbaseExecutor, etc.).
Each executor handles order submission, cancellation, and fill notification
for its venue. The SmartOrderRouter chooses which executor(s) to use per order.
"""

from __future__ import annotations

import abc
from typing import List, Optional

from omega.utils.events import FillEvent, OrderEvent


class Executor(abc.ABC):
    """One execution venue (exchange)."""

    venue: str = "abstract"

    @abc.abstractmethod
    async def submit(self, order: OrderEvent) -> str:
        """Submit an order. Returns the exchange-assigned order ID."""
        ...

    @abc.abstractmethod
    async def cancel(self, exchange_order_id: str) -> bool:
        """Cancel an order. Returns True if cancelled, False if already filled/unknown."""
        ...

    @abc.abstractmethod
    async def cancel_all(self) -> int:
        """Cancel all open orders at this venue. Returns count cancelled."""
        ...

    @abc.abstractmethod
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[dict]:
        ...

    @abc.abstractmethod
    async def fetch_balance(self) -> dict:
        """Return {asset: free_balance} dict."""
        ...
