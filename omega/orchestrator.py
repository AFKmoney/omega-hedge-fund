"""
OmegaOrchestrator — top-level coordinator that wires all 6 layers together.

Event flow:
    1. DataNexus emits MarketEvent / NewsEvent / MacroEvent / OnChainEvent
    2. RegimeDetector classifies regime → updates Alpha Swarm weights
    3. AlphaSwarm processes events → emits SignalEvents
    4. DebateChamber consolidates signals → emits consolidated SignalEvents
    5. RiskAegis sizes positions → emits OrderEvents
    6. ExecutionBlade executes orders → emits FillEvents
    7. MetaCognition autopsies closed trades → retrains/mutates agents

The orchestrator owns the asyncio event loop and runs all of this concurrently.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from omega.alpha_swarm.swarm import AlphaSwarm
from omega.config.settings import Settings, load_settings
from omega.data_nexus.nexus import DataNexus
from omega.execution.blade import ExecutionBlade
from omega.meta_cognition.meta import MetaCognition
from omega.regime.hmm_detector import RegimeDetector
from omega.regime.weight_router import RegimeWeightRouter
from omega.risk_aegis.aegis import RiskAegis
from omega.utils.events import MarketEvent, SignalEvent
from omega.utils.logger import get_logger

logger = get_logger("omega.orchestrator")


class OmegaOrchestrator:
    """Top-level coordinator. Owns all layers and the event loop."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or load_settings()
        # Layer 1
        self.data_nexus = DataNexus(self.settings.data_nexus)
        # Layer 2
        self.alpha_swarm = AlphaSwarm(
            symbols=self.settings.data_nexus.symbols,
            alpha_settings=self.settings.alpha_swarm,
            regime_settings=self.settings.regime,
        )
        # Layer 3
        self.regime_detector = RegimeDetector(self.settings.regime)
        self.weight_router = RegimeWeightRouter(self.settings.regime)
        # Layer 4
        self.risk_aegis = RiskAegis(self.settings.risk)
        # Layer 5
        self.execution_blade = ExecutionBlade(
            self.settings.execution,
            binance_api_key=self.settings.binance_api_key,
            binance_api_secret=self.settings.binance_api_secret,
            binance_testnet=self.settings.binance_testnet,
        )
        # Layer 6
        self.meta_cognition = MetaCognition(self.settings.meta_cognition)
        # State
        self._running = False
        self._last_regime: str = "unknown"
        self._signal_count = 0
        self._order_count = 0
        self._fill_count = 0
        self._loop_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start OMEGA: bring up all layers and the event loop."""
        if self._running:
            return
        self._running = True
        logger.info(
            "=" * 60 + "\n"
            "OMEGA starting up\n"
            f"  Environment: {self.settings.env}\n"
            f"  Symbols: {self.settings.data_nexus.symbols}\n"
            f"  Live trading: {self.settings.is_live}\n"
            f"  Initial equity: ${self.risk_aegis.equity:,.2f}\n"
            + "=" * 60,
            extra={"component": "orchestrator"},
        )
        # Bring up layers in order
        await self.alpha_swarm.start()
        await self.meta_cognition.start_background()
        await self.data_nexus.start()
        # Spawn the main event loop
        self._loop_task = asyncio.create_task(self._main_loop())
        logger.info("OMEGA is live. Press Ctrl+C to shut down.",
                    extra={"component": "orchestrator"})

    async def stop(self) -> None:
        """Graceful shutdown."""
        if not self._running:
            return
        self._running = False
        logger.info("OMEGA shutting down...", extra={"component": "orchestrator"})
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(f"Loop task shutdown error: {exc}")
        await self.alpha_swarm.stop()
        await self.meta_cognition.stop_background()
        await self.data_nexus.stop()
        await self.execution_blade.close()
        logger.info("OMEGA stopped.", extra={"component": "orchestrator"})

    async def _main_loop(self) -> None:
        """Consume events from DataNexus, route to each layer."""
        queue = self.data_nexus.subscribe()
        try:
            while self._running:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                # Route event to each layer
                try:
                    if isinstance(event, MarketEvent):
                        await self._on_market(event)
                    # News/Macro/OnChain go to AlphaSwarm agents
                    from omega.utils.events import NewsEvent, MacroEvent, OnChainEvent
                    if isinstance(event, NewsEvent):
                        signals = self.alpha_swarm.on_news(event)
                        await self._process_signals(signals, event.timestamp)
                    elif isinstance(event, MacroEvent):
                        signals = self.alpha_swarm.on_macro(event)
                        await self._process_signals(signals, event.timestamp)
                    elif isinstance(event, OnChainEvent):
                        signals = self.alpha_swarm.on_onchain(event)
                        await self._process_signals(signals, event.timestamp)
                except Exception as exc:
                    logger.exception(f"Event processing error: {exc}")
        except asyncio.CancelledError:
            logger.info("Main loop cancelled")
            raise

    async def _on_market(self, event: MarketEvent) -> None:
        """Handle a MarketEvent: regime → risk tracking → alpha swarm → risk → execution."""
        # 1. Regime detection
        regime = self.regime_detector.on_market(event)
        if regime is not None and regime != self._last_regime:
            self._last_regime = regime
            weights = self.weight_router.weights_for(regime)
            self.alpha_swarm.set_regime_weights(weights)
        # 2. Feed Risk Aegis with market data so it can:
        #    - track last prices (used for position sizing in _process_signals)
        #    - feed the Monte Carlo return pool
        #    - feed the portfolio-heat correlation matrix
        #    - feed the kill-switch flash-crash + drawdown detectors
        # BUGFIX: previously this was never called, so _process_signals always
        # skipped every signal because portfolio_heat._last_prices was empty.
        self.risk_aegis.on_market(event)
        # 3. Update meta-cognition with price (for MFE/MAE tracking)
        self.meta_cognition.update_price(event.symbol, event.last_price)
        # 4. Alpha swarm produces signals
        signals = self.alpha_swarm.on_market(event)
        await self._process_signals(signals, event.timestamp)

    async def _process_signals(self, signals, timestamp: str) -> None:
        """Pass signals through Risk Aegis → Execution Blade."""
        if not signals:
            return
        # Need current price for sizing — use last market event per symbol
        for signal in signals:
            self._signal_count += 1
            # Get current price from the Risk Aegis portfolio heat tracker
            price = self.risk_aegis.portfolio_heat._last_prices.get(signal.symbol, 0.0)
            if price <= 0:
                logger.warning(
                    f"No price available for {signal.symbol}, skipping signal",
                    extra={"component": "orchestrator", "symbol": signal.symbol},
                )
                continue
            # Risk Aegis → OrderEvent
            order = self.risk_aegis.on_signal(signal, price)
            if order is None:
                continue
            # Execution Blade
            self._order_count += 1
            fill = await self.execution_blade.submit(order, arrival_price=price)
            if fill is not None:
                self._fill_count += 1
                # Meta-Cognition tracks fills → produces TradeClosedEvent on close
                closed = self.meta_cognition.on_fill(fill)
                if closed is not None:
                    self.risk_aegis.on_trade_closed(closed)

    def stats(self) -> dict:
        return {
            "running": self._running,
            "regime": self._last_regime,
            "regime_confidence": self.regime_detector.confidence,
            "signals_processed": self._signal_count,
            "orders_sent": self._order_count,
            "fills_received": self._fill_count,
            "alpha_swarm": self.alpha_swarm.stats(),
            "risk_aegis": self.risk_aegis.stats(),
            "execution": self.execution_blade.stats(),
            "meta_cognition": self.meta_cognition.stats(),
            "data_nexus_kafka": self.data_nexus.bus.is_kafka,
            "vector_store_milvus": self.data_nexus.vector_store.is_milvus,
        }

    async def run_forever(self) -> None:
        """Start and run until interrupted."""
        await self.start()
        try:
            while self._running:
                await asyncio.sleep(60)
                logger.info(
                    f"OMEGA stats: {json.dumps(self.stats(), default=str)[:500]}",
                    extra={"component": "orchestrator"},
                )
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.stop()
