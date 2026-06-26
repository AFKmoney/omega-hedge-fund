"""
CrowdWeightOptimizer — V4 auto-tuning of the fusion weights.

The CrowdPositioningEngine fuses N signals with fixed weights by default.
This optimizer evolves those weights based on the realized PnL of the
ContrarianAgent, so the system learns which signals actually predicted
profitable fades.

Approach — gradient-free evolutionary tuning (simple, robust, no NN needed):
    - Maintain a population of 1 (the current weights) + track a rolling
      fitness per signal (the contrarian PnL attributable to that signal).
    - Periodically (every `eval_window` contrarian trades), compare each
      signal's recent contribution to the consensus crowd_score at trade time
      against the realized PnL. Signals whose extreme readings preceded winning
      fades get their weight bumped up; those that misled get bumped down.
    - Mutation noise keeps the search exploring; weight clamping + normalization
      keeps the fusion valid.

This is intentionally lightweight — the contrarian is rule-based, and tuning
6 scalar weights doesn't warrant a full GA. A signed-attribution gradient ascent
does the job and is auditable.

Contract:
    record_trade(pnl_bps, components)  — called by MetaCognition when a contrarian
                                         trade closes, with the CrowdPositioningEvent
                                         components dict from the trade metadata.
    maybe_tune()                        — called periodically; returns the new weights
                                         if a tuning step ran, else None.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from omega.utils.logger import get_logger

logger = get_logger("omega.crowd_engine.optimizer")


class CrowdWeightOptimizer:
    """Evolves fusion weights from realized contrarian PnL."""

    def __init__(
        self,
        signal_names: Tuple[str, ...] = (
            "liquidations", "funding", "open_interest",
            "ls_ratio", "sentiment", "social", "iceberg", "inflow",
        ),
        initial_weights: Optional[Dict[str, float]] = None,
        eval_window: int = 20,           # tune after every N contrarian trades
        lr: float = 0.10,                # weight adjustment step
        mutation_std: float = 0.03,      # exploration noise
        min_weight: float = 0.05,
        max_weight: float = 0.60,
        min_trades_to_tune: int = 10,
    ) -> None:
        self.signal_names = tuple(signal_names)
        self.eval_window = eval_window
        self.lr = lr
        self.mutation_std = mutation_std
        self.min_weight = min_weight
        self.max_weight = max_weight
        self.min_trades_to_tune = min_trades_to_tune
        # Current weights (normalized)
        if initial_weights:
            self._weights = {n: float(initial_weights.get(n, 0.2)) for n in self.signal_names}
        else:
            self._weights = {
                "liquidations": 0.45, "funding": 0.40, "open_interest": 0.30,
                "ls_ratio": 0.35, "sentiment": 0.25, "social": 0.20,
                "iceberg": 0.25, "inflow": 0.30,
            }
        # Rolling record of (components_at_trade, pnl_bps) for attribution
        self._attribution: Deque[Tuple[Dict[str, float], float]] = deque(maxlen=200)
        self._tune_count = 0
        self._rng = np.random.default_rng(seed=42)

    def weights(self) -> Dict[str, float]:
        return dict(self._weights)

    def record_trade(self, pnl_bps: float, components: Dict[str, float]) -> None:
        """Record a closed contrarian trade's outcome + the crowd components
        that were active when the trade was opened."""
        self._attribution.append((dict(components), float(pnl_bps)))

    def maybe_tune(self) -> Optional[Dict[str, float]]:
        """Run a tuning step if enough trades have closed. Returns new weights
        if a step ran, else None."""
        if len(self._attribution) < self.min_trades_to_tune:
            return None
        if len(self._attribution) % self.eval_window != 0:
            return None
        # Compute per-signal attribution: for each signal, correlate its signed
        # score at trade time with the realized pnl. A signal that agreed with
        # winning fades (its score had the same sign as the eventual fade
        # direction) should be up-weighted.
        new_weights = dict(self._weights)
        for name in self.signal_names:
            scores = []
            pnls = []
            for components, pnl in self._attribution:
                if name not in components:
                    continue
                scores.append(components[name])
                # Positive pnl = the fade won. The fade direction was opposite
                # to crowd_score, so a signal that had a LARGE |score| in the
                # crowd direction is "credited" when pnl > 0 (it correctly
                # flagged the extreme) and "debited" when pnl < 0.
                pnls.append(pnl)
            if len(scores) < 5:
                continue
            scores_a = np.array(scores)
            pnls_a = np.array(pnls)
            # Attribution = mean( |score| * sign(pnl) )  → high if the signal's
            # extremes preceded profitable fades, low/negative if it misled.
            attribution = float(np.mean(np.abs(scores_a) * np.sign(pnls_a)))
            # Gradient-ascent style update + exploration noise
            grad = attribution * self.lr
            noise = float(self._rng.normal(0, self.mutation_std))
            new_weights[name] = new_weights.get(name, 0.2) + grad + noise
            new_weights[name] = max(self.min_weight, min(self.max_weight, new_weights[name]))
        # Normalize so the sum is meaningful (weights are relative)
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: v / total * len(new_weights) for k, v in new_weights.items()}
        old = self._weights
        self._weights = new_weights
        self._tune_count += 1
        # Keep the attribution window rolling (drop oldest 50% to adapt to regime)
        keep = len(self._attribution) // 2
        self._attribution = deque(list(self._attribution)[-keep:], maxlen=200)
        logger.info(
            f"Crowd weights tuned #{self._tune_count}: "
            f"{ {k: round(v, 3) for k, v in new_weights.items()} }",
            extra={"component": "crowd_engine.optimizer"},
        )
        return new_weights

    def stats(self) -> dict:
        return {
            "tune_count": self._tune_count,
            "trades_recorded": len(self._attribution),
            "weights": {k: round(v, 4) for k, v in self._weights.items()},
        }
