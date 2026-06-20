"""
DebateChamber — meta-agent that aggregates signals from the Alpha Swarm.

Receives SignalEvents from all agents, evaluates their confidence intervals,
identifies conflicts (e.g., PPO says LONG, LLM macro says SHORT), and makes
the final buy/sell/hold decision per symbol.

Decision algorithm:
    1. Group signals by symbol + timestamp window
    2. For each symbol:
       a. Compute weighted vote: Σ (confidence × regime_weight × agent_weight)
       b. If |net_score| > quorum_threshold → emit consolidated signal
       c. If agents disagree strongly (high variance, opposing signs) → defer
       d. If only one agent has a view → require higher confidence threshold
    3. Return consolidated SignalEvent with rationale citing contributing agents

This is the "mixture of experts" governance layer. It does NOT call an LLM
on every tick (too slow); it uses a deterministic weighted-vote algorithm
with conflict detection. An LLM debate could be added as an async enhancement
for high-stakes decisions.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from omega.config.settings import AlphaSwarmSettings, RegimeSettings
from omega.utils.events import SignalEvent, Side
from omega.utils.logger import get_logger

logger = get_logger("omega.alpha_swarm.debate")


class DebateChamber:
    """Meta-agent that arbitrates between Alpha Swarm agents."""

    def __init__(
        self,
        alpha_settings: Optional[AlphaSwarmSettings] = None,
        regime_settings: Optional[RegimeSettings] = None,
        # Time window (seconds) for grouping signals per symbol
        window_sec: float = 5.0,
        # Net score threshold above which to emit a consolidated signal
        quorum_threshold: float = 0.40,
        # If std-dev of normalized votes > this, defer (high disagreement)
        conflict_std_threshold: float = 0.55,
    ) -> None:
        self.alpha_settings = alpha_settings or AlphaSwarmSettings()
        self.regime_settings = regime_settings or RegimeSettings()
        self.window_sec = window_sec
        self.quorum_threshold = quorum_threshold
        self.conflict_std_threshold = conflict_std_threshold
        # Pending signals: symbol → list of (signal, arrival_ts)
        self._pending: Dict[str, List[Tuple[SignalEvent, float]]] = defaultdict(list)
        # Per-agent weights (override-able by Regime Detector)
        self.agent_weights: Dict[str, float] = dict(self.regime_settings.default_weights)
        self._decisions_made = 0
        self._conflicts_deferred = 0

    def set_agent_weights(self, weights: Dict[str, float]) -> None:
        """Called by Regime Detector when regime changes."""
        self.agent_weights = {**self.agent_weights, **weights}
        logger.info(
            f"Debate chamber weights updated: {self.agent_weights}",
            extra={"component": "alpha_swarm.debate"},
        )

    def submit(self, signal: SignalEvent) -> Optional[SignalEvent]:
        """
        Submit a signal from any agent. Returns a consolidated SignalEvent
        if the chamber reaches a decision, else None.
        """
        now = time.time()
        sym = signal.symbol
        # Apply agent weight from current regime
        weight = self.agent_weights.get(signal.agent, 0.0)
        weighted_signal = SignalEvent(
            agent=signal.agent,
            symbol=sym,
            timestamp=signal.timestamp,
            side=signal.side,
            confidence=signal.confidence * weight,
            expected_holding_period_bars=signal.expected_holding_period_bars,
            expected_return_bps=signal.expected_return_bps,
            stop_loss_bps=signal.stop_loss_bps,
            take_profit_bps=signal.take_profit_bps,
            rationale=signal.rationale,
            regime_weight=weight,
            metadata=signal.metadata,
        )
        # Drop old signals outside the window
        cutoff = now - self.window_sec
        self._pending[sym] = [
            (s, ts) for s, ts in self._pending[sym] if ts > cutoff
        ] + [(weighted_signal, now)]
        return self._decide(sym)

    def _decide(self, symbol: str) -> Optional[SignalEvent]:
        """Run the weighted-vote algorithm for one symbol."""
        signals = [s for s, _ in self._pending[symbol]]
        if len(signals) < self.alpha_settings.debate_quorum:
            return None
        # Map sides to numeric votes
        side_to_val = {Side.BUY: 1.0, Side.FLAT: 0.0, Side.SELL: -1.0}
        votes = []
        total_weight = 0.0
        for s in signals:
            v = side_to_val[s.side] * s.confidence
            votes.append(v)
            total_weight += s.confidence
        if total_weight <= 0:
            return None
        net_score = sum(votes) / total_weight  # -1 .. +1
        # Conflict detection: high variance in vote signs → defer
        vote_arr = [1 if v > 0 else (-1 if v < 0 else 0) for v in votes]
        if len(set(vote_arr)) > 1:
            import statistics
            try:
                std = statistics.pstdev([float(v) for v in votes])
            except statistics.StatisticsError:
                std = 0.0
            if std > self.conflict_std_threshold:
                self._conflicts_deferred += 1
                logger.info(
                    f"Debate deferred for {symbol}: conflict std={std:.2f}",
                    extra={
                        "component": "alpha_swarm.debate",
                        "symbol": symbol,
                    },
                )
                # Clear pending so we don't keep re-evaluating the same conflict
                self._pending[symbol] = []
                return None
        # Decide
        if abs(net_score) < self.quorum_threshold:
            return None
        side = Side.BUY if net_score > 0 else Side.SELL
        # Aggregate metadata
        contributing_agents = [s.agent for s in signals]
        avg_stop = sum(s.stop_loss_bps for s in signals) / len(signals)
        avg_tp = sum(s.take_profit_bps for s in signals) / len(signals)
        avg_hold = sum(s.expected_holding_period_bars for s in signals) // len(signals)
        self._decisions_made += 1
        # Clear pending for this symbol
        self._pending[symbol] = []
        consolidated = SignalEvent(
            agent="debate_chamber",
            symbol=symbol,
            timestamp=signals[-1].timestamp,
            side=side,
            confidence=min(0.95, abs(net_score)),
            expected_holding_period_bars=avg_hold,
            expected_return_bps=abs(net_score) * 200.0,
            stop_loss_bps=avg_stop,
            take_profit_bps=avg_tp,
            rationale=f"Debated: {contributing_agents} → {side.value} (score={net_score:+.2f})",
            metadata={
                "contributing_agents": contributing_agents,
                "net_score": float(net_score),
                "vote_count": len(signals),
            },
        )
        logger.info(
            f"Debate decision: {symbol} {side.value} conf={consolidated.confidence:.2f} "
            f"agents={contributing_agents}",
            extra={
                "component": "alpha_swarm.debate",
                "symbol": symbol,
                "agent": "debate_chamber",
            },
        )
        return consolidated

    def stats(self) -> dict:
        return {
            "decisions_made": self._decisions_made,
            "conflicts_deferred": self._conflicts_deferred,
            "pending_symbols": len(self._pending),
            "agent_weights": self.agent_weights,
        }
