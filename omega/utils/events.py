"""
Event types passed between OMEGA layers.

All events are immutable dataclasses with a strict schema so that any layer
can be replaced (e.g. swap Binance feed for Coinbase) without breaking
downstream consumers. Events flow:

    Data Nexus ─► MarketEvent / NewsEvent / MacroEvent / OnChainEvent
                           │
                           ▼
                   Alpha Swarm ─► SignalEvent
                           │
                           ▼
                    Regime Detector ─► adjusts SignalEvent weights
                           │
                           ▼
                     Risk Aegis ─► OrderEvent (sized) or rejection
                           │
                           ▼
                  Execution Blade ─► FillEvent
                           │
                           ▼
                Meta-Cognition ─► TradeClosedEvent ─► (retrain loop)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    FLAT = "FLAT"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    ICEBERG = "ICEBERG"
    TWAP = "TWAP"
    VWAP = "VWAP"


class TimeInForce(str, Enum):
    GTC = "GTC"   # Good till cancelled
    IOC = "IOC"   # Immediate or cancel
    FOK = "FOK"   # Fill or kill


@dataclass(frozen=True)
class MarketEvent:
    """L1/L2 market data tick from Data Nexus."""
    symbol: str
    timestamp: str               # ISO-8601 UTC
    last_price: float
    volume_24h: float
    bid: float
    ask: float
    bid_qty: float = 0.0
    ask_qty: float = 0.0
    depth_bids: List[tuple] = field(default_factory=list)  # [(price, qty), ...]
    depth_asks: List[tuple] = field(default_factory=list)
    funding_rate: Optional[float] = None  # crypto perpetual funding
    source: str = "binance"


@dataclass(frozen=True)
class NewsEvent:
    """Tokenized news headline + LLM sentiment score from Data Nexus."""
    headline: str
    timestamp: str
    source: str                  # bloomberg | reuters | x | reddit
    url: str = ""
    sentiment_score: float = 0.0   # -1.0 .. +1.0
    relevance: float = 0.0         # 0.0 .. 1.0
    symbols_mentioned: tuple = ()


@dataclass(frozen=True)
class MacroEvent:
    """Macroeconomic indicator update."""
    indicator: str               # e.g. "CPI_YOY", "FED_FUNDS", "10Y_YIELD"
    timestamp: str
    value: float
    prior_value: Optional[float] = None
    surprise: Optional[float] = None  # actual - expected
    source: str = "fred"


@dataclass(frozen=True)
class OnChainEvent:
    """On-chain analytics event (whale moves, exchange flows, gas)."""
    chain: str                   # ethereum | bitcoin | solana
    event_type: str              # whale_move | exchange_inflow | gas_spike
    timestamp: str
    value_usd: float
    from_addr: str = ""
    to_addr: str = ""
    tx_hash: str = ""
    details: Dict = field(default_factory=dict)


@dataclass(frozen=True)
class SignalEvent:
    """Alpha Swarm → Risk Aegis signal. One per agent per timestamp."""
    agent: str                   # ppo_trend | ppo_meanrev | llm_macro | stat_arb
    symbol: str
    timestamp: str
    side: Side
    confidence: float            # 0.0 .. 1.0
    expected_holding_period_bars: int = 60
    expected_return_bps: float = 0.0
    stop_loss_bps: float = 100.0
    take_profit_bps: float = 200.0
    rationale: str = ""
    regime_weight: float = 1.0   # weight assigned by Regime Detector
    metadata: Dict = field(default_factory=dict)


@dataclass(frozen=True)
class OrderEvent:
    """Risk Aegis → Execution Blade. Already position-sized and risk-approved."""
    order_id: str = field(default_factory=lambda: _uid("ord"))
    symbol: str = ""
    side: Side = Side.FLAT
    qty: float = 0.0
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    twap_slices: int = 1
    iceberg_display_qty: Optional[float] = None
    strategy: str = ""           # which agent generated the underlying signal
    risk_score: float = 0.0
    metadata: Dict = field(default_factory=dict)


@dataclass(frozen=True)
class FillEvent:
    """Execution Blade → Meta-Cognition. Confirmed fill."""
    order_id: str
    symbol: str
    side: Side
    qty: float
    fill_price: float
    timestamp: str
    slippage_bps: float = 0.0
    exchange: str = "binance"
    fee_paid: float = 0.0


@dataclass(frozen=True)
class TradeClosedEvent:
    """A round-trip trade closed out. Fed to Meta-Cognition for autopsy."""
    trade_id: str = field(default_factory=lambda: _uid("trade"))
    symbol: str = ""
    side: Side = Side.FLAT
    entry_price: float = 0.0
    exit_price: float = 0.0
    qty: float = 0.0
    entry_time: str = ""
    exit_time: str = ""
    realized_pnl: float = 0.0
    realized_pnl_bps: float = 0.0
    max_favorable_excursion_bps: float = 0.0
    max_adverse_excursion_bps: float = 0.0
    holding_bars: int = 0
    strategy: str = ""
    regime: str = ""
    exit_reason: str = ""         # take_profit | stop_loss | signal_exit | kill_switch
    autopsy: Dict = field(default_factory=dict)
