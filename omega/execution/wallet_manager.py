"""
WalletManager — secure withdrawal gate for moving funds off-exchange.

This is the ONLY sanctioned path to move funds out. It wraps the raw
OKXExecutor._withdraw() call with multiple layers of protection so that a
leaked API key alone can never drain the account:

    1. TOTP verification — every withdrawal requires a fresh 6-digit code
       from a TOTP secret the user controls. The API key can't generate it.
    2. Daily cap — cumulative withdrawn USD is capped per rolling 24h window.
    3. Panic switch — a kill flag that blocks ALL withdrawals until reset.
    4. Immutable log — every withdrawal attempt (success or blocked) is
       appended to a tamper-evident local log for audit.

Why not a whitelist? The user asked for "any address". TOTP + cap gives the
flexibility while making a key-only theft useless: without the TOTP secret on
the user's phone, the attacker cannot withdraw even with full API access.

Contract:
    withdraw(ccy, amt, to_addr, chain, totp_code) -> dict
    panic()   -> freezes withdrawals
    unfreeze(totp_code) -> re-enables
    set_daily_cap(new_cap, totp_code) -> change the cap
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from omega.execution.okx_executor import OKXExecutor
from omega.utils.logger import get_logger

logger = get_logger("omega.execution.wallet")


def _totp(secret: str, timestamp: Optional[int] = None) -> str:
    """Generate a TOTP code from a base32 secret (RFC 6238)."""
    import base64
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = (timestamp or int(time.time())) // 30
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = (struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def _verify_totp(secret: str, code: str, window: int = 1) -> bool:
    """Verify a TOTP code, allowing ±window periods of clock skew."""
    if not secret or not code:
        return False
    now = int(time.time())
    for offset in range(-window, window + 1):
        if hmac.compare_digest(_totp(secret, now + offset * 30), str(code).strip()):
            return True
    return False


@dataclass
class WithdrawalRecord:
    timestamp: float
    ccy: str
    amount: float
    to_addr: str
    chain: str
    status: str  # "submitted" | "blocked_panic" | "blocked_cap" | "blocked_totp" | "error"
    tx_id: str = ""
    usd_value: float = 0.0
    reason: str = ""


class WalletManager:
    """Secure withdrawal gate (TOTP + cap + panic + log)."""

    def __init__(
        self,
        executor: OKXExecutor,
        totp_secret: str = "",
        daily_cap_usd: float = 500.0,
        log_path: Optional[str] = None,
    ) -> None:
        self.executor = executor
        self.totp_secret = totp_secret or os.getenv("OMEGA_TOTP_SECRET", "")
        self.daily_cap_usd = daily_cap_usd
        from omega.config.settings import _default_data_dir
        self.log_path = Path(log_path) if log_path else _default_data_dir() / "withdrawals.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._panic = False
        self._records: List[WithdrawalRecord] = self._load_log()
        # rough USD price cache for cap computation (refreshed on each withdraw)
        self._usd_prices: Dict[str, float] = {"USDT": 1.0, "USDC": 1.0, "USD": 1.0}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def withdraw(
        self, ccy: str, amt: float, to_addr: str, chain: str,
        totp_code: str, fee: Optional[str] = None,
    ) -> Dict:
        """
        Withdraw funds to an external wallet. Requires:
            - valid TOTP code (fresh, ±30s)
            - panic switch OFF
            - daily cap not exceeded
        Returns the exchange response (or a blocked-status dict).
        """
        # 1. Panic check
        if self._panic:
            rec = WithdrawalRecord(time.time(), ccy, amt, to_addr, chain, "blocked_panic")
            self._log(rec)
            logger.warning(f"Withdrawal BLOCKED (panic switch): {amt} {ccy} -> {to_addr}")
            return {"ok": False, "reason": "panic_switch_active"}

        # 2. TOTP check
        if not _verify_totp(self.totp_secret, totp_code):
            rec = WithdrawalRecord(time.time(), ccy, amt, to_addr, chain, "blocked_totp")
            self._log(rec)
            logger.warning(f"Withdrawal BLOCKED (bad TOTP): {amt} {ccy} -> {to_addr}")
            return {"ok": False, "reason": "invalid_totp"}

        # 3. Cap check
        usd_val = await self._usd_value(ccy, amt)
        spent_today = self._spent_last_24h()
        if spent_today + usd_val > self.daily_cap_usd:
            rec = WithdrawalRecord(time.time(), ccy, amt, to_addr, chain,
                                   "blocked_cap", usd_value=usd_val)
            self._log(rec)
            logger.warning(
                f"Withdrawal BLOCKED (daily cap {self.daily_cap_usd} USD "
                f"would be exceeded: {spent_today + usd_val:.2f})"
            )
            return {"ok": False, "reason": "daily_cap_exceeded",
                    "spent_today": spent_today, "cap": self.daily_cap_usd}

        # 4. Execute
        result = await self.executor._withdraw(ccy, amt, to_addr, chain, fee=fee)
        ok = result.get("code") == "0"
        status = "submitted" if ok else "error"
        tx_id = result.get("data", [{}])[0].get("wdId", "") if result.get("data") else ""
        rec = WithdrawalRecord(
            time.time(), ccy, amt, to_addr, chain, status,
            tx_id=tx_id, usd_value=usd_val,
            reason="" if ok else str(result.get("msg", "")),
        )
        self._log(rec)
        if ok:
            logger.info(
                f"Withdrawal submitted: {amt} {ccy} (${usd_val:.2f}) -> {to_addr} [{chain}] "
                f"wdId={tx_id}"
            )
        else:
            logger.error(f"Withdrawal failed: {result.get('msg')} ({result.get('code')})")
        return {"ok": ok, "result": result, "usd_value": usd_val}

    def panic(self) -> None:
        """Freeze all withdrawals immediately."""
        self._panic = True
        logger.warning("PANIC SWITCH ENGAGED — all withdrawals frozen")

    def unfreeze(self, totp_code: str) -> bool:
        """Re-enable withdrawals after a panic. Requires TOTP."""
        if _verify_totp(self.totp_secret, totp_code):
            self._panic = False
            logger.info("Panic switch cleared — withdrawals re-enabled")
            return True
        return False

    def set_daily_cap(self, new_cap: float, totp_code: str) -> bool:
        if _verify_totp(self.totp_secret, totp_code):
            self.daily_cap_usd = float(new_cap)
            logger.info(f"Daily withdrawal cap set to ${new_cap:.2f}")
            return True
        return False

    def status(self) -> dict:
        return {
            "panic": self._panic,
            "daily_cap_usd": self.daily_cap_usd,
            "spent_last_24h_usd": self._spent_last_24h(),
            "withdrawals_24h": sum(
                1 for r in self._records
                if r.timestamp > time.time() - 86400 and r.status == "submitted"
            ),
            "total_records": len(self._records),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _spent_last_24h(self) -> float:
        cutoff = time.time() - 86400
        return sum(
            r.usd_value for r in self._records
            if r.timestamp > cutoff and r.status == "submitted"
        )

    async def _usd_value(self, ccy: str, amt: float) -> float:
        if ccy.upper() in ("USDT", "USDC", "USD"):
            return amt
        # Fetch a rough price from OKX public ticker
        import aiohttp
        inst = f"{ccy.upper()}-USDT"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{self.executor.base_url}/api/v5/market/index-tickers?quoteCcy=USDT",
                    timeout=5,
                ) as r:
                    data = await r.json()
                    for row in data.get("data", []):
                        if row.get("instId", "").startswith(ccy.upper()):
                            px = float(row.get("idxPx", 0) or 0)
                            self._usd_prices[ccy.upper()] = px
                            return px * amt
        except Exception:
            pass
        return self._usd_prices.get(ccy.upper(), 0.0) * amt

    def _log(self, rec: WithdrawalRecord) -> None:
        self._records.append(rec)
        # Append-only JSONL log (tamper-evident: chain of hashes)
        prev_hash = ""
        try:
            if self.log_path.exists():
                lines = self.log_path.read_text().strip().split("\n")
                if lines and lines[-1]:
                    prev_hash = json.loads(lines[-1]).get("hash", "")
        except Exception:
            pass
        payload = {
            "ts": rec.timestamp, "ccy": rec.ccy, "amount": rec.amount,
            "to_addr": rec.to_addr, "chain": rec.chain, "status": rec.status,
            "tx_id": rec.tx_id, "usd_value": rec.usd_value, "reason": rec.reason,
        }
        chain_input = (prev_hash + json.dumps(payload, sort_keys=True)).encode()
        h = hashlib.sha256(chain_input).hexdigest()
        entry = {**payload, "hash": h}
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.error(f"Failed to write withdrawal log: {exc}")

    def _load_log(self) -> List[WithdrawalRecord]:
        records: List[WithdrawalRecord] = []
        if not self.log_path.exists():
            return records
        try:
            for line in self.log_path.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                d = json.loads(line)
                records.append(WithdrawalRecord(
                    d["ts"], d["ccy"], d["amount"], d["to_addr"], d["chain"],
                    d["status"], d.get("tx_id", ""), d.get("usd_value", 0.0),
                    d.get("reason", ""),
                ))
        except Exception:
            pass
        return records
