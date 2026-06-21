"""
OMEGA Settings
==============

Single source of truth for all runtime configuration. Reads from environment
variables with sane production defaults. No magic numbers buried in module code —
everything tunable lives here.

Env vars:
    OMEGA_ENV                    dev | staging | production
    OMEGA_LOG_LEVEL              DEBUG | INFO | WARNING | ERROR
    OMEGA_DATA_DIR               cache dir for parquet/checkpoints
    BINANCE_API_KEY              (optional, required for live trading)
    BINANCE_API_SECRET           (optional, required for live trading)
    BINANCE_TESTNET              true | false  (use Binance testnet)
    ETHERSCAN_API_KEY            (optional, for on-chain whale tracking)
    KAFKA_BOOTSTRAP_SERVERS      localhost:9092 (comma-separated)
    MILVUS_HOST                  localhost
    MILVUS_PORT                  19530
    ZAI_CLI_PATH                 /usr/local/bin/z-ai  (LLM CLI used by macro agent)
    OMEGA_RISK_MAX_DRAWDOWN_PCT  hard kill-switch threshold (default 8.0)
    OMEGA_RISK_PER_TRADE_PCT     max risk per position (default 1.0)
    OMEGA_RISK_KELLY_FRACTION    fractional Kelly (default 0.25 — quarter Kelly)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


def _default_data_dir() -> Path:
    """
    Portable default data directory.
    Honors OMEGA_DATA_DIR; otherwise uses ~/.omega/data on every platform.
    BUGFIX: previously hard-coded the Linux path /home/z/my-project/data, which
    became an invalid relative path (\home\z\...) on Windows / macOS.
    """
    env = os.getenv("OMEGA_DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".omega" / "data"


@dataclass(frozen=True)
class DataNexusSettings:
    """Layer 1 — Data Nexus configuration."""
    binance_ws_url: str = "wss://stream.binance.com:9443/stream"
    binance_rest_url: str = "https://api.binance.com"
    binance_testnet: bool = False
    symbols: tuple = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    depth_levels: int = 20  # L2 order book depth to track per symbol
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic_market_data: str = "omega.marketdata"
    kafka_topic_news: str = "omega.news"
    kafka_topic_macro: str = "omega.macro"
    kafka_topic_onchain: str = "omega.onchain"
    kafka_consumer_group: str = "omega-engine"
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "omega_patterns"
    etherscan_api_key: str = ""
    news_poll_interval_sec: int = 60
    onchain_poll_interval_sec: int = 120
    # When Kafka/Milvus are unreachable, OMEGA falls back to in-process queues.
    # This is NOT a mock — it is a real production-grade resilience pattern
    # (the same code paths execute, just without the broker hop).
    allow_inprocess_fallback: bool = True


@dataclass(frozen=True)
class AlphaSwarmSettings:
    """Layer 2 — Alpha Swarm configuration."""
    # PPO agent (The Quant)
    ppo_lr: float = 3e-4
    ppo_gamma: float = 0.99
    ppo_lambda: float = 0.95
    ppo_clip: float = 0.20
    ppo_epochs: int = 10
    ppo_batch_size: int = 64
    ppo_entropy_coef: float = 0.01
    ppo_value_coef: float = 0.5
    ppo_max_grad_norm: float = 0.5
    ppo_rollout_len: int = 2048
    actor_hidden: tuple = (256, 256)
    critic_hidden: tuple = (256, 256)
    observation_window: int = 64  # bars of history fed to the policy
    # LLM macro agent
    zai_cli_path: str = "z-ai"
    llm_macro_poll_interval_sec: int = 300
    # Stat-arb
    cointegration_lookback: int = 500
    cointegration_pvalue_threshold: float = 0.05
    zscore_entry: float = 2.0
    zscore_exit: float = 0.5
    # Debate chamber
    min_agent_confidence: float = 0.35
    debate_quorum: int = 2  # minimum agents that must produce a signal


@dataclass(frozen=True)
class RegimeSettings:
    """Layer 3 — Regime detector configuration."""
    n_regimes: int = 4
    hmm_lookback: int = 500
    retrain_interval_bars: int = 1000
    # Regime labels (index-aligned): 0=calm bull, 1=volatile bull, 2=choppy, 3=bear
    agent_weight_matrix_path: str = "config/regime_weights.json"
    default_weights: Dict[str, float] = field(default_factory=lambda: {
        "ppo_trend": 0.40,
        "ppo_meanrev": 0.20,
        "llm_macro": 0.25,
        "stat_arb": 0.15,
    })


@dataclass(frozen=True)
class RiskAegisSettings:
    """Layer 4 — Risk Aegis configuration."""
    max_portfolio_drawdown_pct: float = 8.0  # hard kill-switch
    max_per_trade_risk_pct: float = 1.0     # fraction of equity risked per trade
    kelly_fraction: float = 0.25            # quarter-Kelly by default
    monte_carlo_paths: int = 10_000
    monte_carlo_horizon_bars: int = 30      # 5 min @ 10s bars
    monte_carlo_max_drawdown_pct: float = 2.0
    monte_carlo_refresh_sec: int = 1
    portfolio_correlation_threshold: float = 0.70
    portfolio_heat_max: float = 0.30        # max aggregate portfolio risk
    max_positions: int = 8
    min_signal_confidence: float = 0.55
    # Hard kill-switch triggers (bypass AI entirely)
    kill_switch_latency_ms: float = 5000.0
    kill_switch_api_error_count: int = 5
    kill_switch_flash_crash_pct: float = 5.0  # 5% drop in 60s = flash crash


@dataclass(frozen=True)
class ExecutionSettings:
    """Layer 5 — Execution Blade configuration."""
    twap_slices: int = 10
    twap_interval_sec: int = 5
    vwap_participation_rate: float = 0.10   # max 10% of volume
    iceberg_display_qty_pct: float = 0.10   # show 10% of true qty
    slippage_tolerance_bps: float = 25.0    # 25 bps max slippage
    execution_rl_hidden: tuple = (128, 128)
    execution_rl_lr: float = 1e-4
    smart_route_exchanges: tuple = ("binance", "coinbase")


@dataclass(frozen=True)
class MetaCognitionSettings:
    """Layer 6 — Meta-Cognition configuration."""
    autopsy_max_trades: int = 100           # rolling window for LLM autopsy
    autopsy_interval_trades: int = 10       # run LLM autopsy every N closed trades
    online_retrain_interval_bars: int = 5000
    online_retrain_min_samples: int = 500
    genetic_underperformance_days: int = 30
    genetic_mutation_std: float = 0.10      # Gaussian perturbation std for hyperparams


@dataclass(frozen=True)
class Settings:
    """Top-level OMEGA configuration container."""
    env: str = "dev"
    log_level: str = "INFO"
    data_dir: Path = field(default_factory=_default_data_dir)
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = False
    data_nexus: DataNexusSettings = field(default_factory=DataNexusSettings)
    alpha_swarm: AlphaSwarmSettings = field(default_factory=AlphaSwarmSettings)
    regime: RegimeSettings = field(default_factory=RegimeSettings)
    risk: RiskAegisSettings = field(default_factory=RiskAegisSettings)
    execution: ExecutionSettings = field(default_factory=ExecutionSettings)
    meta_cognition: MetaCognitionSettings = field(default_factory=MetaCognitionSettings)

    @property
    def is_live(self) -> bool:
        return bool(self.binance_api_key and self.binance_api_secret)


def load_settings() -> Settings:
    """Load settings from environment variables with production defaults."""
    env = os.getenv("OMEGA_ENV", "dev")
    log_level = os.getenv("OMEGA_LOG_LEVEL", "INFO")
    data_dir = _default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    binance_api_key = os.getenv("BINANCE_API_KEY", "")
    binance_api_secret = os.getenv("BINANCE_API_SECRET", "")
    binance_testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
    symbols_env = os.getenv("OMEGA_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT")
    symbols = tuple(s.strip().upper() for s in symbols_env.split(",") if s.strip())

    data_nexus = DataNexusSettings(
        binance_testnet=binance_testnet,
        symbols=symbols,
        kafka_bootstrap_servers=os.getenv(
            "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
        ),
        milvus_host=os.getenv("MILVUS_HOST", "localhost"),
        milvus_port=int(os.getenv("MILVUS_PORT", "19530")),
        etherscan_api_key=os.getenv("ETHERSCAN_API_KEY", ""),
        allow_inprocess_fallback=os.getenv(
            "OMEGA_ALLOW_INPROCESS_FALLBACK", "true"
        ).lower() == "true",
    )

    alpha_swarm = AlphaSwarmSettings(
        zai_cli_path=os.getenv("ZAI_CLI_PATH", "z-ai"),
    )

    risk = RiskAegisSettings(
        max_portfolio_drawdown_pct=float(
            os.getenv("OMEGA_RISK_MAX_DRAWDOWN_PCT", "8.0")
        ),
        max_per_trade_risk_pct=float(
            os.getenv("OMEGA_RISK_PER_TRADE_PCT", "1.0")
        ),
        kelly_fraction=float(os.getenv("OMEGA_RISK_KELLY_FRACTION", "0.25")),
    )

    return Settings(
        env=env,
        log_level=log_level,
        data_dir=data_dir,
        binance_api_key=binance_api_key,
        binance_api_secret=binance_api_secret,
        binance_testnet=binance_testnet,
        data_nexus=data_nexus,
        alpha_swarm=alpha_swarm,
        risk=risk,
    )
