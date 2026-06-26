"""B11 — VolumeProfile: builds a volume-at-price histogram.

Shows WHERE the most trading happened at what price. High-volume nodes (HVN)
act as magnets (price tends to revisit). Low-volume nodes (LVN) act as voids
(price passes through quickly). POC (Point of Control) = the price with the
most volume = strongest support/resistance.
"""
from __future__ import annotations
from collections import defaultdict
from typing import Dict, Tuple
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.volume_profile")

class VolumeProfile:
    """Volume-at-price histogram for support/resistance detection."""
    def __init__(self, bucket_size_bps: float = 50.0, max_buckets: int = 100) -> None:
        self.bucket_size_bps = bucket_size_bps
        self.max_buckets = max_buckets
        self._profile: Dict[int, float] = defaultdict(float)
        self._total_vol: float = 0.0
        self._poc: float = 0.0  # point of control price

    def update(self, price: float, volume: float) -> None:
        if price <= 0 or volume <= 0:
            return
        bucket = int(price / (price * self.bucket_size_bps / 10000))
        self._profile[bucket] += volume
        self._total_vol += volume
        # Update POC
        if self._profile[bucket] > self._profile.get(int(self._poc / (self._poc * self.bucket_size_bps / 10000 + 1)), 0):
            self._poc = price

    @property
    def point_of_control(self) -> float:
        return self._poc

    def value_area(self, pct: float = 0.70) -> Tuple[float, float]:
        """Return (low, high) of the value area (where pct% of volume traded)."""
        if not self._profile:
            return (0.0, 0.0)
        sorted_buckets = sorted(self._profile.items(), key=lambda x: -x[1])
        target = self._total_vol * pct
        cumulative = 0.0
        prices = []
        for bucket, vol in sorted_buckets:
            cumulative += vol
            prices.append(bucket)
            if cumulative >= target:
                break
        if not prices:
            return (0.0, 0.0)
        return (min(prices), max(prices))

    def stats(self) -> dict:
        va_low, va_high = self.value_area()
        return {"name": "volume_profile", "poc": round(self._poc, 2),
                "value_area": [va_low, va_high], "buckets": len(self._profile),
                "total_volume": round(self._total_vol, 2)}
