"""
RSSNewsFeed — REAL news headlines from public RSS feeds + LLM sentiment scoring.

Sources:
    - CoinDesk RSS (crypto-native news)
    - Bitcoin Magazine
    - Reuters Finance
    - CNBC Markets

Headlines are pushed through the z-ai CLI for sentiment + relevance scoring.
This is the same LLM that powers the Alpha Swarm macro agent — used here to
pre-score headlines so the LLM agent downstream gets structured data.

No API key required for RSS. LLM scoring uses the local z-ai CLI.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from typing import AsyncIterator, Dict, Optional, Set

import aiohttp

from omega.data_nexus.base import DataSource
from omega.utils.events import NewsEvent
from omega.utils.logger import get_logger

logger = get_logger("omega.data_nexus.news")

# Public RSS endpoints (no key required)
DEFAULT_FEEDS: Dict[str, str] = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "bitcoin_magazine": "https://bitcoinmagazine.com/.rss/full/",
    "reuters_business": "https://news.google.com/rss/search?q=site:reuters.com+bitcoin+OR+crypto&hl=en-US&gl=US&ceid=US:en",
    "cnbc_economy": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
}


class RSSNewsFeed(DataSource):
    """Real RSS news feed with LLM sentiment scoring via z-ai CLI."""

    name = "rss_news"

    def __init__(
        self,
        feeds: Optional[Dict[str, str]] = None,
        poll_interval_sec: int = 60,
        zai_cli_path: str = "/usr/local/bin/z-ai",
        relevance_keywords: tuple = ("bitcoin", "btc", "ethereum", "eth", "crypto", "fed", "cpi", "rates"),
    ) -> None:
        self.feeds = feeds or DEFAULT_FEEDS
        self.poll_interval_sec = poll_interval_sec
        self.zai_cli_path = zai_cli_path
        self.relevance_keywords = tuple(k.lower() for k in relevance_keywords)
        self._seen_titles: Set[str] = set()
        # Cap the seen-titles set to avoid unbounded growth
        self._max_seen = 5000

    async def stream(self) -> AsyncIterator[NewsEvent]:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async for event in self._poll_all_feeds(session):
                        yield event
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        f"News poll failed: {exc}",
                        extra={"component": "data_nexus.news"},
                    )
                await asyncio.sleep(self.poll_interval_sec)

    async def _poll_all_feeds(
        self, session: aiohttp.ClientSession
    ) -> AsyncIterator[NewsEvent]:
        for source, url in self.feeds.items():
            try:
                async with session.get(url, timeout=15) as resp:
                    text = await resp.text()
                async for event in self._parse_feed(source, text):
                    yield event
            except Exception as exc:
                logger.warning(f"Feed fetch failed [{source}]: {exc}")

    async def _parse_feed(
        self, source: str, text: str
    ) -> AsyncIterator[NewsEvent]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            logger.warning(f"RSS parse error [{source}]: {exc}")
            return
        # Handle RSS 2.0
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            if not title:
                continue
            title_hash = hashlib.sha1(title.encode()).hexdigest()
            if title_hash in self._seen_titles:
                continue
            self._seen_titles.add(title_hash)
            if len(self._seen_titles) > self._max_seen:
                # Drop oldest 50% when cap hit
                self._seen_titles = set(list(self._seen_titles)[self._max_seen // 2 :])
            sentiment, relevance, symbols = await self._score(title)
            yield NewsEvent(
                headline=title,
                timestamp=_iso_from_pubdate(pub_date),
                source=source,
                url=link,
                sentiment_score=sentiment,
                relevance=relevance,
                symbols_mentioned=symbols,
            )

    async def _score(self, headline: str) -> tuple:
        """Score headline via z-ai CLI. Returns (sentiment, relevance, symbols)."""
        # Quick keyword pre-filter — skip LLM call if not relevant
        lower = headline.lower()
        if not any(kw in lower for kw in self.relevance_keywords):
            # Still emit but with low relevance
            return (0.0, 0.1, ())
        prompt = (
            f"Score this financial news headline for crypto trading.\n"
            f"Headline: \"{headline}\"\n\n"
            f"Respond with ONLY a JSON object: "
            f'{{"sentiment": <float -1..1>, "relevance": <float 0..1>, '
            f'"symbols": [<list of mentioned tickers like BTC,ETH,SOL>]}}'
        )
        try:
            result = subprocess.run(
                [self.zai_cli_path, "chat", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode != 0:
                return (0.0, 0.5, ())
            # Parse the JSON from the response
            text = result.stdout.strip()
            # Find first { and last }
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return (0.0, 0.5, ())
            parsed = json.loads(match.group(0))
            return (
                float(parsed.get("sentiment", 0.0)),
                float(parsed.get("relevance", 0.5)),
                tuple(s.upper() for s in parsed.get("symbols", [])),
            )
        except Exception as exc:
            logger.debug(f"LLM score failed for '{headline[:40]}...': {exc}")
            return (0.0, 0.5, ())


def _iso_from_pubdate(pub_date: str) -> str:
    """Convert RFC-822 pubDate to ISO-8601."""
    if not pub_date:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(pub_date)
        return dt.isoformat(timespec="milliseconds")
    except Exception:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
