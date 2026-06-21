"""
DataNexus — Layer 1 orchestrator.

Owns all data sources, the Kafka event bus, and the vector store. Spawns each
source as an asyncio task, fans events out to in-process subscribers (Alpha
Swarm, Risk Aegis) AND publishes them to Kafka for durability / cross-service
replay.

This is the only entry point Layer 2+ should use to obtain data — it provides
a unified async iterator over all event types.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, List, Optional

from omega.config.settings import DataNexusSettings
from omega.data_nexus.base import DataSource
from omega.data_nexus.binance_feed import BinanceWebSocketFeed
from omega.data_nexus.etherscan_feed import EtherscanOnChainFeed
from omega.data_nexus.kafka_bus import KafkaEventBus
from omega.data_nexus.macro_feed import FREDMacroFeed
from omega.data_nexus.news_feed import RSSNewsFeed
from omega.data_nexus.vector_store import MilvusVectorStore
from omega.utils.events import (
    MacroEvent, MarketEvent, NewsEvent, OnChainEvent,
)
from omega.utils.logger import get_logger

logger = get_logger("omega.data_nexus")


class DataNexus:
    """Layer 1: omniscient data ingestion + event distribution."""

    def __init__(
        self,
        settings: Optional[DataNexusSettings] = None,
        binance_feed: Optional[BinanceWebSocketFeed] = None,
        etherscan_feed: Optional[EtherscanOnChainFeed] = None,
        news_feed: Optional[RSSNewsFeed] = None,
        macro_feed: Optional[FREDMacroFeed] = None,
        bus: Optional[KafkaEventBus] = None,
        vector_store: Optional[MilvusVectorStore] = None,
    ) -> None:
        self.settings = settings or DataNexusSettings()
        self.binance = binance_feed or BinanceWebSocketFeed(
            symbols=self.settings.symbols,
            depth_levels=self.settings.depth_levels,
        )
        self.etherscan = etherscan_feed or EtherscanOnChainFeed(
            api_key=self.settings.etherscan_api_key,
            poll_interval_sec=self.settings.onchain_poll_interval_sec,
        )
        self.news = news_feed or RSSNewsFeed(
            poll_interval_sec=self.settings.news_poll_interval_sec,
            zai_cli_path=_zai_cli_path(),
        )
        self.macro = macro_feed or FREDMacroFeed()
        self.bus = bus or KafkaEventBus(
            bootstrap_servers=self.settings.kafka_bootstrap_servers,
            consumer_group=self.settings.kafka_consumer_group,
            allow_fallback=self.settings.allow_inprocess_fallback,
        )
        self.vector_store = vector_store or MilvusVectorStore(
            host=self.settings.milvus_host,
            port=self.settings.milvus_port,
            collection=self.settings.milvus_collection,
        )

        self._subscriber_queues: List[asyncio.Queue] = []
        self._tasks: List[asyncio.Task] = []
        self._running = False

    def subscribe(self) -> asyncio.Queue:
        """Get a queue that receives every event the Nexus emits."""
        q: asyncio.Queue = asyncio.Queue(maxsize=10_000)
        self._subscriber_queues.append(q)
        return q

    async def start(self) -> None:
        """Spawn all source ingestion tasks."""
        if self._running:
            return
        self._running = True
        await self.vector_store._ensure_connected()
        self._tasks = [
            asyncio.create_task(self._ingest(self.binance, "market")),
            asyncio.create_task(self._ingest(self.etherscan, "onchain")),
            asyncio.create_task(self._ingest(self.news, "news")),
            asyncio.create_task(self._ingest(self.macro, "macro")),
        ]
        logger.info(
            f"DataNexus started with {len(self._tasks)} sources",
            extra={"component": "data_nexus"},
        )

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(f"Source shutdown error: {exc}")
        await self.binance.stop()
        await self.etherscan.stop()
        await self.news.stop()
        await self.macro.stop()
        await self.bus.close()
        await self.vector_store.close()
        logger.info("DataNexus stopped", extra={"component": "data_nexus"})

    async def _ingest(self, source: DataSource, kind: str) -> None:
        """Run one source forever, fan-out events to all subscribers + Kafka."""
        try:
            async for event in source.stream():
                # 1. Publish to Kafka (durability / replay)
                try:
                    await self.bus.publish(_topic_for_kind(kind), event)
                except Exception as exc:
                    logger.warning(f"Kafka publish failed: {exc}")
                # 2. Fan out to in-process subscribers (low-latency path)
                for q in list(self._subscriber_queues):
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        # Drop oldest if consumer is too slow
                        try:
                            q.get_nowait()
                            q.put_nowait(event)
                        except Exception:
                            pass
        except asyncio.CancelledError:
            logger.info(f"Ingest task [{kind}] cancelled")
            raise
        except Exception as exc:
            logger.exception(f"Ingest task [{kind}] crashed: {exc}")


def _topic_for_kind(kind: str) -> str:
    return {
        "market": "omega.marketdata",
        "news": "omega.news",
        "macro": "omega.macro",
        "onchain": "omega.onchain",
    }.get(kind, "omega.events")


def _zai_cli_path() -> str:
    import os
    return os.getenv("ZAI_CLI_PATH", "/usr/local/bin/z-ai")
