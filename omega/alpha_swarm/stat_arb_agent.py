"""
StatArbAgent — Statistical arbitrage via cointegration pair trading.

Agent 3 in the Alpha Swarm. Monitors cointegration between correlated asset
pairs (BTC/ETH, SOL/ETH, etc.) using the Engle-Granger test. When the
spread's z-score exceeds the entry threshold, emits a signal to short the
over-valued leg and long the under-valued leg.

Implementation:
    - Maintains rolling price history per symbol
    - Periodically refits the cointegration test (default every 500 bars)
    - Tracks the spread = log(P_A) - β·log(P_B) where β is the hedge ratio
    - Z-score = (spread - μ) / σ over a rolling window
    - Entry: |z| > 2.0   Exit: |z| < 0.5

This is real stat-arb — uses statsmodels for OLS + ADF test, no shortcuts.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np
import statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller

from omega.alpha_swarm.base import AlphaAgent
from omega.config.settings import AlphaSwarmSettings
from omega.utils.events import MarketEvent, SignalEvent, Side
from omega.utils.logger import get_logger

logger = get_logger("omega.alpha_swarm.stat_arb")


class StatArbAgent(AlphaAgent):
    """Cointegration-based statistical arbitrage agent."""

    name = "stat_arb"

    def __init__(
        self,
        symbols: tuple,
        settings: Optional[AlphaSwarmSettings] = None,
        pairs: Optional[Tuple[Tuple[str, str], ...]] = None,
    ) -> None:
        super().__init__(symbols)
        self.settings = settings or AlphaSwarmSettings()
        # Default pairs: all 2-combinations of provided symbols
        if pairs is None:
            pairs = tuple(
                (symbols[i], symbols[j])
                for i in range(len(symbols))
                for j in range(i + 1, len(symbols))
            )
        self.pairs = pairs
        self.lookback = self.settings.cointegration_lookback
        self.z_entry = self.settings.zscore_entry
        self.z_exit = self.settings.zscore_exit
        self.p_threshold = self.settings.cointegration_pvalue_threshold
        self._prices: Dict[str, Deque[float]] = {
            s: deque(maxlen=self.lookback) for s in symbols
        }
        # Cached cointegration stats per pair
        self._coint: Dict[Tuple[str, str], dict] = {}
        self._refit_counter = 0
        self._refit_every = 200  # bars
        self._open_positions: Dict[Tuple[str, str], str] = {}  # pair → "long_a_short_b" | "long_b_short_a"
        self.is_ready = True

    def on_market(self, event: MarketEvent) -> List[SignalEvent]:
        sym = event.symbol
        if sym not in self._prices:
            return []
        self._prices[sym].append(event.last_price)
        self._refit_counter += 1
        signals: List[SignalEvent] = []
        # Refit cointegration periodically
        if self._refit_counter >= self._refit_every:
            self._refit_counter = 0
            self._refit_all()
        # Check each pair that includes this symbol
        for pair in self.pairs:
            if sym not in pair:
                continue
            signals.extend(self._evaluate_pair(pair, event.timestamp))
        return signals

    def _refit_all(self) -> None:
        """Run Engle-Granger cointegration test on every pair."""
        for pair in self.pairs:
            a, b = pair
            if len(self._prices[a]) < 100 or len(self._prices[b]) < 100:
                continue
            pa = np.array(self._prices[a], dtype=np.float64)
            pb = np.array(self._prices[b], dtype=np.float64)
            n = min(len(pa), len(pb))
            pa = pa[-n:]
            pb = pb[-n:]
            # Avoid zero/negative prices
            if (pa <= 0).any() or (pb <= 0).any():
                continue
            log_a = np.log(pa)
            log_b = np.log(pb)
            # OLS: log_a = α + β·log_b + ε
            X = sm.add_constant(log_b)
            try:
                model = sm.OLS(log_a, X).fit()
            except Exception:
                continue
            beta = float(model.params[1])
            alpha = float(model.params[0])
            resid = log_a - (alpha + beta * log_b)
            # ADF test on residuals
            try:
                adf_stat, p_value, _, _, _, _ = adfuller(resid, maxlag=1)
            except Exception:
                continue
            # Rolling spread stats
            spread_mean = float(np.mean(resid[-100:]))
            spread_std = float(np.std(resid[-100:])) + 1e-9
            self._coint[pair] = {
                "alpha": alpha,
                "beta": beta,
                "p_value": float(p_value),
                "adf_stat": float(adf_stat),
                "spread_mean": spread_mean,
                "spread_std": spread_std,
                "is_cointegrated": p_value < self.p_threshold,
            }

    def _evaluate_pair(self, pair: Tuple[str, str], timestamp: str) -> List[SignalEvent]:
        a, b = pair
        stats = self._coint.get(pair)
        if stats is None or not stats["is_cointegrated"]:
            return []
        if len(self._prices[a]) == 0 or len(self._prices[b]) == 0:
            return []
        pa = self._prices[a][-1]
        pb = self._prices[b][-1]
        if pa <= 0 or pb <= 0:
            return []
        spread = np.log(pa) - (stats["alpha"] + stats["beta"] * np.log(pb))
        z = (spread - stats["spread_mean"]) / stats["spread_std"]
        signals: List[SignalEvent] = []
        open_pos = self._open_positions.get(pair)
        if open_pos is None:
            if z > self.z_entry:
                # Spread too wide: short A, long B
                self._open_positions[pair] = "short_a_long_b"
                signals.append(self._make_signal(a, Side.SELL, timestamp, pair, z, "short_leg"))
                signals.append(self._make_signal(b, Side.BUY, timestamp, pair, z, "long_leg"))
            elif z < -self.z_entry:
                self._open_positions[pair] = "long_a_short_b"
                signals.append(self._make_signal(a, Side.BUY, timestamp, pair, z, "long_leg"))
                signals.append(self._make_signal(b, Side.SELL, timestamp, pair, z, "short_leg"))
        else:
            # Check exit
            if abs(z) < self.z_exit:
                # Close position
                if open_pos == "short_a_long_b":
                    signals.append(self._make_signal(a, Side.BUY, timestamp, pair, z, "exit_short"))
                    signals.append(self._make_signal(b, Side.SELL, timestamp, pair, z, "exit_long"))
                else:
                    signals.append(self._make_signal(a, Side.SELL, timestamp, pair, z, "exit_long"))
                    signals.append(self._make_signal(b, Side.BUY, timestamp, pair, z, "exit_short"))
                self._open_positions.pop(pair, None)
        return signals

    def _make_signal(
        self, symbol: str, side: Side, timestamp: str, pair: Tuple[str, str], z: float, leg: str
    ) -> SignalEvent:
        return SignalEvent(
            agent=self.name,
            symbol=symbol,
            timestamp=timestamp,
            side=side,
            confidence=min(0.85, 0.5 + abs(z) / 10.0),
            expected_return_bps=abs(z) * 50.0,
            stop_loss_bps=200.0,
            take_profit_bps=abs(z) * 100.0,
            expected_holding_period_bars=240,
            rationale=f"Stat-arb {pair[0]}/{pair[1]} z={z:+.2f} ({leg})",
            metadata={
                "pair": list(pair),
                "zscore": float(z),
                "leg": leg,
                "p_value": self._coint[pair]["p_value"],
                "beta": self._coint[pair]["beta"],
            },
        )

    def stats(self) -> dict:
        return {
            "name": self.name,
            "ready": self.is_ready,
            "cointegrated_pairs": sum(
                1 for s in self._coint.values() if s["is_cointegrated"]
            ),
            "tracked_pairs": len(self._coint),
            "open_positions": len(self._open_positions),
            "history_lens": {s: len(p) for s, p in self._prices.items()},
        }
