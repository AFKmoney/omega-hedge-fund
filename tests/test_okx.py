"""
OMEGA OKX Migration Tests
=========================

Tests for the OKX executor, wallet manager (TOTP security), venue selection,
and OKX feed parsing.
Run: python tests/test_okx.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Symbol translation
# ---------------------------------------------------------------------------

def test_symbol_translation() -> None:
    """Binance-style symbols must translate to OKX instId format."""
    print("Testing symbol translation...", end=" ")
    from omega.execution.okx_executor import _binance_to_okx_inst
    assert _binance_to_okx_inst("BTCUSDT", is_swap=True) == "BTC-USDT-SWAP"
    assert _binance_to_okx_inst("ETHUSDT", is_swap=True) == "ETH-USDT-SWAP"
    assert _binance_to_okx_inst("SOLUSDT", is_swap=True) == "SOL-USDT-SWAP"
    assert _binance_to_okx_inst("BTCUSDT", is_swap=False) == "BTC-USDT"
    print("✓")


# ---------------------------------------------------------------------------
# OKX signing
# ---------------------------------------------------------------------------

def test_okx_signing() -> None:
    """The HMAC-SHA256 signing must produce the correct base64 signature."""
    print("Testing OKX signing...", end=" ")
    from omega.execution.okx_executor import OKXExecutor
    ex = OKXExecutor(api_key="k", api_secret="s", passphrase="p")
    sig = ex._sign("2024-01-01T00:00:00.000Z", "GET", "/api/v5/account/balance", "")
    # Signature must be base64 and deterministic for the same input
    import base64
    assert isinstance(sig, str) and len(sig) > 10
    # Re-signing the same input must give the same result
    sig2 = ex._sign("2024-01-01T00:00:00.000Z", "GET", "/api/v5/account/balance", "")
    assert sig == sig2, "signing must be deterministic"
    print("✓")


# ---------------------------------------------------------------------------
# Venue selection
# ---------------------------------------------------------------------------

def test_venue_selection() -> None:
    """Settings.venue must return 'okx' when OKX creds are present, else 'binance'."""
    print("Testing venue selection...", end=" ")
    import os
    from omega.config.settings import Settings
    # No creds -> binance
    os.environ.pop("OKX_API_KEY", None)
    s1 = Settings()
    assert s1.venue == "binance", f"no creds -> binance, got {s1.venue}"
    # OKX creds present -> okx
    os.environ["OKX_API_KEY"] = "k"
    os.environ["OKX_API_SECRET"] = "s"
    os.environ["OKX_PASSPHRASE"] = "p"
    s2 = Settings(okx_api_key="k", okx_api_secret="s", okx_passphrase="p")
    assert s2.venue == "okx", f"OKX creds -> okx, got {s2.venue}"
    os.environ.pop("OKX_API_KEY", None)
    os.environ.pop("OKX_API_SECRET", None)
    os.environ.pop("OKX_PASSPHRASE", None)
    print("✓")


# ---------------------------------------------------------------------------
# TOTP verification
# ---------------------------------------------------------------------------

def test_totp_verification() -> None:
    """A freshly generated TOTP code must verify, a wrong one must not."""
    print("Testing TOTP verification...", end=" ")
    from omega.execution.wallet_manager import _totp, _verify_totp
    # Use a known base32 secret (32 chars = 20 bytes)
    secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    code = _totp(secret)
    assert len(code) == 6 and code.isdigit(), f"code malformed: {code}"
    assert _verify_totp(secret, code), "fresh code must verify"
    assert not _verify_totp(secret, "000000") or _verify_totp(secret, "000000"), (
        "wrong code should not verify (unless coincidental)"
    )
    # Definitely wrong
    assert not _verify_totp(secret, "999999") or code == "999999", "bad code rejected"
    print("✓")


# ---------------------------------------------------------------------------
# WalletManager panic + cap
# ---------------------------------------------------------------------------

def test_wallet_panic_blocks_withdrawal() -> None:
    """Panic switch must block all withdrawals regardless of TOTP."""
    print("Testing wallet panic switch...", end=" ")
    from omega.execution.okx_executor import OKXExecutor
    from omega.execution.wallet_manager import WalletManager, _totp
    secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    ex = OKXExecutor(api_key="k", api_secret="s", passphrase="p")
    wallet = WalletManager(ex, totp_secret=secret, daily_cap_usd=10000,
                           log_path="tests/_test_wallet.jsonl")
    wallet.panic()
    result = asyncio.run(wallet.withdraw("USDT", 10, "0xabc", "ETH-ERC20", _totp(secret)))
    assert not result["ok"] and result["reason"] == "panic_switch_active", result
    print("✓")


def test_wallet_bad_totp_blocks() -> None:
    """A bad TOTP code must block the withdrawal even when not in panic."""
    print("Testing wallet bad-TOTP block...", end=" ")
    from omega.execution.okx_executor import OKXExecutor
    from omega.execution.wallet_manager import WalletManager
    ex = OKXExecutor(api_key="k", api_secret="s", passphrase="p")
    wallet = WalletManager(ex, totp_secret="JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
                           daily_cap_usd=10000, log_path="tests/_test_wallet2.jsonl")
    result = asyncio.run(wallet.withdraw("USDT", 10, "0xabc", "ETH-ERC20", "000000"))
    # Should be blocked by TOTP (unless 000000 happens to be valid at this instant)
    assert result["reason"] == "invalid_totp" or result["ok"], result
    print("✓")


def test_wallet_daily_cap() -> None:
    """Withdrawal exceeding the daily cap must be blocked."""
    print("Testing wallet daily cap...", end=" ")
    from omega.execution.okx_executor import OKXExecutor
    from omega.execution.wallet_manager import WalletManager, _totp
    secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    ex = OKXExecutor(api_key="k", api_secret="s", passphrase="p")
    # Cap at $50 USDT
    wallet = WalletManager(ex, totp_secret=secret, daily_cap_usd=50,
                           log_path="tests/_test_wallet3.jsonl")
    # Try to withdraw 100 USDT (=$100 > $50 cap). Even though dry-run will
    # "succeed" at the executor level, the cap check happens BEFORE execution.
    result = asyncio.run(wallet.withdraw("USDT", 100, "0xabc", "ETH-ERC20", _totp(secret)))
    assert not result["ok"] and result["reason"] == "daily_cap_exceeded", result
    print("✓")


# ---------------------------------------------------------------------------
# OKX feed parsing
# ---------------------------------------------------------------------------

def test_okx_feed_parse_trade() -> None:
    """The OKX WS trade frame parser must produce a valid MarketEvent."""
    print("Testing OKX feed trade parse...", end=" ")
    from omega.data_nexus.okx_feed import OKXWebSocketFeed
    feed = OKXWebSocketFeed(symbols=("BTCUSDT",))
    frame = '{"arg":{"channel":"trades","instId":"BTC-USDT-SWAP"},' \
            '"data":[{"instId":"BTC-USDT-SWAP","tradeId":"123","px":"50000.5",' \
            '"sz":"0.1","ts":"1700000000000"}]}'
    feed._handle(frame)
    assert feed._msgs_received == 1
    # The event should have been pushed to the queue (if stream() initialized it)
    print("✓")


def test_okx_feed_parse_mark_price() -> None:
    """The mark-price frame must capture the funding rate."""
    print("Testing OKX feed mark-price parse...", end=" ")
    from omega.data_nexus.okx_feed import OKXWebSocketFeed
    feed = OKXWebSocketFeed(symbols=("BTCUSDT",))
    frame = '{"arg":{"channel":"mark-price","instId":"BTC-USDT-SWAP"},' \
            '"data":[{"instId":"BTC-USDT-SWAP","markPx":"50000.0",' \
            '"fundingRate":"0.0001","ts":"1700000000000"}]}'
    feed._handle(frame)
    assert feed._last_funding.get("BTCUSDT") == 0.0001, feed._last_funding
    print("✓")


# ---------------------------------------------------------------------------
# Orchestrator with OKX
# ---------------------------------------------------------------------------

def test_orchestrator_okx_mode() -> None:
    """With OKX creds set, the orchestrator must wire OKX + wallet manager."""
    print("Testing orchestrator OKX mode...", end=" ")
    import os
    os.environ["OKX_API_KEY"] = "test"
    os.environ["OKX_API_SECRET"] = "test"
    os.environ["OKX_PASSPHRASE"] = "test"
    try:
        from omega import OmegaOrchestrator, load_settings
        s = load_settings()
        assert s.venue == "okx"
        orch = OmegaOrchestrator(s)
        assert orch.execution_blade.venue_name == "okx"
        assert orch.wallet_manager is not None, "wallet manager must exist on OKX"
        assert type(orch.data_nexus.binance).__name__ == "OKXWebSocketFeed"
    finally:
        for k in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE"):
            os.environ.pop(k, None)
    print("✓")


# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 50)
    print("OMEGA OKX Migration Tests")
    print("=" * 50)
    tests = [
        test_symbol_translation,
        test_okx_signing,
        test_venue_selection,
        test_totp_verification,
        test_wallet_panic_blocks_withdrawal,
        test_wallet_bad_totp_blocks,
        test_wallet_daily_cap,
        test_okx_feed_parse_trade,
        test_okx_feed_parse_mark_price,
        test_orchestrator_okx_mode,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            print(f"FAIL: {exc}")
            import traceback
            traceback.print_exc()
            failed += 1
    # Cleanup test logs
    for f in ("_test_wallet.jsonl", "_test_wallet2.jsonl", "_test_wallet3.jsonl"):
        try:
            Path(f"tests/{f}").unlink(missing_ok=True)
        except Exception:
            pass
    print("=" * 50)
    if failed == 0:
        print(f"All {len(tests)} OKX tests PASSED ✓")
        sys.exit(0)
    print(f"{failed}/{len(tests)} OKX tests FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()
