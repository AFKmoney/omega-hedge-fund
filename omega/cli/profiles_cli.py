#!/usr/bin/env python3
"""
OMEGA profile manager — switch between key sets instantly.

Usage:
    python -m omega.cli.profiles_cli add live-okx
    python -m omega.cli.profiles_cli list
    python -m omega.cli.profiles_cli use live-okx
    python -m omega.cli.profiles_cli show live-okx
    python -m omega.cli.profiles_cli export live-okx keys.txt
    python -m omega.cli.profiles_cli import new-profile keys.txt
    python -m omega.cli.profiles_cli delete demo-okx
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from omega.config.profiles import get_profile_manager, ProfileManager
from omega.config.keystore import _FIELDS


def cmd_list(args) -> None:
    pm = get_profile_manager()
    profiles = pm.list_all()
    print(f"\n{'='*60}")
    print(f"  OMEGA Profiles")
    print(f"{'='*60}\n")
    if not profiles:
        print("  (no profiles yet — run 'add' to create one)\n")
        return
    for p in profiles:
        mark = "▶" if p["active"] else " "
        demo = " [demo]" if p["demo"] else " [live]"
        print(f"  {mark} {p['name']:20} venue={p['venue']:8} "
              f"fields={p['field_count']:2}{demo}")
    print()


def cmd_add(args) -> None:
    pm = get_profile_manager()
    name = args.name
    print(f"\n  Creating profile '{name}' — enter values (Enter to skip):\n")
    fields = {}
    for profile_group, field_defs in _FIELDS.items():
        print(f"  [{profile_group.upper()}]")
        for fname, fdesc in field_defs.items():
            if "secret" in fname or "passphrase" in fname or "totp" in fname:
                val = getpass.getpass(f"    {fdesc}: ").strip()
            else:
                val = input(f"    {fdesc}: ").strip()
            if val:
                fields[fname] = val
        print()
    if pm.add(name, fields):
        print(f"  ✓ Profile '{name}' saved with {len(fields)} fields")
        if args.use:
            pm.use(name)
            print(f"  ✓ Switched to '{name}'")
    else:
        print("  ✗ Failed to create profile")
        sys.exit(1)


def cmd_use(args) -> None:
    pm = get_profile_manager()
    if pm.use(args.name):
        print(f"  ✓ Active profile: {args.name}")
    else:
        print(f"  ✗ Profile '{args.name}' not found")
        sys.exit(1)


def cmd_show(args) -> None:
    pm = get_profile_manager()
    profile = pm.show(args.name)
    if not profile:
        print(f"  ✗ Profile '{args.name}' not found")
        sys.exit(1)
    print(f"\n  Profile: {args.name}\n  {'─'*40}")
    for k, v in profile.items():
        print(f"  {k:30} {v}")
    print()


def cmd_export(args) -> None:
    pm = get_profile_manager()
    if pm.export_profile(args.name, args.file):
        print(f"  ✓ Exported '{args.name}' to {args.file}")
        print(f"  ⚠️  This file contains REAL secrets — handle carefully!")
    else:
        print(f"  ✗ Profile '{args.name}' not found")
        sys.exit(1)


def cmd_import(args) -> None:
    pm = get_profile_manager()
    if pm.import_profile(args.name, args.file):
        print(f"  ✓ Imported profile '{args.name}' from {args.file}")
    else:
        print(f"  ✗ Import failed (file not found or no valid fields)")
        sys.exit(1)


def cmd_delete(args) -> None:
    pm = get_profile_manager()
    if pm.delete(args.name):
        print(f"  ✓ Deleted profile '{args.name}'")
    else:
        print(f"  ✗ Profile '{args.name}' not found")
        sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser(description="OMEGA profile manager")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all profiles")

    a = sub.add_parser("add", help="Create a new profile (interactive)")
    a.add_argument("name", type=str)
    a.add_argument("--use", action="store_true", help="Switch to this profile after creating")

    u = sub.add_parser("use", help="Switch active profile")
    u.add_argument("name", type=str)

    s = sub.add_parser("show", help="Show profile fields (masked)")
    s.add_argument("name", type=str)

    e = sub.add_parser("export", help="Export profile to text file")
    e.add_argument("name", type=str)
    e.add_argument("file", type=str)

    i = sub.add_parser("import", help="Import profile from text file")
    i.add_argument("name", type=str)
    i.add_argument("file", type=str)

    d = sub.add_parser("delete", help="Delete a profile")
    d.add_argument("name", type=str)

    args = p.parse_args()
    {
        "list": cmd_list, "add": cmd_add, "use": cmd_use, "show": cmd_show,
        "export": cmd_export, "import": cmd_import, "delete": cmd_delete,
    }[args.command](args)


if __name__ == "__main__":
    main()
