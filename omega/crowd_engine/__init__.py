"""
Layer 1.5 — Crowd Positioning Engine.

Fuses three positioning signals (funding rate, long/short ratio, sentiment)
into a single CrowdPositioningEvent that the Alpha Swarm's ContrarianAgent
fades. The engine also nudges the RegimeWeightRouter to defund trend agents
when the crowd is at a cascade-imminent extreme.

Thesis: 80% of traders lose because they pile into overcrowded extremes at the
worst moment. This engine quantifies that extreme and takes the other side.
"""
from omega.crowd_engine.engine import CrowdPositioningEngine
from omega.crowd_engine.signals.funding_signal import FundingRateSignal
from omega.crowd_engine.signals.ls_ratio_signal import LSRatioSignal
from omega.crowd_engine.signals.sentiment_signal import SentimentSignal

__all__ = [
    "CrowdPositioningEngine",
    "FundingRateSignal",
    "LSRatioSignal",
    "SentimentSignal",
]
