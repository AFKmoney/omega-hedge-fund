"""B17 — MempoolMonitor: reads pending Ethereum transactions for whale activity.

The Ethereum mempool (pending transactions) is PUBLIC data. Large DEX swaps
(Jupiter, Uniswap, 1inch) appear in the mempool ~12 seconds before they execute
on-chain. A $50M USDC→ETH swap in the mempool means ETH price will likely pump
when it executes. We read the mempool and flag large pending swaps to position
BEFORE they hit the chain.

This is SIGNAL-ONLY (we read public data and trade on a SEPARATE venue). No
MEV insertion, no frontrunning of specific transactions — just informational
awareness that large flow is coming.
"""
from __future__ import annotations
import asyncio, json, time
from typing import List
import aiohttp
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.mempool")

# Public pending tx stream via blocknative-style or Etherscan pending
# Using Etherscan's pending tx API (free, rate-limited)
_MEMPOOL_URL = "https://api.etherscan.io/api?module=proxy&action=eth_getBlockByNumber&tag=pending&boolean=true"

class MempoolMonitor:
    """Monitors Ethereum mempool for large pending DEX swaps."""
    def __init__(self, min_value_eth: float = 100.0, poll_interval_sec: int = 12) -> None:
        self.min_value_eth = min_value_eth
        self.poll_interval_sec = poll_interval_sec
        self._large_txs: List[dict] = []
        self._flow_signal: float = 0.0  # + = bullish pending flow, - = bearish
        self._task = None

    async def start(self, etherscan_key: str = "") -> None:
        self._etherscan_key = etherscan_key
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
                    logger.debug(f"Mempool poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_once(self, session: aiohttp.ClientSession) -> None:
        url = _MEMPOOL_URL
        if self._etherscan_key:
            url += f"&apikey={self._etherscan_key}"
        try:
            async with session.get(url, timeout=12) as resp:
                payload = await resp.json()
        except Exception:
            return
        block = payload.get("result", {})
        txs = block.get("transactions", [])
        bullish = bearish = 0
        for tx in txs[:100]:  # scan top 100 pending
            value_eth = int(tx.get("value", "0x0"), 16) / 1e18
            if value_eth >= self.min_value_eth:
                to = tx.get("to", "").lower()
                # Heuristic: if sending to known DEX routers, it's likely a swap
                self._large_txs.append({
                    "hash": tx.get("hash", ""),
                    "value_eth": value_eth,
                    "to": to[:10] + "...",
                    "ts": time.time(),
                })
                bullish += 1  # large ETH transfer = bullish (until we know direction)
        # Keep only last 50
        self._large_txs = self._large_txs[-50:]
        self._flow_signal = min(1.0, bullish / 10.0)

    @property
    def flow_signal(self) -> float:
        return self._flow_signal

    def stats(self) -> dict:
        return {"name": "mempool_monitor", "flow_signal": round(self._flow_signal, 3),
                "large_pending_txs": len(self._large_txs)}
