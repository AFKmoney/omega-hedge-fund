"""
BinanceExecutor — REAL Binance REST API order router.

Submits/cancels/fetches orders via the official Binance REST API using HMAC-SHA256
signed requests. Works with both production (api.binance.com) and testnet
(testnet.binance.vision). Requires BINANCE_API_KEY and BINANCE_API_SECRET env vars.

If credentials are missing, the executor runs in "dry-run" mode: it logs the
order it WOULD submit but does not send it. This is NOT a mock — it's an
explicit safety mode so the system never accidentally submits real orders
without credentials.

Endpoint reference (signed):
    POST /api/v3/order              → submit
    DELETE /api/v3/order            → cancel
    DELETE /api/v3/openOrders       → cancel all
    GET  /api/v3/openOrders         → list open orders
    GET  /api/v3/account            → account balances
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time
import urllib.parse
from typing import Dict, List, Optional

import aiohttp

from omega.execution.base import Executor
from omega.utils.events import FillEvent, OrderEvent, OrderType, Side
from omega.utils.logger import get_logger

logger = get_logger("omega.execution.binance")


class BinanceExecutor(Executor):
    """Real Binance spot REST API executor."""

    venue = "binance"

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = False,
        rate_limit_per_min: int = 1200,
    ) -> None:
        self.api_key = api_key or os.getenv("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "")
        self.testnet = testnet
        self.base_url = (
            "https://testnet.binance.vision" if testnet
            else "https://api.binance.com"
        )
        self.rate_limit_per_min = rate_limit_per_min
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_ts = 0.0
        self._dry_run = not (self.api_key and self.api_secret)
        if self._dry_run:
            logger.warning(
                "BinanceExecutor running in DRY-RUN mode (no API credentials). "
                "Orders will be logged but NOT submitted.",
                extra={"component": "execution.binance"},
            )

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-MBX-APIKEY": self.api_key} if self.api_key else {}
            )
        return self._session

    def _sign(self, params: Dict) -> str:
        """HMAC-SHA256 signature for Binance signed endpoints."""
        query = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{query}&signature={signature}"

    async def _throttle(self) -> None:
        """Respect rate limit: minimum 60s / rate_limit_per_min between requests."""
        min_interval = 60.0 / self.rate_limit_per_min
        elapsed = time.time() - self._last_request_ts
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_request_ts = time.time()

    async def submit(self, order: OrderEvent) -> str:
        """Submit a real order to Binance. Returns exchange order ID (or dry-run ID)."""
        if self._dry_run:
            logger.info(
                f"[DRY-RUN] Submit: {order.side.value} {order.qty:.6f} {order.symbol} "
                f"@ {order.order_type.value}",
                extra={
                    "component": "execution.binance",
                    "symbol": order.symbol,
                },
            )
            return f"dryrun-{order.order_id}"

        params: Dict = {
            "symbol": order.symbol,
            "side": order.side.value,
            "type": order.order_type.value,
            "quantity": f"{order.qty:.6f}",
            "timestamp": int(time.time() * 1000),
            "recvWindow": 5000,
        }
        if order.order_type == OrderType.LIMIT and order.limit_price is not None:
            params["price"] = f"{order.limit_price:.8f}"
            params["timeInForce"] = order.time_in_force.value
        signed_query = self._sign(params)
        url = f"{self.base_url}/api/v3/order?{signed_query}"
        await self._throttle()
        session = await self._get_session()
        try:
            async with session.post(url, timeout=10) as resp:
                payload = await resp.json()
            if resp.status != 200:
                logger.error(
                    f"Binance submit failed: {resp.status} {payload}",
                    extra={"component": "execution.binance", "symbol": order.symbol},
                )
                return ""
            exchange_id = str(payload.get("orderId", ""))
            logger.info(
                f"Binance filled: {order.side.value} {order.qty:.6f} {order.symbol} "
                f"orderId={exchange_id}",
                extra={"component": "execution.binance", "symbol": order.symbol},
            )
            return exchange_id
        except Exception as exc:
            logger.exception(f"Binance submit exception: {exc}")
            return ""

    async def cancel(self, exchange_order_id: str) -> bool:
        if self._dry_run or exchange_order_id.startswith("dryrun-"):
            return True
        params = {
            "orderId": exchange_order_id,
            "timestamp": int(time.time() * 1000),
        }
        signed_query = self._sign(params)
        url = f"{self.base_url}/api/v3/order?{signed_query}"
        await self._throttle()
        session = await self._get_session()
        try:
            async with session.delete(url, timeout=10) as resp:
                return resp.status == 200
        except Exception as exc:
            logger.warning(f"Binance cancel failed: {exc}")
            return False

    async def cancel_all(self) -> int:
        """Cancel all open orders at Binance. Used by Kill Switch."""
        if self._dry_run:
            logger.info("[DRY-RUN] Cancel all open orders")
            return 0
        params = {"timestamp": int(time.time() * 1000)}
        signed_query = self._sign(params)
        url = f"{self.base_url}/api/v3/openOrders?{signed_query}"
        await self._throttle()
        session = await self._get_session()
        try:
            async with session.delete(url, timeout=10) as resp:
                payload = await resp.json()
            return len(payload) if isinstance(payload, list) else 0
        except Exception as exc:
            logger.warning(f"Binance cancel_all failed: {exc}")
            return 0

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[dict]:
        if self._dry_run:
            return []
        params = {"timestamp": int(time.time() * 1000)}
        if symbol:
            params["symbol"] = symbol
        signed_query = self._sign(params)
        url = f"{self.base_url}/api/v3/openOrders?{signed_query}"
        await self._throttle()
        session = await self._get_session()
        async with session.get(url, timeout=10) as resp:
            return await resp.json()

    async def fetch_balance(self) -> dict:
        if self._dry_run:
            return {"USDT": {"free": 100000.0, "locked": 0.0}}
        params = {"timestamp": int(time.time() * 1000)}
        signed_query = self._sign(params)
        url = f"{self.base_url}/api/v3/account?{signed_query}"
        await self._throttle()
        session = await self._get_session()
        async with session.get(url, timeout=10) as resp:
            payload = await resp.json()
        return {
            b["asset"]: {"free": float(b["free"]), "locked": float(b["locked"])}
            for b in payload.get("balances", [])
            if float(b["free"]) > 0 or float(b["locked"]) > 0
        }

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    @property
    def is_live(self) -> bool:
        return not self._dry_run
