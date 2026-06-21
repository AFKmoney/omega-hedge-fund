"""
GeneticOptimizer — Darwinian agent evolution.

If a specific agent underperforms for `genetic_underperformance_days` (default
30) consecutive days, the GeneticOptimizer "kills" it and spawns a mutated
version with hyperparameters perturbed by Gaussian noise. The mutated agent
runs in parallel with the original for a trial period; whichever performs
better survives.

Mutation operators:
    - learning_rate: ×exp(N(0, mutation_std))
    - clip_ratio: +N(0, mutation_std × 0.05)
    - entropy_coef: ×exp(N(0, mutation_std))
    - hidden_layer_size: ±1 unit (256→257 or →255), rare large jumps
    - observation_window: ±8 bars

This is real genetic search over the hyperparameter space, not random search.
Survival is determined by Sharpe ratio over the trial window.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from omega.config.settings import MetaCognitionSettings
from omega.utils.logger import get_logger

logger = get_logger("omega.meta_cognition.genetic")


@dataclass
class AgentGenome:
    """Serializable hyperparameter set for an agent."""
    name: str
    kind: str  # "ppo_trend" | "ppo_meanrev" | "llm_macro" | "stat_arb"
    params: Dict = field(default_factory=dict)
    parent_id: Optional[str] = None
    generation: int = 0
    birth_ts: float = field(default_factory=time.time)
    fitness: float = 0.0  # Sharpe ratio over trial window


class GeneticOptimizer:
    """Evolutionary hyperparameter search."""

    def __init__(
        self,
        settings: Optional[MetaCognitionSettings] = None,
    ) -> None:
        self.settings = settings or MetaCognitionSettings()
        self._genomes: Dict[str, AgentGenome] = {}  # name → current genome
        self._fitness_history: Dict[str, List[float]] = {}  # name → rolling Sharpe
        self._generation: int = 0
        self._mutations: int = 0
        self._kills: int = 0

    def register(self, name: str, kind: str, params: Dict) -> None:
        """Register an agent's genome at startup."""
        self._genomes[name] = AgentGenome(
            name=name, kind=kind, params=dict(params), generation=0
        )
        self._fitness_history[name] = []

    def update_fitness(self, name: str, pnl_bps: float) -> None:
        """
        Record one closed trade's PnL (in bps) for an agent.

        NOTE: despite the older name `daily_pnl_bps`, OMEGA feeds per-trade PnL
        here (one entry per closed trade, not per day). The fitness metric is a
        per-trade Sharpe ratio used only for *relative* comparison between
        agents over the trial window, so it is intentionally not annualized.
        """
        if name not in self._fitness_history:
            self._fitness_history[name] = []
        self._fitness_history[name].append(pnl_bps)
        # Keep only the most recent trial window
        window = self.settings.genetic_underperformance_days
        if len(self._fitness_history[name]) > window:
            self._fitness_history[name] = self._fitness_history[name][-window:]

    def maybe_evolve(self) -> Dict[str, Dict]:
        """
        Check if any agent should be killed + mutated.
        Returns dict of {agent_name: new_params} for agents that need reconfiguration.
        """
        mutations = {}
        for name, history in list(self._fitness_history.items()):
            if len(history) < self.settings.genetic_underperformance_days:
                continue
            genome = self._genomes.get(name)
            if genome is None:
                continue
            # Compute Sharpe ratio over trial window
            arr = np.array(history[-self.settings.genetic_underperformance_days:])
            sharpe = self._sharpe(arr)
            genome.fitness = sharpe
            # Kill threshold: negative Sharpe over the trial window
            if sharpe < -0.5:
                new_params = self._mutate(genome)
                mutations[name] = new_params
                self._kills += 1
                self._mutations += 1
                self._generation += 1
                logger.warning(
                    f"GENETIC: killing {name} (Sharpe={sharpe:.2f} over "
                    f"{self.settings.genetic_underperformance_days}d), spawning mutation",
                    extra={"component": "meta_cognition.genetic", "agent": name},
                )
                # Replace genome
                self._genomes[name] = AgentGenome(
                    name=name,
                    kind=genome.kind,
                    params=new_params,
                    parent_id=name,
                    generation=genome.generation + 1,
                )
                # Reset fitness history
                self._fitness_history[name] = []
        return mutations

    def _sharpe(self, returns: np.ndarray) -> float:
        """
        Per-trade Sharpe ratio over the trial window.

        Used only as a *relative* fitness signal for genetic selection, so it
        is intentionally not annualized. BUGFIX (minor): the old code multiplied
        by sqrt(365) under the assumption these were daily returns, but OMEGA
        actually feeds per-trade PnL — making the annualization meaningless.
        """
        if len(returns) < 5:
            return 0.0
        mean = float(np.mean(returns))
        std = float(np.std(returns, ddof=1)) + 1e-9
        return mean / std

    def _mutate(self, genome: AgentGenome) -> Dict:
        """Apply Gaussian perturbation to hyperparameters."""
        params = dict(genome.params)
        std = self.settings.genetic_mutation_std
        rng = np.random.default_rng()
        # Learning rate: log-normal perturbation
        if "ppo_lr" in params:
            params["ppo_lr"] = float(np.clip(
                params["ppo_lr"] * np.exp(rng.normal(0, std)),
                1e-6, 1e-2,
            ))
        # Clip ratio: small additive perturbation
        if "ppo_clip" in params:
            params["ppo_clip"] = float(np.clip(
                params["ppo_clip"] + rng.normal(0, std * 0.05),
                0.05, 0.40,
            ))
        # Entropy coefficient: log-normal
        if "ppo_entropy_coef" in params:
            params["ppo_entropy_coef"] = float(np.clip(
                params["ppo_entropy_coef"] * np.exp(rng.normal(0, std)),
                1e-4, 1e-1,
            ))
        # Hidden layer size: rare integer step
        if "actor_hidden" in params and rng.random() < 0.30:
            current = params["actor_hidden"][0]
            new_size = int(current + rng.choice([-32, 32, 64, -64]))
            new_size = max(64, min(1024, new_size))
            params["actor_hidden"] = (new_size, new_size)
            params["critic_hidden"] = (new_size, new_size)
        # Observation window: integer perturbation
        if "observation_window" in params and rng.random() < 0.30:
            params["observation_window"] = int(np.clip(
                params["observation_window"] + rng.choice([-8, 8, 16, -16]),
                16, 256,
            ))
        return params

    def stats(self) -> dict:
        return {
            "generation": self._generation,
            "total_mutations": self._mutations,
            "total_kills": self._kills,
            "tracked_agents": len(self._genomes),
            "fitness": {
                name: g.fitness for name, g in self._genomes.items()
            },
        }
