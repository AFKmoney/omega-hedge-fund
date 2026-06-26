"""Layer 2 — Alpha Swarm: multi-agent mixture-of-experts."""
from omega.alpha_swarm.base import AlphaAgent
from omega.alpha_swarm.ppo_agent import PPOAgent, PPONetworks
from omega.alpha_swarm.llm_macro_agent import LLMMacroAgent
from omega.alpha_swarm.stat_arb_agent import StatArbAgent
from omega.alpha_swarm.contrarian_agent import ContrarianAgent
from omega.alpha_swarm.swarm import AlphaSwarm
from omega.alpha_swarm.debate_chamber import DebateChamber

__all__ = [
    "AlphaAgent",
    "PPOAgent",
    "PPONetworks",
    "LLMMacroAgent",
    "StatArbAgent",
    "ContrarianAgent",
    "AlphaSwarm",
    "DebateChamber",
]
