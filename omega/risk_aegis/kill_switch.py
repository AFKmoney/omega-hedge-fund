"""
KillSwitch — hard-coded safety layer that bypasses all AI.

Triggers an immediate "cancel all + flatten" if ANY of:
    1. End-to-end latency exceeds kill_switch_latency_ms (5s default)
    2. Exchange API error count exceeds kill_switch_api_error_count (5)
    3. Flash crash detected: >5% drop in 60 seconds
    4. Portfolio drawdown exceeds max_portfolio_drawdown_pct (8%)
    5. Manual trigger via `trigger("manual")`

Once triggered, the kill switch LATCHES — it must be explicitly reset before
the system can trade again. This is the single most important safety
component in OMEGA.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Optional

from omega.config.settings import RiskAegisSettings
from omega.utils.logger import get_logger

logger = get_logger("omega.risk_aegis.kill_switch")


class KillSwitch:
    """Hard safety switch. Bypasses all AI when triggered."""

    def __init__(self, settings: Optional[RiskAegisSettings] = None) -> None:
        self.settings = settings or RiskAegisSettings()
        self._triggered: bool = False
        self._trigger_reason: Optional[str] = None
        self._trigger_time: float = 0.0
        self._api_errors: int = 0
        self._last_latency_ms: float = 0.0
        self._recent_prices: Deque[tuple] = deque(maxlen=60)  # (ts, price)
        self._peak_equity: float = 0.0
        self._current_equity: float = 0.0

    def record_latency(self, latency_ms: float) -> None:
        self._last_latency_ms = latency_ms
        if latency_ms > self.settings.kill_switch_latency_ms and not self._triggered:
            self.trigger(f"latency_spike_{latency_ms:.0f}ms")

    def record_api_error(self) -> None:
        self._api_errors += 1
        if self._api_errors >= self.settings.kill_switch_api_error_count and not self._triggered:
            self.trigger(f"api_errors_{self._api_errors}")

    def record_api_success(self) -> None:
        self._api_errors = 0

    def record_price(self, price: float) -> None:
        """Track prices for flash-crash detection."""
        now = time.time()
        self._recent_prices.append((now, price))
        # Drop entries older than 60s
        cutoff = now - 60.0
        while self._recent_prices and self._recent_prices[0][0] < cutoff:
            self._recent_prices.popleft()
        # Check for flash crash
        if len(self._recent_prices) >= 10:
            oldest_price = self._recent_prices[0][1]
            if oldest_price > 0:
                drop_pct = (oldest_price - price) / oldest_price * 100.0
                if drop_pct >= self.settings.kill_switch_flash_crash_pct and not self._triggered:
                    self.trigger(f"flash_crash_{drop_pct:.1f}pct")

    def record_equity(self, equity: float) -> None:
        self._current_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity > 0:
            dd_pct = (self._peak_equity - equity) / self._peak_equity * 100.0
            if dd_pct >= self.settings.max_portfolio_drawdown_pct and not self._triggered:
                self.trigger(f"max_drawdown_{dd_pct:.1f}pct")

    def trigger(self, reason: str) -> None:
        """Manually or programmatically trigger the kill switch."""
        if self._triggered:
            return
        self._triggered = True
        self._trigger_reason = reason
        self._trigger_time = time.time()
        logger.error(
            f"⚠️ KILL SWITCH TRIGGERED: {reason}. All trading halted. "
            f"Manual reset required.",
            extra={"component": "risk_aegis.kill_switch"},
        )

    def reset(self) -> None:
        """Manually reset the kill switch (requires human action)."""
        self._triggered = False
        self._trigger_reason = None
        self._api_errors = 0
        self._recent_prices.clear()
        logger.info(
            "Kill switch reset — trading may resume",
            extra={"component": "risk_aegis.kill_switch"},
        )

    @property
    def is_triggered(self) -> bool:
        return self._triggered

    @property
    def trigger_reason(self) -> Optional[str]:
        return self._trigger_reason

    @property
    def seconds_since_trigger(self) -> float:
        return time.time() - self._trigger_time if self._triggered else 0.0

    def stats(self) -> dict:
        return {
            "triggered": self._triggered,
            "reason": self._trigger_reason,
            "seconds_since_trigger": self.seconds_since_trigger,
            "api_errors": self._api_errors,
            "last_latency_ms": self._last_latency_ms,
            "peak_equity": self._peak_equity,
            "current_equity": self._current_equity,
            "current_drawdown_pct": (
                (self._peak_equity - self._current_equity) / self._peak_equity * 100.0
                if self._peak_equity > 0 else 0.0
            ),
        }
