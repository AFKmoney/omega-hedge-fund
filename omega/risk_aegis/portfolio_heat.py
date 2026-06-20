"""
PortfolioHeatTracker — correlation-aware aggregate risk limiter.

Prevents the system from accumulating 10 long positions that are all 90%
correlated to BTC (effectively 10× BTC exposure). Tracks:
    - Correlation matrix of all open positions (rolling 100-bar returns)
    - Portfolio heat = Σ |position_value × position_vol × correlation_factor|
    - If portfolio heat > portfolio_heat_max, reject new positions

Also enforces max_positions cap and min correlation between new and existing
positions (don't add a position that's >0.85 correlated to an existing one
unless it's a deliberate stat-arb pair).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

import numpy as np

from omega.config.settings import RiskAegisSettings
from omega.utils.logger import get_logger

logger = get_logger("omega.risk_aegis.portfolio_heat")


@dataclass
class Position:
    symbol: str
    side: str          # "long" | "short"
    qty: float
    entry_price: float
    current_price: float
    entry_time: float
    strategy: str = ""
    stop_loss_bps: float = 100.0
    take_profit_bps: float = 200.0
    unrealized_pnl: float = 0.0


class PortfolioHeatTracker:
    """Correlation-aware portfolio risk limiter."""

    def __init__(self, settings: Optional[RiskAegisSettings] = None) -> None:
        self.settings = settings or RiskAegisSettings()
        self._positions: Dict[str, Position] = {}  # symbol → Position
        self._returns_history: Dict[str, Deque[float]] = {}  # symbol → deque of returns
        self._last_prices: Dict[str, float] = {}

    def update_price(self, symbol: str, price: float) -> None:
        """Update price tracking; compute rolling return."""
        prev = self._last_prices.get(symbol)
        if prev is not None and prev > 0:
            ret = (price - prev) / prev
            if symbol not in self._returns_history:
                self._returns_history[symbol] = deque(maxlen=100)
            self._returns_history[symbol].append(ret)
        self._last_prices[symbol] = price
        # Update unrealized PnL of open positions
        if symbol in self._positions:
            pos = self._positions[symbol]
            pos.current_price = price
            direction = 1.0 if pos.side == "long" else -1.0
            pos.unrealized_pnl = direction * pos.qty * (price - pos.entry_price)

    def open_position(self, position: Position) -> bool:
        """Try to open a new position. Returns True if accepted."""
        if len(self._positions) >= self.settings.max_positions:
            logger.info(
                f"Portfolio heat: rejected {position.symbol} — max positions reached",
                extra={"component": "risk_aegis.portfolio_heat", "symbol": position.symbol},
            )
            return False
        # Check correlation with existing positions
        if len(self._positions) > 0 and position.symbol in self._returns_history:
            new_rets = list(self._returns_history[position.symbol])
            if len(new_rets) >= 30:
                for sym, pos in self._positions.items():
                    if sym == position.symbol or sym not in self._returns_history:
                        continue
                    existing_rets = list(self._returns_history[sym])
                    n = min(len(new_rets), len(existing_rets))
                    if n < 30:
                        continue
                    corr = float(np.corrcoef(
                        new_rets[-n:], existing_rets[-n:]
                    )[0, 1])
                    # If correlation > threshold AND same direction → reject
                    same_direction = (
                        (pos.side == position.side)
                        if position.side in ("long", "short")
                        else False
                    )
                    if abs(corr) > self.settings.portfolio_correlation_threshold and same_direction:
                        logger.info(
                            f"Portfolio heat: rejected {position.symbol} — "
                            f"corr={corr:.2f} with {sym} (same direction)",
                            extra={"component": "risk_aegis.portfolio_heat"},
                        )
                        return False
        # Check portfolio heat
        if self.portfolio_heat() > self.settings.portfolio_heat_max:
            logger.info(
                f"Portfolio heat: rejected {position.symbol} — portfolio heat exceeded",
                extra={"component": "risk_aegis.portfolio_heat"},
            )
            return False
        self._positions[position.symbol] = position
        return True

    def close_position(self, symbol: str) -> Optional[Position]:
        return self._positions.pop(symbol, None)

    def positions(self) -> List[Position]:
        return list(self._positions.values())

    def portfolio_heat(self) -> float:
        """
        Aggregate risk metric. Returns a value in [0, 1+] where higher = more risk.
        Approximation: sqrt(Σ (w_i × σ_i)² + 2·Σ_{i≠j} w_i·w_j·σ_i·σ_j·ρ_ij)
        """
        if not self._positions:
            return 0.0
        symbols = list(self._positions.keys())
        weights = []
        vols = []
        for sym in symbols:
            pos = self._positions[sym]
            equity_at_risk = abs(pos.qty * pos.current_price) * (pos.stop_loss_bps / 10000.0)
            weights.append(equity_at_risk)
            rets = list(self._returns_history.get(sym, []))
            if len(rets) >= 20:
                vols.append(float(np.std(rets[-20:])))
            else:
                vols.append(0.02)  # 2% default vol
        weights = np.array(weights)
        vols = np.array(vols)
        total_weight = weights.sum()
        if total_weight <= 0:
            return 0.0
        weights = weights / total_weight  # normalize to 1
        # Correlation matrix
        n = len(symbols)
        if n == 1:
            return float(weights[0] * vols[0])
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                ri = list(self._returns_history.get(symbols[i], []))
                rj = list(self._returns_history.get(symbols[j], []))
                n_pts = min(len(ri), len(rj))
                if n_pts >= 30:
                    c = float(np.corrcoef(ri[-n_pts:], rj[-n_pts:])[0, 1])
                    corr[i, j] = corr[j, i] = c
        cov = np.diag(vols) @ corr @ np.diag(vols)
        port_var = float(weights @ cov @ weights)
        port_vol = float(np.sqrt(max(port_var, 0.0)))
        return port_vol

    def stats(self) -> dict:
        return {
            "open_positions": len(self._positions),
            "max_positions": self.settings.max_positions,
            "portfolio_heat": self.portfolio_heat(),
            "portfolio_heat_max": self.settings.portfolio_heat_max,
            "symbols": [p.symbol for p in self._positions.values()],
        }
