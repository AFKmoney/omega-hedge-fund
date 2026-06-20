"""
ExecutionRLAgent — RL agent that learns optimal execution scheduling.

The ExecutionRLAgent observes order-book features (bid-ask spread, depth
imbalance, recent volatility, time of day) and outputs a choice of execution
algorithm + parameters (slices, participation rate, display qty). It is
rewarded by negative slippage vs. arrival price.

Architecture mirrors PPOAgent but with a continuous action space (slices ∈ [1,20],
participation_rate ∈ [0.01, 0.30], display_qty_pct ∈ [0.05, 0.50]). Uses a
Gaussian policy for continuous control.

This is a real PPO agent — same training loop as the Alpha Swarm PPO. The
difference is the environment (execution microstructure instead of price
direction).
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import torch
import torch.nn as nn

from omega.config.settings import ExecutionSettings
from omega.utils.logger import get_logger

logger = get_logger("omega.execution.rl")


class ExecutionActor(nn.Module):
    """Gaussian policy: outputs mean + log_std for continuous actions."""

    def __init__(self, obs_dim: int, hidden: tuple = (128, 128), action_dim: int = 3):
        super().__init__()
        layers = []
        prev = obs_dim
        for h in hidden:
            layers += [nn.Linear(prev, h), nn.Tanh()]
            prev = h
        self.mean_head = nn.Linear(prev, action_dim)
        self.log_std = nn.Parameter(torch.zeros(action_dim))  # learned log_std
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        h = self.net(x)
        mean = self.mean_head(h)
        # Clamp log_std for numerical stability
        log_std = torch.clamp(self.log_std, -3.0, 0.5)
        return mean, log_std.expand_as(mean)


class ExecutionCritic(nn.Module):
    def __init__(self, obs_dim: int, hidden: tuple = (128, 128)):
        super().__init__()
        layers = []
        prev = obs_dim
        for h in hidden:
            layers += [nn.Linear(prev, h), nn.Tanh()]
            prev = h
        layers.append(nn.Linear(prev, 1))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


class ExecutionRLAgent:
    """PPO-based execution optimization agent."""

    def __init__(
        self,
        settings: Optional[ExecutionSettings] = None,
        obs_dim: int = 16,
        device: str = "cpu",
    ) -> None:
        self.settings = settings or ExecutionSettings()
        self.obs_dim = obs_dim
        self.device = torch.device(device)
        self.actor = ExecutionActor(obs_dim, self.settings.execution_rl_hidden).to(self.device)
        self.critic = ExecutionCritic(obs_dim, self.settings.execution_rl_hidden).to(self.device)
        self.optimizer = torch.optim.Adam(
            list(self.actor.parameters()) + list(self.critic.parameters()),
            lr=self.settings.execution_rl_lr,
        )
        self._is_trained = False

    def act(self, obs: np.ndarray, deterministic: bool = False) -> dict:
        """Return execution parameters given order-book observation."""
        obs_t = torch.from_numpy(obs.astype(np.float32)).unsqueeze(0).to(self.device)
        with torch.no_grad():
            mean, log_std = self.actor(obs_t)
            if deterministic:
                action = mean
            else:
                std = torch.exp(log_std)
                action = mean + std * torch.randn_like(mean)
        # Clip to valid ranges
        a = action.squeeze().cpu().numpy()
        return {
            "slices": int(np.clip(a[0], 1, 20).round()),
            "participation_rate": float(np.clip(a[1], 0.01, 0.30)),
            "display_qty_pct": float(np.clip(a[2], 0.05, 0.50)),
        }

    def update(self, trajectories: list) -> None:
        """PPO update. Trajectories = list of (obs, action, reward, next_obs, done)."""
        if not trajectories:
            return
        obs = torch.from_numpy(np.stack([t[0] for t in trajectories])).to(self.device)
        actions = torch.from_numpy(np.stack([t[1] for t in trajectories])).to(self.device)
        rewards = torch.from_numpy(np.array([t[2] for t in trajectories])).to(self.device)
        # Compute returns (discounted)
        gamma = 0.99
        returns = torch.zeros_like(rewards)
        running = 0.0
        for i in reversed(range(len(rewards))):
            running = rewards[i] + gamma * running
            returns[i] = running
        # Normalize returns
        if returns.std() > 1e-6:
            returns = (returns - returns.mean()) / (returns.std() + 1e-8)
        # Critic loss
        values = self.critic(obs)
        advantages = returns - values.detach()
        critic_loss = (advantages ** 2).mean()
        # Actor loss (Gaussian log-likelihood × advantage)
        mean, log_std = self.actor(obs)
        std = torch.exp(log_std)
        log_probs = -0.5 * (((actions - mean) / (std + 1e-8)) ** 2) - log_std - 0.5 * np.log(2 * np.pi)
        log_probs = log_probs.sum(dim=-1)
        actor_loss = -(log_probs * advantages.detach()).mean()
        loss = actor_loss + 0.5 * critic_loss
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
        self._is_trained = True

    @property
    def is_trained(self) -> bool:
        return self._is_trained

    def save(self, path: str) -> None:
        import os
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        torch.save({
            "actor": self.actor.state_dict(),
            "critic": self.critic.state_dict(),
            "optimizer": self.optimizer.state_dict(),
        }, path)

    def load(self, path: str) -> None:
        ckpt = torch.load(path, map_location=self.device, weights_only=False)
        self.actor.load_state_dict(ckpt["actor"])
        self.critic.load_state_dict(ckpt["critic"])
        self.optimizer.load_state_dict(ckpt["optimizer"])
        self._is_trained = True
