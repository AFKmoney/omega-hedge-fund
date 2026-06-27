"""
MicroAlphaEngine — generates frequent signals with tiered conviction.

The problem: the crowd engine only fires on rare extremes (correct — that's
where the big money is). But idle capital = opportunity cost, and a bot that
sits silent for days feels dead.

The solution: THREE TIERS of conviction, each with different size/risk:

    TIER 1 — SCALP (every few minutes)
        Fast momentum + order-flow imbalance + spread anomalies.
        Tiny size (0.2% equity), very tight TP/SL. High frequency, low edge.
        Purpose: stay engaged, capture micro-moves, keep the book active.

    TIER 2 — NORMAL (every 30-60 min)
        Breakthrough module signals (toxic flow, smart money div, multi-TF align).
        Medium size (0.5-1% equity). Moderate TP/SL.
        Purpose: catch intraday swings.

    TIER 3 — HIGH CONVICTION (rare, every few hours/days)
        Crowd engine extreme + cascade predictor + stress alignment.
        Full Kelly size. Wide TP, tight stop (asymmetric contrarian).
        Purpose: the money-makers.

Each tier has its OWN min-interval, so Tier 1 can fire every 2 min while Tier 3
waits for a true extreme. All three coexist.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from omega.utils.events import MarketEvent, SignalEvent, Side
from omega.utils.logger import get_logger

logger = get_logger("omega.alpha_swarm.micro_alpha")


@dataclass
class TierConfig:
    """Configuration for one conviction tier."""
    name: str
    min_interval_sec: float       # minimum time between signals of this tier
    size_fraction: float          # fraction of Kelly to use (0-1)
    confidence_floor: float       # min confidence to emit
    tp_bps: float
    sl_bps: float
    holding_bars: int


# The three tiers
TIER_SCALP = TierConfig(
    name="scalp", min_interval_sec=120, size_fraction=0.15,
    confidence_floor=0.45, tp_bps=15, sl_bps=8, holding_bars=12,
)
TIER_NORMAL = TierConfig(
    name="normal", min_interval_sec=600, size_fraction=0.40,
    confidence_floor=0.55, tp_bps=60, sl_bps=25, holding_bars=60,
)
TIER_HIGH = TierConfig(
    name="high_conviction", min_interval_sec=1200, size_fraction=1.0,
    confidence_floor=0.70, tp_bps=200, sl_bps=60, holding_bars=240,
)


class MicroAlphaEngine:
    """
    Frequent-signal engine with 3 conviction tiers.

    Feeds on MarketEvents and produces signals across all tiers. Each tier
    has its own cooldown and confidence floor, so the bot is always active
    (scalp tier) while reserving big bets for true extremes (high tier).
    """

    def __init__(self, symbols: tuple = ("BTCUSDT", "ETHUSDT", "SOLUSDT")) -> None:
        self.symbols = symbols
        self._prices: Dict[str, Deque[float]] = {s: deque(maxlen=200) for s in symbols}
        self._volumes: Dict[str, Deque[float]] = {s: deque(maxlen=200) for s in symbols}
        self._bid_q: Dict[str, Deque[float]] = {s: deque(maxlen=50) for s in symbols}
        self._ask_q: Dict[str, Deque[float]] = {s: deque(maxlen=50) for s in symbols}
        self._last_signal_ts: Dict[str, float] = {}  # "BTCUSDT:scalp" -> ts
        self._signals_emitted = {"scalp": 0, "normal": 0, "high_conviction": 0}
        self._bars: int = 0

    def on_market(self, event: MarketEvent) -> List[SignalEvent]:
        """Process a market event. Returns 0-3 signals (one per tier max)."""
        sym = event.symbol
        if sym not in self._prices:
            return []
        self._prices[sym].append(event.last_price)
        self._volumes[sym].append(event.volume_24h)
        if event.bid_qty > 0:
            self._bid_q[sym].append(event.bid_qty)
        if event.ask_qty > 0:
            self._ask_q[sym].append(event.ask_qty)
        self._bars += 1

        signals: List[SignalEvent] = []
        # Don't fire until we have enough data
        if len(self._prices[sym]) < 20:
            return []

        # --- TIER 1: SCALP (fast momentum + imbalance) ---
        scalp = self._check_scalp(sym, event)
        if scalp:
            signals.append(scalp)

        # --- TIER 2: NORMAL (multi-signal confluence) ---
        if self._bars % 10 == 0:  # check every 10 bars to save CPU
            normal = self._check_normal(sym, event)
            if normal:
                signals.append(normal)

        # --- TIER 3: HIGH CONVICTION (extreme momentum + vol) ---
        if self._bars % 20 == 0:
            high = self._check_high_conviction(sym, event)
            if high:
                signals.append(high)

        return signals

    def _can_emit(self, sym: str, tier: str, cooldown: float) -> bool:
        key = f"{sym}:{tier}"
        last = self._last_signal_ts.get(key, 0)
        if time.time() - last < cooldown:
            return False
        self._last_signal_ts[key] = time.time()
        return True

    # ------------------------------------------------------------------
    # TIER 1: Scalp — fast EMA cross + order book imbalance
    # ------------------------------------------------------------------

    def _check_scalp(self, sym: str, event: MarketEvent) -> Optional[SignalEvent]:
        if not self._can_emit(sym, "scalp", TIER_SCALP.min_interval_sec):
            return None
        prices = list(self._prices[sym])
        if len(prices) < 20:
            return None
        # Fast EMA (3) vs slow EMA (8)
        ema3 = self._ema(prices[-12:], 3)
        ema8 = self._ema(prices[-24:], 8)
        momentum = (ema3 - ema8) / (ema8 + 1e-9)
        # Order book imbalance
        bid_q = list(self._bid_q[sym])
        ask_q = list(self._ask_q[sym])
        if not bid_q or not ask_q:
            return None
        avg_bid = sum(bid_q) / len(bid_q)
        avg_ask = sum(ask_q) / len(ask_q)
        imbalance = (avg_bid - avg_ask) / (avg_bid + avg_ask + 1e-9)
        # Combined score: momentum (scaled to 0-1 range) + imbalance
        # Momentum of 0.002 = strong for crypto intrabar; scale ×200
        score = momentum * 200 + imbalance * 1.0
        if abs(score) < TIER_SCALP.confidence_floor:
            return None
        side = Side.BUY if score > 0 else Side.SELL
        confidence = min(0.75, abs(score))
        self._signals_emitted["scalp"] += 1
        return SignalEvent(
            agent="micro_scalp", symbol=sym, timestamp=event.timestamp,
            side=side, confidence=confidence,
            expected_holding_period_bars=TIER_SCALP.holding_bars,
            stop_loss_bps=TIER_SCALP.sl_bps, take_profit_bps=TIER_SCALP.tp_bps,
            rationale=f"Scalp: ema_cross={momentum:+.4f} imb={imbalance:+.2f}",
            metadata={"tier": "scalp", "size_fraction": TIER_SCALP.size_fraction},
        )

    # ------------------------------------------------------------------
    # TIER 2: Normal — RSI + vol breakout + multi-bar momentum
    # ------------------------------------------------------------------

    def _check_normal(self, sym: str, event: MarketEvent) -> Optional[SignalEvent]:
        if not self._can_emit(sym, "normal", TIER_NORMAL.min_interval_sec):
            return None
        prices = list(self._prices[sym])
        if len(prices) < 50:
            return None
        # RSI
        rsi = self._rsi(prices, 14)
        # Volatility breakout: current bar range vs avg
        recent = prices[-20:]
        avg_range = sum(abs(recent[i] - recent[i-1]) for i in range(1, len(recent))) / (len(recent) - 1)
        last_move = abs(prices[-1] - prices[-2]) if len(prices) >= 2 else 0
        vol_ratio = last_move / (avg_range + 1e-9)
        # Trend strength (20-bar)
        trend = (prices[-1] - prices[-20]) / (prices[-20] + 1e-9) * 100  # bps
        # Confluence: RSI extreme + vol breakout + trend direction
        score = 0.0
        if rsi > 65 and vol_ratio > 1.5:
            score = 0.6  # overbought + breakout = momentum continuation
        elif rsi < 35 and vol_ratio > 1.5:
            score = -0.6
        elif abs(trend) > 50:  # strong 20-bar trend
            score = 0.5 * (1 if trend > 0 else -1)
        if abs(score) < TIER_NORMAL.confidence_floor:
            return None
        side = Side.BUY if score > 0 else Side.SELL
        confidence = min(0.80, abs(score))
        self._signals_emitted["normal"] += 1
        return SignalEvent(
            agent="micro_normal", symbol=sym, timestamp=event.timestamp,
            side=side, confidence=confidence,
            expected_holding_period_bars=TIER_NORMAL.holding_bars,
            stop_loss_bps=TIER_NORMAL.sl_bps, take_profit_bps=TIER_NORMAL.tp_bps,
            rationale=f"Normal: RSI={rsi:.0f} vol_ratio={vol_ratio:.1f} trend={trend:+.0f}bps",
            metadata={"tier": "normal", "size_fraction": TIER_NORMAL.size_fraction},
        )

    # ------------------------------------------------------------------
    # TIER 3: High conviction — extreme momentum + vol spike + directional
    # ------------------------------------------------------------------

    def _check_high_conviction(self, sym: str, event: MarketEvent) -> Optional[SignalEvent]:
        if not self._can_emit(sym, "high_conviction", TIER_HIGH.min_interval_sec):
            return None
        prices = list(self._prices[sym])
        if len(prices) < 80:
            return None
        # Extreme RSI
        rsi = self._rsi(prices, 14)
        # Volatility (30-bar std of returns)
        rets = np.diff(np.log(np.array(prices[-30:]) + 1e-9))
        vol = float(np.std(rets))
        vol_z = vol / 0.003 if vol > 0 else 0  # normalize (0.3% = baseline)
        # 50-bar trend
        trend = (prices[-1] - prices[-50]) / (prices[-50] + 1e-9)
        # High conviction requires MULTIPLE confluences
        bull_score = 0.0
        bear_score = 0.0
        if rsi < 25: bear_score += 0.3  # extreme oversold
        if rsi > 75: bull_score += 0.3  # extreme overbought
        if vol_z > 2.0: bull_score += 0.2; bear_score += 0.2  # vol spike both directions
        if trend > 0.02: bull_score += 0.3  # strong uptrend
        if trend < -0.02: bear_score += 0.3
        # Mean-reversion bias for extreme: if RSI extreme, we FADE it
        if rsi > 80: bear_score += 0.2  # fade overbought
        if rsi < 20: bull_score += 0.2  # fade oversold
        score = bull_score - bear_score
        if abs(score) < TIER_HIGH.confidence_floor:
            return None
        side = Side.BUY if score > 0 else Side.SELL
        confidence = min(0.92, abs(score))
        self._signals_emitted["high_conviction"] += 1
        logger.info(
            f"HIGH CONVICTION signal: {sym} {side.value} conf={confidence:.2f} "
            f"RSI={rsi:.0f} vol_z={vol_z:.1f} trend={trend*100:+.1f}%",
            extra={"component": "alpha_swarm.micro_alpha", "symbol": sym},
        )
        return SignalEvent(
            agent="micro_high", symbol=sym, timestamp=event.timestamp,
            side=side, confidence=confidence,
            expected_holding_period_bars=TIER_HIGH.holding_bars,
            stop_loss_bps=TIER_HIGH.sl_bps, take_profit_bps=TIER_HIGH.tp_bps,
            rationale=f"HighConviction: RSI={rsi:.0f} vol_z={vol_z:.1f} bull={bull_score:.2f} bear={bear_score:.2f}",
            metadata={"tier": "high_conviction", "size_fraction": TIER_HIGH.size_fraction},
        )

    @staticmethod
    def _ema(data: list, period: int) -> float:
        if not data:
            return 0.0
        k = 2 / (period + 1)
        ema = data[0]
        for p in data[1:]:
            ema = p * k + ema * (1 - k)
        return ema

    @staticmethod
    def _rsi(prices: list, period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        deltas = np.diff(prices[-(period + 1):])
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses) + 1e-9
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def stats(self) -> dict:
        return {
            "name": "micro_alpha_engine",
            "signals_emitted": self._signals_emitted,
            "bars_processed": self._bars,
            "symbols": list(self.symbols),
        }
