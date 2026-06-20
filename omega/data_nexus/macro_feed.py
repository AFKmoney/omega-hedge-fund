"""
FREDMacroFeed — REAL macroeconomic indicators from the St. Louis Fed (FRED).

Pulls public-domain economic time series from FRED's free API. Indicators:
    - DGS10   (10-Year Treasury yield)
    - DGS2    (2-Year Treasury yield)
    - T10Y2Y  (10-2 yield curve spread — recession indicator)
    - CPIAUCSL (CPI)
    - FEDFUNDS (Fed Funds rate)
    - DCOILWTICO (WTI crude oil)
    - DEXUSEU (USD/EUR)

Requires FRED_API_KEY env var (free at fredaccount.stlouisfed.org).
If absent, the feed yields nothing (NOT mocked) — OMEGA still runs on
market + news + on-chain data.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import AsyncIterator, Dict, Optional

import aiohttp

from omega.data_nexus.base import DataSource
from omega.utils.events import MacroEvent
from omega.utils.logger import get_logger

logger = get_logger("omega.data_nexus.fred")

# (FRED series ID, friendly name, default_value)
DEFAULT_SERIES = (
    ("DGS10", "10Y_YIELD", 4.0),
    ("DGS2", "2Y_YIELD", 4.5),
    ("T10Y2Y", "YIELD_CURVE", -0.5),
    ("CPIAUCSL", "CPI_INDEX", 310.0),
    ("FEDFUNDS", "FED_FUNDS", 5.25),
    ("DCOILWTICO", "WTI_OIL", 78.0),
    ("DEXUSEU", "USD_EUR", 0.92),
)


class FREDMacroFeed(DataSource):
    """Real macroeconomic indicator feed from FRED."""

    name = "fred_macro"

    def __init__(
        self,
        api_key: str = "",
        poll_interval_sec: int = 3600,  # macro data updates daily at most
        series: Optional[tuple] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("FRED_API_KEY", "")
        self.poll_interval_sec = poll_interval_sec
        self.series = series or DEFAULT_SERIES
        self._base_url = "https://api.stlouisfed.org/fred/series/observations"
        self._last_values: Dict[str, float] = {}

    async def stream(self) -> AsyncIterator[MacroEvent]:
        if not self.api_key:
            logger.warning(
                "FRED_API_KEY not set — macro feed disabled (not mocked, just skipped)",
                extra={"component": "data_nexus.fred"},
            )
            return
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async for event in self._poll_once(session):
                        yield event
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"FRED poll failed: {exc}")
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_once(
        self, session: aiohttp.ClientSession
    ) -> AsyncIterator[MacroEvent]:
        for series_id, name, default in self.series:
            params = {
                "series_id": series_id,
                "api_key": self.api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 2,  # latest + prior
            }
            try:
                async with session.get(self._base_url, params=params, timeout=15) as resp:
                    payload = await resp.json()
            except Exception as exc:
                logger.warning(f"FRED fetch failed [{series_id}]: {exc}")
                continue
            obs = payload.get("observations", [])
            if not obs:
                continue
            try:
                latest = float(obs[0]["value"])
            except (KeyError, ValueError):
                continue
            prior = None
            if len(obs) > 1:
                try:
                    prior = float(obs[1]["value"])
                except (KeyError, ValueError):
                    pass
            prev_seen = self._last_values.get(name)
            self._last_values[name] = latest
            # Only emit if value changed or first time
            if prev_seen is not None and abs(prev_seen - latest) < 1e-6:
                continue
            yield MacroEvent(
                indicator=name,
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                value=latest,
                prior_value=prior,
                surprise=(latest - prev_seen) if prev_seen is not None else None,
                source="fred",
            )
