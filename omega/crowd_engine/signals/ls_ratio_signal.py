"""
LSRatioSignal — Binance Futures long/short account ratio.

Binance publishes the global long/short account ratio for each symbol on the
futures data REST endpoint:
    GET /futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=5m

It returns longAccount / shortAccount (and longPosition / shortPosition). When
a large majority of accounts are long, the crowd is overcrowded long — a
cascade on the long side would hurt the most people.

Normalization:
    long_pct = longAccount / (longAccount + shortAccount) * 100
    score = (long_pct - 50) / 50   # +1 if 100% long, -1 if 100% short, 0 if 50/50

This signal polls the REST endpoint on a fixed interval (default 5 min, the
finest free granularity). No API key required for public futures data.
"""

from __future__ import annotations

import asyncio
import json
from typing import Dict, Optional

import aiohttp

from omega.crowd_engine.signals.base import PositioningSignal, SignalReading
from omega.utils.logger import get_logger

logger = get_logger("omega.crowd_engine.ls_ratio")

# Binance Futures public data endpoints (no key required)
_LS_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"


class LSRatioSignal(PositioningSignal):
    """Crowd positioning from Binance Futures long/short account ratio."""

    name = "ls_ratio"

    def __init__(
        self,
        symbols: tuple = ("BTCUSDT",),
        period: str = "5m",
        poll_interval_sec: int = 300,
        weight: float = 0.35,
        horizon: str = "hours",
    ) -> None:
        self.symbols = tuple(s.upper() for s in symbols)
        self.period = period
        self.poll_interval_sec = poll_interval_sec
        self.weight = weight
        self.horizon = horizon
        # symbol -> latest long_pct (0..100)
        self._long_pct: Dict[str, float] = {}
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
                    for sym in self.symbols:
                        await self._poll_one(session, sym)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"L/S ratio poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_one(self, session: aiohttp.ClientSession, symbol: str) -> None:
        params = {"symbol": symbol, "period": self.period, "limit": 1}
        try:
            async with session.get(_LS_URL, params=params, timeout=10) as resp:
                if resp.status != 200:
                    return
                payload = await resp.json()
        except Exception as exc:
            logger.debug(f"L/S fetch failed [{symbol}]: {exc}")
            return
        if not payload:
            return
        row = payload[0]
        longacct = float(row.get("longAccount", 0.5))
        shortacct = float(row.get("shortAccount", 0.5))
        total = longacct + shortacct
        if total <= 0:
            return
        self._long_pct[symbol] = longacct / total * 100.0

    def reading_for(self, symbol: str) -> Optional[SignalReading]:
        pct = self._long_pct.get(symbol)
        if pct is None:
            return None
        score = (pct - 50.0) / 50.0  # +1 all-long, -1 all-short
        score = max(-1.0, min(1.0, score))
        return SignalReading(
            score=score,
            horizon=self.horizon,
            weight=self.weight,
            raw={"long_pct": pct},
        )

    def reading(self) -> Optional[SignalReading]:
        if not self._long_pct:
            return None
        vals = list(self._long_pct.values())
        mean = sum(vals) / len(vals)
        score = max(-1.0, min(1.0, (mean - 50.0) / 50.0))
        return SignalReading(score=score, horizon=self.horizon, weight=self.weight,
                             raw={"mean_long_pct": mean})

    def stats(self) -> dict:
        return {"name": self.name, "symbols": len(self._long_pct),
                "long_pct": dict(self._long_pct)}
