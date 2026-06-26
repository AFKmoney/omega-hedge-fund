"""B10 — FlashCrashScanner: scans for flash crash precursors in real-time.

Flash crashes leave footprints: a rapid widening of the bid-ask spread combined
with a drop in depth (liquidity vacuum) precedes the actual crash by seconds.
We monitor spread × depth across venues and alert when the precursor pattern
appears — giving the bot time to flatten before the cascade.
"""
from __future__ import annotations
import time
from collections import deque
from typing import Deque, Dict, Tuple
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.flash_crash")

class FlashCrashScanner:
    """Scans for spread/depth anomalies that precede flash crashes."""
    def __init__(self, window: int = 60, spread_threshold: float = 3.0) -> None:
        self.window = window
        self.spread_threshold = spread_threshold  # std devs above mean
        self._spreads: Dict[str, Deque[float]] = {}
        self._depths: Dict[str, Deque[float]] = {}
        self._alerts: list = []

    def update(self, symbol: str, bid: float, ask: float, bid_qty: float, ask_qty: float) -> bool:
        if bid <= 0 or ask <= 0:
            return False
        spread_bps = (ask - bid) / ((ask + bid) / 2) * 10000
        depth = bid_qty + ask_qty
        if symbol not in self._spreads:
            self._spreads[symbol] = deque(maxlen=self.window)
            self._depths[symbol] = deque(maxlen=self.window)
        self._spreads[symbol].append(spread_bps)
        self._depths[symbol].append(depth)
        if len(self._spreads[symbol]) < 30:
            return False
        spreads = list(self._spreads[symbol])
        depths = list(self._depths[symbol])
        mean_spread = sum(spreads) / len(spreads)
        std_spread = (sum((s - mean_spread) ** 2 for s in spreads) / len(spreads)) ** 0.5
        z_score = (spread_bps - mean_spread) / (std_spread + 1e-9)
        mean_depth = sum(depths) / len(depths)
        depth_ratio = depth / (mean_depth + 1e-9)
        # Flash crash precursor: spread spikes (>3 std) AND depth drops (<50% of mean)
        is_precursor = z_score > self.spread_threshold and depth_ratio < 0.5
        if is_precursor:
            msg = (f"FLASH CRASH PRECURSOR {symbol}: spread z={z_score:.1f} "
                   f"depth={depth_ratio:.0%} of normal")
            self._alerts.append(msg)
            logger.warning(msg)
        return is_precursor

    @property
    def alerts(self) -> list:
        return self._alerts[-5:]

    def stats(self) -> dict:
        return {"name": "flash_crash_scanner", "alerts": self._alerts[-3:],
                "symbols_tracked": list(self._spreads.keys())}
