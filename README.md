# OMEGA — Autonomous Contrarian AI Hedge Fund

> A crypto-native trading system that doesn't trade the price — it trades **where the crowd is overcrowded**. 8 crowd-positioning signals + 25 breakthrough alpha modules detect statistical extremes in retail leverage, sentiment, microstructure, and on-chain flow. 10 exchanges aggregated. MetaMask/Web3 integrated. **Full glassmorphism web GUI controls 100% of the backend.**

**Status**: Production-ready. 54 tests passing. PPO agents trained on real OKX data (trend +4.06%, meanrev +1.89% vs random −0.84%). Repo is **public**.

---

## What makes OMEGA different

Every retail bot tries to predict price direction. That's the impossible game. OMEGA predicts **where the crowd is overcrowded** — and takes the other side. When 80% of traders pile into an extreme with maximum leverage, the inverse cascade is the most predictable move in crypto. We quantify that extreme across 33 dimensions and fade it.

This is an outsider's edge. Institutional giants (Jump, Jane Street, Citadel) cannot do sentiment-based contrarian trading — their compliance forbids it and their sizes are too big to fade retail flows. OMEGA operates entirely outside that dogma.

---

## Quick Start

```bash
git clone https://github.com/AFKmoney/omega-hedge-fund.git
cd omega-hedge-fund
pip install -r requirements.txt
python scripts/dashboard.py          # opens GUI in browser
```

Configure your keys in the **Profiles** tab, then hit **Start** on the dashboard. Or use CLI:

```bash
python -m omega.cli.profiles_cli wizard     # interactive setup
python scripts/live_trade.py --symbols BTCUSDT --load-checkpoints --paper  # safe validation
```

---

## System Map

```
                    ┌──────────────────────────────────────────────────┐
                    │            10 EXCHANGES (multi-venue)             │
                    │  OKX · Binance · Kraken · Coinbase · Bybit        │
                    │  KuCoin · MEXC · Gemini · Crypto.com · Gate.io    │
                    └─────────────────────┬────────────────────────────┘
                                          │
          ┌───────────────────────────────▼──────────────────────────────┐
          │              8 CROWD POSITIONING SIGNALS (Layer 1.5)         │
          │  liquidations · funding · open_interest · ls_ratio           │
          │  sentiment · social · iceberg · inflow                       │
          │  → CrowdPositioningEvent {score, conviction, horizon}        │
          │  → CrowdWeightOptimizer (V4 auto-tuning from realized PnL)   │
          └───────────────────────┬──────────────────────────────────────┘
                                  │
          ┌───────────────────────▼──────────────────────────────────────┐
          │          5 ALPHA AGENTS (Layer 2 — Debate Chamber)           │
          │  PPO Trend (trained) · PPO Meanrev (trained) · Contrarian    │
          │  LLM Macro · Stat-Arb                                         │
          └───────────────────────┬──────────────────────────────────────┘
                                  │
          ┌───────────────────────▼──────────────────────────────────────┐
          │        25 BREAKTHROUGH ALPHA MODULES (Layer 2.5)             │
          │  Cascade prediction · Funding forecast · Whale tracker       │
          │  Gamma signal · Depeg alert · Toxic flow · Smart money       │
          │  Vol forecast · Correlation breakdown · Flash crash scanner  │
          │  Volume profile · Time-of-day · BTC dominance · Reserves     │
          │  Multi-timeframe · Stablecoin flow · Mempool · Bridges       │
          │  Economic calendar · Stress index · Cross-venue arb          │
          │  Adaptive risk · DeFi yield · Sentiment NLP · Portfolio opt  │
          └───────────────────────┬──────────────────────────────────────┘
                                  │
          ┌───────────────────────▼──────────────────────────────────────┐
          │     RISK AEGIS (Layer 4) · EXECUTION (Layer 5)                │
          │  Kelly · Monte Carlo · Kill switch · Portfolio heat           │
          │  SOR · TWAP · VWAP · Iceberg · Secure wallet (TOTP+cap+panic) │
          └───────────────────────┬──────────────────────────────────────┘
                                  │
          ┌───────────────────────▼──────────────────────────────────────┐
          │     GLASSMORPHISM WEB GUI (Layer 7 — 9 tabs, 100% control)    │
          └──────────────────────────────────────────────────────────────┘
```

---

## The 8 Crowd Signals

| Signal | Source | What it measures |
|--------|--------|------------------|
| **liquidations** | WS real-time | Cascade confirmation — most predictive |
| **funding rate** | `@markPrice` / `mark-price` | Perp leverage crowding |
| **open interest** | REST `openInterestHist` | Leverage piling in / flushing (ROC) |
| **ls_ratio** | REST `globalLongShortAccountRatio` | Retail account positioning |
| **sentiment** | Fear & Greed API | Narrative fear/euphoria extremes |
| **social** | CoinGecko trending | Retail euphoria (meme coin ratio) |
| **iceberg** | Depth feed (passive) | Hidden order walls — microstructure |
| **inflow** | Whale Alert / on-chain | Exchange-bound whale transfers |

**Fusion**: `crowd_score = Σ(score × weight) / Σ(weights)` clamped `[-1,+1]`. `conviction = |score| × (1 − divergence)`. When all signals agree → high conviction → `cascade_imminent`. When they diverge → low conviction → no trade. Weights auto-tune from realized contrarian PnL (V4).

---

## The 25 Breakthrough Modules

| # | Module | Edge |
|---|--------|------|
| B1 | CascadePredictor | Predicts liquidation cascades from OI + funding extremes |
| B2 | FundingForecast | Forecasts next funding from perp-spot basis |
| B3 | WhaleTracker | Unified on-chain + CEX whale flow |
| B4 | GammaExposureSignal | Dealer gamma pressure from Deribit options |
| B5 | DepegAlert | Stablecoin depeg early warning |
| B6 | ToxicFlowDetector | CVD acceleration = informed flow |
| B7 | SmartMoneyDivergence | Price vs CVD divergence = distribution |
| B8 | VolatilityForecast | EWMA/GARCH vol forecasting |
| B9 | CorrelationBreakdown | Asset correlation regime shifts |
| B10 | FlashCrashScanner | Spread + depth precursor detection |
| B11 | VolumeProfile | Volume-at-price (POC, value area) |
| B12 | TimeOfDayAlpha | Session volatility patterns |
| B13 | BTCDominanceSignal | Dominance trend = risk-on/off |
| B14 | ExchangeReserves | Coin reserve flow (withdrawal = bullish) |
| B15 | MultiTimeframeSignal | Momentum alignment 1m/5m/15m/1h |
| B16 | StablecoinFlow | USDT market cap change = liquidity flow |
| B17 | MempoolMonitor | Pending ETH txs → large DEX swap early warning |
| B18 | BridgeTracker | Cross-chain bridge flow = capital repositioning |
| B19 | EconomicCalendar | CPI/FOMC/NFP imminent event alerts |
| B20 | StressIndex | Composite 0-100 market stress (crypto VIX) |
| B21 | CrossVenueArbitrage | Scans 10 exchanges for price gaps |
| B22 | AdaptiveRiskManager | Dynamic Kelly scaling from stress + loss streaks |
| B23 | DeFiYieldScanner | Safe yield for idle capital (DefiLlama) |
| B24 | SentimentNLP | Fast keyword crypto sentiment (no LLM) |
| B25 | PortfolioOptimizer | Markowitz mean-variance allocation |

---

## The Web GUI

```bash
python scripts/dashboard.py           # http://localhost:8080
```

9 tabs, all controlling the live backend:

| Tab | What it does |
|-----|-------------|
| **Dashboard** | Balance, P&L, positions, crowd regime, start/stop trading |
| **Crowd Engine** | 8 signal bars (live), fusion weights, engine stats |
| **Alpha Swarm** | 5 agents + regime weights |
| **Risk** | Kill switch, Kelly, drawdown, equity |
| **Wallet** | Withdraw form (TOTP), panic button, daily cap |
| **Profiles** | Create/switch/delete credential profiles |
| **Markets** | Cross-venue prices (10 exchanges), best buy/sell, spread |
| **Web3** | MetaMask wallet balances (5 chains) |
| **Settings** | Keystore — set/delete individual keys |

Real-time WebSocket updates. Glassmorphism design.

---

## 10 Exchanges Supported

| Exchange | Canada | Ticker | Executor |
|----------|--------|--------|----------|
| **OKX** | ✅ | ✅ Live | ✅ Full (trade + withdraw) |
| **Binance** | ⚠️ Ontario | ✅ Live | ✅ Full |
| **Kraken** | ✅ 🇨🇦 | ✅ Live | Ticker |
| **Coinbase** | ✅ 🇨🇦 | ✅ Live | Ticker |
| **Bybit** | ✅ | ✅ Live | Ticker |
| **KuCoin** | ✅ | ✅ Live | Ticker |
| **MEXC** | ✅ | ✅ Live | Ticker |
| **Gemini** | ✅ 🇨🇦 | ✅ Live | Ticker |
| **Crypto.com** | ✅ 🇨🇦 | ✅ Live | Ticker |
| **Gate.io** | ✅ | ✅ Live | Ticker |

The **MultiVenueAggregator** reads all 10 in parallel, finds best buy/sell venue, detects divergences.

---

## Wallet Security

| Layer | What it does |
|-------|-------------|
| **TOTP** | Every withdrawal needs a 6-digit code from your phone |
| **Daily cap** | Max $500/day default (configurable) |
| **Panic switch** | `omega panic` freezes ALL withdrawals instantly |
| **Audit log** | Every attempt logged (chained SHA-256 hash) |

A leaked API key alone **cannot withdraw** without your TOTP.

---

## PPO Training Results

Agents trained on **real OKX historical data** (15k candles):

| | Random | **Trained Trend** | **Trained Meanrev** |
|---|---|---|---|
| Return ($100) | −0.84% | **+4.06%** | **+1.89%** |
| Sharpe | −12.70 | **+58.49** | **+26.50** |

---

## Project Structure

```
omega/
├── orchestrator.py             # 7-layer coordinator
├── config/
│   ├── settings.py             # env-driven config
│   ├── keystore.py             # persistent credential store (obfuscated)
│   ├── profiles.py             # named profile switching
│   └── exchanges.py            # 14-exchange registry
├── data_nexus/                  # Layer 1
│   ├── okx_feed.py             # OKX WS
│   ├── binance_feed.py         # Binance WS
│   ├── multi_venue.py          # 10-exchange aggregator
│   └── ...
├── crowd_engine/               # Layer 1.5 (8 signals + fusion + V4 optimizer)
├── alpha_swarm/                # Layer 2 (5 agents)
│   ├── ppo_agent.py            # trained PPO (trend + meanrev)
│   ├── contrarian_agent.py     # fades crowd extremes
│   └── ...
├── breakthroughs/              # Layer 2.5 (25 alpha modules)
├── regime/                     # Layer 3 (HMM + weight router)
├── risk_aegis/                 # Layer 4 (Kelly, MC, kill switch)
├── execution/                  # Layer 5 (OKX, Binance, wallet, SOR)
├── web3/                       # MetaMask wallet (5 chains)
├── web/                        # REST API + glassmorphism GUI
└── cli/                        # dashboard + keys + profiles CLI

scripts/
├── dashboard.py                # GUI launcher
├── live_trade.py               # live trading entry
├── train_ppo.py                # PPO training (actually learns)
├── backtest.py                 # backtest engine
├── download_okx_history.py     # real data downloader

tests/                           # 54 tests (5 suites)
```

---

## Configuration

| Env Var | Default | Purpose |
|---------|---------|---------|
| `OKX_API_KEY` | — | OKX API key |
| `OKX_API_SECRET` | — | OKX secret |
| `OKX_PASSPHRASE` | — | OKX passphrase |
| `OKX_DEMO` | `false` | Paper trading |
| `OMEGA_PAPER` | `false` | Paper-live (real data, log orders only) |
| `OMEGA_TOTP_SECRET` | — | Wallet TOTP secret |
| `OMEGA_DAILY_CAP_USD` | `500` | Daily withdrawal cap |
| `OMEGA_MIN_NOTIONAL_USD` | `2` | Min trade size (micro-capital friendly) |
| `OMEGA_SYMBOLS` | `BTCUSDT,ETHUSDT,SOLUSDT` | Trading symbols |
| `OMEGA_RISK_PER_TRADE_PCT` | `1.0` | Max % equity per trade |
| `OMEGA_RISK_KELLY_FRACTION` | `0.25` | Quarter-Kelly |
| `BINANCE_API_KEY` | — | Binance (fallback) |
| `WEB3_RPC_URL` | — | Ethereum RPC (Alchemy/Infura) |

---

## Tests

```
tests/
├── test_smoke.py            12  layer smoke tests
├── test_regression.py       11  audit bug regression
├── test_crowd_engine.py     16  fusion + contrarian + optimizer
├── test_okx.py              10  OKX + wallet + TOTP + venue
└── test_microstructure.py    5  iceberg + inflow
                          ───────
                           54 total
```

---

## What OMEGA does NOT do

- **No market manipulation** (no spoofing, no intentional sweeping, no forced cascades)
- **No MEV insertion** (mempool reading is signal-only)
- **No HFT arms race** (minutes-to-days horizons)
- **No frontrunning client orders** (iceberg detection is passive)

---

## Disclaimer

Trading cryptocurrencies involves substantial risk of loss. The contrarian thesis degrades if it becomes popular. **Never deploy with capital you cannot afford to lose.** Always start with `--paper` mode, paper-trade for weeks before live trading.

MIT License. Provided as-is.
