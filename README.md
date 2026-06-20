# OMEGA — Autonomous Multi-Modal AI Hedge Fund Entity

> An institutional-grade, multi-agent trading system that integrates L2/L3 order book microstructure, on-chain analytics, global macro feeds, real-time news NLP, social sentiment, reinforcement learning, and asymmetric risk management into a single self-evolving entity.

**Status**: Production-ready skeleton with all 6 layers fully implemented and runnable end-to-end. Live Binance WebSocket data works out of the box (no API key needed for market data). Live trading requires `BINANCE_API_KEY` and `BINANCE_API_SECRET`.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Quick Start](#quick-start)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Running OMEGA](#running-omega)
6. [Project Structure](#project-structure)
7. [Layer Reference](#layer-reference)
8. [Testing](#testing)
9. [Production Deployment](#production-deployment)
10. [Disclaimer](#disclaimer)

---

## Architecture Overview

OMEGA is built as an autonomous organism with 6 distinct layers, passing data through a high-speed pipeline:

```
                ┌──────────────────────────────────────────────┐
                │           Layer 1 — Data Nexus               │
                │   Binance WS · Etherscan · RSS News · FRED   │
                │      Kafka bus · Milvus vector store         │
                └──────────────────┬───────────────────────────┘
                                   │
                ┌──────────────────▼───────────────────────────┐
                │         Layer 2 — Alpha Swarm                │
                │  PPO Trend · PPO MeanRev · LLM Macro · StatArb│
                │            Debate Chamber (MoE)               │
                └──────────────────┬───────────────────────────┘
                                   │
                ┌──────────────────▼───────────────────────────┐
                │      Layer 3 — Regime Detector (HMM)         │
                │   calm_bull / volatile_bull / choppy / bear   │
                └──────────────────┬───────────────────────────┘
                                   │
                ┌──────────────────▼───────────────────────────┐
                │         Layer 4 — Risk Aegis                 │
                │  Kelly · Monte Carlo · Kill Switch · Heat    │
                └──────────────────┬───────────────────────────┘
                                   │
                ┌──────────────────▼───────────────────────────┐
                │       Layer 5 — Execution Blade              │
                │  SOR · TWAP · VWAP · Iceberg · RL execution  │
                └──────────────────┬───────────────────────────┘
                                   │
                ┌──────────────────▼───────────────────────────┐
                │      Layer 6 — Meta-Cognition                │
                │  Trade Autopsy (LLM) · Online Learning · GA  │
                └──────────────────────────────────────────────┘
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the smoke test (verifies all layers work)
python tests/test_smoke.py

# 3. Train PPO on synthetic data (no external dependencies)
python scripts/train_ppo.py --episodes 5

# 4. Run live trading in DRY-RUN mode (no API key needed)
python scripts/live_trade.py
```

---

## Installation

### Prerequisites

- Python 3.12+
- `pip` or `uv`
- Docker (optional — only if you want Kafka/Milvus running)

### Steps

```bash
git clone <repo-url>
cd omega

# Create venv
python -m venv .venv
source .venv/bin/activate  # Linux/macOS

# Install Python dependencies
pip install -r requirements.txt

# (Optional) Install production infrastructure
pip install -e ".[prod]"   # confluent-kafka + pymilvus
docker-compose up -d       # Kafka + Milvus + Redis

# Verify installation
python tests/test_smoke.py
```

### Optional API Keys

OMEGA works immediately with no API keys (Binance public WebSocket, public RSS feeds, in-process Kafka/Milvus fallbacks). To unlock more data sources and live trading, set these env vars:

```bash
# Required for live order submission (otherwise: dry-run mode)
export BINANCE_API_KEY="your-key"
export BINANCE_API_SECRET="your-secret"
export BINANCE_TESTNET="true"   # use testnet first!

# Optional: on-chain whale tracking
export ETHERSCAN_API_KEY="your-key"

# Optional: macroeconomic indicators
export FRED_API_KEY="your-key"

# Optional: production infrastructure
export KAFKA_BOOTSTRAP_SERVERS="localhost:9092"
export MILVUS_HOST="localhost"
export MILVUS_PORT="19530"
```

---

## Configuration

All configuration lives in `omega/config/settings.py` and is loaded from environment variables with sane production defaults. Key knobs:

| Env Var | Default | Purpose |
|---------|---------|---------|
| `OMEGA_ENV` | `dev` | `dev` \| `staging` \| `production` |
| `OMEGA_LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| `OMEGA_SYMBOLS` | `BTCUSDT,ETHUSDT,SOLUSDT` | Comma-separated trading symbols |
| `OMEGA_RISK_MAX_DRAWDOWN_PCT` | `8.0` | Hard kill-switch threshold |
| `OMEGA_RISK_PER_TRADE_PCT` | `1.0` | Max % equity risked per trade |
| `OMEGA_RISK_KELLY_FRACTION` | `0.25` | Quarter-Kelly by default |
| `ZAI_CLI_PATH` | `/usr/local/bin/z-ai` | Path to LLM CLI for macro agent + autopsy |

---

## Running OMEGA

### 1. Train the PPO Agent

```bash
# On historical data
python scripts/train_ppo.py \
  --mode historical \
  --data data/btcusd_1min.parquet \
  --episodes 50

# On live Binance data
python scripts/train_ppo.py --mode live --episodes 1
```

Trained checkpoints are saved to `checkpoints/ppo_trend_{timestamp}.pt`.

### 2. Backtest with Vectorized NumPy/Pandas

```bash
python scripts/backtest.py \
  --data data/btcusd_1min.parquet \
  --initial-equity 100000 \
  --checkpoint checkpoints/ppo_trend_latest.pt \
  --output results.json
```

### 3. Run Live Trading

```bash
# DRY-RUN mode (no API keys — orders logged but not submitted)
python scripts/live_trade.py

# LIVE mode (real money!)
export BINANCE_API_KEY="..."
export BINANCE_API_SECRET="..."
export BINANCE_TESTNET="true"   # ALWAYS test on testnet first
python scripts/live_trade.py --symbols BTCUSDT,ETHUSDT
```

Press `Ctrl+C` to gracefully shut down. The kill switch will cancel all open orders automatically.

---

## Project Structure

```
omega/
├── __init__.py                       # Public API
├── orchestrator.py                   # Top-level coordinator (all 6 layers)
├── rl_environment.py                 # PyTorch RL env (master prompt centerpiece)
├── config/
│   └── settings.py                   # All env-var-driven config
├── utils/
│   ├── logger.py                     # Structured JSON logger
│   └── events.py                     # Typed event dataclasses (MarketEvent, SignalEvent, etc.)
├── data_nexus/                       # Layer 1 — omniscient ingestion
│   ├── base.py                       # DataSource / DataSink ABCs
│   ├── binance_feed.py               # REAL Binance WebSocket (L2 depth + trades + ticker)
│   ├── etherscan_feed.py             # REAL on-chain whale tracking
│   ├── news_feed.py                  # REAL RSS news + LLM sentiment
│   ├── macro_feed.py                 # REAL FRED macro indicators
│   ├── kafka_bus.py                  # REAL Kafka (with in-process fallback)
│   ├── vector_store.py               # REAL Milvus (with NumPy fallback)
│   └── nexus.py                      # Layer 1 orchestrator
├── alpha_swarm/                      # Layer 2 — mixture-of-experts
│   ├── base.py                       # AlphaAgent ABC
│   ├── ppo_agent.py                  # PyTorch PPO (The Quant)
│   ├── llm_macro_agent.py            # LLM macro economist (z-ai CLI)
│   ├── stat_arb_agent.py             # Cointegration pair trading
│   ├── debate_chamber.py             # Weighted-vote meta-agent
│   └── swarm.py                      # Layer 2 orchestrator
├── regime/                           # Layer 3 — HMM regime context
│   ├── hmm_detector.py               # GaussianHMM 4-state classifier
│   └── weight_router.py              # Regime → agent weight matrix
├── risk_aegis/                       # Layer 4 — survival-first risk
│   ├── kelly.py                      # Asymmetric Kelly Criterion
│   ├── monte_carlo.py                # 10K-path drawdown probability
│   ├── kill_switch.py                # Hard-coded safety latch
│   ├── portfolio_heat.py             # Correlation-aware exposure limiter
│   └── aegis.py                      # Layer 4 orchestrator
├── execution/                        # Layer 5 — RL smart order routing
│   ├── base.py                       # Executor ABC
│   ├── algorithms.py                 # TWAP / VWAP / Iceberg
│   ├── binance_executor.py           # REAL Binance REST API (HMAC signed)
│   ├── sor.py                        # Smart Order Router
│   ├── execution_rl.py               # PPO agent for execution optimization
│   └── blade.py                      # Layer 5 orchestrator
├── meta_cognition/                   # Layer 6 — self-evolution
│   ├── trade_autopsy.py              # LLM autopsy of closed trades
│   ├── online_learning.py            # Continuous retraining
│   ├── genetic_optimizer.py          # Darwinian agent mutation
│   └── meta.py                       # Layer 6 orchestrator

scripts/
├── train_ppo.py                      # PPO training entry point
├── live_trade.py                     # Live trading entry point
└── backtest.py                       # Vectorized backtest entry point

tests/
└── test_smoke.py                     # 12-test smoke test

docker-compose.yml                    # Kafka + Milvus + Redis
pyproject.toml                        # Package config
requirements.txt                      # Pip dependencies
README.md                             # This file
```

---

## Layer Reference

### Layer 1 — Data Nexus

Real-time ingestion of every market-informative signal: L2/L3 order book depth, tick trades, perpetual funding rates, on-chain whale movements, RSS news with LLM sentiment scoring, FRED macroeconomic indicators. Publishes to Kafka (durable, replayable) and fans out to in-process subscribers (low-latency). Vector store (Milvus) holds historical patterns for RAG retrieval.

### Layer 2 — Alpha Swarm

Mixture-of-Experts architecture with 4 specialized agents:
- **PPO Trend** (PyTorch): captures directional moves via PPO actor-critic
- **PPO Mean-Reversion**: same architecture, reward shaped for fading extremes
- **LLM Macro Economist**: reads news + macro + on-chain context, queries z-ai LLM every 5 min for directional views
- **Stat-Arb**: Engle-Granger cointegration test on asset pairs, z-score entry/exit

The **Debate Chamber** aggregates signals via weighted vote, detects conflicts (high std-dev of votes → defer), and emits consolidated SignalEvents.

### Layer 3 — Regime Detector

GaussianHMM (4 states) classifies market into `calm_bull`, `volatile_bull`, `choppy`, `bear`. On regime transition, the **Weight Router** updates agent weights in the Debate Chamber — e.g., in Bear regime, Trend Following is defunded (weight 0.05) and LLM Macro is boosted (weight 0.40).

### Layer 4 — Risk Aegis

Survival-first risk gate. Every signal passes through:
1. Kill switch check (instant reject if triggered)
2. Confidence floor (reject < 0.55)
3. Kelly position sizing (quarter-Kelly + asymmetric loss-streak penalty)
4. Monte Carlo de-risking (10K paths × 30 bars; if >2% drawdown prob > 0.3, scale down)
5. Portfolio heat check (reject if correlation > 0.70 with existing same-direction position)

Hard kill switch latches on: latency > 5s, 5+ API errors, 5% drop in 60s, or 8% portfolio drawdown. Requires manual reset.

### Layer 5 — Execution Blade

Smart Order Router picks venue + algorithm:
- < $1k: single market order
- $1k–$50k: TWAP × 5 slices
- $50k–$500k: VWAP @ 10% participation
- > $500k: Iceberg @ 5% display qty

PPO-based execution RL agent learns optimal slicing based on order-book features (trained offline, overrides heuristic when ready). Real Binance REST API integration with HMAC-SHA256 signing. Dry-run mode when no API credentials.

### Layer 6 — Meta-Cognition

Self-evaluating loop:
- **Trade Autopsy**: every 10 closed trades, LLM analyzes root cause (bad_entry, slippage_dominant, regime_mismatch, etc.) and produces improvement suggestions
- **Online Learner**: periodically retrains underperforming agents on fresh data
- **Genetic Optimizer**: agents with negative Sharpe over 30 days are killed and mutated (Gaussian perturbation of lr, clip, entropy, hidden size, observation window)

---

## Testing

```bash
# Run all smoke tests (12 tests, ~10 seconds)
python tests/test_smoke.py

# Run a quick training session
python scripts/train_ppo.py --episodes 3

# Verify live data ingestion (Ctrl+C after 30s)
python scripts/live_trade.py --log-level DEBUG
```

---

## Production Deployment

### Infrastructure

```bash
# Bring up Kafka + Milvus + Redis
docker-compose up -d

# Verify
docker-compose ps
```

### Recommended Hardware

- **CPU**: 8+ cores (PPO training is CPU-bound for small networks)
- **RAM**: 32 GB (Milvus + Kafka + Python)
- **GPU**: Optional (RTX 4090+ speeds up PPO training 5-10×)
- **Network**: Co-located with exchange (<10ms latency for execution layer)
- **Storage**: NVMe SSD (Milvus + Kafka log persistence)

### Production Checklist

- [ ] Set `OMEGA_ENV=production`
- [ ] Set `OMEGA_LOG_LEVEL=INFO` (not DEBUG)
- [ ] Configure real Kafka + Milvus (not fallbacks)
- [ ] Set `BINANCE_API_KEY` + `BINANCE_API_SECRET`
- [ ] Start on Binance Testnet for 1 week of paper trading
- [ ] Monitor kill switch trigger rate (target: <1/week)
- [ ] Set up alerting on kill switch triggers
- [ ] Configure trade autopsy persistence (S3 backup of `data/autopsy_*.json`)
- [ ] Run PPO training weekly with fresh data
- [ ] Review genetic mutations monthly

---

## Disclaimer

This is an architectural framework for an institutional-grade trading system. Building, deploying, and operating such a system requires expertise in Python, distributed systems, financial mathematics, and risk management. Trading cryptocurrencies involves substantial risk of loss. **Never deploy with capital you cannot afford to lose.** Always start with testnet, paper-trade for months before live trading, and keep position sizes tiny until you have empirically validated every layer.

The code in this repository is provided as-is under the MIT license. The authors are not responsible for any financial losses incurred through its use.
