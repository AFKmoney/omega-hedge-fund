#!/usr/bin/env python3
"""
OMEGA Live Trading Script
=========================

Runs the full OMEGA orchestrator against live Binance data. Defaults to
DRY-RUN mode (no API credentials → orders logged but not submitted).
Set BINANCE_API_KEY and BINANCE_API_SECRET env vars to enable live order
submission.

Usage:
    python scripts/live_trade.py                          # dry-run, default symbols
    python scripts/live_trade.py --symbols BTCUSDT,ETHUSDT
    BINANCE_API_KEY=xxx BINANCE_API_SECRET=yyy python scripts/live_trade.py  # live
    BINANCE_TESTNET=true BINANCE_API_KEY=xxx ... python scripts/live_trade.py  # testnet

Press Ctrl+C to gracefully shut down. The kill switch will cancel all open
orders automatically on shutdown.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omega.config.settings import load_settings
from omega.orchestrator import OmegaOrchestrator
from omega.utils.logger import get_logger

logger = get_logger("omega.scripts.live_trade")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OMEGA live trading")
    p.add_argument("--symbols", type=str, default=None,
                   help="Comma-separated list (e.g. BTCUSDT,ETHUSDT)")
    p.add_argument("--log-level", type=str, default="INFO")
    p.add_argument("--stats-interval-sec", type=int, default=30)
    return p.parse_args()


async def run() -> None:
    args = parse_args()
    # Override env vars from CLI args
    if args.symbols:
        import os
        os.environ["OMEGA_SYMBOLS"] = args.symbols
    if args.log_level:
        import os
        os.environ["OMEGA_LOG_LEVEL"] = args.log_level
    settings = load_settings()
    orchestrator = OmegaOrchestrator(settings)
    # Handle Ctrl+C
    stop_event = asyncio.Event()

    def _signal_handler(sig, frame):
        logger.info("Shutdown signal received", extra={"component": "scripts"})
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    await orchestrator.start()
    try:
        while not stop_event.is_set():
            await asyncio.sleep(args.stats_interval_sec)
            stats = orchestrator.stats()
            logger.info(
                f"OMEGA stats: regime={stats['regime']} "
                f"signals={stats['signals_processed']} "
                f"orders={stats['orders_sent']} fills={stats['fills_received']} "
                f"equity=${stats['risk_aegis']['equity']:,.2f} "
                f"pnl={stats['risk_aegis']['pnl_pct']:+.2f}%",
                extra={"component": "scripts"},
            )
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
