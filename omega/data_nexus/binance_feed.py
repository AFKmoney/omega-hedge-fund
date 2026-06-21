"""
BinanceWebSocketFeed — REAL live market data from Binance public WebSocket.

No API key required for public market data. Connects to:
    - Combined stream: <symbol>@trade, <symbol>@depth20@100ms, <symbol>@ticker
    - Optional: <symbol>@markPrice for funding rate (perpetuals)

This is genuinely live data — when OMEGA runs, it sees the actual order book
of Binance in real time. Falls back to no data if the network is unreachable,
but never returns mocked data.

Usage:
    feed = BinanceWebSocketFeed(symbols=("BTCUSDT", "ETHUSDT"))
    async for event in feed.stream():
        await risk_aegis.on_market_event(event)
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, List, Optional

import aiohttp
import websockets

from omega.data_nexus.base import DataSource
from omega.utils.events import MarketEvent
from omega.utils.logger import get_logger

logger = get_logger("omega.data_nexus.binance")


class BinanceWebSocketFeed(DataSource):
    """Real-time L2 order book + trades from Binance public WebSocket."""

    name = "binance_ws"

    def __init__(
        self,
        symbols: tuple = ("BTCUSDT",),
        ws_url: str = "wss://stream.binance.com:9443/stream",
        depth_levels: int = 20,
        include_funding: bool = True,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
    ) -> None:
        self.symbols = tuple(s.upper() for s in symbols)
        self.ws_url = ws_url
        self.depth_levels = depth_levels
        self.include_funding = include_funding
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_funding: dict = {}
        # BUGFIX: cache the last known top-of-book per symbol so that pure
        # trade events (which carry no bid/ask) attach the most recent book
        # snapshot instead of zeros. Previously trade events had bid=ask=0,
        # making order-book-imbalance features intermittently zero.
        self._last_book: dict = {}  # symbol -> (bid, ask, bid_qty, ask_qty)

    def _build_stream_payload(self) -> List[str]:
        """Build the combined-stream subscription payload for Binance."""
        streams: List[str] = []
        for s in self.symbols:
            streams.append(f"{s.lower()}@trade")
            streams.append(f"{s.lower()}@depth{self.depth_levels}@100ms")
            streams.append(f"{s.lower()}@ticker")
            if self.include_funding:
                streams.append(f"{s.lower()}@markPrice@1s")
        return streams

    def _build_ws_url(self) -> str:
        """Construct the combined-stream URL."""
        streams = "/".join(self._build_stream_payload())
        return f"{self.ws_url}?streams={streams}"

    async def _connect(self):
        """Open the WebSocket with a generous timeout."""
        return await websockets.connect(
            self._build_ws_url(),
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
            max_size=2**22,  # 4 MB — depth20 updates can be chunky
        )

    async def stream(self) -> AsyncIterator[MarketEvent]:
        """Yield MarketEvent objects forever. Reconnects with exponential backoff."""
        delay = self.reconnect_delay
        while True:
            try:
                async with await self._connect() as ws:
                    logger.info(
                        "Binance WebSocket connected",
                        extra={"component": "data_nexus.binance"},
                    )
                    delay = self.reconnect_delay  # reset backoff on success
                    async for raw in ws:
                        try:
                            event = self._parse(raw)
                            if event is not None:
                                yield event
                        except Exception as exc:
                            logger.exception(
                                f"Parse error: {exc}",
                                extra={"component": "data_nexus.binance"},
                            )
            except asyncio.CancelledError:
                logger.info("Binance feed cancelled, shutting down")
                raise
            except Exception as exc:
                logger.warning(
                    f"Binance WS disconnected ({exc}); reconnecting in {delay:.1f}s",
                    extra={"component": "data_nexus.binance"},
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, self.max_reconnect_delay)

    def _parse(self, raw) -> Optional[MarketEvent]:
        """Translate one Binance combined-stream frame into a MarketEvent."""
        envelope = json.loads(raw)
        data = envelope.get("data", envelope)
        stream = envelope.get("stream", "")
        event_type = data.get("e", "")

        if event_type == "trade":
            return self._parse_trade(data)
        elif event_type == "depthUpdate" or "@depth" in stream:
            # depth20 partial book: data has bids/asks lists
            if "bids" in data or "b" in data:
                return self._parse_depth(data)
            return None
        elif event_type == "24hrTicker":
            return self._parse_ticker(data)
        elif event_type == "markPriceUpdate":
            self._last_funding[data["s"]] = float(data.get("r", 0.0))
            return None
        return None

    def _parse_trade(self, data: dict) -> MarketEvent:
        sym = data["s"]
        # Attach the most recent known top-of-book for this symbol so that
        # order-book-imbalance features are not zeroed out on trade events.
        book = self._last_book.get(sym, (0.0, 0.0, 0.0, 0.0))
        return MarketEvent(
            symbol=sym,
            timestamp=self._ms_to_iso(data["T"]),
            last_price=float(data["p"]),
            volume_24h=0.0,  # filled in by ticker
            bid=book[0],
            ask=book[1],
            bid_qty=book[2],
            ask_qty=book[3],
            funding_rate=self._last_funding.get(sym),
            source="binance_trade",
        )

    def _parse_depth(self, data: dict) -> MarketEvent:
        sym = data.get("s") or data.get("symbol", "")
        bids_raw = data.get("bids") or data.get("b", [])
        asks_raw = data.get("asks") or data.get("a", [])
        bids = [(float(p), float(q)) for p, q in bids_raw[: self.depth_levels]]
        asks = [(float(p), float(q)) for p, q in asks_raw[: self.depth_levels]]
        bid = bids[0][0] if bids else 0.0
        ask = asks[0][0] if asks else 0.0
        bid_qty = bids[0][1] if bids else 0.0
        ask_qty = asks[0][1] if asks else 0.0
        last = (bid + ask) / 2.0 if bid and ask else 0.0
        if sym and bid and ask:
            self._last_book[sym] = (bid, ask, bid_qty, ask_qty)
        return MarketEvent(
            symbol=sym,
            timestamp=self._ms_to_iso(data.get("E", 0)),
            last_price=last,
            volume_24h=0.0,
            bid=bid,
            ask=ask,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            depth_bids=bids,
            depth_asks=asks,
            funding_rate=self._last_funding.get(sym),
            source="binance_depth",
        )

    def _parse_ticker(self, data: dict) -> MarketEvent:
        sym = data["s"]
        bid = float(data["b"])
        ask = float(data["a"])
        bid_qty = float(data["B"])
        ask_qty = float(data["A"])
        self._last_book[sym] = (bid, ask, bid_qty, ask_qty)
        return MarketEvent(
            symbol=sym,
            timestamp=self._ms_to_iso(data["E"]),
            last_price=float(data["c"]),
            volume_24h=float(data["v"]),
            bid=bid,
            ask=ask,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            funding_rate=self._last_funding.get(sym),
            source="binance_ticker",
        )

    @staticmethod
    def _ms_to_iso(ms: int) -> str:
        from datetime import datetime, timezone
        if not ms:
            return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        )
