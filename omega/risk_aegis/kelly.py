"""
KellyPositionSizer — Asymmetric Kelly Criterion position sizing.

Computes optimal position size from win rate + win/loss ratio, then applies:
    1. Fractional Kelly (default 0.25 = quarter-Kelly) to reduce variance
    2. Asymmetric cap: smaller size when recent loss streak detected
    3. Per-trade risk cap: max 1% of equity per position
    4. Volatility scaling: size inversely proportional to ATR

Formula:
    f* = (p*b - q) / b
    where:
        p = win probability (from agent confidence)
        b = win/loss ratio (from take_profit / stop_loss)
        q = 1 - p
    size_usd = equity * kelly_fraction * f* * vol_scale
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import numpy as np

from omega.config.settings import RiskAegisSettings
from omega.utils.events import SignalEvent, Side
from omega.utils.logger import get_logger

logger = get_logger("omega.risk_aegis.kelly")


@dataclass
class KellyResult:
    size_usd: float
    size_qty: float
    kelly_fraction_raw: float       # raw f*
    kelly_fraction_applied: float   # after fractional + caps
    win_probability: float
    win_loss_ratio: float
    vol_scale: float
    rejected_reason: Optional[str] = None


class KellyPositionSizer:
    """Kelly Criterion position sizing with fractional + asymmetric caps."""

    def __init__(self, settings: Optional[RiskAegisSettings] = None) -> None:
        self.settings = settings or RiskAegisSettings()
        # Rolling win/loss stats per agent
        self._agent_wins: dict = {}
        self._agent_losses: dict = {}
        self._agent_avg_win_bps: dict = {}
        self._agent_avg_loss_bps: dict = {}

    def update_stats(
        self,
        agent: str,
        pnl_bps: float,
    ) -> None:
        """Called by Meta-Cognition when a trade closes. Updates Kelly inputs."""
        if pnl_bps > 0:
            self._agent_wins[agent] = self._agent_wins.get(agent, 0) + 1
            n = self._agent_wins[agent]
            self._agent_avg_win_bps[agent] = (
                (self._agent_avg_win_bps.get(agent, 0.0) * (n - 1) + pnl_bps) / n
            )
        else:
            self._agent_losses[agent] = self._agent_losses.get(agent, 0) + 1
            n = self._agent_losses[agent]
            self._agent_avg_loss_bps[agent] = (
                (self._agent_avg_loss_bps.get(agent, 0.0) * (n - 1) + abs(pnl_bps)) / n
            )

    def size(
        self,
        signal: SignalEvent,
        equity: float,
        price: float,
        current_atr_bps: float = 100.0,
    ) -> KellyResult:
        """
        Compute position size for a signal.
        Returns KellyResult with rejection reason if size is zero.
        """
        # 1. Win probability: use agent confidence as prior, blend with historical win rate.
        # BUGFIX: the previous two lines were:
        #     agent = signal.metadata.get("contributing_agents", [signal.agent])[0] if signal.metadata else signal.agent
        #     agent = signal.agent if signal.agent == "debate_chamber" else signal.agent
        # The second line unconditionally overwrote the first (both branches
        # returned signal.agent), so per-agent Kelly stats were always recorded
        # under "debate_chamber" instead of the originating alpha agent.
        # Now: attribute to the contributing agent when the Debate Chamber
        # consolidated the signal, else to the signal's own agent.
        if signal.agent == "debate_chamber" and signal.metadata.get("contributing_agents"):
            agents_list = signal.metadata["contributing_agents"]
            agent = agents_list[0] if isinstance(agents_list, list) and agents_list else signal.agent
        else:
            agent = signal.agent
        hist_wins = self._agent_wins.get(agent, 0)
        hist_losses = self._agent_losses.get(agent, 0)
        total = hist_wins + hist_losses
        if total >= 30:
            # Blend 50/50 with historical
            hist_p = hist_wins / total
            p = 0.5 * signal.confidence + 0.5 * hist_p
        else:
            p = signal.confidence
        p = float(np.clip(p, 0.05, 0.95))
        q = 1.0 - p
        # 2. Win/loss ratio from signal's TP/SL
        b = signal.take_profit_bps / max(signal.stop_loss_bps, 1.0)
        b = float(np.clip(b, 0.1, 10.0))
        # 3. Kelly fraction
        f_star = (p * b - q) / b
        f_star = float(np.clip(f_star, 0.0, 1.0))
        if f_star <= 0:
            return KellyResult(
                size_usd=0.0, size_qty=0.0,
                kelly_fraction_raw=f_star, kelly_fraction_applied=0.0,
                win_probability=p, win_loss_ratio=b, vol_scale=1.0,
                rejected_reason="negative_kelly",
            )
        # 4. Fractional Kelly (reduce variance)
        f_applied = f_star * self.settings.kelly_fraction
        # 5. Asymmetric loss-streak penalty
        if total >= 10:
            recent = self._agent_losses.get(agent, 0) / max(total, 1)
            if recent > 0.5:
                # Penalize agents with >50% recent losses
                f_applied *= (1.0 - recent)
        # 6. Per-trade risk cap
        max_risk_usd = equity * (self.settings.max_per_trade_risk_pct / 100.0)
        risk_per_unit = (signal.stop_loss_bps / 10000.0) * price
        if risk_per_unit > 0:
            max_qty_by_risk = max_risk_usd / risk_per_unit
        else:
            max_qty_by_risk = float("inf")
        # 7. Volatility scaling
        ref_atr = 100.0  # bps reference
        vol_scale = float(np.clip(ref_atr / max(current_atr_bps, 10.0), 0.25, 2.0))
        # 8. Final size
        size_usd = equity * f_applied * vol_scale
        size_qty = size_usd / price if price > 0 else 0.0
        # Cap by per-trade risk
        if size_qty > max_qty_by_risk:
            size_qty = max_qty_by_risk
            size_usd = size_qty * price
        # Hard floor: if size too small to be worth executing, reject.
        # The floor scales with equity: 0.1% of equity, but at least a minimum
        # notional that can be set via OMEGA_MIN_NOTIONAL_USD (default $2 so
        # micro-accounts of $50-100 can still trade).
        min_notional = float(os.getenv("OMEGA_MIN_NOTIONAL_USD", "2.0"))
        floor = max(equity * 0.001, min_notional)
        if size_usd < floor:
            return KellyResult(
                size_usd=0.0, size_qty=0.0,
                kelly_fraction_raw=f_star, kelly_fraction_applied=f_applied,
                win_probability=p, win_loss_ratio=b, vol_scale=vol_scale,
                rejected_reason="size_too_small",
            )
        logger.info(
            f"Kelly sized {signal.symbol} {signal.side.value}: "
            f"qty={size_qty:.4f} usd={size_usd:.2f} f*={f_star:.3f} p={p:.2f} b={b:.2f}",
            extra={
                "component": "risk_aegis.kelly",
                "symbol": signal.symbol,
                "agent": agent,
            },
        )
        return KellyResult(
            size_usd=size_usd,
            size_qty=size_qty,
            kelly_fraction_raw=f_star,
            kelly_fraction_applied=f_applied,
            win_probability=p,
            win_loss_ratio=b,
            vol_scale=vol_scale,
        )
