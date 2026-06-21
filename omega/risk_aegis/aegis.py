"""
RiskAegis — Layer 4 orchestrator.

Sits between the Alpha Swarm (Layer 2) and Execution Blade (Layer 5).
Receives a SignalEvent from the Debate Chamber, runs it through:

    1. Kill switch check (instant rejection if triggered)
    2. Confidence floor (reject signals below min_signal_confidence)
    3. Kelly position sizing (computes qty in USD and units)
    4. Monte Carlo de-risking (scales size by drawdown probability)
    5. Portfolio heat check (rejects if correlated exposure too high)
    6. → Emits OrderEvent to Execution Blade

If any step rejects, the signal is dropped and logged. No order is emitted.
This is the "survival-first" gate — capital preservation always wins over
alpha generation.
"""

from __future__ import annotations

import time
from typing import List, Optional

from omega.config.settings import RiskAegisSettings
from omega.risk_aegis.kill_switch import KillSwitch
from omega.risk_aegis.kelly import KellyPositionSizer
from omega.risk_aegis.monte_carlo import MonteCarloEngine
from omega.risk_aegis.portfolio_heat import PortfolioHeatTracker, Position
from omega.utils.events import (
    FillEvent, MarketEvent, OrderEvent, OrderType, Side, SignalEvent, TimeInForce,
)
from omega.utils.logger import get_logger

logger = get_logger("omega.risk_aegis")


class RiskAegis:
    """Layer 4: survival-first risk gating."""

    def __init__(
        self,
        settings: Optional[RiskAegisSettings] = None,
        initial_equity: float = 100_000.0,
        kelly: Optional[KellyPositionSizer] = None,
        monte_carlo: Optional[MonteCarloEngine] = None,
        kill_switch: Optional[KillSwitch] = None,
        portfolio_heat: Optional[PortfolioHeatTracker] = None,
    ) -> None:
        self.settings = settings or RiskAegisSettings()
        self.equity = initial_equity
        self.initial_equity = initial_equity
        self.kelly = kelly or KellyPositionSizer(self.settings)
        self.monte_carlo = monte_carlo or MonteCarloEngine(self.settings)
        self.kill_switch = kill_switch or KillSwitch(self.settings)
        self.portfolio_heat = portfolio_heat or PortfolioHeatTracker(self.settings)
        self._rejected_count = 0
        self._approved_count = 0

    def on_market(self, event: MarketEvent) -> None:
        """Track market data for risk calculations."""
        self.kill_switch.record_price(event.last_price)
        self.portfolio_heat.update_price(event.symbol, event.last_price)
        # Track returns for Monte Carlo
        # (Using last_price as a proxy; in production, track per-position returns)
        # We use BTC's price as a portfolio proxy if available
        if event.symbol.startswith("BTC"):
            ret = 0.0  # First price has no return
            if hasattr(self, "_last_btc_price") and self._last_btc_price > 0:
                ret = (event.last_price - self._last_btc_price) / self._last_btc_price
            self._last_btc_price = event.last_price
            self.monte_carlo.on_return(ret)
        # Record equity for drawdown tracking
        self.kill_switch.record_equity(self.equity)

    def on_signal(self, signal: SignalEvent, current_price: float) -> Optional[OrderEvent]:
        """
        Process a signal. Returns an OrderEvent if approved, else None.
        This is the single entry point from Alpha Swarm → Execution.
        """
        # 1. Kill switch
        if self.kill_switch.is_triggered:
            self._rejected_count += 1
            logger.warning(
                f"Signal rejected (kill switch): {signal.symbol} {signal.side.value}",
                extra={"component": "risk_aegis", "symbol": signal.symbol},
            )
            return None
        # 2. Confidence floor
        if signal.confidence < self.settings.min_signal_confidence:
            self._rejected_count += 1
            logger.info(
                f"Signal rejected (low confidence {signal.confidence:.2f} < "
                f"{self.settings.min_signal_confidence}): {signal.symbol}",
                extra={"component": "risk_aegis", "symbol": signal.symbol},
            )
            return None
        # 3. Kelly sizing
        atr_bps = 100.0  # placeholder — in production compute from rolling ATR
        kelly = self.kelly.size(signal, self.equity, current_price, atr_bps)
        if kelly.rejected_reason is not None:
            self._rejected_count += 1
            logger.info(
                f"Signal rejected (Kelly: {kelly.rejected_reason}): {signal.symbol}",
                extra={"component": "risk_aegis", "symbol": signal.symbol},
            )
            return None
        # 4. Monte Carlo de-risking
        if self.monte_carlo.should_rerun():
            current_position_value = sum(
                abs(p.qty * p.current_price) for p in self.portfolio_heat.positions()
            )
            self.monte_carlo.run(self.equity, current_position_value)
        mc_multiplier = self.monte_carlo._last_multiplier
        final_qty = kelly.size_qty * mc_multiplier
        if final_qty * current_price < max(self.equity * 0.001, 10.0):
            self._rejected_count += 1
            logger.info(
                f"Signal rejected (post-MC size too small): {signal.symbol}",
                extra={"component": "risk_aegis", "symbol": signal.symbol},
            )
            return None
        # 5. Portfolio heat check
        # BUGFIX (minor): a FLAT signal would have been labeled "short" by the
        # old ternary; map it explicitly. (FLAT signals normally do not reach
        # here because of the confidence floor, but be correct regardless.)
        if signal.side == Side.BUY:
            side_str = "long"
        elif signal.side == Side.SELL:
            side_str = "short"
        else:
            side_str = "flat"
        position = Position(
            symbol=signal.symbol,
            side=side_str,
            qty=final_qty,
            entry_price=current_price,
            current_price=current_price,
            entry_time=time.time(),
            strategy=signal.agent,
            stop_loss_bps=signal.stop_loss_bps,
            take_profit_bps=signal.take_profit_bps,
        )
        if not self.portfolio_heat.open_position(position):
            self._rejected_count += 1
            return None
        # 6. Build OrderEvent
        self._approved_count += 1
        order = OrderEvent(
            symbol=signal.symbol,
            side=signal.side,
            qty=final_qty,
            order_type=OrderType.MARKET,  # Execution Blade will decide TWAP/VWAP/iceberg
            time_in_force=TimeInForce.IOC,
            strategy=signal.agent,
            risk_score=1.0 - signal.confidence,
            metadata={
                "kelly_fraction": kelly.kelly_fraction_raw,
                "kelly_applied": kelly.kelly_fraction_applied,
                "mc_multiplier": mc_multiplier,
                "win_prob": kelly.win_probability,
                "win_loss_ratio": kelly.win_loss_ratio,
                "vol_scale": kelly.vol_scale,
                "consolidated_signal": signal.metadata,
            },
        )
        logger.info(
            f"Risk Aegis approved: {signal.symbol} {signal.side.value} "
            f"qty={final_qty:.4f} @ ~${current_price:.2f} "
            f"(kelly={kelly.kelly_fraction_raw:.2f}, mc={mc_multiplier:.2f})",
            extra={
                "component": "risk_aegis",
                "symbol": signal.symbol,
                "agent": signal.agent,
            },
        )
        return order

    def on_fill(self, fill: FillEvent) -> None:
        """Update equity on fill. Realized P&L booked here."""
        # Position close → update equity and Kelly stats
        # (This is a simplification — full accounting would track cost basis)
        pass

    def on_trade_closed(self, trade) -> None:
        """Called by Meta-Cognition when a trade closes. Update Kelly stats."""
        pnl_bps = trade.realized_pnl_bps
        self.equity += trade.realized_pnl
        # Update Kelly stats for the originating agent
        self.kelly.update_stats(trade.strategy, pnl_bps)
        # Remove from portfolio heat
        self.portfolio_heat.close_position(trade.symbol)
        # Update Monte Carlo return pool
        self.monte_carlo.on_return(pnl_bps / 10000.0)
        logger.info(
            f"Trade closed: {trade.symbol} pnl_bps={pnl_bps:+.1f} "
            f"equity=${self.equity:,.2f}",
            extra={
                "component": "risk_aegis",
                "symbol": trade.symbol,
                "trade_id": trade.trade_id,
            },
        )

    def stats(self) -> dict:
        return {
            "equity": self.equity,
            "initial_equity": self.initial_equity,
            "pnl_pct": (self.equity - self.initial_equity) / self.initial_equity * 100.0,
            "approved": self._approved_count,
            "rejected": self._rejected_count,
            "kill_switch": self.kill_switch.stats(),
            "monte_carlo": self.monte_carlo.stats(),
            "portfolio_heat": self.portfolio_heat.stats(),
        }
