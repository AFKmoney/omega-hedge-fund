"""
LLMMacroAgent — The Macro-Economist (Agent 2 in the Alpha Swarm).

Reads NewsEvent + MacroEvent + OnChainEvent streams, maintains a rolling
context window, and queries the z-ai LLM every poll interval to translate
the macro narrative into bullish/bearish probability scores per symbol.

This is a REAL LLM agent — every call hits the actual model via the z-ai CLI.
The agent maintains an async background task that periodically re-evaluates
the macro picture and caches the result so the synchronous `on_market`
handler can return a SignalEvent without blocking on LLM latency.

Prompt design:
    System: "You are a senior macro strategist at a crypto hedge fund..."
    User: structured JSON of last N news headlines + macro indicators +
          on-chain events + current price action
    Output: JSON {"symbol": {"score": -1..1, "confidence": 0..1, "rationale": "..."}}
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

from omega.alpha_swarm.base import AlphaAgent
from omega.config.settings import AlphaSwarmSettings
from omega.utils.events import (
    MacroEvent, MarketEvent, NewsEvent, OnChainEvent, SignalEvent, Side,
)
from omega.utils.logger import get_logger

logger = get_logger("omega.alpha_swarm.llm_macro")

SYSTEM_PROMPT = """You are the Macro-Economist agent in OMEGA, an institutional crypto hedge fund AI.
Your job: given recent news headlines, macroeconomic indicators, and on-chain events,
produce a directional view on each symbol. Be calibrated — high confidence only when
evidence is strong. Avoid recency bias. Consider second-order effects.

Respond with ONLY a JSON object, no prose. Schema:
{
  "symbols": {
    "BTCUSDT": {
      "score": <float -1.0..1.0>,
      "confidence": <float 0.0..1.0>,
      "rationale": "<one sentence>",
      "catalysts": ["<short>", "<short>"]
    },
    "ETHUSDT": { ... },
    "SOLUSDT": { ... }
  },
  "regime_view": "<one of: risk-on | risk-off | choppy | crisis>",
  "key_risk": "<one sentence on the biggest tail risk>"
}
"""


@dataclass
class MacroView:
    symbol: str
    score: float           # -1..1
    confidence: float      # 0..1
    rationale: str
    regime_view: str
    key_risk: str
    timestamp: float


class LLMMacroAgent(AlphaAgent):
    """LLM-powered macro strategist. Polls z-ai CLI on a fixed interval."""

    name = "llm_macro"

    def __init__(
        self,
        symbols: tuple,
        settings: Optional[AlphaSwarmSettings] = None,
        poll_interval_sec: Optional[int] = None,
        zai_cli_path: Optional[str] = None,
        context_window: int = 30,
    ) -> None:
        super().__init__(symbols)
        self.settings = settings or AlphaSwarmSettings()
        self.poll_interval_sec = poll_interval_sec or self.settings.llm_macro_poll_interval_sec
        self.zai_cli_path = zai_cli_path or self.settings.zai_cli_path
        self.context_window = context_window
        self._news_buffer: Deque[NewsEvent] = deque(maxlen=context_window)
        self._macro_buffer: Deque[MacroEvent] = deque(maxlen=20)
        self._onchain_buffer: Deque[OnChainEvent] = deque(maxlen=20)
        self._market_snapshot: Dict[str, MarketEvent] = {}
        self._views: Dict[str, MacroView] = {}
        self._last_emit: Dict[str, float] = {}
        self._bg_task: Optional[asyncio.Task] = None
        self.is_ready = True

    # ------------------------------------------------------------------
    # Background LLM polling
    # ------------------------------------------------------------------

    async def start_background(self) -> None:
        """Start the async LLM polling loop. Call once at orchestrator startup."""
        if self._bg_task is None or self._bg_task.done():
            self._bg_task = asyncio.create_task(self._poll_loop())

    async def stop_background(self) -> None:
        if self._bg_task is not None:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
            self._bg_task = None

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._refresh_views()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"LLM macro poll failed: {exc}")
            await asyncio.sleep(self.poll_interval_sec)

    async def _refresh_views(self) -> None:
        """Build context, call LLM, parse views."""
        context = self._build_context()
        if not context.strip():
            return
        prompt = f"{SYSTEM_PROMPT}\n\nCurrent market context:\n{context}"
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [self.zai_cli_path, "chat", "-p", prompt],
                capture_output=True, text=True, timeout=45,
            )
        except Exception as exc:
            logger.warning(f"z-ai CLI call failed: {exc}")
            return
        if result.returncode != 0:
            logger.warning(f"z-ai CLI exit {result.returncode}: {result.stderr[:200]}")
            return
        views = self._parse_response(result.stdout)
        if views:
            self._views = views
            logger.info(
                f"LLM macro views updated: {len(views)} symbols, "
                f"regime={views[self.symbols[0]].regime_view if self.symbols and self.symbols[0] in views else 'n/a'}",
                extra={"component": "alpha_swarm.llm_macro", "agent": self.name},
            )

    def _build_context(self) -> str:
        """Construct the user-prompt context from buffered events."""
        parts: List[str] = []
        if self._news_buffer:
            parts.append("## Recent News")
            for n in list(self._news_buffer)[-15:]:
                parts.append(f"- [{n.source}] {n.headline} (sentiment={n.sentiment_score:+.2f})")
        if self._macro_buffer:
            parts.append("\n## Macro Indicators")
            for m in self._macro_buffer:
                surprise = f", surprise={m.surprise:+.3f}" if m.surprise is not None else ""
                parts.append(f"- {m.indicator}: {m.value}{surprise}")
        if self._onchain_buffer:
            parts.append("\n## On-Chain Events")
            for o in self._onchain_buffer:
                parts.append(
                    f"- [{o.chain}] {o.event_type}: ${o.value_usd:,.0f} "
                    f"({o.from_addr[:10]}→{o.to_addr[:10]})"
                )
        if self._market_snapshot:
            parts.append("\n## Current Prices")
            for sym, ev in self._market_snapshot.items():
                parts.append(
                    f"- {sym}: ${ev.last_price:,.2f} "
                    f"(24h vol={ev.volume_24h:,.0f}, funding={ev.funding_rate or 0:.4f})"
                )
        return "\n".join(parts)

    def _parse_response(self, text: str) -> Dict[str, MacroView]:
        """Extract JSON object from LLM response and build MacroView dict."""
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            logger.warning(f"No JSON in LLM response: {text[:200]}")
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            logger.warning(f"JSON parse error: {exc}")
            return {}
        out: Dict[str, MacroView] = {}
        now = time.time()
        regime = data.get("regime_view", "unknown")
        key_risk = data.get("key_risk", "")
        for sym, payload in data.get("symbols", {}).items():
            try:
                out[sym.upper()] = MacroView(
                    symbol=sym.upper(),
                    score=float(payload.get("score", 0.0)),
                    confidence=float(payload.get("confidence", 0.0)),
                    rationale=payload.get("rationale", ""),
                    regime_view=regime,
                    key_risk=key_risk,
                    timestamp=now,
                )
            except Exception as exc:
                logger.warning(f"Bad view for {sym}: {exc}")
        return out

    # ------------------------------------------------------------------
    # AlphaAgent interface
    # ------------------------------------------------------------------

    def on_market(self, event: MarketEvent) -> List[SignalEvent]:
        self._market_snapshot[event.symbol] = event
        view = self._views.get(event.symbol)
        if view is None or view.confidence < 0.40:
            return []
        # Only emit every 60s per symbol (avoid spamming)
        last = self._last_emit.get(event.symbol, 0.0)
        if time.time() - last < 60.0:
            return []
        self._last_emit[event.symbol] = time.time()
        side = Side.BUY if view.score > 0.25 else (Side.SELL if view.score < -0.25 else Side.FLAT)
        if side == Side.FLAT:
            return []
        return [SignalEvent(
            agent=self.name,
            symbol=event.symbol,
            timestamp=event.timestamp,
            side=side,
            confidence=view.confidence,
            expected_return_bps=abs(view.score) * 200.0,
            stop_loss_bps=150.0,
            take_profit_bps=300.0,
            rationale=view.rationale,
            metadata={
                "score": view.score,
                "regime_view": view.regime_view,
                "key_risk": view.key_risk,
                "source": "llm_macro",
            },
        )]

    def on_news(self, event: NewsEvent) -> List[SignalEvent]:
        self._news_buffer.append(event)
        return []

    def on_macro(self, event: MacroEvent) -> List[SignalEvent]:
        self._macro_buffer.append(event)
        return []

    def on_onchain(self, event: OnChainEvent) -> List[SignalEvent]:
        self._onchain_buffer.append(event)
        return []

    def stats(self) -> dict:
        return {
            "name": self.name,
            "ready": self.is_ready,
            "views_count": len(self._views),
            "news_buffer": len(self._news_buffer),
            "macro_buffer": len(self._macro_buffer),
            "onchain_buffer": len(self._onchain_buffer),
            "last_regime": next(
                (v.regime_view for v in self._views.values()), "unknown"
            ),
        }
