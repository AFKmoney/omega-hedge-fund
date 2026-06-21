"""
TradeAutopsy — LLM-driven post-trade analysis.

After every N closed trades (default 10), an LLM analyzes the batch to
identify *why* trades won or lost. Categorizes outcomes:
    - good_entry: entry timing was correct
    - bad_entry: entry was poorly timed
    - slippage_dominant: P&L was eaten by slippage
    - news_catalyst: outcome was driven by news event
    - regime_mismatch: strategy was wrong for the prevailing regime
    - stop_too_tight: stop loss was hit before thesis played out
    - take_profit_too_early: TP was hit but trade would have run further

Output: structured findings dict, also persisted to the data dir for the
OnlineLearner and GeneticOptimizer to consume.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional

from omega.config.settings import MetaCognitionSettings
from omega.utils.events import TradeClosedEvent
from omega.utils.logger import get_logger

logger = get_logger("omega.meta_cognition.autopsy")

SYSTEM_PROMPT = """You are the Meta-Cognition module of OMEGA, an autonomous crypto hedge fund AI.
Your job: analyze closed trades and identify the ROOT CAUSE of each outcome.
Be specific, blame techniques not luck. Use the provided trade data plus the
metadata field which may contain agent rationale, market conditions, and signal metadata.

Respond with ONLY a JSON array (no prose). Each element:
{
  "trade_id": "<id>",
  "outcome_category": "<one of: good_entry | bad_entry | slippage_dominant | news_catalyst | regime_mismatch | stop_too_tight | take_profit_too_early | kill_switch_forced | other>",
  "root_cause": "<one sentence>",
  "improvement_suggestion": "<one sentence, actionable>",
  "confidence_in_analysis": <float 0..1>
}
"""


class TradeAutopsy:
    """LLM-powered batch trade analysis."""

    def __init__(
        self,
        settings: Optional[MetaCognitionSettings] = None,
        zai_cli_path: str = "z-ai",
        data_dir: Optional[str] = None,
    ) -> None:
        from omega.config.settings import _default_data_dir
        self.settings = settings or MetaCognitionSettings()
        self.zai_cli_path = zai_cli_path
        self.data_dir = str(Path(data_dir) if data_dir else _default_data_dir())
        os.makedirs(self.data_dir, exist_ok=True)
        self._recent_trades: Deque[TradeClosedEvent] = deque(
            maxlen=self.settings.autopsy_max_trades
        )
        self._autopsy_count = 0
        self._last_findings: List[Dict] = []

    def record_trade(self, trade: TradeClosedEvent) -> None:
        """Add a closed trade to the autopsy queue."""
        self._recent_trades.append(trade)

    async def maybe_run(self) -> Optional[List[Dict]]:
        """Run autopsy if enough new trades have closed."""
        if len(self._recent_trades) < self.settings.autopsy_interval_trades:
            return None
        trades = list(self._recent_trades)
        self._recent_trades.clear()
        findings = await self._analyze_batch(trades)
        self._last_findings = findings
        self._autopsy_count += 1
        self._persist(trades, findings)
        return findings

    async def _analyze_batch(self, trades: List[TradeClosedEvent]) -> List[Dict]:
        """Call the LLM to analyze a batch of trades."""
        # Build the user prompt with trade data
        trade_lines = []
        for t in trades:
            trade_lines.append(json.dumps({
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "side": t.side.value,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "qty": t.qty,
                "realized_pnl_bps": t.realized_pnl_bps,
                "max_favorable_excursion_bps": t.max_favorable_excursion_bps,
                "max_adverse_excursion_bps": t.max_adverse_excursion_bps,
                "holding_bars": t.holding_bars,
                "strategy": t.strategy,
                "regime": t.regime,
                "exit_reason": t.exit_reason,
            }))
        user_prompt = f"{SYSTEM_PROMPT}\n\nAnalyze these {len(trades)} closed trades:\n" + "\n".join(trade_lines)
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [self.zai_cli_path, "chat", "-p", user_prompt],
                capture_output=True, text=True, timeout=60,
            )
        except Exception as exc:
            logger.warning(f"Autopsy LLM call failed: {exc}")
            return []
        if result.returncode != 0:
            logger.warning(f"z-ai exit {result.returncode}: {result.stderr[:200]}")
            return []
        # Parse JSON array from response
        match = re.search(r"\[.*\]", result.stdout, re.DOTALL)
        if not match:
            logger.warning(f"No JSON array in autopsy response: {result.stdout[:200]}")
            return []
        try:
            findings = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            logger.warning(f"Autopsy JSON parse error: {exc}")
            return []
        logger.info(
            f"Trade autopsy complete: {len(findings)} trades analyzed. "
            f"Sample: {findings[0].get('root_cause', '')[:80] if findings else 'no findings'}",
            extra={"component": "meta_cognition.autopsy"},
        )
        return findings

    def _persist(self, trades: List[TradeClosedEvent], findings: List[Dict]) -> None:
        """Save autopsy results to disk for the OnlineLearner / GeneticOptimizer."""
        out_path = os.path.join(self.data_dir, f"autopsy_{int(time.time())}.json")
        payload = {
            "timestamp": time.time(),
            "trades": [
                {
                    "trade_id": t.trade_id,
                    "symbol": t.symbol,
                    "strategy": t.strategy,
                    "regime": t.regime,
                    "pnl_bps": t.realized_pnl_bps,
                    "exit_reason": t.exit_reason,
                    "mfe_bps": t.max_favorable_excursion_bps,
                    "mae_bps": t.max_adverse_excursion_bps,
                }
                for t in trades
            ],
            "findings": findings,
        }
        try:
            with open(out_path, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as exc:
            logger.warning(f"Failed to persist autopsy: {exc}")

    @property
    def last_findings(self) -> List[Dict]:
        return self._last_findings

    def stats(self) -> dict:
        return {
            "autopsies_run": self._autopsy_count,
            "pending_trades": len(self._recent_trades),
            "last_findings_count": len(self._last_findings),
        }
