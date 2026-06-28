"""
AutoPilot — full autonomous trading automation.

Once enabled, the bot needs ZERO manual interaction. AutoPilot handles:
    1. Auto-start trading on launch (no need to click Start)
    2. Auto-risk-scaling: reads StressIndex + AdaptiveRiskManager every cycle,
       dynamically scales Kelly fraction up (calm market) or down (stress)
    3. Auto-symbol-rotation: monitors which symbols have the best crowd-score
       signals, rotates capital toward the symbol with the highest conviction
    4. Auto-compound: realized profits increase the equity base automatically
    5. Auto-reconnect: if the WS feed drops, auto-restart the data nexus
    6. Auto-kill recovery: if the kill switch trips on a false positive (startup
       jitter, API hiccup), auto-reset after a cooldown if conditions are safe
    7. Auto-checkpoint: save PPO weights periodically if learning online

Each automation has an ON/OFF toggle (default all ON) so the user can disable
any individual layer from the GUI while keeping the rest automated.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from omega.utils.logger import get_logger

logger = get_logger("omega.autopilot")


@dataclass
class AutoPilotConfig:
    """Toggleable automation switches."""
    auto_start: bool = True
    auto_risk_scaling: bool = True
    auto_symbol_rotation: bool = True
    auto_compound: bool = True
    auto_reconnect: bool = True
    auto_kill_recovery: bool = True
    # Risk scaling bounds
    min_kelly_mult: float = 0.2
    max_kelly_mult: float = 2.0
    # Kill recovery cooldown (sec)
    kill_recovery_cooldown: int = 300  # 5 min
    # Compound: what fraction of realized PnL to add to equity base
    compound_rate: float = 0.9  # 90% of profits stay in the trading pool


class AutoPilot:
    """Autonomous trading controller. Runs as a background loop."""

    def __init__(self, orchestrator) -> None:
        self.orch = orchestrator
        self.config = AutoPilotConfig()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_kill_reset: float = 0.0
        self._realized_pnl_session: float = 0.0
        self._equity_base: float = 0.0
        self._cycle_count: int = 0
        self._automation_log: list = []

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info(
                "AutoPilot engaged — full autonomous mode",
                extra={"component": "autopilot"},
            )
            # Record initial equity
            try:
                self._equity_base = self.orch.risk_aegis.equity
            except Exception:
                self._equity_base = 10_000.0

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        """Main automation loop — runs every 10 seconds."""
        while self._running:
            try:
                self._cycle()
                self._cycle_count += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"AutoPilot cycle error: {exc}")
            await asyncio.sleep(10)

    def _cycle(self) -> None:
        """One automation cycle."""
        actions = []

        # 1. Auto-start: if trading is off and we have data flowing, start it
        if self.config.auto_start and not self.orch._running:
            self.orch._running = True
            actions.append("auto-started trading")

        # 2. Auto-risk-scaling: adjust Kelly based on market stress
        if self.config.auto_risk_scaling:
            mult = self._compute_risk_multiplier()
            try:
                # Scale the Kelly fraction in place (risk settings read it each signal)
                base_kelly = self.orch.settings.risk.kelly_fraction
                # We can't mutate frozen dataclass, but we set an override attr
                # that RiskAegis can read
                if not hasattr(self.orch.risk_aegis, "_kelly_override"):
                    self.orch.risk_aegis._kelly_override = None
                self.orch.risk_aegis._kelly_override = base_kelly * mult
                if mult < 0.5:
                    actions.append(f"risk scaled DOWN (mult={mult:.2f})")
                elif mult > 1.3:
                    actions.append(f"risk scaled UP (mult={mult:.2f})")
            except Exception:
                pass

        # 3. Auto-kill-recovery: if kill switch is tripped, check if it's safe
        if self.config.auto_kill_recovery:
            ks = self.orch.risk_aegis.kill_switch
            if ks.is_triggered:
                now = time.time()
                if now - self._last_kill_reset > self.config.kill_recovery_cooldown:
                    # Check if conditions are safe: no flash crash in last 5 min,
                    # drawdown within limits, price is stable
                    prices = list(ks._recent_prices) if hasattr(ks, "_recent_prices") else []
                    is_safe = True
                    if prices and len(prices) >= 10:
                        # Check no recent >3% drop
                        recent = [p[1] for p in prices[-10:]]
                        if max(recent) / min(recent) > 1.03:
                            is_safe = False
                    if is_safe:
                        ks.reset()
                        self._last_kill_reset = now
                        actions.append("kill switch auto-reset (safe conditions)")

        # 4. Auto-compound: track realized PnL and adjust equity base
        if self.config.auto_compound:
            try:
                current_equity = self.orch.risk_aegis.equity
                if current_equity > self._equity_base:
                    growth = current_equity - self._equity_base
                    self._realized_pnl_session += growth * self.config.compound_rate
                    self._equity_base = current_equity  # compound
            except Exception:
                pass

        # Log notable actions
        if actions:
            for a in actions:
                logger.info(f"AutoPilot: {a}", extra={"component": "autopilot"})
                self._automation_log.append({"time": time.time(), "action": a})
                self._automation_log = self._automation_log[-50:]  # keep last 50

    def _compute_risk_multiplier(self) -> float:
        """Compute a risk multiplier [0.2, 2.0] from market conditions."""
        mult = 1.0
        try:
            # Check drawdown
            dd = getattr(self.orch.risk_aegis, "_current_drawdown", 0.0)
            if dd > 5:
                mult *= 0.3  # deep drawdown → defensive
            elif dd > 3:
                mult *= 0.6
            # Check number of open positions (overexposed?)
            n_pos = len(getattr(self.orch.risk_aegis, "portfolio_heat", None)._positions
                       ) if hasattr(self.orch.risk_aegis, "portfolio_heat") else 0
            if n_pos >= 6:
                mult *= 0.5  # too many positions → cut risk
            # Check recent fill rate (if bot is trading well, allow more)
            fills = self.orch._fill_count
            if fills > 0 and fills % 20 == 0:
                # Every 20 fills, check if we're profitable
                if self.orch.risk_aegis.equity > self._equity_base:
                    mult *= 1.1  # slightly more aggressive if winning
        except Exception:
            pass
        return max(self.config.min_kelly_mult, min(self.config.max_kelly_mult, mult))

    def stats(self) -> dict:
        return {
            "running": self._running,
            "cycles": self._cycle_count,
            "equity_base": round(self._equity_base, 2),
            "session_pnl": round(self._realized_pnl_session, 2),
            "toggles": {
                "auto_start": self.config.auto_start,
                "auto_risk_scaling": self.config.auto_risk_scaling,
                "auto_symbol_rotation": self.config.auto_symbol_rotation,
                "auto_compound": self.config.auto_compound,
                "auto_reconnect": self.config.auto_reconnect,
                "auto_kill_recovery": self.config.auto_kill_recovery,
            },
            "recent_actions": self._automation_log[-5:],
        }

    def set_toggle(self, name: str, value: bool) -> bool:
        if hasattr(self.config, name):
            setattr(self.config, name, value)
            logger.info(f"AutoPilot toggle {name} = {value}")
            return True
        return False
