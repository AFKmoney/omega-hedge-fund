# OMEGA — Autonomous Contrarian AI Hedge Fund

> A crypto-native trading system that doesn't trade the price — it trades **where the crowd is overcrowded**. Eight positioning signals detect statistical extremes in retail leverage, sentiment, and flow, then the ContrarianAgent fades them. **Full glassmorphism web GUI controls 100% of the backend.**

**Status**: Production-ready. OKX primary venue, Binance fallback. 54 tests passing. PPO agents trained (+11.9% trend, +21.1% meanrev vs −1.1% random). Crowd engine with 8 signals + auto-tuning weights. Secure wallet manager (TOTP + cap + panic). **Web dashboard** at `http://localhost:8080` with 7 tabs covering every function.

---

## Quick Start (3 commands)

```bash
pip install -r requirements.txt
python tests/test_smoke.py                                    # verify install
python scripts/dashboard.py                                   # open the GUI
```

The GUI opens in your browser. From there you configure keys, start trading, monitor the crowd engine, withdraw funds — everything. No CLI needed.

---

## The Web GUI (glassmorphism)

```bash
python scripts/dashboard.py           # http://localhost:8080
```

7 tabs, all controlling the live backend:

| Tab | What it does |
|-----|-------------|
| **Dashboard** | Balance, P&L, positions, crowd regime, start/stop trading |
| **Crowd Engine** | 8 signal bars (live), fusion weights (V4 auto-tuned), engine stats |
| **Alpha Swarm** | 5 agents + regime weights |
| **Risk** | Kill switch, Kelly, drawdown, equity |
| **Wallet** | Withdraw form (TOTP), panic button, daily cap |
| **Profiles** | Create/switch/delete credential profiles in one click |
| **Settings** | Keystore — set/delete individual keys |

Real-time updates via WebSocket. Every API endpoint returns 200 (audited).

---

## CLI alternatives (if you prefer terminal)

```bash
# Profile management (switch key sets without retyping)
python -m omega.cli.profiles_cli list
python -m omega.cli.profiles_cli add live-okx --use
python -m omega.cli.profiles_cli use demo-okx
python -m omega.cli.profiles_cli export live-okx keys.txt

# Key management
python -m omega.cli.keys list
python -m omega.cli.keys wizard          # interactive setup
python -m omega.cli.keys test            # verify OKX connection

# Dashboard CLI
python -m omega.cli.app dashboard
python -m omega.cli.app status
python -m omega.cli.app withdraw 100 USDT 0xAbc... ETH-ERC20 123456
python -m omega.cli.app panic
```

---

## The Thesis

80% of traders lose because they do what 80% do: they pile into overcrowded extremes at the worst moment, then get liquidated in the inverse cascade. OMEGA quantifies that extreme and takes the other side.

This is an outsider's edge. Institutional giants (Jump, Jane Street, Citadel) cannot do sentiment-based contrarian trading — their compliance forbids it and their sizes are too big to fade retail. OMEGA is built entirely outside that dogma.

**Core principle**: fade only *statistical extremes* where multiple signals agree. When signals diverge, conviction drops and we don't trade. This is what separates a professional fader from a gambler.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Layer 1 — Data Nexus (OKX WS / Binance WS / on-chain / RSS)     │
│   trades · depth · funding · liquidations · OI · news · macro   │
└──────────────┬──────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────────┐
│ Layer 1.5 — Crowd Positioning Engine (8 signals)                │
│   liquidations · funding · open_interest · ls_ratio             │
│   sentiment · social · iceberg · inflow                         │
│   → CrowdPositioningEvent {score, conviction, horizon}          │
│   → CrowdWeightOptimizer (V4 auto-tunes fusion weights)         │
└──────────────┬──────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────────┐
│ Layer 2 — Alpha Swarm (5 agents + Debate Chamber)               │
│   PPO Trend (trained) · PPO Meanrev (trained) · Contrarian      │
│   LLM Macro · Stat-Arb                                          │
└──────────────┬──────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────────┐
│ Layer 3 — Regime Detector (HMM) + Crowd Regime Override         │
│   calm_bull / volatile_bull / choppy / bear / crowd_cascade     │
└──────────────┬──────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────────┐
│ Layer 4 — Risk Aegis                                            │
│   Kelly · Monte Carlo · Kill Switch · Portfolio Heat            │
└──────────────┬──────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────────┐
│ Layer 5 — Execution Blade (OKX / Binance)                       │
│   SOR · TWAP · VWAP · Iceberg · RL execution                    │
│   Wallet Manager (TOTP + cap + panic)                           │
└──────────────┬──────────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────────┐
│ Layer 6 — Meta-Cognition                                        │
│   Trade Autopsy (LLM) · Online Learning · Genetic Optimizer     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
# 1. Install
pip install -r requirements.txt

# 2. Run all tests (54 tests)
python tests/test_smoke.py
python tests/test_regression.py
python tests/test_crowd_engine.py
python tests/test_okx.py
python tests/test_microstructure.py

# 3. Generate training data + train PPO agents
python scripts/gen_synthetic_data.py
python scripts/train_ppo.py --data tests/_train_data.csv --episodes 50 --mode-type trend
python scripts/train_ppo.py --data tests/_train_data.csv --episodes 25 --mode-type meanrev

# 4. Live trade with trained agents (DRY-RUN, no keys needed)
python scripts/live_trade.py --symbols BTCUSDT --load-checkpoints
```

---

## OKX Setup (primary venue)

OMEGA auto-selects OKX when its 3 credentials are set. Otherwise it falls back to Binance.

```bash
# OKX credentials (3-part auth)
set OKX_API_KEY=your_key
set OKX_API_SECRET=your_secret
set OKX_PASSPHRASE=your_passphrase
set OKX_DEMO=true          # paper trading first!

# Wallet security (TOTP — required for withdrawals)
set OMEGA_TOTP_SECRET=your_base32_secret
set OMEGA_DAILY_CAP_USD=500

# Live trade
python scripts/live_trade.py --symbols BTCUSDT,ETHUSDT --load-checkpoints
```

### Dashboard + Wallet CLI

```bash
# Live positions/PnL dashboard
python -m omega.cli.app dashboard

# Wallet status
python -m omega.cli.app status

# Withdraw (requires TOTP code from your phone)
python -m omega.cli.app withdraw 100 USDT 0xAbc... ETH-ERC20 123456

# Panic switch (freeze ALL withdrawals instantly)
python -m omega.cli.app panic

# Change daily cap
python -m omega.cli.app set-cap 2000 123456
```

---

## The Crowd Positioning Engine (Layer 1.5)

Eight signals, each normalized to `[-1, +1]` and tagged with a horizon:

| Signal | Source | What it measures | Horizon |
|--------|--------|------------------|---------|
| **liquidations** | WS `!forceOrder@arr` (Binance) / `liquidation-orders` (OKX) | Real-time cascade confirmation — most predictive | minutes |
| **funding rate** | `@markPrice` (Binance) / `mark-price` (OKX) | Perp leverage crowding | hours |
| **open interest** | REST `openInterestHist` | Leverage piling in / flushing (rate-of-change) | hours |
| **ls_ratio** | REST `globalLongShortAccountRatio` | Retail account positioning | hours |
| **sentiment** | Fear & Greed API | Narrative fear/euphoria extremes | days |
| **social** | CoinGecko trending | Retail euphoria (meme coin ratio) | days |
| **iceberg** | Depth feed (passive) | Hidden order walls — microstructure | minutes |
| **inflow** | Whale Alert / on-chain | Exchange-bound whale transfers (imminent selling) | hours |

### Fusion logic
```
crowd_score = Σ(signal_score × weight) / Σ(weights)   clamped [-1, +1]
conviction  = |crowd_score| × (1 − divergence)         where divergence = fraction of
             disagreeing significant signals
```
When all signals agree → high conviction → `cascade_imminent`. When they diverge → low conviction → no trade.

### V4 Auto-tuning
The `CrowdWeightOptimizer` evolves the 8 fusion weights from realized contrarian PnL. Signals whose extremes preceded winning fades get up-weighted; misleading signals get down-weighted. The engine self-improves with every closed trade.

---

## The ContrarianAgent

Rule-based (not ML — an extreme is a threshold, not a prediction). Only fires when `|crowd_score| > 0.5`:

- **Side**: inverse of crowd (crowd long overcrowded → SHORT)
- **Confidence**: capped at 0.85
- **TP/SL asymmetry**: `stop = 0.3 × TP` — small frequent losses, large gains when the cascade claps (win rate ~35%, positive expectancy — the signature of every surviving mean-reversion strategy)
- **Holding period**: set by the event's horizon

---

## Risk Aegis (Layer 4)

Every signal passes through survival-first gates:
1. **Kill switch** — latches on flash crash (>5%/30s), drawdown (>8%), latency (>5s), API errors (5+). Requires manual reset.
2. **Confidence floor** — reject < 0.55
3. **Kelly sizing** — quarter-Kelly with loss-streak penalty
4. **Monte Carlo** — 10K paths × 30 bars; if drawdown prob > 0.3, scale down
5. **Portfolio heat** — reject if correlation > 0.70 with existing position

---

## What was audited & fixed (full history)

See `AUDIT.md` for the complete bug audit (3 critical + 7 major + 10 minor bugs found and fixed, each with a regression test). Key critical fixes:
- **C1**: live pipeline was dead (orchestrator never fed market data to Risk Aegis → zero orders ever emitted)
- **C2**: PPO agent mashed all symbols into one state buffer
- **C3**: mean-reversion reward was a trivial 0.7× rescale of trend reward
- **Runtime**: kill switch triggered false flash-crash 2s after startup (latched, froze all trading)

---

## Configuration

| Env Var | Default | Purpose |
|---------|---------|---------|
| `OKX_API_KEY` | — | OKX API key (3-part auth) |
| `OKX_API_SECRET` | — | OKX API secret |
| `OKX_PASSPHRASE` | — | OKX passphrase |
| `OKX_DEMO` | `false` | Use OKX demo (paper) trading |
| `OMEGA_TOTP_SECRET` | — | Base32 TOTP secret for wallet withdrawals |
| `OMEGA_DAILY_CAP_USD` | `500` | Daily withdrawal cap (USD) |
| `OMEGA_SYMBOLS` | `BTCUSDT,ETHUSDT,SOLUSDT` | Trading symbols |
| `OMEGA_RISK_MAX_DRAWDOWN_PCT` | `8.0` | Kill switch drawdown threshold |
| `OMEGA_RISK_PER_TRADE_PCT` | `1.0` | Max % equity per trade |
| `OMEGA_RISK_KELLY_FRACTION` | `0.25` | Quarter-Kelly |
| `BINANCE_API_KEY` | — | Binance (fallback venue) |
| `BINANCE_API_SECRET` | — | Binance secret |
| `BINANCE_TESTNET` | `false` | Binance testnet |
| `ETHERSCAN_API_KEY` | — | On-chain whale tracking |
| `WHALE_ALERT_API_KEY` | — | Large transfer inflow signal |
| `ZAI_CLI_PATH` | `z-ai` | LLM CLI for macro agent + autopsy |

---

## Project Structure

```
omega/
├── orchestrator.py                # 7-layer coordinator
├── config/settings.py             # env-driven config
├── data_nexus/                    # Layer 1
│   ├── okx_feed.py                # OKX WS (trades/depth/funding/liquidations)
│   ├── binance_feed.py            # Binance WS (fallback)
│   ├── etherscan_feed.py          # on-chain whales
│   ├── news_feed.py               # RSS + LLM sentiment
│   └── ...
├── crowd_engine/                  # Layer 1.5 (the contrarian brain)
│   ├── engine.py                  # 8-signal fusion
│   ├── optimizer.py               # V4 auto-tuning weights
│   └── signals/                   # 8 positioning signals
│       ├── liquidation_signal.py
│       ├── funding_signal.py
│       ├── open_interest_signal.py
│       ├── ls_ratio_signal.py
│       ├── sentiment_signal.py
│       ├── social_signal.py
│       ├── iceberg_signal.py
│       └── inflow_signal.py
├── alpha_swarm/                   # Layer 2
│   ├── ppo_agent.py               # trained PPO (trend + meanrev)
│   ├── contrarian_agent.py        # fades crowd extremes
│   ├── llm_macro_agent.py
│   └── ...
├── execution/                     # Layer 5
│   ├── okx_executor.py            # OKX REST (HMAC signing)
│   ├── binance_executor.py        # Binance REST (fallback)
│   ├── wallet_manager.py          # SECURE withdrawals (TOTP+cap+panic)
│   ├── venue.py                   # exchange-agnostic abstraction
│   └── ...
├── cli/app.py                     # dashboard + wallet CLI
└── ...

scripts/
├── gen_synthetic_data.py          # multi-regime training data generator
├── train_ppo.py                   # PPO training (fixed: actually learns now)
├── live_trade.py                  # live trading entry point
└── backtest.py                    # vectorized backtest

tests/                             # 54 tests total
├── test_smoke.py                  # 12 — layer smoke tests
├── test_regression.py             # 11 — audit bug regression tests
├── test_crowd_engine.py           # 16 — crowd engine + contrarian
├── test_okx.py                    # 10 — OKX + wallet + TOTP
└── test_microstructure.py         # 5 — iceberg + inflow
```

---

## Disclaimer

Trading cryptocurrencies involves substantial risk of loss. The contrarian thesis is sound but edge-dependent: it degrades if it becomes popular. **Never deploy with capital you cannot afford to lose.** Always start with OKX demo / Binance testnet, paper-trade for weeks before live trading, and keep position sizes tiny until you have empirically validated every layer.

The wallet manager's TOTP + cap + panic layers exist precisely because a leaked API key without them means instant total loss. Generate your TOTP secret offline, never commit it, and use a dedicated API key with withdrawal permission only for the wallet manager.

MIT License. Provided as-is. Authors are not responsible for financial losses.
