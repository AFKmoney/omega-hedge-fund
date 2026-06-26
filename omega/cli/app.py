#!/usr/bin/env python3
"""
OMEGA live dashboard + wallet control CLI.

Two modes:
    omega-dashboard          # live view of positions, PnL, orders, crowd signals
    omega withdraw AMT CCY ADDR CHAIN TOTP   # secured withdrawal
    omega panic              # freeze all withdrawals
    omega status             # wallet + account status

The dashboard polls OKX for balance/positions every few seconds and renders a
compact terminal table. Press Ctrl+C to exit.

Usage:
    python -m omega.cli.app dashboard
    python -m omega.cli.app withdraw 100 USDT 0xAbc... ETH-ERC20 123456
    python -m omega.cli.app panic
    python -m omega.cli.app status
    python -m omega.cli.app set-cap 2000 TOTP
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from omega.execution.okx_executor import OKXExecutor
from omega.execution.wallet_manager import WalletManager
from omega.utils.logger import get_logger

logger = get_logger("omega.cli")


def _make_executor(demo: bool = False) -> OKXExecutor:
    return OKXExecutor(
        api_key=os.getenv("OKX_API_KEY", ""),
        api_secret=os.getenv("OKX_API_SECRET", ""),
        passphrase=os.getenv("OKX_PASSPHRASE", ""),
        demo=demo or os.getenv("OKX_DEMO", "").lower() in ("1", "true"),
    )


def _make_wallet(executor: OKXExecutor) -> WalletManager:
    return WalletManager(
        executor,
        totp_secret=os.getenv("OMEGA_TOTP_SECRET", ""),
        daily_cap_usd=float(os.getenv("OMEGA_DAILY_CAP_USD", "500")),
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_dashboard(args) -> None:
    """Live dashboard: balance, positions, crowd signals (if orchestrator running)."""
    ex = _make_executor(args.demo)
    print(f"\n{'='*60}")
    print(f"  OMEGA Dashboard — {ex.venue.upper()} "
          f"{'[DEMO]' if ex.demo else '[LIVE]' if not ex.dry_run else '[DRY-RUN]'}")
    print(f"{'='*60}\n")
    try:
        while True:
            balance = await ex.get_balance("USDT")
            positions = await ex.get_positions()
            # Render
            # ANSI clear screen
            print("\033[2J\033[H", end="")
            print(f"  OMEGA Dashboard — {ex.venue.upper()}  "
                  f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
            print(f"  {'─'*56}")
            print(f"  USDT balance:  ${balance:,.2f}")
            print(f"  Open positions: {len(positions)}")
            for p in positions[:10]:
                inst = p.get("instId", "?")
                pos = p.get("pos", "0")
                pnl = p.get("upl", "0")
                side = "LONG" if float(pos or 0) > 0 else "SHORT" if float(pos or 0) < 0 else "FLAT"
                print(f"    {inst:18} {side:5} pos={pos:>10}  uPnL={pnl}")
            if not positions:
                print(f"    (no open positions)")
            print(f"  {'─'*56}")
            print(f"  Ctrl+C to exit")
            await asyncio.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n  Dashboard stopped.")
    finally:
        await ex.close()


async def cmd_withdraw(args) -> None:
    """Withdraw funds (requires TOTP)."""
    ex = _make_executor(args.demo)
    wallet = _make_wallet(ex)
    result = await wallet.withdraw(
        ccy=args.ccy, amt=args.amount, to_addr=args.address,
        chain=args.chain, totp_code=args.totp,
    )
    if result.get("ok"):
        print(f"✓ Withdrawal submitted: {args.amount} {args.ccy} -> {args.address}")
        if result.get("usd_value"):
            print(f"  USD value: ${result['usd_value']:.2f}")
    else:
        print(f"✗ Withdrawal BLOCKED: {result.get('reason', 'unknown')}")
        if result.get("spent_today") is not None:
            print(f"  Spent today: ${result['spent_today']:.2f} / cap ${result['cap']:.2f}")
        sys.exit(1)
    await ex.close()


async def cmd_panic(args) -> None:
    """Engage the panic switch (freeze all withdrawals)."""
    ex = _make_executor(args.demo)
    wallet = _make_wallet(ex)
    wallet.panic()
    print("🚨 PANIC SWITCH ENGAGED — all withdrawals frozen.")
    print("  To re-enable: omega unfreeze <TOTP>")
    await ex.close()


async def cmd_unfreeze(args) -> None:
    ex = _make_executor(args.demo)
    wallet = _make_wallet(ex)
    if wallet.unfreeze(args.totp):
        print("✓ Withdrawals re-enabled.")
    else:
        print("✗ Invalid TOTP code.")
        sys.exit(1)
    await ex.close()


async def cmd_status(args) -> None:
    """Show wallet + account status."""
    ex = _make_executor(args.demo)
    wallet = _make_wallet(ex)
    balance = await ex.get_balance("USDT")
    ws = wallet.status()
    print(f"\n  OMEGA Wallet Status — {ex.venue.upper()}")
    print(f"  {'─'*40}")
    print(f"  USDT balance:    ${balance:,.2f}")
    print(f"  Panic switch:    {'🚨 ENGAGED' if ws['panic'] else '✓ clear'}")
    print(f"  Daily cap:       ${ws['daily_cap_usd']:.2f}")
    print(f"  Spent (24h):     ${ws['spent_last_24h_usd']:.2f}")
    print(f"  Withdrawals 24h: {ws['withdrawals_24h']}")
    print(f"  Total log records: {ws['total_records']}")
    print()
    await ex.close()


async def cmd_set_cap(args) -> None:
    ex = _make_executor(args.demo)
    wallet = _make_wallet(ex)
    if wallet.set_daily_cap(args.cap, args.totp):
        print(f"✓ Daily cap set to ${args.cap:.2f}")
    else:
        print("✗ Invalid TOTP code.")
        sys.exit(1)
    await ex.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="OMEGA CLI — dashboard + wallet control")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("dashboard", help="Live positions/PnL dashboard")
    d.add_argument("--interval", type=float, default=5.0)
    d.add_argument("--demo", action="store_true")

    w = sub.add_parser("withdraw", help="Withdraw funds (requires TOTP)")
    w.add_argument("amount", type=float)
    w.add_argument("ccy", type=str)
    w.add_argument("address", type=str)
    w.add_argument("chain", type=str, help="e.g. ETH-ERC20, TRX-TRC20, BTC-Bitcoin")
    w.add_argument("totp", type=str, help="6-digit TOTP code")
    w.add_argument("--demo", action="store_true")

    sub.add_parser("panic", help="Freeze all withdrawals").add_argument("--demo", action="store_true")

    u = sub.add_parser("unfreeze", help="Re-enable withdrawals after panic")
    u.add_argument("totp", type=str)
    u.add_argument("--demo", action="store_true")

    sub.add_parser("status", help="Wallet + account status").add_argument("--demo", action="store_true")

    c = sub.add_parser("set-cap", help="Change the daily withdrawal cap")
    c.add_argument("cap", type=float)
    c.add_argument("totp", type=str)
    c.add_argument("--demo", action="store_true")

    args = p.parse_args()
    cmd = args.command
    if cmd == "dashboard":
        asyncio.run(cmd_dashboard(args))
    elif cmd == "withdraw":
        asyncio.run(cmd_withdraw(args))
    elif cmd == "panic":
        asyncio.run(cmd_panic(args))
    elif cmd == "unfreeze":
        asyncio.run(cmd_unfreeze(args))
    elif cmd == "status":
        asyncio.run(cmd_status(args))
    elif cmd == "set-cap":
        asyncio.run(cmd_set_cap(args))


if __name__ == "__main__":
    main()
