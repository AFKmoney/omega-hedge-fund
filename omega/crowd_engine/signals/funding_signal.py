"""
FundingRateSignal — perpetual futures funding rate as a crowd-positioning proxy.

The funding rate is the periodic payment longs pay to shorts (or vice versa)
to keep the perp price tethered to spot. It is the most direct measure of
crowd leverage on the long or short side:

    funding very positive → longs are overcrowded, paying a premium to hold →
                            cascade risk on the long side → we fade by SHORTING
    funding very negative → shorts overcrowded → we fade by going LONG

We ingest the @markPrice stream (already wired in BinanceWebSocketFeed) and
normalize via tanh so the score saturates at extreme funding.

Normalization:
    score = tanh(funding_rate / threshold)
    threshold = 0.0005 (0.05% per 8h funding = ~55% APR — a historically
    extreme level). tanh gives a smooth curve that reaches ~0.76 at threshold
    and ~0.99 at 2×threshold.
"""

from __future__ import annotations

import asyncio
import math
from typing import Dict, Optional

import aiohttp

from omega.crowd_engine.signals.base import PositioningSignal, SignalReading
from omega.utils.logger import get_logger

logger = get_logger("omega.crowd_engine.funding")


class FundingRateSignal(PositioningSignal):
    """Crowd positioning from perpetual funding rate.

    Fetches funding from public REST (more reliable than fstream WS which is
    geo-blocked in some regions). Polls every 60s — funding changes slowly."""

    name = "funding"

    def __init__(
        self,
        threshold: float = 0.0005,
        weight: float = 0.40,
        horizon: str = "hours",
        poll_interval_sec: int = 60,
    ) -> None:
        self.threshold = threshold
        self.weight = weight
        self.horizon = horizon
        self.poll_interval_sec = poll_interval_sec
        self._latest: Dict[str, float] = {}
        self._task = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
            self._task = None

    async def _poll_loop(self) -> None:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await self._poll_once(session)
                except asyncio.CancelledError: raise
                except Exception as exc:
                    logger.debug(f"Funding REST poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_once(self, session) -> None:
        import aiohttp
        # Binance futures public funding rate (no key, works globally)
        for sym in self._symbols_tracked:
            bin_sym = sym.upper()
            url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={bin_sym}"
            try:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        rate = float(data.get("lastFundingRate", 0) or 0)
                        self._latest[bin_sym] = rate
                        if rate != 0:
                            logger.debug(f"Funding {bin_sym}: {rate:.6f}")
            except Exception:
                pass

    _symbols_tracked: list = []

    def set_symbols(self, symbols: list) -> None:
        self._symbols_tracked = symbols

    def reading_for(self, symbol: str) -> Optional[SignalReading]:
        rate = self._latest.get(symbol)
        if rate is None:
            return None
        # tanh normalization: positive funding -> crowd long overcrowded -> +score
        import math
        score = math.tanh(rate / self.threshold) if self.threshold > 0 else 0.0
        # Clamp to [-1, 1] defensively
        score = max(-1.0, min(1.0, score))
        return SignalReading(
            score=score,
            horizon=self.horizon,
            weight=self.weight,
            raw={"funding_rate": rate, "threshold": self.threshold},
        )

    # Default no-arg reading() returns the aggregate (mean across symbols);
    # the engine uses reading_for(symbol) per-symbol.
    def reading(self) -> Optional[SignalReading]:
        if not self._latest:
            return None
        import statistics
        vals = list(self._latest.values())
        mean = statistics.fmean(vals)
        import math
        score = max(-1.0, min(1.0, math.tanh(mean / self.threshold)))
        return SignalReading(score=score, horizon=self.horizon, weight=self.weight,
                             raw={"mean_funding": mean, "symbols": len(vals)})

    def stats(self) -> dict:
        return {"name": self.name, "symbols": len(self._latest),
                "latest": dict(self._latest)}
