#!/usr/bin/env python3
"""
OMEGA keys manager — the "settings tab" for your credentials.

Manage trading keys, withdrawal keys, and the TOTP secret from one persistent
local store. Everything is saved to ~/.omega/data/keys.json (obfuscated, never
in git). Once set, every OMEGA command picks them up automatically.

Usage:
    python -m omega.cli.keys list              # show what's configured
    python -m omega.cli.keys set okx_api_key VALUE
    python -m omega.cli.keys set okx_api_secret VALUE
    python -m omega.cli.keys set okx_passphrase VALUE
    python -m omega.cli.keys set totp_secret VALUE
    python -m omega.cli.keys set okx_demo true
    python -m omega.cli.keys get okx_api_key
    python -m omega.cli.keys delete okx_api_key
    python -m omega.cli.keys status            # readiness summary
    python -m omega.cli.keys test              # verify connection works
    python -m omega.cli.keys wizard            # interactive setup
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from omega.config.keystore import KeyStore, get_keystore


def cmd_list(args) -> None:
    ks = get_keystore()
    profiles = ks.list_profiles()
    print(f"\n{'='*60}")
    print(f"  OMEGA KeyStore — {ks.path}")
    print(f"{'='*60}\n")
    for profile_name, fields in profiles.items():
        print(f"  [{profile_name.upper()}]")
        for field, info in fields.items():
            mark = "✓" if info["set"] else "✗"
            preview = info["preview"] if info["set"] else "(not set)"
            print(f"    {mark} {field:30} {preview:15} {info['description']}")
        print()
    s = ks.status()
    print(f"  Trading ready:   {'✓ YES' if s['trading_ready'] else '✗ NO'}")
    print(f"  Withdrawal ready: {'✓ YES' if s['withdrawal_ready'] else '✗ NO'}")
    print()


def cmd_set(args) -> None:
    ks = get_keystore()
    value = args.value if args.value else getpass.getpass(f"  Enter {args.key}: ")
    ks.set(args.key, value)
    print(f"✓ Saved {args.key} ({len(value)} chars)")


def cmd_get(args) -> None:
    ks = get_keystore()
    val = ks.get(args.key, "")
    if val:
        print(val)
    else:
        print(f"(not set)", file=sys.stderr)
        sys.exit(1)


def cmd_delete(args) -> None:
    ks = get_keystore()
    if ks.delete(args.key):
        print(f"✓ Deleted {args.key}")
    else:
        print(f"✗ {args.key} was not set")
        sys.exit(1)


def cmd_status(args) -> None:
    ks = get_keystore()
    s = ks.status()
    print(f"\n  OMEGA KeyStore Status")
    print(f"  {'─'*40}")
    print(f"  File:            {s['path']}")
    print(f"  Trading ready:   {'✓' if s['trading_ready'] else '✗'} "
          f"({'OKX' if ks.has('okx_api_key') else 'Binance' if ks.has('binance_api_key') else 'NONE'})")
    print(f"  Withdrawal ready: {'✓' if s['withdrawal_ready'] else '✗'}")
    print()


async def cmd_test(args) -> None:
    """Verify the stored credentials work by attempting a read-only API call."""
    ks = get_keystore()
    ks.apply_to_env()
    print("\n  Testing credentials...\n")
    if ks.has("okx_api_key"):
        from omega.execution.okx_executor import OKXExecutor
        ex = OKXExecutor()
        if ex.dry_run:
            print("  ✗ OKX: missing credentials (need key + secret + passphrase)")
        else:
            try:
                bal = await ex.get_balance("USDT")
                positions = await ex.get_positions()
                print(f"  ✓ OKX connected ({'demo' if ex.demo else 'LIVE'})")
                print(f"    USDT balance: ${bal:,.2f}")
                print(f"    Open positions: {len(positions)}")
            except Exception as exc:
                print(f"  ✗ OKX connection failed: {exc}")
        await ex.close()
    else:
        print("  (no OKX keys configured)")
    if ks.has("totp_secret"):
        from omega.execution.wallet_manager import _totp
        code = _totp(ks.get("totp_secret"))
        print(f"  ✓ TOTP working (current code: {code})")
    else:
        print("  ✗ TOTP not configured (needed for withdrawals)")
    print()


def cmd_wizard(args) -> None:
    """Interactive setup — guides you through all keys."""
    ks = get_keystore()
    print("\n" + "=" * 60)
    print("  OMEGA Setup Wizard")
    print("=" * 60)
    print("  This will configure your keys once. They're saved to:")
    print(f"  {ks.path}")
    print("  (obfuscated, never committed to git)\n")

    steps = [
        ("okx_api_key", "OKX API key (Trade permission)"),
        ("okx_api_secret", "OKX API secret"),
        ("okx_passphrase", "OKX passphrase"),
    ]
    for key, prompt in steps:
        current = ks.get(key, "")
        if current:
            print(f"  {prompt}: already set ({current[:4]}...)")
            skip = input("  Re-enter? [y/N]: ").strip().lower()
            if skip != "y":
                continue
        val = getpass.getpass(f"  {prompt}: ").strip()
        if val:
            ks.set(key, val)
            print(f"  ✓ Saved\n")

    # Demo mode
    demo = input("  Use OKX demo (paper trading)? [Y/n]: ").strip().lower()
    ks.set("okx_demo", "false" if demo == "n" else "true")
    print(f"  ✓ Demo mode: {'ON (paper)' if ks.get('okx_demo') == 'true' else 'OFF (live)'}\n")

    # Withdrawal keys (optional)
    print("  --- Withdrawal keys (optional, needed for wallet transfers) ---")
    w_keys = [
        ("okx_withdraw_key", "OKX withdrawal key (separate, WITH withdrawal permission)"),
        ("okx_withdraw_secret", "OKX withdrawal secret"),
        ("okx_withdraw_passphrase", "OKX withdrawal passphrase"),
        ("totp_secret", "TOTP secret (base32, from your Authenticator setup)"),
    ]
    for key, prompt in w_keys:
        val = getpass.getpass(f"  {prompt} (Enter to skip): ").strip()
        if val:
            ks.set(key, val)
            print(f"  ✓ Saved\n")

    # Daily cap
    cap = input("  Daily withdrawal cap USD [500]: ").strip()
    if not cap:
        cap = "500"
    ks.set("daily_cap_usd", cap)
    print(f"  ✓ Daily cap: ${cap}\n")

    print("=" * 60)
    s = ks.status()
    print(f"  Trading ready:   {'✓' if s['trading_ready'] else '✗'}")
    print(f"  Withdrawal ready: {'✓' if s['withdrawal_ready'] else '✗'}")
    print(f"\n  Run 'python -m omega.cli.keys test' to verify the connection.")
    print("=" * 60 + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description="OMEGA key/credential manager")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Show all keys and their set/unset status")
    sub.add_parser("status", help="Readiness summary")

    s = sub.add_parser("set", help="Set a key value")
    s.add_argument("key", type=str)
    s.add_argument("value", type=str, nargs="?", default="")

    g = sub.add_parser("get", help="Get a key value (prints to stdout)")
    g.add_argument("key", type=str)

    d = sub.add_parser("delete", help="Delete a key")
    d.add_argument("key", type=str)

    sub.add_parser("test", help="Verify credentials work (read-only API call)")
    sub.add_parser("wizard", help="Interactive setup")

    args = p.parse_args()
    if args.command == "list":
        cmd_list(args)
    elif args.command == "set":
        cmd_set(args)
    elif args.command == "get":
        cmd_get(args)
    elif args.command == "delete":
        cmd_delete(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "test":
        asyncio.run(cmd_test(args))
    elif args.command == "wizard":
        cmd_wizard(args)


if __name__ == "__main__":
    main()
