"""
CrowdPositioningEngine — Layer 1.5 orchestrator.

Fuses an extensible set of positioning signals into a single
CrowdPositioningEvent per symbol. V1 shipped 3 signals (funding, L/S ratio,
sentiment); V3 adds open interest, liquidations, and social euphoria.

Fusion logic:
    crowd_score = weighted sum of component scores (clamp [-1, +1])
    conviction  = |crowd_score| * (1 - divergence)   where divergence is the
                 fraction of significant components whose sign disagrees with
                 the net direction — high agreement boosts conviction,
                 disagreement deflates it.
    horizon     = the longest horizon among significant components
    regime_hint = classified from score + conviction

V4 — auto-tuning:
    The fusion weights are mutable and exposed via `set_weights()` / `weights()`
    so the GeneticOptimizer (Layer 6) can evolve them based on realized
    contrarian PnL. The engine itself never mutates weights; it only reads the
    per-signal `weight` attribute, which the optimizer updates in place.

A component is "significant" if |score| >= 0.15.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from omega.crowd_engine.optimizer import CrowdWeightOptimizer
from omega.crowd_engine.signals.base import PositioningSignal, SignalReading
from omega.crowd_engine.signals.funding_signal import FundingRateSignal
from omega.crowd_engine.signals.liquidation_signal import LiquidationSignal
from omega.crowd_engine.signals.ls_ratio_signal import LSRatioSignal
from omega.crowd_engine.signals.open_interest_signal import OpenInterestSignal
from omega.crowd_engine.signals.sentiment_signal import SentimentSignal
from omega.crowd_engine.signals.social_signal import SocialSentimentSignal
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
        open_interest: Optional[OpenInterestSignal] = None,
        liquidations: Optional[LiquidationSignal] = None,
        social: Optional[SocialSentimentSignal] = None,
        # Min |score| to emit an event at all (avoid spamming on noise)
        emit_threshold: float = 0.20,
        # Move in |score| required to re-emit for an already-extreme symbol
        reemit_delta: float = 0.10,
        cascade_conviction: float = 0.70,
    ) -> None:
        self.symbols = tuple(s.upper() for s in symbols)
        # Core V1 signals
        self.funding = funding or FundingRateSignal()
        self.ls_ratio = ls_ratio or LSRatioSignal(symbols=self.symbols)
        self.sentiment = sentiment or SentimentSignal()
        # V3 signals
        self.open_interest = open_interest or OpenInterestSignal(symbols=self.symbols)
        self.liquidations = liquidations or LiquidationSignal(symbols=self.symbols)
        self.social = social or SocialSentimentSignal()
        # Ordered signal registry — the fusion iterates this list. Adding a new
        # signal means appending here + giving it a fusion weight.
        self._signals: List[PositioningSignal] = [
            self.liquidations,   # most predictive (real cascade confirmation)
            self.funding,
            self.open_interest,
            self.ls_ratio,
            self.sentiment,
            self.social,
        ]
        self.emit_threshold = emit_threshold
        self.reemit_delta = reemit_delta
        self.cascade_conviction = cascade_conviction
        self._last_emitted_score: Dict[str, float] = {}
        self._events_emitted = 0
        # V4 — auto-tuning of fusion weights from realized contrarian PnL.
        # Disabled (no-op) until `on_contrarian_trade_closed` is wired up by
        # the orchestrator; harmless when idle.
        self.optimizer = CrowdWeightOptimizer(signal_names=tuple(s.name for s in self._signals))

    @property
    def signals(self) -> List[PositioningSignal]:
        """All registered signals (for the GeneticOptimizer to tune weights)."""
        return list(self._signals)

    def weights(self) -> Dict[str, float]:
        """Current fusion weights, keyed by signal name. Tunable via V4."""
        # Prefer the optimizer's evolved weights if they've diverged from the
        # signal defaults; otherwise read the live signal weights.
        return {s.name: s.weight for s in self._signals}

    def set_weights(self, weights: Dict[str, float]) -> None:
        """Update fusion weights in place (called by the GeneticOptimizer)."""
        by_name = {s.name: s for s in self._signals}
        for name, w in weights.items():
            sig = by_name.get(name)
            if sig is not None:
                sig.weight = float(w)

    def on_contrarian_trade_closed(self, pnl_bps: float, components: Dict[str, float]) -> None:
        """V4 hook — called by the orchestrator when a contrarian trade closes.
        Feeds the outcome to the weight optimizer and applies any new weights."""
        self.optimizer.record_trade(pnl_bps, components)
        new_weights = self.optimizer.maybe_tune()
        if new_weights is not None:
            self.set_weights(new_weights)

    async def start(self) -> None:
        """Start background polling/streaming tasks for each signal."""
        for sig in self._signals:
            starter = getattr(sig, "start", None)
            if starter is not None:
                await starter()
        logger.info(
            f"CrowdPositioningEngine started: {len(self.symbols)} symbols, "
            f"{len(self._signals)} signals ({[s.name for s in self._signals]})",
            extra={"component": "crowd_engine"},
        )

    async def stop(self) -> None:
        for sig in self._signals:
            stopper = getattr(sig, "stop", None)
            if stopper is not None:
                try:
                    await stopper()
                except Exception as exc:
                    logger.warning(f"Signal {sig.name} stop error: {exc}")

    def on_market(self, event: MarketEvent) -> Optional[CrowdPositioningEvent]:
        """
        Ingest a MarketEvent (carrying the latest funding rate). Returns a
        CrowdPositioningEvent if the symbol's positioning meaningfully changed,
        else None.
        """
        sym = event.symbol
        if sym not in self.symbols:
            return None
        # Feed funding rate into the funding signal (the only reactive signal;
        # the others poll/stream independently)
        self.funding.update(sym, event.funding_rate)
        return self._compute_event(sym, event.timestamp)

    def _compute_event(self, symbol: str, timestamp: str) -> Optional[CrowdPositioningEvent]:
        """Fuse all signals for one symbol into one event."""
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

        # Divergence: fraction of significant components whose sign disagrees
        # with the net crowd direction.
        sigs = [r.score for r in readings if abs(r.score) >= 0.15]
        if len(sigs) >= 2:
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
            horizon = max(
                (r.horizon for r in sig_readings),
                key=lambda h: _HORIZON_RANK.get(h, 0),
            )
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
            "weights": self.weights(),
            "signals": {s.name: s.stats() for s in self._signals},
        }
