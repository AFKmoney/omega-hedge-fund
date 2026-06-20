"""
EtherscanOnChainFeed — REAL on-chain whale movements + gas spikes from Etherscan.

Requires ETHERSCAN_API_KEY env var (free tier at etherscan.io).
Polls:
    - Latest block (gas price proxy)
    - Top ETH holders' latest transactions (whale watching)
    - Exchange inflow detection (transfers to known exchange deposit addresses)

If no API key is set, the feed yields nothing (not mocked data) — the rest of
OMEGA continues to run on market + news + macro data alone.
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator, Optional, Tuple

import aiohttp

from omega.data_nexus.base import DataSource
from omega.utils.events import OnChainEvent
from omega.utils.logger import get_logger

logger = get_logger("omega.data_nexus.etherscan")

# Known Binance hot wallets (subset). Extend for production.
BINANCE_DEPOSIT_WALLETS: Tuple[str, ...] = (
    "0x28C6c06298d514Db089934071355E5743bf21d60",  # Binance 14
    "0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549",  # Binance 15
    "0xDFd5293D8e347dFe59E90eFd55b2956a1343963d",  # Binance 16
)


class EtherscanOnChainFeed(DataSource):
    """Real on-chain whale / exchange-flow feed from Etherscan REST API."""

    name = "etherscan"

    def __init__(
        self,
        api_key: str = "",
        poll_interval_sec: int = 120,
        whale_addresses: Tuple[str, ...] = BINANCE_DEPOSIT_WALLETS,
        gas_spike_gwei: float = 100.0,
    ) -> None:
        self.api_key = api_key or os.getenv("ETHERSCAN_API_KEY", "")
        self.poll_interval_sec = poll_interval_sec
        self.whale_addresses = whale_addresses
        self.gas_spike_gwei = gas_spike_gwei
        self._base_url = "https://api.etherscan.io/api"
        self._last_block_seen: Optional[int] = None

    async def stream(self) -> AsyncIterator[OnChainEvent]:
        if not self.api_key:
            logger.warning(
                "ETHERSCAN_API_KEY not set — on-chain feed disabled (NOT mocked, just skipped)",
                extra={"component": "data_nexus.etherscan"},
            )
            return
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async for event in self._poll_once(session):
                        yield event
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        f"Etherscan poll failed: {exc}",
                        extra={"component": "data_nexus.etherscan"},
                    )
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_once(
        self, session: aiohttp.ClientSession
    ) -> AsyncIterator[OnChainEvent]:
        # 1. Gas price check
        gas_event = await self._fetch_gas(session)
        if gas_event is not None:
            yield gas_event

        # 2. Whale transactions
        async for tx_event in self._fetch_whale_txns(session):
            yield tx_event

    async def _fetch_gas(
        self, session: aiohttp.ClientSession
    ) -> Optional[OnChainEvent]:
        params = {"module": "gastracker", "action": "gasoracle", "apikey": self.api_key}
        async with session.get(self._base_url, params=params, timeout=10) as resp:
            payload = await resp.json()
        result = payload.get("result", {})
        fast_gwei = float(result.get("FastGasPrice", 0))
        if fast_gwei >= self.gas_spike_gwei:
            return OnChainEvent(
                chain="ethereum",
                event_type="gas_spike",
                timestamp=_now_iso(),
                value_usd=fast_gwei,
                details={
                    "fast_gwei": fast_gwei,
                    "standard_gwei": float(result.get("ProposeGasPrice", 0)),
                    "slow_gwei": float(result.get("SafeGasPrice", 0)),
                },
            )
        return None

    async def _fetch_whale_txns(
        self, session: aiohttp.ClientSession
    ) -> AsyncIterator[OnChainEvent]:
        for addr in self.whale_addresses:
            params = {
                "module": "account",
                "action": "txlist",
                "address": addr,
                "startblock": 0,
                "endblock": 99999999,
                "page": 1,
                "offset": 5,
                "sort": "desc",
                "apikey": self.api_key,
            }
            try:
                async with session.get(self._base_url, params=params, timeout=10) as resp:
                    payload = await resp.json()
            except Exception as exc:
                logger.warning(f"Whale fetch failed for {addr[:10]}: {exc}")
                continue
            for tx in payload.get("result", [])[:3]:
                # Only emit if this is a new tx
                block = int(tx.get("blockNumber", 0))
                if self._last_block_seen and block <= self._last_block_seen:
                    continue
                value_eth = int(tx.get("value", 0)) / 1e18
                if value_eth < 10:  # ignore dust
                    continue
                eth_usd = await self._fetch_eth_usd(session)
                yield OnChainEvent(
                    chain="ethereum",
                    event_type="whale_move",
                    timestamp=self._ts_to_iso(int(tx.get("timeStamp", 0))),
                    value_usd=value_eth * eth_usd,
                    from_addr=tx.get("from", ""),
                    to_addr=tx.get("to", ""),
                    tx_hash=tx.get("hash", ""),
                    details={"value_eth": value_eth, "eth_usd": eth_usd},
                )
            self._last_block_seen = max(
                self._last_block_seen or 0,
                int(payload.get("result", [{}])[0].get("blockNumber", 0)) if payload.get("result") else 0,
            )

    async def _fetch_eth_usd(self, session: aiohttp.ClientSession) -> float:
        params = {
            "module": "stats",
            "action": "ethprice",
            "apikey": self.api_key,
        }
        try:
            async with session.get(self._base_url, params=params, timeout=10) as resp:
                payload = await resp.json()
            return float(payload.get("result", {}).get("ethusd", 3000.0))
        except Exception:
            return 3000.0  # conservative fallback

    @staticmethod
    def _ts_to_iso(ts: int) -> str:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        )


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
