"""
OpenInterestSignal — open-interest rate-of-change as a leverage crowding proxy.

Open interest (OI) is the total number of outstanding derivative contracts.
Its *rate of change* is the signal, not its level: when OI spikes while price
is flat/down, leverage is piling in on one side → the crowd is overcrowded and
fuel for a cascade. When OI is falling, positions are being flushed (post-cascade).

Binance Futures publishes OI history publicly (no key):
    GET /futures/data/openInterestHist?symbol=BTCUSDT&period=5m&limit=N

Normalization:
    score = clamp(rocs_sum * 0.1, -1, 1)
    where roc over the last N periods (default 12 × 5m = 1h).
    Rising OI → positive score (crowd piling in long on momentum, typically),
    falling OI → negative (deleveraging). The engine combines this with funding
    to disambiguate which side is crowded.

We poll on a 5-minute cadence (finest free granularity).
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

import aiohttp

from omega.crowd_engine.signals.base import PositioningSignal, SignalReading
from omega.utils.logger import get_logger

logger = get_logger("omega.crowd_engine.open_interest")

_OI_HIST_URL = "https://fapi.binance.com/futures/data/openInterestHist"


class OpenInterestSignal(PositioningSignal):
    """Crowd positioning from open-interest rate-of-change."""

    name = "open_interest"

    def __init__(
        self,
        symbols: tuple = ("BTCUSDT",),
        period: str = "5m",
        lookback_periods: int = 12,   # 1h of 5m candles
        poll_interval_sec: int = 300,
        weight: float = 0.30,
        horizon: str = "hours",
        # Multiplier applied to the summed rate-of-change (tuning knob)
        roc_gain: float = 10.0,
    ) -> None:
        self.symbols = tuple(s.upper() for s in symbols)
        self.period = period
        self.lookback_periods = lookback_periods
        self.poll_interval_sec = poll_interval_sec
        self.weight = weight
        self.horizon = horizon
        self.roc_gain = roc_gain
        # symbol -> latest summed ROC (sum of period-to-period ROCs)
        self._roc: Dict[str, float] = {}
        self._oi_series: Dict[str, List[float]] = {}  # raw OI series for audit
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
                    logger.warning(f"OI poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_one(self, session: aiohttp.ClientSession, symbol: str) -> None:
        params = {
            "symbol": symbol, "period": self.period,
            "limit": self.lookback_periods + 2,
        }
        try:
            async with session.get(_OI_HIST_URL, params=params, timeout=10) as resp:
                if resp.status != 200:
                    return
                payload = await resp.json()
        except Exception as exc:
            logger.debug(f"OI fetch failed [{symbol}]: {exc}")
            return
        if not payload or len(payload) < 2:
            return
        try:
            oi = [float(r["sumOpenInterest"]) for r in payload]
        except (KeyError, ValueError, TypeError):
            return
        # Period-to-period rate of change, summed (captures acceleration not level)
        rocs = [(oi[i] - oi[i - 1]) / oi[i - 1] for i in range(1, len(oi)) if oi[i - 1] > 0]
        self._roc[symbol] = sum(rocs)
        self._oi_series[symbol] = oi

    def reading_for(self, symbol: str) -> Optional[SignalReading]:
        roc = self._roc.get(symbol)
        if roc is None:
            return None
        score = max(-1.0, min(1.0, roc * self.roc_gain))
        return SignalReading(
            score=score,
            horizon=self.horizon,
            weight=self.weight,
            raw={"oi_roc": round(roc, 5), "oi_last": self._oi_series.get(symbol, [None])[-1]},
        )

    def reading(self) -> Optional[SignalReading]:
        if not self._roc:
            return None
        import statistics
        mean = statistics.fmean(self._roc.values())
        score = max(-1.0, min(1.0, mean * self.roc_gain))
        return SignalReading(score=score, horizon=self.horizon, weight=self.weight,
                             raw={"mean_oi_roc": mean})

    def stats(self) -> dict:
        return {"name": self.name, "symbols": len(self._roc),
                "oi_roc": {k: round(v, 5) for k, v in self._roc.items()}}
