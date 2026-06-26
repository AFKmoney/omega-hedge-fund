"""
CrowdPositioningEngine — Layer 1.5 orchestrator.

Fuses the three positioning signals (funding rate, long/short ratio,
sentiment) into a single CrowdPositioningEvent per symbol.

Fusion logic:
    crowd_score = weighted sum of component scores (clamp [-1, +1])
    conviction  = |crowd_score| * (1 - divergence)   where divergence is the
                 spread of the normalized component signs — high agreement
                 boosts conviction, disagreement deflates it.
    horizon     = the longest horizon among significant components
    regime_hint = classified from score + conviction

The engine is reactive: it updates per-symbol state on each funding tick and
emits a CrowdPositioningEvent whenever a symbol's state meaningfully changes.
The orchestrator routes the event to the ContrarianAgent and to the
RegimeWeightRouter (to defund trend agents at cascade-imminent extremes).

A component is "significant" if |score| >= 0.15.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from omega.crowd_engine.signals.base import PositioningSignal, SignalReading
from omega.crowd_engine.signals.funding_signal import FundingRateSignal
from omega.crowd_engine.signals.ls_ratio_signal import LSRatioSignal
from omega.crowd_engine.signals.sentiment_signal import SentimentSignal
from omega.utils.events import CrowdPositioningEvent, MarketEvent
from omega.utils.logger import get_logger

logger = get_logger("omega.crowd_engine")

_HORIZON_RANK = {"minutes": 0, "hours": 1, "days": 2}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class CrowdPositioningEngine:
    """Fuses positioning signals into CrowdPositioningEvents."""

    def __init__(
        self,
        symbols: tuple = ("BTCUSDT",),
        funding: Optional[FundingRateSignal] = None,
        ls_ratio: Optional[LSRatioSignal] = None,
        sentiment: Optional[SentimentSignal] = None,
        # Min |score| to emit an event at all (avoid spamming on noise)
        emit_threshold: float = 0.20,
        # Move in |score| required to re-emit for an already-extreme symbol
        reemit_delta: float = 0.10,
        cascade_conviction: float = 0.70,
    ) -> None:
        self.symbols = tuple(s.upper() for s in symbols)
        self.funding = funding or FundingRateSignal()
        self.ls_ratio = ls_ratio or LSRatioSignal(symbols=self.symbols)
        self.sentiment = sentiment or SentimentSignal()
        self.emit_threshold = emit_threshold
        self.reemit_delta = reemit_delta
        self.cascade_conviction = cascade_conviction
        # The three signals, in fixed order for fusion math
        self._signals: List[PositioningSignal] = [self.funding, self.ls_ratio, self.sentiment]
        self._last_emitted_score: Dict[str, float] = {}
        self._events_emitted = 0

    async def start(self) -> None:
        """Start background polling tasks (L/S ratio, sentiment)."""
        await self.ls_ratio.start()
        await self.sentiment.start()
        logger.info(
            f"CrowdPositioningEngine started: {len(self.symbols)} symbols, "
            f"3 signals",
            extra={"component": "crowd_engine"},
        )

    async def stop(self) -> None:
        await self.ls_ratio.stop()
        await self.sentiment.stop()

    def on_market(self, event: MarketEvent) -> Optional[CrowdPositioningEvent]:
        """
        Ingest a MarketEvent (carrying the latest funding rate). Returns a
        CrowdPositioningEvent if the symbol's positioning meaningfully changed,
        else None.
        """
        sym = event.symbol
        if sym not in self.symbols:
            return None
        # Feed funding rate into the funding signal
        self.funding.update(sym, event.funding_rate)
        return self._compute_event(sym, event.timestamp)

    def _compute_event(self, symbol: str, timestamp: str) -> Optional[CrowdPositioningEvent]:
        """Fuse the three signals for one symbol into one event."""
        components: Dict[str, float] = {}
        readings: List[SignalReading] = []
        for sig in self._signals:
            r = sig.reading_for(symbol)
            if r is None:
                continue
            components[sig.name] = round(r.score, 4)
            readings.append(r)
        if not readings:
            return None

        # Weighted crowd score
        total_w = sum(r.weight for r in readings)
        if total_w <= 0:
            return None
        crowd_score = sum(r.score * r.weight for r in readings) / total_w
        crowd_score = max(-1.0, min(1.0, crowd_score))

        # Divergence: how much do the component SIGNS disagree?
        # If all agree on direction → low divergence → high conviction.
        sigs = [r.score for r in readings if abs(r.score) >= 0.15]
        if len(sigs) >= 2:
            # Fraction of components whose sign disagrees with the crowd_score sign
            direction = 1.0 if crowd_score >= 0 else -1.0
            disagree = sum(1 for s in sigs if (s >= 0) != (direction >= 0))
            divergence = disagree / len(sigs)
        else:
            divergence = 0.0 if sigs else 1.0  # no significant component → no conviction

        conviction = abs(crowd_score) * (1.0 - divergence)
        conviction = max(0.0, min(1.0, conviction))

        # Horizon = longest significant component horizon
        sig_readings = [r for r in readings if abs(r.score) >= 0.15]
        if sig_readings:
            horizon = max((r.horizon for r in sig_readings), key=lambda h: _HORIZON_RANK.get(h, 0))
        else:
            horizon = "hours"

        regime_hint = self._classify(crowd_score, conviction)
        expected_move_bps = self._expected_move(crowd_score, conviction, horizon)

        # Throttle: only emit if above threshold OR meaningfully changed
        last = self._last_emitted_score.get(symbol)
        if abs(crowd_score) < self.emit_threshold:
            return None
        if last is not None and abs(crowd_score - last) < self.reemit_delta:
            return None
        self._last_emitted_score[symbol] = crowd_score
        self._events_emitted += 1

        event = CrowdPositioningEvent(
            symbol=symbol,
            timestamp=timestamp or _now_iso(),
            crowd_score=crowd_score,
            conviction=conviction,
            horizon=horizon,
            components=components,
            regime_hint=regime_hint,
            expected_move_bps=expected_move_bps,
        )
        if conviction > 0.4:
            logger.info(
                f"Crowd positioning {symbol}: score={crowd_score:+.2f} "
                f"conv={conviction:.2f} hint={regime_hint} {components}",
                extra={"component": "crowd_engine", "symbol": symbol},
            )
        return event

    @staticmethod
    def _classify(crowd_score: float, conviction: float) -> str:
        if conviction < 0.30:
            return "neutral"
        if crowd_score > 0:
            return "cascade_imminent" if conviction > 0.55 else "euphoria"
        else:
            return "cascade_imminent" if conviction > 0.55 else "fear"

    @staticmethod
    def _expected_move(crowd_score: float, conviction: float, horizon: str) -> float:
        """Rough expected size of the inverse move, in bps."""
        base = {"minutes": 50.0, "hours": 200.0, "days": 600.0}.get(horizon, 200.0)
        return abs(crowd_score) * conviction * base

    def stats(self) -> dict:
        return {
            "symbols": list(self.symbols),
            "events_emitted": self._events_emitted,
            "last_scores": self._last_emitted_score,
            "funding": self.funding.stats(),
            "ls_ratio": self.ls_ratio.stats(),
            "sentiment": self.sentiment.stats(),
        }
