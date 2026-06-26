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
from omega.crowd_engine.engine import CrowdPositioningEngine
from omega.data_nexus.nexus import DataNexus
from omega.execution.blade import ExecutionBlade
from omega.execution.okx_executor import OKXExecutor
from omega.execution.sor import SmartOrderRouter
from omega.meta_cognition.meta import MetaCognition
from omega.regime.hmm_detector import RegimeDetector
from omega.regime.weight_router import RegimeWeightRouter
from omega.risk_aegis.aegis import RiskAegis
from omega.utils.events import CrowdPositioningEvent, MarketEvent, SignalEvent
from omega.utils.logger import get_logger

logger = get_logger("omega.orchestrator")


class OmegaOrchestrator:
    """Top-level coordinator. Owns all layers and the event loop."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or load_settings()
        # Layer 1 — DataNexus (venue-aware market feed)
        self.data_nexus = self._build_data_nexus()
        # Layer 1.5 — Crowd Positioning Engine (the contrarian brain)
        self.crowd_engine = CrowdPositioningEngine(
            symbols=self.settings.data_nexus.symbols,
        )
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
        # Layer 5 — venue selection: OKX if its creds are present, else Binance.
        self.execution_blade = self._build_execution_blade()
        # Wallet manager (withdrawals) — only on OKX for now
        self.wallet_manager = None
        if self.settings.venue == "okx":
            from omega.execution.wallet_manager import WalletManager
            executor = self.execution_blade.sor.get_venue("okx")
            if executor is not None:
                self.wallet_manager = WalletManager(
                    executor,
                    totp_secret=self.settings.omega_totp_secret,
                    daily_cap_usd=self.settings.omega_daily_cap_usd,
                )
        # Layer 6
        self.meta_cognition = MetaCognition(self.settings.meta_cognition)
        # State
        self._running = False
        self._last_regime: str = "unknown"
        self._last_crowd_regime: str = "neutral"
        self._last_crowd_components: dict = {}  # symbol -> latest crowd components (V4 attribution)
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
        await self.crowd_engine.start()
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
        await self.crowd_engine.stop()
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
        """Handle a MarketEvent: regime → crowd engine → risk → alpha swarm → risk → execution."""
        # 1. Regime detection (HMM)
        regime = self.regime_detector.on_market(event)
        if regime is not None and regime != self._last_regime:
            self._last_regime = regime
            self._apply_regime_weights()
        # 2. Crowd Positioning Engine — produces CrowdPositioningEvent when the
        #    crowd is at an extreme. This reconfigures agent weights (defund
        #    trend at a cascade-imminent extreme) and feeds the ContrarianAgent.
        crowd_event = self.crowd_engine.on_market(event)
        if crowd_event is not None:
            self._on_crowd_positioning(crowd_event)
        # 3. Feed Risk Aegis with market data so it can:
        #    - track last prices (used for position sizing in _process_signals)
        #    - feed the Monte Carlo return pool
        #    - feed the portfolio-heat correlation matrix
        #    - feed the kill-switch flash-crash + drawdown detectors
        # BUGFIX: previously this was never called, so _process_signals always
        # skipped every signal because portfolio_heat._last_prices was empty.
        self.risk_aegis.on_market(event)
        # 4. Update meta-cognition with price (for MFE/MAE tracking)
        self.meta_cognition.update_price(event.symbol, event.last_price)
        # 5. Alpha swarm produces signals
        signals = self.alpha_swarm.on_market(event)
        await self._process_signals(signals, event.timestamp)

    def _on_crowd_positioning(self, event: CrowdPositioningEvent) -> None:
        """React to a CrowdPositioningEvent: reconfigure regime weights if the
        crowd is at a cascade-imminent extreme, then feed the ContrarianAgent."""
        # Remember the latest components per symbol so that when a contrarian
        # trade closes we can attribute its PnL to the right signals (V4 tuning).
        self._last_crowd_components[event.symbol] = dict(event.components)
        crowd_regime = (
            f"crowd_cascade_{'long' if event.crowd_score > 0 else 'short'}"
            if event.regime_hint == "cascade_imminent"
            else "neutral"
        )
        if crowd_regime != self._last_crowd_regime:
            self._last_crowd_regime = crowd_regime
            if crowd_regime != "neutral":
                # Cascade override: defund trend, boost contrarian
                weights = self.weight_router.weights_for(crowd_regime)
                self.alpha_swarm.set_regime_weights(weights)
            else:
                # Restore the HMM-driven regime weights
                self._apply_regime_weights()
        # Feed the contrarian agent (and any agent that reacts to positioning)
        signals = self.alpha_swarm.on_positioning(event)
        if signals:
            asyncio.create_task(self._process_signals(signals, event.timestamp))

    def _build_execution_blade(self) -> ExecutionBlade:
        """Build the execution blade for the active venue (OKX or Binance)."""
        if self.settings.venue == "okx":
            executor = OKXExecutor(
                api_key=self.settings.okx_api_key,
                api_secret=self.settings.okx_api_secret,
                passphrase=self.settings.okx_passphrase,
                demo=self.settings.okx_demo,
            )
            sor = SmartOrderRouter(executors=[executor])
            blade = ExecutionBlade(self.settings.execution, sor=sor)
            logger.info(
                f"Execution venue: OKX ({'demo' if self.settings.okx_demo else 'live'})",
                extra={"component": "orchestrator"},
            )
            return blade
        return ExecutionBlade(
            self.settings.execution,
            binance_api_key=self.settings.binance_api_key,
            binance_api_secret=self.settings.binance_api_secret,
            binance_testnet=self.settings.binance_testnet,
        )

    def _build_data_nexus(self) -> DataNexus:
        """Build DataNexus with the venue-appropriate market feed."""
        if self.settings.venue == "okx":
            from omega.data_nexus.okx_feed import OKXWebSocketFeed
            okx_feed = OKXWebSocketFeed(symbols=self.settings.data_nexus.symbols, swap=True)
            return DataNexus(self.settings.data_nexus, binance_feed=okx_feed)
        return DataNexus(self.settings.data_nexus)

    def _apply_regime_weights(self) -> None:
        """Push the current HMM regime's weights into the debate chamber."""
        if self._last_regime and self._last_regime != "unknown":
            weights = self.weight_router.weights_for(self._last_regime)
            self.alpha_swarm.set_regime_weights(weights)

    def _lookup_crowd_components(self, symbol: str) -> dict:
        """Return the latest crowd components for a symbol (for V4 attribution)."""
        return self._last_crowd_components.get(symbol, {})

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
                    # V4: feed contrarian trade outcomes to the crowd engine's
                    # weight optimizer so fusion weights self-tune over time.
                    if closed.strategy == "contrarian":
                        components = closed.autopsy.get("crowd_components", {}) \
                            if closed.autopsy else {}
                        # Fall back to the signal metadata carried by the order
                        if not components:
                            components = self._lookup_crowd_components(closed.symbol)
                        if components:
                            self.crowd_engine.on_contrarian_trade_closed(
                                closed.realized_pnl_bps, components
                            )

    def stats(self) -> dict:
        return {
            "running": self._running,
            "regime": self._last_regime,
            "regime_confidence": self.regime_detector.confidence,
            "signals_processed": self._signal_count,
            "orders_sent": self._order_count,
            "fills_received": self._fill_count,
            "crowd_regime": self._last_crowd_regime,
            "crowd_engine": self.crowd_engine.stats(),
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
