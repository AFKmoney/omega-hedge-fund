"""
OMEGA — Autonomous Multi-Modal AI Hedge Fund Entity
====================================================

A self-evolving, multi-agent trading system integrating:
  - L2/L3 order book microstructure
  - On-chain analytics
  - Global macroeconomic feeds
  - Real-time news NLP
  - Social sentiment
  - Reinforcement learning (PPO)
  - Asymmetric risk management (Kelly + Monte Carlo)
  - Smart order routing with RL execution
  - Meta-cognitive self-evaluation

Layers:
    1. Data Nexus      — omniscient streaming ingestion
    2. Alpha Swarm     — multi-agent mixture-of-experts
    3. Regime Detector — HMM-based market regime context
    4. Risk Aegis      — survival-first risk gating
    5. Execution Blade — RL-driven smart order routing
    6. Meta-Cognition  — self-evaluating evolution loop

Public API:
    from omega import OmegaOrchestrator, Settings
    from omega.rl_environment import TradingEnvironment
"""

__version__ = "1.0.0"
__author__ = "OMGA Quantitative Research"

from omega.config.settings import Settings, load_settings
from omega.orchestrator import OmegaOrchestrator

__all__ = [
    "Settings",
    "load_settings",
    "OmegaOrchestrator",
    "__version__",
]
