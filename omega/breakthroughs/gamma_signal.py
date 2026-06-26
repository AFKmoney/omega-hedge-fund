"""B4 — GammaExposureSignal: estimates dealer hedging pressure from options.

When options market makers are short gamma, they must hedge by buying into
rallies and selling into dips — amplifying moves. Long gamma = dampening.

We estimate this from the BTC dominance of OTM call/put volume (Deribit public).
A skew toward OTM calls = dealers short calls = negative gamma above current
price = forced buying on a breakout (gamma squeeze fuel).

This is a proxy — true gamma requires full options chain analysis. But the
call/put skew is a strong directional hint.
"""
from __future__ import annotations
import asyncio
import json
from typing import Optional
import aiohttp
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.gamma")

_DERIBIT_URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"

class GammaExposureSignal:
    """Estimates dealer gamma pressure from Deribit options skew."""
    def __init__(self, poll_interval_sec: int = 300) -> None:
        self.poll_interval_sec = poll_interval_sec
        self._call_vol: float = 0.0
        self._put_vol: float = 0.0
        self._gamma_tilt: float = 0.0
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
                    await self._poll_once(session)
                except asyncio.CancelledError: raise
                except Exception as exc:
                    logger.debug(f"Gamma poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_once(self, session: aiohttp.ClientSession) -> None:
        try:
            async with session.get(_DERIBIT_URL, timeout=15) as resp:
                payload = await resp.json()
        except Exception:
            return
        calls = puts = 0.0
        for opt in payload.get("result", []):
            instrument = opt.get("instrument_name", "")
            # Format: BTC-DDMMMYY-STRIKE-C/P
            if instrument.endswith("-C"):
                calls += float(opt.get("volume", 0) or 0)
            elif instrument.endswith("-P"):
                puts += float(opt.get("volume", 0) or 0)
        self._call_vol = calls
        self._put_vol = puts
        total = calls + puts
        if total > 0:
            self._gamma_tilt = (calls - puts) / total  # + = call heavy

    @property
    def tilt(self) -> float:
        return self._gamma_tilt

    def stats(self) -> dict:
        return {"name": "gamma_signal", "tilt": round(self._gamma_tilt, 3),
                "call_vol": self._call_vol, "put_vol": self._put_vol}
