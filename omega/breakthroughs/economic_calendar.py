"""B19 — EconomicCalendar: tracks macro events that move crypto.

CPI prints, FOMC meetings, NFP jobs reports, and Powell speeches cause
predictable volatility spikes in crypto. We maintain a hardcoded calendar of
high-impact events and alert when one is imminent (within 1 hour), so the bot
can reduce exposure ahead of the print.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import List, Tuple
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.econ_calendar")

# Major recurring macro events (approximate days/times UTC)
# For production, replace with a live economic calendar API (e.g. TradingEconomics)
MACRO_EVENTS: List[Tuple[str, str, int, int]] = [
    # (name, impact, day_of_week, hour_utc) — day 0=Monday
    ("CPI YoY", "high", 1, 13),     # Tuesday 1pm UTC (8:30am EST)
    ("Core CPI", "high", 1, 13),
    ("FOMC Rate Decision", "extreme", 2, 19),  # Wednesday 7pm UTC (2pm EST)
    ("FOMC Press Conf", "extreme", 2, 19, 30),
    ("Initial Jobless Claims", "medium", 3, 13),  # Thursday
    ("NFP", "high", 4, 13),          # First Friday of month, 1pm UTC
    ("PPI YoY", "medium", 1, 13),
    ("Retail Sales", "medium", 2, 13),
    ("GDP Advance", "high", 3, 13),
]

class EconomicCalendar:
    """Tracks macro events that cause predictable crypto volatility."""
    def __init__(self, alert_minutes_before: int = 60) -> None:
        self.alert_minutes_before = alert_minutes_before

    def next_events(self, hours_ahead: int = 24) -> List[dict]:
        """Return upcoming macro events in the next hours."""
        now = datetime.now(timezone.utc)
        events = []
        for event in MACRO_EVENTS:
            name, impact = event[0], event[1]
            day, hour = event[2], event[3]
            minute = event[4] if len(event) > 4 else 30
            # Find next occurrence
            days_ahead = (day - now.weekday()) % 7
            event_time = (now + timedelta(days=days_ahead)).replace(
                hour=hour, minute=minute, second=0, microsecond=0)
            if event_time < now:
                event_time += timedelta(days=7)
            if (event_time - now).total_seconds() < hours_ahead * 3600:
                events.append({
                    "name": name, "impact": impact,
                    "time_utc": event_time.strftime("%Y-%m-%d %H:%M UTC"),
                    "minutes_until": int((event_time - now).total_seconds() / 60),
                })
        return sorted(events, key=lambda e: e["minutes_until"])

    def is_imminent(self) -> bool:
        """True if a high/extreme impact event is within alert window."""
        for e in self.next_events(3):
            if e["impact"] in ("high", "extreme") and e["minutes_until"] < self.alert_minutes_before:
                return True
        return False

    def stats(self) -> dict:
        events = self.next_events(24)
        return {"name": "economic_calendar",
                "next_events": events[:5],
                "imminent_high_impact": self.is_imminent()}
