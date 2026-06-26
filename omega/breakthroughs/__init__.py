"""
OMEGA Breakthroughs — 25 disruptive alpha modules.

Each module reads PUBLIC data and extracts a signal that retail doesn't see.
No market manipulation, no spoofing, no MEV insertion — pure information edge.

Grouped into 5 lots of 5:
    B1-B5:  Cascade prediction + funding forecast + whale tracker + options gamma + depeg
    B6-B10: Toxic flow + smart money + vol forecast + correlation breakdown + flash crash
    B11-B15: Volume profile + time-of-day + BTC dominance + exchange reserves + multi-TF
    B16-B20: Stablecoin flow + mempool + bridge tracker + economic calendar + stress
    B21-B25: Cross-venue arb + adaptive risk + DeFi yield + sentiment NLP + portfolio opt
"""
from omega.breakthroughs.cascade_predictor import CascadePredictor
from omega.breakthroughs.funding_forecast import FundingForecast
from omega.breakthroughs.whale_tracker import WhaleTracker
from omega.breakthroughs.gamma_signal import GammaExposureSignal
from omega.breakthroughs.depeg_alert import DepegAlert
from omega.breakthroughs.toxic_flow import ToxicFlowDetector
from omega.breakthroughs.smart_money import SmartMoneyDivergence
from omega.breakthroughs.vol_forecast import VolatilityForecast
from omega.breakthroughs.correlation_breakdown import CorrelationBreakdown
from omega.breakthroughs.flash_crash_scanner import FlashCrashScanner
from omega.breakthroughs.volume_profile import VolumeProfile
from omega.breakthroughs.time_of_day import TimeOfDayAlpha
from omega.breakthroughs.btc_dominance import BTCDominanceSignal
from omega.breakthroughs.exchange_reserves import ExchangeReserves
from omega.breakthroughs.multi_timeframe import MultiTimeframeSignal
from omega.breakthroughs.stablecoin_flow import StablecoinFlow
from omega.breakthroughs.mempool_monitor import MempoolMonitor
from omega.breakthroughs.bridge_tracker import BridgeTracker
from omega.breakthroughs.economic_calendar import EconomicCalendar
from omega.breakthroughs.stress_index import StressIndex
from omega.breakthroughs.cross_venue_arb import CrossVenueArbitrage
from omega.breakthroughs.adaptive_risk import AdaptiveRiskManager
from omega.breakthroughs.defi_yield import DeFiYieldScanner
from omega.breakthroughs.sentiment_nlp import SentimentNLP
from omega.breakthroughs.portfolio_optimizer import PortfolioOptimizer

__all__ = [
    "CascadePredictor", "FundingForecast", "WhaleTracker", "GammaExposureSignal",
    "DepegAlert", "ToxicFlowDetector", "SmartMoneyDivergence", "VolatilityForecast",
    "CorrelationBreakdown", "FlashCrashScanner", "VolumeProfile", "TimeOfDayAlpha",
    "BTCDominanceSignal", "ExchangeReserves", "MultiTimeframeSignal",
    "StablecoinFlow", "MempoolMonitor", "BridgeTracker", "EconomicCalendar",
    "StressIndex", "CrossVenueArbitrage", "AdaptiveRiskManager", "DeFiYieldScanner",
    "SentimentNLP", "PortfolioOptimizer",
]
