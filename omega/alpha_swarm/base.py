"""
AlphaAgent — abstract base class for all Alpha Swarm agents.

Every agent (PPO trend, PPO mean-reversion, LLM macro, stat-arb) implements
the same `on_event` interface. The agent ingests a stream of typed events
from the Data Nexus, maintains its own internal state (price history, news
buffer, etc.), and emits SignalEvent objects when it has a view.

This contract makes agents pluggable: you can drop in a new agent without
touching the orchestrator, the Risk Aegis, or the Execution Blade.
"""

from __future__ import annotations

import abc
from typing import List, Optional

from omega.utils.events import (
    MacroEvent, MarketEvent, NewsEvent, OnChainEvent, SignalEvent,
)


class AlphaAgent(abc.ABC):
    """Base contract for all alpha-generating agents."""

    name: str = "abstract"

    def __init__(self, symbols: tuple) -> None:
        self.symbols = symbols
        self.is_ready: bool = False

    @abc.abstractmethod
    def on_market(self, event: MarketEvent) -> List[SignalEvent]:
        """Process a market event. Return 0+ signals (empty list = no view)."""
        ...

    def on_news(self, event: NewsEvent) -> List[SignalEvent]:
        """Override to react to news. Default: no signals."""
        return []

    def on_macro(self, event: MacroEvent) -> List[SignalEvent]:
        """Override to react to macro updates. Default: no signals."""
        return []

    def on_onchain(self, event: OnChainEvent) -> List[SignalEvent]:
        """Override to react to on-chain events. Default: no signals."""
        return []

    def reset(self) -> None:
        """Reset internal state. Called at the start of each backtest episode."""
        pass

    def save(self, path: str) -> None:
        """Persist learned parameters. Default: no-op (stateless agents)."""
        pass

    def load(self, path: str) -> None:
        """Load learned parameters. Default: no-op."""
        pass

    def stats(self) -> dict:
        """Return agent-internal statistics for logging/Meta-Cognition."""
        return {"name": self.name, "ready": self.is_ready}
