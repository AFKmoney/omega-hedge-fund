"""
SentimentSignal — Fear & Greed index as a crowd-sentiment extreme proxy.

The CNN-style crypto Fear & Greed index (api.alternative.me, free, no key)
aggregates volatility, momentum, social media, dominance, and trends into a
0..100 score:
    0-24   Extreme Fear  → crowd is capitulating → overcrowded SHORT → we LONG
    75-100 Extreme Greed → crowd is euphoric    → overcrowded LONG  → we SHORT

This is the slowest, most narrative signal — it captures multi-day extremes
that precede major reversals. Horizon is "days".

Normalization:
    F&G > 80 → greed extreme → score = +(fg-80)/20   (caps at +1 at F&G=100)
    F&G < 20 → fear extreme → score = -(20-fg)/20    (caps at -1 at F&G=0)
    20..80   → neutral      → score ≈ 0
"""

from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp

from omega.crowd_engine.signals.base import PositioningSignal, SignalReading
from omega.utils.logger import get_logger

logger = get_logger("omega.crowd_engine.sentiment")

_FG_URL = "https://api.alternative.me/fng/?limit=1"


class SentimentSignal(PositioningSignal):
    """Crowd positioning from the Fear & Greed index."""

    name = "sentiment"

    def __init__(
        self,
        poll_interval_sec: int = 1800,  # 30 min — F&G updates ~hourly
        greed_threshold: int = 80,
        fear_threshold: int = 20,
        weight: float = 0.25,
        horizon: str = "days",
    ) -> None:
        self.poll_interval_sec = poll_interval_sec
        self.greed_threshold = greed_threshold
        self.fear_threshold = fear_threshold
        self.weight = weight
        self.horizon = horizon
        # F&G is market-wide, so it applies to every symbol uniformly
        self._fg_value: Optional[int] = None
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
                    logger.warning(f"F&G poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_once(self, session: aiohttp.ClientSession) -> None:
        try:
            async with session.get(_FG_URL, timeout=10) as resp:
                if resp.status != 200:
                    return
                payload = await resp.json()
        except Exception as exc:
            logger.debug(f"F&G fetch failed: {exc}")
            return
        data = payload.get("data", [])
        if not data:
            return
        try:
            self._fg_value = int(data[0]["value"])
        except (KeyError, ValueError):
            return

    def _score_from_fg(self, fg: int) -> float:
        if fg >= self.greed_threshold:
            return (fg - self.greed_threshold) / (100 - self.greed_threshold + 1e-9)
        if fg <= self.fear_threshold:
            return -(self.fear_threshold - fg) / (self.fear_threshold + 1e-9)
        return 0.0

    def reading_for(self, symbol: str) -> Optional[SignalReading]:
        if self._fg_value is None:
            return None
        score = max(-1.0, min(1.0, self._score_from_fg(self._fg_value)))
        return SignalReading(
            score=score,
            horizon=self.horizon,
            weight=self.weight,
            raw={"fear_greed": self._fg_value},
        )

    def reading(self) -> Optional[SignalReading]:
        if self._fg_value is None:
            return None
        score = max(-1.0, min(1.0, self._score_from_fg(self._fg_value)))
        return SignalReading(score=score, horizon=self.horizon, weight=self.weight,
                             raw={"fear_greed": self._fg_value})

    def stats(self) -> dict:
        return {"name": self.name, "fear_greed": self._fg_value}
