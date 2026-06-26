"""B5 — DepegAlert: monitors stablecoins for depeg events.

When a stablecoin (USDT, USDC, DAI) loses its $1 peg, it signals a liquidity
stress event that often precedes a crypto market crash (Silicon Valley Bank
crash → USDC depeg → BTC dump). We monitor the stablecoin prices across venues
and alert when any deviates from $1 by >0.3%.
"""
from __future__ import annotations
from typing import Dict
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.depeg")

class DepegAlert:
    """Monitors stablecoin peg stability."""
    STABLECOINS = ("USDT", "USDC", "DAI", "FRAX", "TUSD", "USDD")
    THRESHOLD = 0.003  # 0.3% deviation from $1

    def __init__(self) -> None:
        self._prices: Dict[str, float] = {}
        self._alerts: list = []

    def update_price(self, coin: str, price: float) -> None:
        coin = coin.upper()
        if coin not in self.STABLECOINS:
            return
        self._prices[coin] = price
        deviation = abs(price - 1.0)
        if deviation > self.THRESHOLD:
            direction = "below" if price < 1.0 else "above"
            alert = f"DEPEG: {coin} at ${price:.4f} ({direction} peg by {deviation*100:.2f}%)"
            if alert not in self._alerts[-3:]:  # avoid spam
                self._alerts.append(alert)
                logger.warning(alert)

    @property
    def is_stressed(self) -> bool:
        return any(abs(p - 1.0) > self.THRESHOLD for p in self._prices.values())

    def stats(self) -> dict:
        return {"name": "depeg_alert", "prices": self._prices,
                "alerts": self._alerts[-3:], "is_stressed": self.is_stressed}
