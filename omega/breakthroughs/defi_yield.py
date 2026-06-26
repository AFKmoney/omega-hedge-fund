"""B23 — DeFiYieldScanner: scans DeFi protocols for yield opportunities.

When the bot is in cash (no trading signal), idle capital should earn yield
rather than sit stagnant. We scan major DeFi protocols (Aave, Compound, Uniswap
v3 pools) for safe yield opportunities. This turns 'dead capital' into productive
capital between trades.

Returns the best risk-adjusted yield for each asset (USDT, USDC, ETH, WBTC).
"""
from __future__ import annotations
import asyncio
from typing import Dict, List
import aiohttp
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.defi_yield")

# DefiLlama public API — free, no key
_LLAMA_URL = "https://yields.llama.fi/pools"

class DeFiYieldScanner:
    """Scans DeFi protocols for the best safe yields."""
    def __init__(self, poll_interval_sec: int = 600, min_tvl: float = 10_000_000) -> None:
        self.poll_interval_sec = poll_interval_sec
        self.min_tvl = min_tvl
        self._pools: List[dict] = []
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
                    async with session.get(_LLAMA_URL, timeout=20) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            self._pools = data.get("data", [])[:500]  # top 500
                except asyncio.CancelledError: raise
                except Exception as exc:
                    logger.debug(f"DeFi yield poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    def best_yields(self, asset: str = "USDT", top_n: int = 5) -> List[dict]:
        """Return the top-N safest high-yield pools for an asset."""
        asset_upper = asset.upper()
        filtered = [
            p for p in self._pools
            if p.get("symbol", "").upper() == asset_upper
            and (p.get("tvlUsd", 0) or 0) >= self.min_tvl
            # Only stable/low-risk protocols
            and p.get("project", "") in ("aave-v3", "compound-v3", "aave",
                                          "spark", "morpho", "moonwell")
        ]
        filtered.sort(key=lambda p: p.get("apy", 0) or 0, reverse=True)
        return [{
            "project": p.get("project", ""),
            "chain": p.get("chain", ""),
            "apy": round(p.get("apy", 0) or 0, 2),
            "tvl_usd": round(p.get("tvlUsd", 0) or 0),
            "stable": p.get("stablecoin", False),
        } for p in filtered[:top_n]]

    def stats(self) -> dict:
        return {"name": "defi_yield", "pools_tracked": len(self._pools),
                "best_usdt": self.best_yields("USDT", 3)}
