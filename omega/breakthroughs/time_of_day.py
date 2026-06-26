"""B12 — TimeOfDayAlpha: exploits time-based market patterns.

Crypto markets have rhythm: London open (8am UTC) and NY open (1pm UTC / 9:30am
EST) bring volume spikes. Asian session (midnight-8am UTC) is often range-bound.
Funding settlements happen at 00:00, 08:00, 16:00 UTC. We score the time-of-day
alpha: which hours historically produce the biggest moves, and whether we're
entering a high-volatility window.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.time_of_day")

class TimeOfDayAlpha:
    """Scores time-based volatility patterns."""
    # Empirical: which UTC hours have historically the most volatility
    HIGH_VOL_HOURS = {0, 1, 8, 9, 13, 14, 15, 16, 23}  # funding + London/NY opens
    LOW_VOL_HOURS = {3, 4, 5, 6, 7, 22}  # Asian dead zone

    def __init__(self) -> None:
        self._current_score: float = 0.0
        self._session: str = "unknown"

    def update(self) -> float:
        now = datetime.now(timezone.utc)
        hour = now.hour
        if hour in self.HIGH_VOL_HOURS:
            self._current_score = 0.7
        elif hour in self.LOW_VOL_HOURS:
            self._current_score = 0.2
        else:
            self._current_score = 0.4
        # Session classification
        if 0 <= hour < 8:
            self._session = "asian"
        elif 8 <= hour < 13:
            self._session = "london"
        elif 13 <= hour < 21:
            self._session = "new_york"
        else:
            self._session = "late_us"
        return self._current_score

    @property
    def score(self) -> float:
        return self._current_score

    @property
    def session(self) -> str:
        return self._session

    def is_funding_time(self) -> bool:
        """Funding settles at 00, 08, 16 UTC. Alert 15 min before."""
        now = datetime.now(timezone.utc)
        minutes_to_funding = min(
            (8 - now.hour % 8) * 60 - now.minute,
        ) % 480
        return minutes_to_funding < 15

    def stats(self) -> dict:
        return {"name": "time_of_day", "score": self._current_score,
                "session": self._session, "near_funding": self.is_funding_time()}
