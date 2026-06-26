"""
Base class for crowd-positioning signals.

Every signal (funding rate, L/S ratio, sentiment) normalizes its raw observation
into a score in [-1, +1] and tags it with a native horizon. The engine fuses
these into a single CrowdPositioningEvent.

Contract:
    score > 0  → crowd is overcrowded LONG  (fade by going SHORT)
    score < 0  → crowd is overcrowded SHORT (fade by going LONG)
    score == 0 → no signal / neutral
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional


@dataclass
class SignalReading:
    """One normalized reading from a positioning signal."""
    score: float          # [-1, +1]
    horizon: str          # "minutes" | "hours" | "days"
    weight: float = 1.0   # fusion weight (overridable by GeneticOptimizer in V4)
    raw: Optional[dict] = None  # raw observation for audit/autopsy


class PositioningSignal(abc.ABC):
    """A source of crowd-positioning readings."""

    name: str = "abstract"

    @abc.abstractmethod
    def reading(self) -> Optional[SignalReading]:
        """Return the current normalized reading, or None if no data yet."""
        raise NotImplementedError
