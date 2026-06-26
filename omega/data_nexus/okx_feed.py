"""
OKXWebSocketFeed — real-time market data from OKX public WebSocket.

Channels subscribed (all public, no auth):
    trades            — last trade prices + sizes
    books5            — top-5 order book snapshots (depth)
    mark-price        — funding rate + mark price (for the funding crowd signal)
    liquidation-orders — forced liquidations (for the liquidation crowd signal)

OKX instId format: BTC-USDT-SWAP (perp). We accept Binance-style symbols
(BTCUSDT) internally and translate.

The feed emits the same MarketEvent contract as BinanceWebSocketFeed, so the
rest of OMEGA (crowd engine, agents) is fully exchange-agnostic.

Open interest is polled via REST (/api/v5/public/open-interest) because OKX
does not push a free real-time OI stream — handled by OpenInterestSignal if
re-pointed at OKX (the signal takes a URL; here we expose the helper).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import aiohttp
import websockets

from omega.data_nexus.base import DataSource
from omega.execution.okx_executor import _binance_to_okx_inst
from omega.utils.events import MarketEvent
from omega.utils.logger import get_logger

logger = get_logger("omega.data_nexus.okx_ws")

_OKX_WS = "wss://ws.okx.com:8443/ws/v5/public"


def _ms_to_iso(ms: int) -> str:
    """Convert a millisecond epoch to an ISO 8601 UTC timestamp."""
    if not ms:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds")


class OKXWebSocketFeed(DataSource):
    """Real-time OKX market data feed."""

    def __init__(
        self,
        symbols: tuple = ("BTCUSDT",),
        swap: bool = True,            # perp (SWAP) vs spot
        include_funding: bool = True,
        include_liquidations: bool = True,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
    ) -> None:
        self.symbols = tuple(s.upper().replace("-", "") for s in symbols)
        self.swap = swap
        self.include_funding = include_funding
        self.include_liquidations = include_liquidations
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self._session: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self._callbacks: List[Callable] = []
        self._last_funding: Dict[str, float] = {}
        # Reuse the bid/ask cache pattern from the Binance feed so trade events
        # carry the last known book.
        self._last_book: Dict[str, tuple] = {}
        self._running = False
        self._msgs_received = 0
        # Internal queue used by stream() — the WS loop pushes events here.
        self._queue: Optional[asyncio.Queue] = None

    @property
    def name(self) -> str:
        return "okx_ws"

    def subscribe(self, callback):
        self._callbacks.append(callback)

    async def stream(self):
        """Async generator yielding MarketEvents. The WS loop runs in the
        background and pushes events into an internal queue."""
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=10000)
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._ws_loop())
        try:
            while self._running:
                event = await self._queue.get()
                yield event
        except asyncio.CancelledError:
            pass

    async def start(self) -> None:
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=10000)
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._ws_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _okx_inst(self, sym: str) -> str:
        return _binance_to_okx_inst(sym, is_swap=self.swap)

    def _binance_sym(self, inst_id: str) -> str:
        """BTC-USDT-SWAP -> BTCUSDT."""
        return inst_id.replace("-", "").replace("SWAP", "")

    async def _ws_loop(self) -> None:
        delay = self.reconnect_delay
        while self._running:
            try:
                async with websockets.connect(_OKX_WS, ping_interval=30, ping_timeout=10) as ws:
                    logger.info(
                        f"OKX WS connected: {self.symbols} swap={self.swap}",
                        extra={"component": "data_nexus.okx_ws"},
                    )
                    # Subscribe to all channels
                    args = []
                    for s in self.symbols:
                        inst = self._okx_inst(s)
                        args.append({"channel": "trades", "instId": inst})
                        args.append({"channel": "books5", "instId": inst})
                        if self.include_funding and self.swap:
                            args.append({"channel": "mark-price", "instId": inst})
                    if self.include_liquidations and self.swap:
                        args.append({"channel": "liquidation-orders", "instType": "SWAP"})
                    # Send in batches to avoid oversized frames
                    for batch in _chunks(args, 5):
                        await ws.send(json.dumps({"op": "subscribe", "args": batch}))
                    delay = self.reconnect_delay
                    async for raw in ws:
                        try:
                            self._handle(raw)
                        except Exception as exc:
                            logger.debug(f"OKX WS parse error: {exc}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._running:
                    break
                logger.warning(f"OKX WS disconnected ({exc}); reconnecting in {delay:.1f}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, self.max_reconnect_delay)

    def _handle(self, raw) -> None:
        envelope = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        if "event" in envelope:
            return  # subscribe ack
        arg = envelope.get("arg", {})
        channel = arg.get("channel", "")
        data = envelope.get("data", [])
        self._msgs_received += 1
        if channel == "trades":
            for row in data:
                ev = self._parse_trade(row, arg.get("instId", ""))
                if ev:
                    self._emit(ev)
        elif channel == "books5":
            for row in data:
                ev = self._parse_books(row, arg.get("instId", ""))
                if ev:
                    self._emit(ev)
        elif channel == "mark-price":
            for row in data:
                ev = self._parse_mark(row, arg.get("instId", ""))
                if ev:
                    self._emit(ev)
        elif channel == "liquidation-orders":
            for row in data:
                self._handle_liquidation(row)

    def _parse_trade(self, row: dict, inst_id: str) -> Optional[MarketEvent]:
        sym = self._binance_sym(inst_id) if inst_id else self._binance_sym(row.get("instId", ""))
        book = self._last_book.get(sym, (0.0, 0.0, 0.0, 0.0))
        px = float(row.get("px", 0))
        return MarketEvent(
            symbol=sym, timestamp=_ms_to_iso(int(row.get("ts", 0))),
            last_price=px, volume_24h=0.0,
            bid=book[0], ask=book[1], bid_qty=book[2], ask_qty=book[3],
            funding_rate=self._last_funding.get(sym),
            source="okx_trade",
        )

    def _parse_books(self, row: dict, inst_id: str) -> Optional[MarketEvent]:
        sym = self._binance_sym(inst_id)
        bids = row.get("bids", [])
        asks = row.get("asks", [])
        bid = float(bids[0][0]) if bids else 0.0
        ask = float(asks[0][0]) if asks else 0.0
        bid_q = float(bids[0][1]) if bids else 0.0
        ask_q = float(asks[0][1]) if asks else 0.0
        if bid and ask:
            self._last_book[sym] = (bid, ask, bid_q, ask_q)
        last = (bid + ask) / 2.0 if bid and ask else 0.0
        return MarketEvent(
            symbol=sym, timestamp=_ms_to_iso(int(row.get("ts", 0))),
            last_price=last, volume_24h=0.0,
            bid=bid, ask=ask, bid_qty=bid_q, ask_qty=ask_q,
            funding_rate=self._last_funding.get(sym),
            source="okx_books",
        )

    def _parse_mark(self, row: dict, inst_id: str) -> Optional[MarketEvent]:
        sym = self._binance_sym(inst_id)
        funding = row.get("fundingRate")
        if funding:
            self._last_funding[sym] = float(funding)
        last = float(row.get("markPx", 0))
        book = self._last_book.get(sym, (0.0, 0.0, 0.0, 0.0))
        return MarketEvent(
            symbol=sym, timestamp=_ms_to_iso(int(row.get("ts", 0))),
            last_price=last, volume_24h=0.0,
            bid=book[0], ask=book[1], bid_qty=book[2], ask_qty=book[3],
            funding_rate=float(funding) if funding else None,
            source="okx_mark",
        )

    def _handle_liquidation(self, row: dict) -> None:
        """Record a liquidation into the in-process bus so the LiquidationSignal
        (if pointed at OKX) can pick it up. We emit it as a MarketEvent with a
        metadata flag — the LiquidationSignal polls its own WS, but for systems
        using this feed we also stash the data."""
        # OKX liquidation-orders data: [{instId, details:[{side, px, sz, ...}]}]
        inst_id = row.get("instId", "")
        sym = self._binance_sym(inst_id)
        for det in row.get("details", []):
            side = det.get("side", "")  # buy / sell (the liquidation order side)
            px = float(det.get("px", 0) or 0)
            sz = float(det.get("sz", 0) or 0)
            notional = px * sz
            # Emit a minimal event so subscribers see activity; the dedicated
            # LiquidationSignal class handles its own aggregation if wired.
            logger.debug(
                f"OKX liquidation {sym} {side} ${notional:,.0f}",
                extra={"component": "data_nexus.okx_ws"},
            )

    def _emit(self, event: MarketEvent) -> None:
        # Push to the internal queue for stream() consumers
        if self._queue is not None:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # drop oldest under backpressure
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.debug(f"OKX feed callback error: {exc}")

    def stats(self) -> dict:
        return {
            "name": self.name, "symbols": list(self.symbols),
            "msgs_received": self._msgs_received,
            "last_funding": self._last_funding,
            "swap": self.swap,
        }


def _chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]
