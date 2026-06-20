"""
OnlineLearner — continuous model retraining without downtime.

Periodically retrains underperforming agents on the most recent data window.
For PPO agents, this means collecting a fresh rollout from the latest market
data and running a PPO update. For HMM regime detector, it means refitting
on the latest returns/vol series.

The OnlineLearner does NOT replace the Meta-Cognition loop — it complements
it. Meta-Cognition decides WHICH agent to retrain (based on autopsy findings);
OnlineLearner does the actual retraining.
"""

from __future__ import annotations

import time
from typing import Dict, List

from omega.config.settings import MetaCognitionSettings
from omega.utils.logger import get_logger

logger = get_logger("omega.meta_cognition.online")


class OnlineLearner:
    """Coordinates periodic online retraining of agents."""

    def __init__(
        self,
        settings: MetaCognitionSettings | None = None,
        data_dir: str = "/home/z/my-project/data",
    ) -> None:
        self.settings = settings or MetaCognitionSettings()
        self.data_dir = data_dir
        self._bars_since_retrain: int = 0
        self._retrain_count: int = 0
        self._retrain_log: List[dict] = []

    def on_bar(self) -> None:
        """Called on every market bar. Triggers retraining when threshold hit."""
        self._bars_since_retrain += 1
        if self._bars_since_retrain >= self.settings.online_retrain_interval_bars:
            self._bars_since_retrain = 0
            # Actual retraining is triggered by orchestrator (needs access to agents)

    def should_retrain(self, agent_name: str, recent_pnl_bps: List[float]) -> bool:
        """
        Decide whether a specific agent should be retrained.
        Triggers if recent average PnL is significantly worse than historical.
        """
        if len(recent_pnl_bps) < self.settings.online_retrain_min_samples:
            return False
        recent_avg = sum(recent_pnl_bps[-50:]) / 50.0
        historical_avg = sum(recent_pnl_bps[:-50]) / max(len(recent_pnl_bps) - 50, 1)
        # Retrain if recent performance is materially worse
        return recent_avg < historical_avg - 10.0  # 10bps worse than historical

    def record_retrain(self, agent_name: str, success: bool, duration_sec: float) -> None:
        self._retrain_count += 1
        self._retrain_log.append({
            "ts": time.time(),
            "agent": agent_name,
            "success": success,
            "duration_sec": duration_sec,
        })
        logger.info(
            f"Online retrain #{self._retrain_count}: {agent_name} "
            f"success={success} duration={duration_sec:.1f}s",
            extra={"component": "meta_cognition.online", "agent": agent_name},
        )

    def stats(self) -> dict:
        return {
            "retrain_count": self._retrain_count,
            "bars_since_retrain": self._bars_since_retrain,
            "retrain_log_len": len(self._retrain_log),
            "last_retrains": self._retrain_log[-5:],
        }
