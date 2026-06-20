"""
Abstract base classes for Data Nexus sources and sinks.

Every concrete data feed (Binance, Etherscan, RSS, FRED) implements `DataSource`.
Every concrete transport (Kafka, in-process queue) implements `DataSink`.
This separation lets us swap any feed or transport without touching the rest
of the system.
"""

from __future__ import annotations

import abc
from typing import Any, AsyncIterator, Optional


class DataSource(abc.ABC):
    """A streaming source of typed events (MarketEvent, NewsEvent, etc.)."""

    name: str = "abstract"

    @abc.abstractmethod
    async def stream(self) -> AsyncIterator[Any]:
        """Yield events forever (or until the source closes)."""
        raise NotImplementedError
        yield  # pragma: no cover  (makes mypy happy with AsyncIterator return)

    async def start(self) -> None:
        """Optional hook for connection setup."""
        pass

    async def stop(self) -> None:
        """Optional hook for graceful shutdown."""
        pass


class DataSink(abc.ABC):
    """A durable transport for publishing and consuming events."""

    @abc.abstractmethod
    async def publish(self, topic: str, event: Any) -> None:
        """Publish a single event to a topic."""
        raise NotImplementedError

    @abc.abstractmethod
    async def subscribe(self, topic: str) -> AsyncIterator[Any]:
        """Subscribe to a topic and yield events."""
        raise NotImplementedError
        yield  # pragma: no cover

    async def flush(self) -> None:
        """Ensure all published events are durable."""
        pass

    async def close(self) -> None:
        pass
