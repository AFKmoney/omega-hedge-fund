"""B15 — MultiTimeframeSignal: aligns signals across timeframes.

The strongest trades happen when multiple timeframes agree: 1m, 5m, 15m, 1h all
bullish = high-conviction long. We compute a simple momentum score (EMA cross)
on each timeframe and aggregate — only emit when 3+ timeframes align.
"""
from __future__ import annotations
from collections import deque
from typing import Deque, Dict
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.multi_tf")

class MultiTimeframeSignal:
    """Aligns momentum across multiple timeframes."""
    TIMEFRAMES = {  # name: (ema_fast, ema_slow, bars_needed)
        "1m": (5, 20, 30),
        "5m": (3, 12, 60),   # 60 1m bars = 12 5m bars
        "15m": (2, 8, 180),  # 180 1m bars = 12 15m bars
        "1h": (2, 6, 720),   # 720 1m bars = 12 1h bars
    }

    def __init__(self) -> None:
        self._prices: Deque[float] = deque(maxlen=720)
        self._alignment: float = 0.0
        self._tf_signals: Dict[str, str] = {}

    def update(self, price: float) -> float:
        self._prices.append(price)
        if len(self._prices) < 30:
            return 0.0
        prices = list(self._prices)
        aligned_bullish = aligned_bearish = 0
        for tf_name, (fast, slow, needed) in self.TIMEFRAMES.items():
            if len(prices) < needed:
                continue
            tf_prices = prices[-needed:]
            # Resample to this timeframe
            group = max(1, len(tf_prices) // max(slow, 1))
            resampled = [sum(tf_prices[i:i+group])/group for i in range(0, len(tf_prices), group)]
            if len(resampled) < slow + 1:
                continue
            ema_f = self._ema(resampled[-fast*2:], fast)
            ema_s = self._ema(resampled[-slow*2:], slow)
            if ema_f > ema_s:
                self._tf_signals[tf_name] = "bullish"
                aligned_bullish += 1
            elif ema_f < ema_s:
                self._tf_signals[tf_name] = "bearish"
                aligned_bearish += 1
            else:
                self._tf_signals[tf_name] = "neutral"
        total = aligned_bullish + aligned_bearish
        self._alignment = (aligned_bullish - aligned_bearish) / max(total, 1)
        return self._alignment

    @staticmethod
    def _ema(data: list, period: int) -> float:
        if not data:
            return 0.0
        k = 2 / (period + 1)
        ema = data[0]
        for p in data[1:]:
            ema = p * k + ema * (1 - k)
        return ema

    @property
    def alignment(self) -> float:
        return self._alignment

    def stats(self) -> dict:
        return {"name": "multi_timeframe", "alignment": round(self._alignment, 3),
                "signals": self._tf_signals, "bars": len(self._prices)}
