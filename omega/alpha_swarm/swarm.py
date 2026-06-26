"""
AlphaSwarm — Layer 2 orchestrator.

Owns all AlphaAgent instances, dispatches Data Nexus events to them, and
forwards their SignalEvents to the DebateChamber. Emits the chamber's
consolidated signals downstream to the Risk Aegis.

Event flow:
    Data Nexus → AlphaSwarm.on_event() → agents[].on_market() → signals
    signals → DebateChamber.submit() → consolidated signal → emit
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from omega.alpha_swarm.base import AlphaAgent
from omega.alpha_swarm.contrarian_agent import ContrarianAgent
from omega.alpha_swarm.debate_chamber import DebateChamber
from omega.alpha_swarm.llm_macro_agent import LLMMacroAgent
from omega.alpha_swarm.ppo_agent import PPOAgent
from omega.alpha_swarm.stat_arb_agent import StatArbAgent
from omega.config.settings import AlphaSwarmSettings, RegimeSettings
from omega.utils.events import (
    CrowdPositioningEvent, MacroEvent, MarketEvent, NewsEvent, OnChainEvent,
    SignalEvent,
)
from omega.utils.logger import get_logger

logger = get_logger("omega.alpha_swarm")


class AlphaSwarm:
    """Layer 2: multi-agent mixture-of-experts."""

    def __init__(
        self,
        symbols: tuple,
        alpha_settings: Optional[AlphaSwarmSettings] = None,
        regime_settings: Optional[RegimeSettings] = None,
        agents: Optional[List[AlphaAgent]] = None,
        debate: Optional[DebateChamber] = None,
    ) -> None:
        self.symbols = symbols
        self.alpha_settings = alpha_settings or AlphaSwarmSettings()
        self.regime_settings = regime_settings or RegimeSettings()
        self.agents: List[AlphaAgent] = agents if agents is not None else self._default_agents()
        self.debate = debate or DebateChamber(
            alpha_settings=self.alpha_settings,
            regime_settings=self.regime_settings,
        )
        self._consolidated_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    def _default_agents(self) -> List[AlphaAgent]:
        """Construct the default swarm: PPO trend + PPO meanrev + LLM macro +
        stat-arb + contrarian (fades crowd-positioning extremes)."""
        return [
            PPOAgent(self.symbols, mode="trend", settings=self.alpha_settings),
            PPOAgent(self.symbols, mode="meanrev", settings=self.alpha_settings),
            LLMMacroAgent(self.symbols, settings=self.alpha_settings),
            StatArbAgent(self.symbols, settings=self.alpha_settings),
            ContrarianAgent(self.symbols),
        ]

    async def start(self) -> None:
        """Initialize any async background tasks (LLM polling loop, etc.)."""
        for agent in self.agents:
            if isinstance(agent, LLMMacroAgent):
                await agent.start_background()
        logger.info(
            f"AlphaSwarm started with {len(self.agents)} agents: "
            f"{[a.name for a in self.agents]}",
            extra={"component": "alpha_swarm"},
        )

    async def stop(self) -> None:
        for agent in self.agents:
            if isinstance(agent, LLMMacroAgent):
                await agent.stop_background()

    def set_regime_weights(self, weights: dict) -> None:
        """Called by Regime Detector when market regime changes."""
        self.debate.set_agent_weights(weights)

    def on_market(self, event: MarketEvent) -> List[SignalEvent]:
        """Fan out market event to all agents, collect raw signals, debate."""
        raw_signals: List[SignalEvent] = []
        for agent in self.agents:
            try:
                raw_signals.extend(agent.on_market(event))
            except Exception as exc:
                logger.warning(
                    f"Agent {agent.name} crashed on market event: {exc}",
                    extra={"component": "alpha_swarm", "agent": agent.name},
                )
        return self._submit_to_debate(raw_signals)

    def on_news(self, event: NewsEvent) -> List[SignalEvent]:
        raw = []
        for agent in self.agents:
            raw.extend(agent.on_news(event))
        return self._submit_to_debate(raw)

    def on_macro(self, event: MacroEvent) -> List[SignalEvent]:
        raw = []
        for agent in self.agents:
            raw.extend(agent.on_macro(event))
        return self._submit_to_debate(raw)

    def on_onchain(self, event: OnChainEvent) -> List[SignalEvent]:
        raw = []
        for agent in self.agents:
            raw.extend(agent.on_onchain(event))
        return self._submit_to_debate(raw)

    def on_positioning(self, event: CrowdPositioningEvent) -> List[SignalEvent]:
        """Route a CrowdPositioningEvent to the ContrarianAgent and any other
        agent that reacts to crowd positioning."""
        raw_signals: List[SignalEvent] = []
        for agent in self.agents:
            handler = getattr(agent, "on_positioning", None)
            if handler is None:
                continue
            try:
                raw_signals.extend(handler(event))
            except Exception as exc:
                logger.warning(
                    f"Agent {agent.name} crashed on positioning event: {exc}",
                    extra={"component": "alpha_swarm", "agent": agent.name},
                )
        return self._submit_to_debate(raw_signals)

    def _submit_to_debate(self, raw_signals: List[SignalEvent]) -> List[SignalEvent]:
        """Submit each raw signal to the debate chamber; collect decisions."""
        decisions: List[SignalEvent] = []
        for sig in raw_signals:
            decision = self.debate.submit(sig)
            if decision is not None:
                decisions.append(decision)
                # Also enqueue for any async consumer
                try:
                    self._consolidated_queue.put_nowait(decision)
                except asyncio.QueueFull:
                    pass
        return decisions

    async def consolidated_stream(self):
        """Async iterator over consolidated signals (for Risk Aegis to consume)."""
        while True:
            sig = await self._consolidated_queue.get()
            yield sig

    def stats(self) -> dict:
        return {
            "agents": [a.stats() for a in self.agents],
            "debate": self.debate.stats(),
        }
