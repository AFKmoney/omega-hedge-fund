"""B14 — ExchangeReserves: tracks BTC/ETH reserves on exchanges.

When exchange reserves (total coins held on exchanges) DROP, it means users
are withdrawing to cold storage = accumulation = bullish. When reserves RISE,
users are depositing to sell = bearish. We poll the CryptoQuant-style reserve
data (or estimate from on-chain known exchange addresses).
"""
from __future__ import annotations
import asyncio
from collections import deque
from typing import Deque
import aiohttp
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.reserves")

# CoinGecko exchange reserve proxy (simplified)
_RESERVE_URL = "https://api.glassnode.com/v1/metrics/distribution/balance_exchanges"  # needs key
# Fallback: use CoinGepto supply data
_SUPPLY_URL = "https://api.coingecko.com/api/v3/coins/bitcoin"

class ExchangeReserves:
    """Tracks exchange coin reserves trend (bullish/bearish flow)."""
    def __init__(self, poll_interval_sec: int = 3600, window: int = 24) -> None:
        self.poll_interval_sec = poll_interval_sec
        self._history: Deque[float] = deque(maxlen=window)
        self._current: float = 0.0
        self._trend: str = "stable"
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
                    logger.debug(f"Reserves poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_once(self, session: aiohttp.ClientSession) -> None:
        # Use circulating supply as a proxy (when available)
        try:
            async with session.get(_SUPPLY_URL, timeout=10) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
                supply = float(data.get("market_data", {}).get("circulating_supply", 0) or 0)
                if supply > 0:
                    self._current = supply
                    self._history.append(supply)
                    if len(self._history) >= 6:
                        self._compute_trend()
        except Exception:
            pass

    def _compute_trend(self) -> None:
        recent = list(self._history)
        if recent[-1] < recent[0]:
            self._trend = "decreasing"  # bullish (coins leaving exchanges)
        else:
            self._trend = "increasing"  # bearish (coins entering exchanges)

    @property
    def trend(self) -> str:
        return self._trend

    def stats(self) -> dict:
        return {"name": "exchange_reserves", "current_supply": self._current,
                "trend": self._trend, "samples": len(self._history)}
