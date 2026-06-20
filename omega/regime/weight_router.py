"""
RegimeWeightRouter — translates regime label → agent weight allocation.

When the HMM detects a regime change, this module returns a new weight
dict that the DebateChamber uses to re-weight each agent's vote.

Default weight matrix (rows = regimes, cols = agents):
                  ppo_trend  ppo_meanrev  llm_macro  stat_arb
    calm_bull        0.50       0.10         0.25       0.15
    volatile_bull    0.30       0.20         0.30       0.20
    choppy           0.10       0.45         0.20       0.25
    bear             0.05       0.30         0.40       0.25

Rationale:
    - Trend following works in trending regimes, dies in choppy/bear
    - Mean reversion excels in choppy markets, dies in trends
    - LLM macro is most valuable in regime transitions and crisis
    - Stat-arb is regime-agnostic but slightly better in stable regimes
"""

from __future__ import annotations

from typing import Dict

from omega.config.settings import RegimeSettings
from omega.utils.logger import get_logger

logger = get_logger("omega.regime.weights")


WEIGHT_MATRIX: Dict[str, Dict[str, float]] = {
    "calm_bull":     {"ppo_trend": 0.50, "ppo_meanrev": 0.10, "llm_macro": 0.25, "stat_arb": 0.15},
    "volatile_bull": {"ppo_trend": 0.30, "ppo_meanrev": 0.20, "llm_macro": 0.30, "stat_arb": 0.20},
    "choppy":        {"ppo_trend": 0.10, "ppo_meanrev": 0.45, "llm_macro": 0.20, "stat_arb": 0.25},
    "bear":          {"ppo_trend": 0.05, "ppo_meanrev": 0.30, "llm_macro": 0.40, "stat_arb": 0.25},
    "unknown":       {"ppo_trend": 0.25, "ppo_meanrev": 0.25, "llm_macro": 0.25, "stat_arb": 0.25},
}


class RegimeWeightRouter:
    """Maps regime label to agent weight dict."""

    def __init__(self, settings: RegimeSettings | None = None) -> None:
        self.settings = settings or RegimeSettings()
        self._matrix = WEIGHT_MATRIX
        self._last_regime: str = "unknown"

    def weights_for(self, regime: str) -> Dict[str, float]:
        """Return agent weights for the given regime."""
        weights = self._matrix.get(regime, self._matrix["unknown"])
        if regime != self._last_regime:
            logger.info(
                f"Regime weights updated for '{regime}': {weights}",
                extra={"component": "regime.weights", "regime": regime},
            )
            self._last_regime = regime
        return dict(weights)
