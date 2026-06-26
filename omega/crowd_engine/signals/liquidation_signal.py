"""
LiquidationSignal — real-time forced-liquidation flow from the Binance Futures
public WebSocket.

Subscribes to the all-symbols liquidation stream:
    wss://fstream.binance.com/ws/!forceOrder@arr

Each message is a forced liquidation (a position that got stopped out by the
exchange because margin ran out). The SIDE of the liquidation tells us which
way the crowd was overcrowded:
    liquidated SELL side  → these were LONGS being force-closed (long cascade)
    liquidated BUY side   → these were SHORTS being force-closed (short cascade)

We aggregate liquidation USD volume over a rolling window (default 5 min) per
symbol, split by side. A spike in long-liquidations means the crowd was
overcrowded long (confirmed by the market actually punishing them) → score > 0
(so the contrarian fades by going short, i.e. with the cascade direction that
has cleared the overcrowded side).

This is the most *predictive* of the cascade signals: liquidations cluster and
cascade. A burst is both confirmation that the crowd was overcrowded AND a
predictor that more liquidations are coming (the cascade feeds itself via the
price impact of each forced sale).

Normalization:
    net_liq = long_liq_usd - short_liq_usd   (positive = longs got wrecked)
    score = clamp(tanh(net_liq / threshold), -1, 1)
    threshold defaults to $50M (a significant 5-min cascade).
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

import websockets

from omega.crowd_engine.signals.base import PositioningSignal, SignalReading
from omega.utils.logger import get_logger

logger = get_logger("omega.crowd_engine.liquidations")

_LIQ_WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"


class LiquidationSignal(PositioningSignal):
    """Crowd positioning from real-time liquidation flow."""

    name = "liquidations"

    def __init__(
        self,
        symbols: tuple = ("BTCUSDT",),
        window_sec: int = 300,        # 5-min rolling window
        threshold_usd: float = 50_000_000.0,  # $50M net = saturation
        weight: float = 0.45,         # highest weight — most predictive
        horizon: str = "minutes",
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
    ) -> None:
        self.symbols = tuple(s.upper() for s in symbols)
        self.window_sec = window_sec
        self.threshold_usd = threshold_usd
        self.weight = weight
        self.horizon = horizon
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        # Rolling window of (timestamp, side, notional_usd) per symbol.
        # side = "LONG" means a LONG position was liquidated (forced SELL).
        self._events: Dict[str, Deque[Tuple[float, str, float]]] = {
            s: deque(maxlen=5000) for s in self.symbols
        }
        self._task: Optional[asyncio.Task] = None
        self._total_seen = 0

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._ws_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _ws_loop(self) -> None:
        delay = self.reconnect_delay
        while True:
            try:
                async with websockets.connect(_LIQ_WS_URL, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(
                        "Liquidation WS connected",
                        extra={"component": "crowd_engine.liquidations"},
                    )
                    delay = self.reconnect_delay
                    async for raw in ws:
                        try:
                            self._handle(raw)
                        except Exception as exc:
                            logger.debug(f"LIQ parse error: {exc}")
            except asyncio.CancelledError:
                logger.info("Liquidation feed cancelled")
                raise
            except Exception as exc:
                logger.warning(
                    f"LIQ WS disconnected ({exc}); reconnecting in {delay:.1f}s"
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, self.max_reconnect_delay)

    def _handle(self, raw) -> None:
        """Parse one !forceOrder@arr frame and record it."""
        envelope = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        order = envelope.get("o", envelope)
        sym = order.get("s", "")
        if not sym:
            return
        # Only track our configured symbols
        if sym not in self._events:
            # Allow case-insensitive match
            if sym.upper() not in self.symbols:
                return
            sym = sym.upper()
        side = order.get("S", "")  # SELL = a long was liquidated
        price = float(order.get("ap", order.get("p", 0)) or 0)
        qty = float(order.get("q", 0) or 0)
        notional = price * qty
        if notional <= 0:
            return
        # Normalize side: a liquidation order SELL means a LONG position died.
        liq_side = "LONG" if side == "SELL" else ("SHORT" if side == "BUY" else "")
        if not liq_side:
            return
        self._total_seen += 1
        self._events.setdefault(sym, deque(maxlen=5000)).append(
            (time.time(), liq_side, notional)
        )

    def _aggregate(self, symbol: str) -> Tuple[float, float]:
        """Return (long_liq_usd, short_liq_usd) over the rolling window."""
        now = time.time()
        cutoff = now - self.window_sec
        events = self._events.get(symbol)
        if not events:
            return 0.0, 0.0
        long_usd = short_usd = 0.0
        for ts, side, notional in events:
            if ts < cutoff:
                continue
            if side == "LONG":
                long_usd += notional
            else:
                short_usd += notional
        return long_usd, short_usd

    def reading_for(self, symbol: str) -> Optional[SignalReading]:
        if symbol not in self._events:
            return None
        long_usd, short_usd = self._aggregate(symbol)
        net = long_usd - short_usd  # + = longs wrecked (crowd was long)
        if self.threshold_usd <= 0:
            score = 0.0
        else:
            score = max(-1.0, min(1.0, math.tanh(net / self.threshold_usd)))
        return SignalReading(
            score=score,
            horizon=self.horizon,
            weight=self.weight,
            raw={"long_liq_usd": long_usd, "short_liq_usd": short_usd,
                 "net_usd": net, "window_sec": self.window_sec},
        )

    def reading(self) -> Optional[SignalReading]:
        if not any(self._events.values()):
            return None
        tot_long = tot_short = 0.0
        for sym in self._events:
            l, s = self._aggregate(sym)
            tot_long += l
            tot_short += s
        net = tot_long - tot_short
        score = max(-1.0, min(1.0, math.tanh(net / self.threshold_usd)))
        return SignalReading(score=score, horizon=self.horizon, weight=self.weight,
                             raw={"net_usd": net})

    def stats(self) -> dict:
        return {
            "name": self.name,
            "total_liquidations_seen": self._total_seen,
            "symbols_tracked": list(self._events.keys()),
            "window_sec": self.window_sec,
        }
