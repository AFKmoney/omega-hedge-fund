"""
MetaCognition — Layer 6 orchestrator.

Ties together TradeAutopsy + OnlineLearner + GeneticOptimizer. Receives
TradeClosedEvents from the Execution Blade, records them for autopsy, and
periodically:
    1. Runs LLM autopsy on the latest batch
    2. Triggers OnlineLearner retraining for underperforming agents
    3. Triggers GeneticOptimizer mutation for chronically failing agents
    4. Feeds findings back to the Risk Aegis (Kelly stats) and Alpha Swarm

This is the loop that makes OMEGA self-evolving.
"""

from __future__ import annotations

import asyncio
import time
from typing import List, Optional

from omega.config.settings import MetaCognitionSettings
from omega.meta_cognition.genetic_optimizer import GeneticOptimizer
from omega.meta_cognition.online_learning import OnlineLearner
from omega.meta_cognition.trade_autopsy import TradeAutopsy
from omega.utils.events import FillEvent, TradeClosedEvent
from omega.utils.logger import get_logger

logger = get_logger("omega.meta_cognition")


class MetaCognition:
    """Layer 6: self-evaluating evolution loop."""

    def __init__(
        self,
        settings: Optional[MetaCognitionSettings] = None,
        autopsy: Optional[TradeAutopsy] = None,
        online_learner: Optional[OnlineLearner] = None,
        genetic: Optional[GeneticOptimizer] = None,
    ) -> None:
        self.settings = settings or MetaCognitionSettings()
        self.autopsy = autopsy or TradeAutopsy(self.settings)
        self.online_learner = online_learner or OnlineLearner(self.settings)
        self.genetic = genetic or GeneticOptimizer(self.settings)
        self._open_positions: dict = {}  # symbol → entry info
        self._closed_trades: List[TradeClosedEvent] = []
        self._bg_task: Optional[asyncio.Task] = None

    async def start_background(self) -> None:
        if self._bg_task is None or self._bg_task.done():
            self._bg_task = asyncio.create_task(self._loop())

    async def stop_background(self) -> None:
        if self._bg_task is not None:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
            self._bg_task = None

    async def _loop(self) -> None:
        """Background loop: run autopsy periodically."""
        while True:
            try:
                await asyncio.sleep(60)
                await self.autopsy.maybe_run()
                # Check for genetic mutations
                mutations = self.genetic.maybe_evolve()
                if mutations:
                    logger.info(
                        f"Genetic mutations triggered: {list(mutations.keys())}",
                        extra={"component": "meta_cognition"},
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Meta-cognition loop error: {exc}")

    def on_fill(self, fill: FillEvent) -> Optional[TradeClosedEvent]:
        """
        Track open positions. When a closing fill arrives, emit a
        TradeClosedEvent and feed it to the autopsy queue.
        """
        sym = fill.symbol
        if sym not in self._open_positions:
            # Opening fill
            self._open_positions[sym] = {
                "side": fill.side,
                "entry_price": fill.fill_price,
                "qty": fill.qty,
                "entry_time": fill.timestamp,
                "strategy": "",
                "mfe_bps": 0.0,
                "mae_bps": 0.0,
                "max_price": fill.fill_price,
                "min_price": fill.fill_price,
            }
            return None
        # Closing fill — compute PnL
        pos = self._open_positions.pop(sym)
        direction = 1.0 if pos["side"].value == "BUY" else -1.0
        pnl = direction * (fill.fill_price - pos["entry_price"]) * pos["qty"]
        pnl_bps = direction * (fill.fill_price - pos["entry_price"]) / pos["entry_price"] * 10000.0
        # Approximate holding bars (would need bar counter in production)
        holding_bars = 60
        trade = TradeClosedEvent(
            symbol=sym,
            side=pos["side"],
            entry_price=pos["entry_price"],
            exit_price=fill.fill_price,
            qty=pos["qty"],
            entry_time=pos["entry_time"],
            exit_time=fill.timestamp,
            realized_pnl=pnl,
            realized_pnl_bps=pnl_bps,
            max_favorable_excursion_bps=pos["mfe_bps"],
            max_adverse_excursion_bps=pos["mae_bps"],
            holding_bars=holding_bars,
            strategy=pos.get("strategy", ""),
            exit_reason=fill.metadata.get("exit_reason", "signal_exit") if hasattr(fill, "metadata") else "signal_exit",
        )
        self._closed_trades.append(trade)
        self.autopsy.record_trade(trade)
        # Update genetic fitness
        self.genetic.update_fitness(pos.get("strategy", ""), pnl_bps / 100.0)
        logger.info(
            f"Trade closed: {sym} pnl_bps={pnl_bps:+.1f}",
            extra={
                "component": "meta_cognition",
                "symbol": sym,
                "trade_id": trade.trade_id,
            },
        )
        return trade

    def update_price(self, symbol: str, price: float) -> None:
        """Track MFE/MAE for open positions."""
        if symbol not in self._open_positions:
            return
        pos = self._open_positions[symbol]
        if pos["side"].value == "BUY":
            pos["mfe_bps"] = max(
                pos["mfe_bps"],
                (price - pos["entry_price"]) / pos["entry_price"] * 10000.0,
            )
            pos["mae_bps"] = min(
                pos["mae_bps"],
                (price - pos["entry_price"]) / pos["entry_price"] * 10000.0,
            )
        else:
            pos["mfe_bps"] = max(
                pos["mfe_bps"],
                (pos["entry_price"] - price) / pos["entry_price"] * 10000.0,
            )
            pos["mae_bps"] = min(
                pos["mae_bps"],
                (pos["entry_price"] - price) / pos["entry_price"] * 10000.0,
            )

    def stats(self) -> dict:
        return {
            "autopsy": self.autopsy.stats(),
            "online_learner": self.online_learner.stats(),
            "genetic": self.genetic.stats(),
            "open_positions": len(self._open_positions),
            "closed_trades": len(self._closed_trades),
            "total_pnl": sum(t.realized_pnl for t in self._closed_trades),
        }
