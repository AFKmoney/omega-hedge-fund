"""B24 — SentimentNLP: lightweight crypto-specific sentiment from public news.

Instead of calling an LLM for every headline (expensive, slow), we use a fast
keyword-based sentiment scorer tuned for crypto markets. Positive crypto keywords
(bullish, breakout, adoption, ETF approved) vs negative (hack, ban, crash,
lawsuit, dump). Faster than LLM, free, and surprisingly accurate for binary
bullish/bearish classification.
"""
from __future__ import annotations
import re
from collections import deque
from typing import Deque
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.sentiment_nlp")

BULLISH_KEYWORDS = {
    "bullish", "breakout", "rally", "surge", "adoption", "approved", "etf",
    "institutional", "accumulate", "support", "bounce", "recovery", "pump",
    "long", "buy", "upgrade", "partnership", "integration", "launch",
    "milestone", "all-time high", "ath", "inflow", "demand", "growth",
}
BEARISH_KEYWORDS = {
    "bearish", "crash", "dump", "hack", "exploit", "ban", "lawsuit", "sec",
    "sell-off", "liquidation", "breakdown", "support broken", "death cross",
    "fear", "panic", "outflow", "fraud", "investigation", "delist", "halt",
    "bankruptcy", "insolvency", " rug", "scam", "warning", "risk",
}
WEIGHTED = {"hack": -3.0, "exploit": -3.0, "etf approved": 3.0, "ban": -2.5,
            "lawsuit": -2.0, "sec": -1.5, "institutional": 2.0, "adoption": 2.0}

class SentimentNLP:
    """Fast keyword-based crypto sentiment scorer."""
    def __init__(self, window: int = 50) -> None:
        self._scores: Deque[float] = deque(maxlen=window)
        self._aggregate: float = 0.0
        self._processed: int = 0

    def score_text(self, text: str) -> float:
        """Score one headline. Returns [-1, +1]."""
        lower = text.lower()
        score = 0.0
        matches = 0
        for word, weight in WEIGHTED.items():
            if word in lower:
                score += weight
                matches += 1
        for word in BULLISH_KEYWORDS:
            if word in lower:
                score += 1.0
                matches += 1
        for word in BEARISH_KEYWORDS:
            if word in lower:
                score -= 1.0
                matches += 1
        if matches == 0:
            return 0.0
        # Normalize by matches, clamp
        final = max(-1.0, min(1.0, score / max(matches, 3)))
        self._scores.append(final)
        self._aggregate = sum(self._scores) / len(self._scores)
        self._processed += 1
        return final

    @property
    def aggregate_sentiment(self) -> float:
        """Rolling average sentiment across all headlines."""
        return self._aggregate

    def stats(self) -> dict:
        return {"name": "sentiment_nlp",
                "aggregate": round(self._aggregate, 3),
                "headlines_processed": self._processed,
                "direction": "bullish" if self._aggregate > 0.1 else (
                    "bearish" if self._aggregate < -0.1 else "neutral")}
