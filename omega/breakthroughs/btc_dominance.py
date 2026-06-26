"""B13 — BTCDominanceSignal: BTC dominance as a risk-on/risk-off indicator.

When BTC dominance rises, capital is flowing FROM altcoins TO Bitcoin (flight
to quality = risk-off). When dominance falls, capital flows TO altcoins
(risk-on, altseason). We poll dominance from CoinGecko and detect trend
reversals — a falling dominance after a peak = altcoin rotation starting.
"""
from __future__ import annotations
import asyncio, json
from collections import deque
from typing import Deque
import aiohttp
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.btc_dom")

_DOM_URL = "https://api.coingecko.com/api/v3/global"

class BTCDominanceSignal:
    """Tracks BTC dominance trend for risk-on/off classification."""
    def __init__(self, poll_interval_sec: int = 600, window: int = 24) -> None:
        self.poll_interval_sec = poll_interval_sec
        self._history: Deque[float] = deque(maxlen=window)
        self._dominance: float = 0.0
        self._trend: str = "flat"
        self._task = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
            self._task = None

    async def _poll_loop(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.get(_DOM_URL, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            dom = data.get("data", {}).get("market_cap_percentage", {}).get("btc", 0)
                            if dom:
                                self._dominance = dom
                                self._history.append(dom)
                                self._compute_trend()
                except asyncio.CancelledError: raise
                except Exception as exc:
                    logger.debug(f"BTC dom poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    def _compute_trend(self) -> None:
        if len(self._history) < 6:
            return
        recent = list(self._history)
        half = len(recent) // 2
        first_avg = sum(recent[:half]) / half
        second_avg = sum(recent[half:]) / (len(recent) - half)
        delta = second_avg - first_avg
        if delta > 0.5:
            self._trend = "rising"  # risk-off (capital → BTC)
        elif delta < -0.5:
            self._trend = "falling"  # risk-on (capital → alts)
        else:
            self._trend = "flat"

    @property
    def dominance(self) -> float:
        return self._dominance

    @property
    def trend(self) -> str:
        return self._trend

    def stats(self) -> dict:
        return {"name": "btc_dominance", "dominance": round(self._dominance, 2),
                "trend": self._trend, "samples": len(self._history)}
