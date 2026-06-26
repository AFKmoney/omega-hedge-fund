"""
Layer 1.5 — Crowd Positioning Engine.

Fuses eight positioning signals into a single CrowdPositioningEvent that the
Alpha Swarm's ContrarianAgent fades:
    - liquidations (real-time cascade confirmation — most predictive)
    - funding rate (perp leverage crowding)
    - open interest rate-of-change (leverage piling in / flushing)
    - long/short account ratio (retail account positioning)
    - sentiment (Fear & Greed — narrative fear)
    - social (CoinGecko trending — retail euphoria)
    - iceberg (passive hidden-order detection from depth microstructure)
    - inflow (on-chain exchange-bound whale transfers — imminent selling)

V4: fusion weights are mutable and tunable by the GeneticOptimizer.
"""
from omega.crowd_engine.engine import CrowdPositioningEngine
from omega.crowd_engine.signals.funding_signal import FundingRateSignal
from omega.crowd_engine.signals.iceberg_signal import IcebergDetectionSignal
from omega.crowd_engine.signals.inflow_signal import OnChainInflowSignal
from omega.crowd_engine.signals.liquidation_signal import LiquidationSignal
from omega.crowd_engine.signals.ls_ratio_signal import LSRatioSignal
from omega.crowd_engine.signals.open_interest_signal import OpenInterestSignal
from omega.crowd_engine.signals.sentiment_signal import SentimentSignal
from omega.crowd_engine.signals.social_signal import SocialSentimentSignal

__all__ = [
    "CrowdPositioningEngine",
    "FundingRateSignal",
    "IcebergDetectionSignal",
    "OnChainInflowSignal",
    "LiquidationSignal",
    "LSRatioSignal",
    "OpenInterestSignal",
    "SentimentSignal",
    "SocialSentimentSignal",
]
