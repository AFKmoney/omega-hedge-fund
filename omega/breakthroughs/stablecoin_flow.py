"""B16 — StablecoinFlow: tracks stablecoin mint/burn as a liquidity proxy.

When Tether (USDT) mints new tokens, it's new capital entering crypto = bullish.
When USDT is burned, capital is leaving = bearish. We track stablecoin market
cap changes from CoinGecko as a proxy for net liquidity flow.
"""
from __future__ import annotations
import asyncio
from collections import deque
from typing import Deque
import aiohttp
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.stablecoin_flow")

_URL = "https://api.coingecko.com/api/v3/coins/tether"

class StablecoinFlow:
    """Tracks USDT market cap changes as a crypto liquidity proxy."""
    def __init__(self, poll_interval_sec: int = 600, window: int = 12) -> None:
        self.poll_interval_sec = poll_interval_sec
        self._history: Deque[float] = deque(maxlen=window)
        self._flow_bps: float = 0.0
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
                    async with session.get(_URL, timeout=10) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            mcap = float(data.get("market_data", {}).get("market_cap", {}).get("usd", 0) or 0)
                            if mcap > 0:
                                self._history.append(mcap)
                                if len(self._history) >= 2:
                                    prev = self._history[0]
                                    self._flow_bps = (mcap - prev) / prev * 10000
                except asyncio.CancelledError: raise
                except Exception as exc:
                    logger.debug(f"Stablecoin flow poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    @property
    def flow_bps(self) -> float:
        """Positive = capital inflow (bullish), negative = outflow (bearish)."""
        return self._flow_bps

    def stats(self) -> dict:
        return {"name": "stablecoin_flow", "flow_bps": round(self._flow_bps, 2),
                "direction": "inflow" if self._flow_bps > 0 else "outflow",
                "samples": len(self._history)}
