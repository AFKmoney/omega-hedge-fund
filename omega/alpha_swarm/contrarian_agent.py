"""
ContrarianAgent — fades crowd-positioning extremes. The heart of the thesis.

When the Crowd Positioning Engine reports that the retail crowd is overcrowded
at an extreme (everyone long with high leverage, or everyone capitulating
short), this agent takes the other side. It is the trade that makes the most
money for the fewest people — the inverse cascade.

Design:
    - Rule-based, NOT ML. An extreme is a threshold, not a prediction. ML tends
      to smooth exactly the tail events we want to capture.
    - Only fires when |crowd_score| exceeds EXTREME_THRESHOLD (default 0.5).
      The naive "always fade the crowd" loses in trending markets; this only
      fades statistical extremes.
    - TP/SL asymmetry: TP = expected_move, stop = 0.3 * TP. A contrarian is
      wrong often (the extreme can extend) but right big (the cascade). Win
      rate ~35%, positive expectancy. This is the signature of every
      mean-reversion strategy that survives.
    - Holding period is set by the event's horizon (cascade = minutes/hours,
      euphoria = days).

It also implements the standard on_market/on_news no-ops so it slots into the
existing AlphaSwarm without special-casing — but its real input is the
CrowdPositioningEvent, delivered via on_positioning().
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from omega.alpha_swarm.base import AlphaAgent
from omega.utils.events import (
    CrowdPositioningEvent, MacroEvent, MarketEvent, NewsEvent,
    OnChainEvent, SignalEvent, Side,
)
from omega.utils.logger import get_logger

logger = get_logger("omega.alpha_swarm.contrarian")

# Map an event horizon to a holding-period bar count (10s bars by default).
_HORIZON_BARS = {
    "minutes": 60,    # ~10 min
    "hours": 240,     # ~40 min
    "days": 1440,     # ~4 hours at 10s bars; for daily plays the Meta-Cognition
                      # exit signal dominates anyway
}


class ContrarianAgent(AlphaAgent):
    """Fade crowd-positioning extremes. Rule-based, asymmetric payoff."""

    name = "contrarian"

    def __init__(
        self,
        symbols: tuple,
        extreme_threshold: float = 0.50,
        confidence_cap: float = 0.85,
        # Asymmetry: stop is this fraction of TP. 0.3 → stop=30% of TP.
        stop_to_tp_ratio: float = 0.30,
        # Throttle: don't re-emit for the same symbol more than once per window
        min_emit_gap_sec: float = 120.0,
    ) -> None:
        super().__init__(symbols)
        self.extreme_threshold = extreme_threshold
        self.confidence_cap = confidence_cap
        self.stop_to_tp_ratio = stop_to_tp_ratio
        self.min_emit_gap_sec = min_emit_gap_sec
        self._last_emit: Dict[str, float] = {}
        self._latest_event: Dict[str, CrowdPositioningEvent] = {}
        self.is_ready = True

    # ------------------------------------------------------------------
    # Primary input: CrowdPositioningEvent from the Crowd Engine
    # ------------------------------------------------------------------

    def on_positioning(self, event: CrowdPositioningEvent) -> List[SignalEvent]:
        """The core. Fade the crowd when it is at a statistical extreme."""
        sym = event.symbol
        self._latest_event[sym] = event

        # Only fade genuine extremes — not every mild tilt
        if abs(event.crowd_score) < self.extreme_threshold:
            return []

        # Throttle per symbol
        last = self._last_emit.get(sym, 0.0)
        if time.time() - last < self.min_emit_gap_sec:
            return []
        self._last_emit[sym] = time.time()

        # FADER: take the opposite side of the crowd
        side = Side.SELL if event.crowd_score > 0 else Side.BUY
        confidence = min(self.confidence_cap, event.conviction * 0.90)

        tp = max(event.expected_move_bps, 100.0)  # floor so we always aim for a real move
        stop = tp * self.stop_to_tp_ratio
        hold = _HORIZON_BARS.get(event.horizon, 240)

        sig = SignalEvent(
            agent=self.name,
            symbol=sym,
            timestamp=event.timestamp,
            side=side,
            confidence=confidence,
            expected_holding_period_bars=hold,
            expected_return_bps=tp,
            stop_loss_bps=stop,
            take_profit_bps=tp,
            rationale=(
                f"Fade crowd {event.regime_hint}: score={event.crowd_score:+.2f} "
                f"conv={event.conviction:.2f} {event.components}"
            ),
            metadata={
                "crowd_score": event.crowd_score,
                "conviction": event.conviction,
                "horizon": event.horizon,
                "regime_hint": event.regime_hint,
                "components": event.components,
                "source": "crowd_engine",
            },
        )
        logger.info(
            f"Contrarian signal: {sym} {side.value} conf={confidence:.2f} "
            f"crowd={event.crowd_score:+.2f} hint={event.regime_hint} "
            f"tp={tp:.0f}bps stop={stop:.0f}bps",
            extra={
                "component": "alpha_swarm.contrarian",
                "symbol": sym,
                "agent": self.name,
            },
        )
        return [sig]

    # ------------------------------------------------------------------
    # AlphaAgent interface (no-ops; this agent only reacts to positioning)
    # ------------------------------------------------------------------

    def on_market(self, event: MarketEvent) -> List[SignalEvent]:
        return []

    def on_news(self, event: NewsEvent) -> List[SignalEvent]:
        return []

    def on_macro(self, event: MacroEvent) -> List[SignalEvent]:
        return []

    def on_onchain(self, event: OnChainEvent) -> List[SignalEvent]:
        return []

    def stats(self) -> dict:
        return {
            "name": self.name,
            "ready": self.is_ready,
            "extreme_threshold": self.extreme_threshold,
            "latest_scores": {
                s: round(e.crowd_score, 3) for s, e in self._latest_event.items()
            },
        }
