"""
OKXExecutor — REST API client for OKX (spot + perp).

OKX uses 3 credentials (key + secret + passphrase) and a different signing
scheme than Binance:
    timestamp = ISO 8601 UTC (e.g. 2024-01-01T12:00:00.000Z)
    prehash   = timestamp + METHOD + requestPath + body
    signature = base64(HMAC-SHA256(secret, prehash))
    headers   = OK-ACCESS-KEY, OK-ACCESS-SIGN, OK-ACCESS-TIMESTAMP,
                OK-ACCESS-PASSPHRASE, Content-Type: application/json

instId format on OKX differs from Binance:
    Binance:  BTCUSDT          (spot)   / BTCUSDT       (perp)
    OKX:      BTC-USDT         (spot)   / BTC-USDT-SWAP (perp)

We handle the translation via _inst_id() so the rest of OMEGA keeps using the
Binance-style symbol internally (BTCUSDT) and this executor converts.

Withdrawals go through the WalletManager (TOTP + cap + panic) — this executor
exposes the raw _withdraw() private method, but the orchestrator must route
withdrawals via WalletManager.withdraw(), never call _withdraw() directly.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiohttp

from omega.execution.base import Executor
from omega.utils.events import OrderEvent, OrderType, Side, TimeInForce
from omega.utils.logger import get_logger

logger = get_logger("omega.execution.okx")

_OKX_REST = "https://www.okx.com"
_OKX_REST_DEMO = "https://www.okx.com"  # demo trading uses same host + x-simulated-trading:1


def _binance_to_okx_inst(sym: str, is_swap: bool = True) -> str:
    """BTCUSDT -> BTC-USDT-SWAP (perp) or BTC-USDT (spot)."""
    s = sym.upper().replace("-", "")
    # Common stablecoin quotes
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote):
            base = s[: -len(quote)]
            return f"{base}-{quote}-SWAP" if is_swap else f"{base}-{quote}"
    return sym


class OKXExecutor(Executor):
    """OKX REST executor (spot + perpetual swap)."""

    venue = "okx"

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        demo: bool = False,
        trade_swap: bool = True,   # default to perp (needed for funding crowd signals)
        rate_limit_per_min: int = 600,
    ) -> None:
        self.api_key = api_key or os.getenv("OKX_API_KEY", "")
        self.api_secret = api_secret or os.getenv("OKX_API_SECRET", "")
        self.passphrase = passphrase or os.getenv("OKX_PASSPHRASE", "")
        self.demo = demo or os.getenv("OKX_DEMO", "").lower() in ("1", "true", "yes")
        self.trade_swap = trade_swap
        self.base_url = _OKX_REST_DEMO if self.demo else _OKX_REST
        self.rate_limit_per_min = rate_limit_per_min
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_ts = 0.0
        self._min_interval = 60.0 / max(rate_limit_per_min, 1)
        self._dry_run = not (self.api_key and self.api_secret and self.passphrase)
        if self._dry_run:
            logger.warning(
                "OKXExecutor in DRY-RUN mode (need OKX_API_KEY/SECRET/PASSPHRASE). "
                "Orders logged but NOT submitted.",
                extra={"component": "execution.okx"},
            )
        else:
            mode = "DEMO (paper)" if self.demo else "LIVE"
            logger.info(
                f"OKXExecutor authenticated [{mode}] swap={trade_swap}",
                extra={"component": "execution.okx"},
            )

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"Content-Type": "application/json"}
            if self.demo:
                headers["x-simulated-trading"] = "1"
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        prehash = f"{timestamp}{method.upper()}{path}{body}"
        mac = hmac.new(
            self.api_secret.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    async def _request(
        self, method: str, path: str, body: Optional[Dict] = None,
        auth: bool = True,
    ) -> Dict[str, Any]:
        session = await self._get_session()
        # Simple rate limit
        gap = time.time() - self._last_request_ts
        if gap < self._min_interval:
            await asyncio.sleep(self._min_interval - gap)
        self._last_request_ts = time.time()
        body_str = ""
        if body is not None:
            body_str = __import__("json").dumps(body)
        headers = {}
        if auth:
            ts = self._timestamp()
            headers.update({
                "OK-ACCESS-KEY": self.api_key,
                "OK-ACCESS-SIGN": self._sign(ts, method, path, body_str),
                "OK-ACCESS-TIMESTAMP": ts,
                "OK-ACCESS-PASSPHRASE": self.passphrase,
            })
        url = self.base_url + path
        try:
            async with session.request(method, url, data=body_str or None, headers=headers) as resp:
                text = await resp.text()
                try:
                    return __import__("json").loads(text)
                except Exception:
                    return {"code": str(resp.status), "msg": text}
        except Exception as exc:
            logger.error(f"OKX request failed {method} {path}: {exc}")
            return {"code": "-1", "msg": str(exc)}

    def _inst_id(self, symbol: str) -> str:
        return _binance_to_okx_inst(symbol, is_swap=self.trade_swap)

    # ------------------------------------------------------------------
    # Executor interface
    # ------------------------------------------------------------------

    async def submit(self, order: OrderEvent) -> str:
        """Submit an order to OKX. Returns exchange order id (or algo client id)."""
        clord = order.order_id[:32] or f"omega{int(time.time()*1000)}"
        if self._dry_run:
            logger.info(
                f"[DRY-RUN] OKX submit: {order.side.value} {order.qty} "
                f"{order.symbol} @ {order.order_type.value}",
                extra={"component": "execution.okx"},
            )
            return f"DRY-{clord}"

        side = "buy" if order.side == Side.BUY else "sell"
        ord_type = "market" if order.order_type == OrderType.MARKET else "limit"
        path = "/api/v5/trade/order"
        body: Dict[str, Any] = {
            "instId": self._inst_id(order.symbol),
            "tdMode": "cross",            # cross margin
            "side": side,
            "ordType": ord_type,
            "sz": str(order.qty),
            "clOrdId": clord,
            "tgtCcy": "base_ccy",
        }
        if ord_type == "limit" and order.limit_price:
            body["px"] = str(order.limit_price)
        result = await self._request("POST", path, body=body, auth=True)
        if result.get("code") == "0" and result.get("data"):
            ord_id = result["data"][0].get("ordId", clord)
            logger.info(f"OKX order accepted: {ord_id} ({order.side.value} {order.qty} {order.symbol})",
                        extra={"component": "execution.okx"})
            return ord_id
        scode = result.get("data", [{}])[0].get("sCode") if result.get("data") else result.get("code")
        smsg = result.get("data", [{}])[0].get("sMsg") if result.get("data") else result.get("msg")
        logger.error(f"OKX order rejected: code={scode} msg={smsg}",
                     extra={"component": "execution.okx"})
        return f"REJECTED-{clord}"

    async def cancel(self, exchange_order_id: str) -> bool:
        if self._dry_run or exchange_order_id.startswith(("DRY-", "REJECTED-")):
            return True
        path = "/api/v5/trade/cancel-order"
        body = {"instId": "", "ordId": exchange_order_id}
        result = await self._request("POST", path, body=body, auth=True)
        return result.get("code") == "0"

    async def cancel_all(self, inst_id: Optional[str] = None) -> int:
        """Cancel all open orders (optionally for one instrument). Returns count."""
        if self._dry_run:
            return 0
        path = "/api/v5/trade/cancel-all-orders"
        body = {"instType": "SWAP" if self.trade_swap else "SPOT"}
        if inst_id:
            body["instId"] = inst_id
        result = await self._request("POST", path, body=body, auth=True)
        return len(result.get("data", [])) if result.get("code") == "0" else 0

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> list:
        """Fetch open orders."""
        if self._dry_run:
            return []
        path = "/api/v5/trade/orders-pending"
        params = {"instType": "SWAP" if self.trade_swap else "SPOT"}
        if symbol:
            params["instId"] = self._inst_id(symbol)
        result = await self._request("GET", path, auth=True)
        return result.get("data", []) if result.get("code") == "0" else []

    async def fetch_balance(self) -> dict:
        """Fetch full balance snapshot."""
        if self._dry_run:
            return {"USDT": {"avail": 10_000.0, "total": 10_000.0}}
        path = "/api/v5/account/balance"
        result = await self._request("GET", path, auth=True)
        if result.get("code") != "0" or not result.get("data"):
            return {}
        out = {}
        for d in result["data"]:
            for det in d.get("details", []):
                ccy = det.get("ccy", "")
                out[ccy] = {
                    "avail": float(det.get("availBal", 0) or 0),
                    "total": float(det.get("cashBal", 0) or 0),
                }
        return out

    async def get_balance(self, ccy: str = "USDT") -> float:
        """Return available balance for a currency."""
        if self._dry_run:
            return 10_000.0
        path = "/api/v5/account/balance"
        result = await self._request("GET", path, auth=True)
        if result.get("code") != "0" or not result.get("data"):
            return 0.0
        for d in result["data"]:
            for det in d.get("details", []):
                if det.get("ccy") == ccy.upper():
                    return float(det.get("availBal", 0) or 0)
        return 0.0

    async def get_positions(self) -> list:
        """Return open positions (for the dashboard)."""
        if self._dry_run:
            return []
        path = "/api/v5/account/positions"
        result = await self._request("GET", path, auth=True)
        if result.get("code") == "0":
            return result.get("data", [])
        return []

    async def _withdraw(
        self, ccy: str, amt: float, to_addr: str, chain: str,
        dest: int = 4, fee: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Raw withdrawal call. DO NOT call directly — route through WalletManager
        which enforces TOTP + cap + panic switch.
        dest=4 means on-chain withdrawal (3 = internal transfer).
        """
        path = "/api/v5/asset/withdrawal"
        body: Dict[str, Any] = {
            "ccy": ccy.upper(),
            "amt": str(amt),
            "dest": str(dest),
            "toAddr": to_addr,
            "chain": chain,
        }
        if fee:
            body["fee"] = fee
        return await self._request("POST", path, body=body, auth=True)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
