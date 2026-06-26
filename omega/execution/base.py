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
    dry_run: bool = True

    @abc.abstractmethod
    async def submit(self, order: OrderEvent) -> str:
        """Submit an order. Returns the exchange order id (or a dry-run id)."""
        raise NotImplementedError

    @abc.abstractmethod
    async def cancel(self, exchange_order_id: str) -> bool:
        """Cancel an order by exchange id. Returns True if cancelled."""
        raise NotImplementedError

    async def get_balance(self, ccy: str = "USDT") -> float:
        """Return the available balance for a currency. Default 0 if unsupported."""
        return 0.0

    async def close(self) -> None:
        """Release any resources (HTTP session, etc). Override if needed."""
        return None

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
