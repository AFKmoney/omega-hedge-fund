"""
Venue abstraction — exchange-agnostic execution interface.

Both BinanceExecutor and OKXExecutor implement this interface so the rest of
OMEGA (SOR, Blade, orchestrator) never needs to know which exchange it is
talking to. This is what lets us migrate from Binance to OKX (or run both)
without touching the alpha/risk layers.

A Venue is a single exchange connection with these capabilities:
    - submit / cancel orders
    - fetch account balance
    - stream market data (delegated to the matching feed, not here)
    - withdraw funds (via a WalletManager gate, not directly)

The interface is deliberately minimal — only what the ExecutionBlade needs.
Market-data streaming lives in the *Feed* classes (BinanceWebSocketFeed /
OKXWebSocketFeed) which share the MarketEvent contract.
"""

from __future__ import annotations

import abc
from typing import Optional

from omega.utils.events import FillEvent, OrderEvent


class Venue(abc.ABC):
    """Abstract exchange venue."""

    name: str = "abstract"
    dry_run: bool = True

    @abc.abstractmethod
    async def submit(self, order: OrderEvent) -> str:
        """Submit an order. Returns the exchange order id (or a dry-run id)."""
        raise NotImplementedError

    @abc.abstractmethod
    async def cancel(self, exchange_order_id: str) -> bool:
        """Cancel an order by exchange id. Returns True if cancelled."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_balance(self, ccy: str = "USDT") -> float:
        """Return the available balance for a currency."""
        raise NotImplementedError

    async def close(self) -> None:
        """Release any resources (HTTP session, etc). Override if needed."""
        return None
