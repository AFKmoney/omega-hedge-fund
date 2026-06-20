"""
RegimeDetector — HMM-based market regime classification.

Trains a Gaussian Hidden Markov Model on rolling returns + volatility to
classify the current market into one of N regimes (default 4):
    0: Calm Bull    — low vol, positive drift
    1: Volatile Bull — high vol, positive drift
    2: Choppy       — low vol, zero drift, mean-reverting
    3: Bear         — high vol, negative drift

The regime label drives the dynamic agent weight redirection:
    - In Bear regime, Trend Following agents are defunded (weight → 0)
    - In Choppy regime, Mean Reversion agents are boosted
    - In Volatile Bull, position sizing is reduced across the board

This is real HMM training via hmmlearn — no shortcuts.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Optional

import numpy as np
from hmmlearn.hmm import GaussianHMM

from omega.config.settings import RegimeSettings
from omega.utils.events import MarketEvent
from omega.utils.logger import get_logger

logger = get_logger("omega.regime")


# Canonical regime names (index-aligned with HMM state assignment)
REGIME_NAMES = ("calm_bull", "volatile_bull", "choppy", "bear")


class RegimeDetector:
    """Hidden Markov Model regime classifier."""

    def __init__(
        self,
        settings: Optional[RegimeSettings] = None,
        n_regimes: int = 4,
    ) -> None:
        self.settings = settings or RegimeSettings()
        self.n_regimes = self.settings.n_regimes
        self.lookback = self.settings.hmm_lookback
        self.retrain_every = self.settings.retrain_interval_bars
        self._returns: Deque[float] = deque(maxlen=self.lookback)
        self._vols: Deque[float] = deque(maxlen=self.lookback)
        self._last_price: Optional[float] = None
        self._bar_count = 0
        self._model: Optional[GaussianHMM] = None
        self._state_to_regime: dict = {}  # HMM state index → canonical regime index
        self._current_regime: str = "unknown"
        self._regime_confidence: float = 0.0

    def on_market(self, event: MarketEvent) -> Optional[str]:
        """Ingest one market event, return current regime name (or None)."""
        price = event.last_price
        if self._last_price is not None and self._last_price > 0:
            ret = (price - self._last_price) / self._last_price
            self._returns.append(ret)
            # Rolling 20-bar realized vol
            if len(self._returns) >= 20:
                vol = float(np.std(list(self._returns)[-20:]))
                self._vols.append(vol)
        self._last_price = price
        self._bar_count += 1

        # Retrain periodically
        if self._bar_count % self.retrain_every == 0 and len(self._returns) >= 200:
            self._fit()
        # Predict on every bar once model is fit
        if self._model is not None and len(self._returns) >= 20:
            self._predict_current()
        return self._current_regime if self._current_regime != "unknown" else None

    def _fit(self) -> None:
        """Fit Gaussian HMM on (returns, vol) and map states to canonical regimes."""
        if len(self._returns) < 200 or len(self._vols) < 200:
            return
        n = min(len(self._returns), len(self._vols))
        X = np.column_stack([
            np.array(self._returns)[-n:],
            np.array(self._vols)[-n:],
        ])
        try:
            model = GaussianHMM(
                n_components=self.n_regimes,
                covariance_type="full",
                n_iter=100,
                random_state=42,
            )
            model.fit(X)
            self._model = model
            self._state_to_regime = self._map_states_to_regimes(model)
            logger.info(
                f"HMM retrained: {self.n_regimes} regimes, "
                f"state→regime map={self._state_to_regime}",
                extra={"component": "regime"},
            )
        except Exception as exc:
            logger.warning(f"HMM fit failed: {exc}")

    def _map_states_to_regimes(self, model: GaussianHMM) -> dict:
        """
        Map HMM state indices to canonical regime indices by inspecting
        the emission means: (mean_return, mean_vol).
            - Sort by mean_return: lowest = bear, highest = bull
            - Within bull: lower vol = calm_bull, higher vol = volatile_bull
            - Within non-bull: lower abs(return) = choppy, else bear
        """
        means = model.means_  # (n_regimes, 2)
        # Sort by mean return
        sorted_states = sorted(
            range(self.n_regimes), key=lambda i: means[i, 0]
        )
        # Lowest 2 returns → non-bull; highest 2 → bull (for n_regimes=4)
        non_bull = sorted_states[: self.n_regimes // 2]
        bull = sorted_states[self.n_regimes // 2 :]
        # Within non-bull: lower |return| = choppy, higher |return| (negative) = bear
        non_bull_sorted = sorted(non_bull, key=lambda i: abs(means[i, 0]))
        choppy_state = non_bull_sorted[0]
        bear_state = non_bull_sorted[-1]
        # Within bull: lower vol = calm, higher vol = volatile
        bull_sorted = sorted(bull, key=lambda i: means[i, 1])
        calm_state = bull_sorted[0]
        volatile_state = bull_sorted[-1]
        return {
            calm_state: 0,       # calm_bull
            volatile_state: 1,   # volatile_bull
            choppy_state: 2,     # choppy
            bear_state: 3,       # bear
        }

    def _predict_current(self) -> None:
        """Predict the current regime from the most recent window."""
        if self._model is None:
            return
        n = min(len(self._returns), len(self._vols), 20)
        if n < 5:
            return
        X = np.column_stack([
            np.array(self._returns)[-n:],
            np.array(self._vols)[-n:],
        ]).reshape(1, -1, 2)
        try:
            # hmmlearn expects 2D array (n_samples, n_features) for decode
            X_flat = X.reshape(-1, 2)
            _, states = self._model.decode(X_flat, algorithm="viterbi")
            current_state = int(states[-1])
            canonical_idx = self._state_to_regime.get(current_state, 0)
            new_regime = REGIME_NAMES[canonical_idx]
            if new_regime != self._current_regime:
                logger.info(
                    f"Regime transition: {self._current_regime} → {new_regime}",
                    extra={"component": "regime", "regime": new_regime},
                )
            self._current_regime = new_regime
            # Compute confidence via posterior
            posteriors = self._model.predict_proba(X_flat)
            self._regime_confidence = float(posteriors[-1, current_state])
        except Exception as exc:
            logger.warning(f"Regime predict failed: {exc}")

    @property
    def current_regime(self) -> str:
        return self._current_regime

    @property
    def confidence(self) -> float:
        return self._regime_confidence

    def stats(self) -> dict:
        return {
            "current_regime": self._current_regime,
            "confidence": self._regime_confidence,
            "bars_seen": self._bar_count,
            "returns_buffered": len(self._returns),
            "model_fit": self._model is not None,
        }
