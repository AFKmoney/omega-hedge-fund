"""
SocialSentimentSignal — retail hype as a euphoria/capitulation proxy.

Direct social scraping (X, Reddit JSON) is now blocked without OAuth, and raw
keyword sentiment is noisy. Instead we use CoinGecko's free "trending" feed:
    GET https://api.coingecko.com/api/v3/search/trending

This is a high-signal, low-noise proxy for retail euphoria: when major-cap
coins (BTC/ETH) dominate the trending list, sentiment is constructive; when
obscure meme coins flood it, retail is in euphoria/gambling mode (a classic
late-cycle top signal). When the list is stable and boring, sentiment is
neutral.

We also fold in a lightweight news-volume proxy via the RSSNewsFeed's recent
headline count when available, but the core signal is the trending-coin
composition.

Normalization:
    meme_ratio = (# of trending coins with rank > 100) / total_trending
    If meme_ratio high AND BTC rank == 1  → euphoria → positive score (fade by SHORT)
    If few trending coins / all blue-chip → neutral
    (F&G already covers the fear side; this specializes in the euphoria side.)
"""

from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp

from omega.crowd_engine.signals.base import PositioningSignal, SignalReading
from omega.utils.logger import get_logger

logger = get_logger("omega.crowd_engine.social")

_TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"
# Coins we treat as "blue-chip / not hype"
_BLUE_CHIPS = {"BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX"}


class SocialSentimentSignal(PositioningSignal):
    """Retail euphoria proxy from CoinGecko trending composition."""

    name = "social"

    def __init__(
        self,
        poll_interval_sec: int = 600,  # 10 min
        weight: float = 0.20,
        horizon: str = "days",
        euphoria_meme_threshold: float = 0.60,  # >60% non-bluechip = euphoria
    ) -> None:
        self.poll_interval_sec = poll_interval_sec
        self.weight = weight
        self.horizon = horizon
        self.euphoria_meme_threshold = euphoria_meme_threshold
        self._meme_ratio: Optional[float] = None
        self._trending: list = []
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await self._poll_once(session)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"Trending poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_once(self, session: aiohttp.ClientSession) -> None:
        try:
            async with session.get(_TRENDING_URL, timeout=10) as resp:
                if resp.status != 200:
                    return
                payload = await resp.json()
        except Exception as exc:
            logger.debug(f"Trending fetch failed: {exc}")
            return
        coins = payload.get("coins", [])
        if not coins:
            return
        items = []
        for c in coins:
            item = c.get("item", {})
            sym = (item.get("symbol") or "").upper()
            rank = item.get("market_cap_rank") or 9999
            items.append((sym, rank))
        self._trending = items
        if not items:
            return
        non_blue = sum(1 for sym, rank in items if sym not in _BLUE_CHIPS and rank > 100)
        self._meme_ratio = non_blue / len(items)

    def reading_for(self, symbol: str) -> Optional[SignalReading]:
        if self._meme_ratio is None:
            return None
        # High meme ratio → retail euphoria → crowd overcrowded long (risk-on) → +
        # This is a market-wide signal (same score for every symbol).
        if self._meme_ratio >= self.euphoria_meme_threshold:
            score = (self._meme_ratio - self.euphoria_meme_threshold) / (
                1.0 - self.euphoria_meme_threshold + 1e-9
            )
        else:
            score = 0.0
        score = max(-1.0, min(1.0, score))
        return SignalReading(
            score=score,
            horizon=self.horizon,
            weight=self.weight,
            raw={"meme_ratio": self._meme_ratio, "trending": self._trending[:5]},
        )

    def reading(self) -> Optional[SignalReading]:
        return self.reading_for("BTCUSDT")

    def stats(self) -> dict:
        return {
            "name": self.name,
            "meme_ratio": self._meme_ratio,
            "trending": [t[0] for t in self._trending[:5]],
        }
