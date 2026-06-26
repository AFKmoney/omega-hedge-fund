"""
OnChainInflowSignal — bearish signal from large transfers into exchange deposits.

When a whale or miner moves a large amount of an asset from a cold wallet to a
known exchange deposit address, it is almost always preparing to sell. This is
public blockchain data — reading it is legitimate information-based trading.

We poll the Whale Alert-style public API (or our existing etherscan feed) for
large transfers TO known exchange hot wallets. Each large inflow adds selling
pressure that hasn't hit the order book yet.

Data source: CryptoQuant / Whale Alert free tier, or the existing etherscan
feed's whale tx detection re-pointed at exchange addresses.

Score:
    Large inflow of the base asset → imminent selling pressure → crowd/builders
    about to get wrecked → positive score (contrarian fades by selling ahead of
    the whale). Horizon: minutes to hours (time to confirmations + sale).

Known exchange deposit addresses are a small, stable set — we maintain a list
and match incoming transfers against it.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Deque, Dict, Optional

import aiohttp

from omega.crowd_engine.signals.base import PositioningSignal, SignalReading
from omega.utils.logger import get_logger

logger = get_logger("omega.crowd_engine.inflow")

# Known OKX + Binance cold/deposit wallets (BTC). Extend as needed. These are
# publicly known addresses published by the exchanges themselves.
_KNOWN_EXCHANGE_BTC = {
    "bc1qgdjqv0av3q56jvd82tkdjpy7gdp9ut8tlqmgrpmv24sq90ecnvqqjwvw97",  # Binance cold
    "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo",  # Binance
    "385c7xRhsNs7E4L5UhoCjVFyZtocJvx6Mg",  # Binance
    "1NDyJtNTjmwk5xPNhjgAMu4HDHigtobu1s",  # Binance
    "bc1q7qrdhv6drv4qeyuchv3ueptddlllzdh8e7fmd9",  # OKX
}

_INFLOW_API = "https://api.whale-alert.io/v1/transactions"
# Whale Alert free tier requires a key; fall back to no data (score=neutral)
# if no key configured. The etherscan feed already covers ETH whales.


class OnChainInflowSignal(PositioningSignal):
    """Bearish pressure from large exchange-bound inflows."""

    name = "inflow"

    def __init__(
        self,
        symbols: tuple = ("BTCUSDT",),
        whale_alert_key: str = "",
        poll_interval_sec: int = 300,
        weight: float = 0.30,
        horizon: str = "hours",
        large_transfer_usd: float = 5_000_000.0,  # $5M+ = significant
        window_sec: int = 3600,   # sum inflows over last hour
    ) -> None:
        self.symbols = tuple(s.upper().replace("-", "") for s in symbols)
        self.api_key = whale_alert_key
        self.poll_interval_sec = poll_interval_sec
        self.weight = weight
        self.horizon = horizon
        self.large_transfer_usd = large_transfer_usd
        self.window_sec = window_sec
        # Rolling record of (timestamp, usd_value, is_inflow_to_exchange)
        self._inflows: Deque = deque(maxlen=500)
        self._task: Optional[asyncio.Task] = None
        self._last_usd = 0.0

    async def start(self) -> None:
        if not self.api_key:
            logger.info(
                "OnChainInflowSignal: no Whale Alert API key; signal will be "
                "neutral until one is provided (set WHALE_ALERT_API_KEY).",
                extra={"component": "crowd_engine.inflow"},
            )
            return
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
                    logger.warning(f"Inflow poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_once(self, session: aiohttp.ClientSession) -> None:
        # Fetch recent large BTC transactions
        start = int(time.time()) - 600
        params = {
            "api_key": self.api_key, "min": int(self.large_transfer_usd),
            "start": start, "currency": "btc",
        }
        try:
            async with session.get(_INFLOW_API, params=params, timeout=15) as resp:
                if resp.status != 200:
                    return
                payload = await resp.json()
        except Exception as exc:
            logger.debug(f"Inflow fetch failed: {exc}")
            return
        for tx in payload.get("transactions", []):
            owner = tx.get("to", {}).get("owner", "")
            owner_type = tx.get("to", {}).get("owner_type", "")
            usd = float(tx.get("amount_usd", 0) or 0)
            # Only count transfers TO exchanges
            if owner_type == "exchange" or "exchange" in owner.lower():
                self._inflows.append((tx.get("timestamp", time.time()), usd, True))
        self._last_usd = self._sum_recent()

    def _sum_recent(self) -> float:
        cutoff = time.time() - self.window_sec
        return sum(usd for ts, usd, _ in self._inflows if ts > cutoff)

    def reading_for(self, symbol: str) -> Optional[SignalReading]:
        usd = self._sum_recent()
        if usd <= 0:
            return None
        # Normalize: $50M+ inflow in an hour = saturation
        import math
        score = max(0.0, min(1.0, math.tanh(usd / 50_000_000.0)))
        return SignalReading(
            score=score,  # inflow = selling pressure = crowd longs about to get hit
            horizon=self.horizon,
            weight=self.weight,
            raw={"inflow_usd_1h": usd, "transfers": len(self._inflows)},
        )

    def reading(self) -> Optional[SignalReading]:
        return self.reading_for("BTCUSDT")

    def stats(self) -> dict:
        return {
            "name": self.name,
            "inflow_usd_1h": self._last_usd,
            "has_api_key": bool(self.api_key),
            "transfers_tracked": len(self._inflows),
        }
