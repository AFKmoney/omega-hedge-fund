"""Layer 1 — Data Nexus: omniscient streaming ingestion."""
from omega.data_nexus.base import DataSource, DataSink
from omega.data_nexus.binance_feed import BinanceWebSocketFeed
from omega.data_nexus.etherscan_feed import EtherscanOnChainFeed
from omega.data_nexus.news_feed import RSSNewsFeed
from omega.data_nexus.macro_feed import FREDMacroFeed
from omega.data_nexus.kafka_bus import KafkaEventBus
from omega.data_nexus.vector_store import MilvusVectorStore
from omega.data_nexus.nexus import DataNexus

__all__ = [
    "DataSource",
    "DataSink",
    "BinanceWebSocketFeed",
    "EtherscanOnChainFeed",
    "RSSNewsFeed",
    "FREDMacroFeed",
    "KafkaEventBus",
    "MilvusVectorStore",
    "DataNexus",
]
