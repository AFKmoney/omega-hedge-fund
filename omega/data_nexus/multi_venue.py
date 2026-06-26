"""
MultiVenueAggregator — reads public tickers from all supported exchanges in
parallel, giving a real-time cross-exchange price view.

This is the "God's eye" of crypto prices: you see BTC priced simultaneously
on OKX, Binance, Kraken, Coinbase, Bybit, KuCoin, MEXC, Gemini, Crypto.com,
and Gate.io. The spread between venues reveals:
    - Arbitrage opportunities (buy low venue, sell high venue)
    - Liquidity migration (which venue is leading the move)
    - Exchange-specific stress (one venue diverging = internal issue)

No API keys needed — all endpoints are public market data.

Each venue is polled asynchronously at a configurable interval. The aggregator
maintains the latest price per venue per symbol and exposes:
    - price_summary(symbol) → {venue: price, ...} + best bid/ask/spread
    - detect_divergence(symbol, threshold_bps) → venues that are off
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import aiohttp

from omega.config.exchanges import EXCHANGES, translate_symbol
from omega.utils.logger import get_logger

logger = get_logger("omega.data_nexus.multi_venue")


@dataclass
class VenuePrice:
    venue: str
    price: float
    bid: float = 0.0
    ask: float = 0.0
    volume_24h: float = 0.0
    timestamp: float = 0.0
    error: str = ""


# Per-venue ticker URL builders (public, no auth)
def _ticker_url(venue: str, symbol: str) -> Tuple[str, str]:
    """Return (url, exchange_symbol) for the public ticker endpoint."""
    spec = EXCHANGES[venue]
    sym = translate_symbol(symbol, spec.symbol_format)
    urls = {
        "okx": f"https://www.okx.com/api/v5/market/ticker?instId={sym}",
        "binance": f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}",
        "kraken": f"https://api.kraken.com/0/public/Ticker?pair={sym}",
        "coinbase": f"https://api.exchange.coinbase.com/products/{sym}/ticker",
        "bybit": f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym}",
        "kucoin": f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={sym}",
        "mexc": f"https://api.mexc.com/api/v3/ticker/24hr?symbol={sym}",
        "gemini": f"https://api.gemini.com/v2/ticker/{sym}",
        "crypto_com": f"https://api.crypto.com/v2/public/get-ticker?instrument_name={sym}",
        "gate": f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={sym}",
    }
    return urls.get(venue, ""), sym


def _parse_ticker(venue: str, data) -> VenuePrice:
    """Parse the venue-specific JSON into a VenuePrice."""
    now = time.time()
    try:
        if venue == "okx":
            d = data["data"][0]
            return VenuePrice(venue, float(d["last"]), float(d.get("bidPx",0) or 0),
                              float(d.get("askPx",0) or 0), float(d.get("vol24h",0) or 0), now)
        if venue == "binance":
            return VenuePrice(venue, float(data["lastPrice"]), float(data["bidPrice"]),
                              float(data["askPrice"]), float(data["volume"]), now)
        if venue == "kraken":
            k = list(data["result"].keys())[0]
            d = data["result"][k]
            return VenuePrice(venue, float(d["c"][0]), float(d["b"][0]), float(d["a"][0]),
                              float(d["v"][1]), now)
        if venue == "coinbase":
            return VenuePrice(venue, float(data["price"]), float(data.get("bid",0) or 0),
                              float(data.get("ask",0) or 0), float(data.get("volume",0) or 0), now)
        if venue == "bybit":
            d = data["result"]["list"][0]
            return VenuePrice(venue, float(d["lastPrice"]), float(d["bid1Price"]),
                              float(d["ask1Price"]), float(d.get("volume24h",0) or 0), now)
        if venue == "kucoin":
            d = data["data"]
            return VenuePrice(venue, float(d["price"]), float(d.get("bestBid",0) or 0),
                              float(d.get("bestAsk",0) or 0), float(d.get("size",0) or 0), now)
        if venue == "mexc":
            return VenuePrice(venue, float(data["lastPrice"]), float(data.get("bidPrice",0) or 0),
                              float(data.get("askPrice",0) or 0), float(data.get("volume",0) or 0), now)
        if venue == "gemini":
            return VenuePrice(venue, float(data.get("close",0) or data.get("last",0) or 0),
                              float(data.get("bid",0) or 0), float(data.get("ask",0) or 0),
                              float(data.get("volume",{}).get("BASE",0) or 0), now)
        if venue == "crypto_com":
            d = data.get("result", {}).get("data", [])
            if isinstance(d, list) and d:
                d = d[0]
            if isinstance(d, dict):
                return VenuePrice(venue, float(d.get("a",0) or 0),
                                  float(d.get("b",0) or 0), float(d.get("a",0) or 0),
                                  float(d.get("v",0) or 0), now)
        if venue == "gate":
            d = data[0] if isinstance(data, list) and data else {}
            return VenuePrice(venue, float(d.get("last",0) or 0), float(d.get("highest_bid",0) or 0),
                              float(d.get("lowest_ask",0) or 0), float(d.get("base_volume",0) or 0), now)
    except Exception as exc:
        return VenuePrice(venue, 0.0, timestamp=now, error=str(exc)[:80])
    return VenuePrice(venue, 0.0, timestamp=now, error="unparsed")


class MultiVenueAggregator:
    """Aggregates public tickers from all exchanges in parallel."""

    def __init__(
        self,
        venues: Optional[List[str]] = None,
        poll_interval_sec: float = 10.0,
    ) -> None:
        # Default: all exchanges in the registry
        self.venues = venues or list(EXCHANGES.keys())
        self.poll_interval_sec = poll_interval_sec
        # symbol -> {venue: VenuePrice}
        self._prices: Dict[str, Dict[str, VenuePrice]] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._poll_loop())
            logger.info(
                f"MultiVenueAggregator started: {len(self.venues)} venues",
                extra={"component": "data_nexus.multi_venue"},
            )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    # Poll all venues for all tracked symbols concurrently
                    symbols = list(self._prices.keys()) or ["BTCUSDT"]
                    tasks = []
                    for sym in symbols:
                        for venue in self.venues:
                            tasks.append(self._poll_one(session, venue, sym))
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"Multi-venue poll error: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_one(
        self, session: aiohttp.ClientSession, venue: str, symbol: str
    ) -> None:
        url, _ = _ticker_url(venue, symbol)
        if not url:
            return
        try:
            async with session.get(url, timeout=8) as resp:
                if resp.status != 200:
                    self._prices.setdefault(symbol, {})[venue] = VenuePrice(
                        venue, 0.0, error=f"HTTP {resp.status}")
                    return
                data = await resp.json()
            vp = _parse_ticker(venue, data)
            self._prices.setdefault(symbol, {})[venue] = vp
        except Exception as exc:
            self._prices.setdefault(symbol, {})[venue] = VenuePrice(
                venue, 0.0, error=str(exc)[:60])

    def track(self, symbol: str) -> None:
        """Add a symbol to track."""
        self._prices.setdefault(symbol, {})

    def price_summary(self, symbol: str) -> Dict:
        """Get the current cross-venue price summary for a symbol."""
        venues = self._prices.get(symbol, {})
        valid = {v: vp for v, vp in venues.items() if vp.price > 0 and not vp.error}
        if not valid:
            return {"symbol": symbol, "venues": {}, "best_buy": None, "best_sell": None}
        prices = {v: vp.price for v, vp in valid.items()}
        # Best venue to BUY = lowest ask, best to SELL = highest bid
        best_buy = min(valid.items(), key=lambda x: x[1].ask or x[1].price)
        best_sell = max(valid.items(), key=lambda x: x[1].bid or x[1].price)
        median = sorted(prices.values())[len(prices) // 2]
        return {
            "symbol": symbol,
            "venues": {v: {"price": vp.price, "bid": vp.bid, "ask": vp.ask,
                           "vol": vp.volume_24h, "error": vp.error}
                       for v, vp in venues.items()},
            "median": median,
            "best_buy_venue": best_buy[0],
            "best_buy_price": best_buy[1].ask or best_buy[1].price,
            "best_sell_venue": best_sell[0],
            "best_sell_price": best_sell[1].bid or best_sell[1].price,
            "spread_bps": abs(best_buy[1].price - best_sell[1].price) / median * 10000
                          if median > 0 else 0,
            "venue_count": len(valid),
        }

    def detect_divergence(self, symbol: str, threshold_bps: float = 20.0) -> List[Dict]:
        """Find venues whose price diverges from the median by > threshold."""
        summary = self.price_summary(symbol)
        if not summary.get("median"):
            return []
        median = summary["median"]
        out = []
        for venue, info in summary["venues"].items():
            if info["price"] <= 0:
                continue
            bps = (info["price"] - median) / median * 10000
            if abs(bps) > threshold_bps:
                out.append({"venue": venue, "price": info["price"], "divergence_bps": round(bps, 1)})
        return out

    def stats(self) -> dict:
        return {
            "venues": self.venues,
            "symbols_tracked": list(self._prices.keys()),
            "total_readings": sum(len(v) for v in self._prices.values()),
        }
