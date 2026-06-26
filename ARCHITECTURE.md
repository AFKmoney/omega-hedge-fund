# OMEGA — Architecture

Deep technical reference for the OMEGA system. For the thesis and high-level
overview, see `WHITEPAPER.md`. For setup and usage, see `README.md`.

---

## Event flow

```
OKX/Binance WS ─┐
on-chain feeds  ─┤
RSS news        ─┼─▶ DataNexus ──▶ MarketEvent/NewsEvent/OnChainEvent
FRED macro      ─┘                        │
                                          ▼
                                CrowdPositioningEngine
                          (8 signals → CrowdPositioningEvent)
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    ▼                     ▼                     ▼
           RegimeDetector         ContrarianAgent        RegimeWeightRouter
           (HMM 4-state)          (fade extremes)        (reconfigures swarm)
                    │                     │
                    └──────────┬──────────┘
                               ▼
                        AlphaSwarm.on_market()
                    (PPO + LLM + StatArb + Contrarian)
                               │
                               ▼
                       DebateChamber.submit()
                    (weighted vote → SignalEvent)
                               │
                               ▼
                     RiskAegis.on_signal()
              (Kelly → MC → kill switch → heat)
                               │
                               ▼
                    ExecutionBlade.submit()
                  (SOR → OKX/Binance REST)
                               │
                               ▼
                         FillEvent
                               │
                               ▼
                    MetaCognition.on_fill()
           (autopsy → online learning → GA)
```

---

## Layer 1 — Data Nexus

**Files**: `omega/data_nexus/`

| Component | Role |
|-----------|------|
| `okx_feed.py` | OKX public WS — trades, books5, mark-price (funding), liquidation-orders |
| `binance_feed.py` | Binance WS — trades, depth20, @markPrice, ticker (fallback venue) |
| `etherscan_feed.py` | ETH whale transfers (on-chain) |
| `news_feed.py` | RSS headlines + LLM sentiment scoring (async, non-blocking) |
| `macro_feed.py` | FRED macro indicators |
| `kafka_bus.py` | Kafka event bus (in-process fallback) |
| `vector_store.py` | Milvus vector store (NumPy fallback) |
| `nexus.py` | Layer orchestrator |

**Venue abstraction**: the orchestrator auto-selects OKX if its 3 credentials are present, else Binance. Both feeds emit the same `MarketEvent` contract, so downstream layers are exchange-agnostic.

---

## Layer 1.5 — Crowd Positioning Engine

**Files**: `omega/crowd_engine/`

The contrarian brain. Fuses 8 positioning signals into one normalized event.

### Signal contract

Every signal implements `PositioningSignal`:
```python
class PositioningSignal(abc.ABC):
    name: str
    def reading_for(self, symbol: str) -> Optional[SignalReading]
    def reading(self) -> Optional[SignalReading]   # aggregate
```

`SignalReading` = `{score ∈ [-1,+1], horizon, weight, raw}`.

### The 8 signals

| Signal | File | Reactive? | Source |
|--------|------|-----------|--------|
| liquidations | `liquidation_signal.py` | WS stream | `!forceOrder@arr` / `liquidation-orders` |
| funding | `funding_signal.py` | on_market | `@markPrice` / `mark-price` |
| open_interest | `open_interest_signal.py` | REST poll | `openInterestHist` |
| ls_ratio | `ls_ratio_signal.py` | REST poll | `globalLongShortAccountRatio` |
| sentiment | `sentiment_signal.py` | REST poll | Fear & Greed API |
| social | `social_signal.py` | REST poll | CoinGecko trending |
| iceberg | `iceberg_signal.py` | on_market | depth feed (passive) |
| inflow | `inflow_signal.py` | REST poll | Whale Alert / on-chain |

### Engine lifecycle

```python
engine = CrowdPositioningEngine(symbols=("BTCUSDT",))
await engine.start()              # start polling/streaming tasks
event = engine.on_market(market)  # returns CrowdPositioningEvent or None
await engine.stop()
```

The engine emits only when `|crowd_score| ≥ emit_threshold` (0.20) and the score moved by `≥ reemit_delta` (0.10) since last emit — prevents spamming.

### V4 auto-tuning

```python
optimizer = CrowdWeightOptimizer(signal_names=(...))
optimizer.record_trade(pnl_bps, components_at_trade)
new_weights = optimizer.maybe_tune()   # fires every eval_window trades
engine.set_weights(new_weights)
```

Attribution: `mean(|score_i| × sign(pnl))` per signal. Gradient-ascent style update + exploration noise.

---

## Layer 2 — Alpha Swarm

**Files**: `omega/alpha_swarm/`

### Agents

| Agent | Type | Role |
|-------|------|------|
| PPO Trend | RL (PyTorch) | Capture directional moves |
| PPO Meanrev | RL (PyTorch) | Fade deviations from rolling mean |
| Contrarian | Rule-based | Fade crowd-positioning extremes (the thesis core) |
| LLM Macro | LLM (z-ai CLI) | Read news+macro → directional views |
| Stat-Arb | Cointegration | Engle-Granger pair trading |

### Debate Chamber

Weighted vote aggregator. Each agent's signal is weighted by the regime matrix. Emits a consolidated `SignalEvent` when quorum is met and votes agree.

---

## Layer 3 — Regime Detector

**Files**: `omega/regime/`

- `hmm_detector.py` — GaussianHMM 4-state (calm_bull, volatile_bull, choppy, bear)
- `weight_router.py` — maps regime → agent weights, includes crowd cascade override

When the crowd engine reports `cascade_imminent`, the router overrides to `crowd_cascade_long/short` (trend defunded to 0.05, contrarian boosted to 0.50).

---

## Layer 4 — Risk Aegis

**Files**: `omega/risk_aegis/`

Pipeline (in order):
1. `kill_switch.is_triggered` → reject
2. `confidence < 0.55` → reject
3. `kelly.size()` → quarter-Kelly with loss-streak penalty
4. `monte_carlo.run()` → scale down if drawdown prob > 0.3
5. `portfolio_heat.check()` → reject if correlation > 0.70

Kill switch latches on: flash crash (>5% over ≥30s real window age), drawdown (>8%), latency (>5s), API errors (5+).

---

## Layer 5 — Execution

**Files**: `omega/execution/`

### Venue abstraction

```python
class Executor(abc.ABC):
    async def submit(order) -> str
    async def cancel(exchange_id) -> bool
    async def get_balance(ccy) -> float
    async def cancel_all() -> int
    async def fetch_open_orders(symbol) -> list
    async def fetch_balance() -> dict
```

`OKXExecutor` and `BinanceExecutor` both implement this. The `SmartOrderRouter` holds a dict of executors and routes by venue name.

### SOR algorithm selection

| Notional | Algorithm |
|----------|-----------|
| < $1k | single market order |
| $1k–$50k | TWAP × 5 slices |
| $50k–$500k | VWAP @ 10% participation |
| > $500k | Iceberg @ 5% display |

Reference price passed for MARKET-order notional estimation (limit_price is None for MARKET).

### Wallet Manager

```python
wallet.withdraw(ccy, amt, addr, chain, totp_code) -> dict
wallet.panic()                          # freeze all
wallet.unfreeze(totp_code)              # re-enable
wallet.set_daily_cap(new_cap, totp_code)
```

Layers: TOTP verify → panic check → daily cap check → execute → log (chained SHA-256 hash).

---

## Layer 6 — Meta-Cognition

**Files**: `omega/meta_cognition/`

- `trade_autopsy.py` — LLM analyzes closed trades every 10 closes
- `online_learning.py` — periodic retraining of underperforming agents
- `genetic_optimizer.py` — agents with negative Sharpe over 30 days are killed and mutated

The crowd engine's V4 optimizer feeds off the PnL of contrarian trades closed here.

---

## Testing

```
tests/
├── test_smoke.py            12  layer smoke tests
├── test_regression.py       11  audit bug regression (each = a fixed bug)
├── test_crowd_engine.py     16  fusion + contrarian + optimizer
├── test_okx.py              10  OKX + wallet + TOTP + venue
└── test_microstructure.py    5  iceberg + inflow
                          ───────
                           54 total
```

Run all:
```bash
for t in tests/test_*.py; do python "$t"; done
```

---

## Key design decisions

1. **Contrarian is rule-based, not ML** — extremes are thresholds; ML smooths the tail events we want.
2. **Conviction drops on divergence** — the core innovation. Agreement across independent signals is the signal.
3. **TP/SL asymmetric (3.3:1)** — small frequent losses, large cascade gains. Positive expectancy at ~35% win rate.
4. **TOTP on withdrawals, not just API key** — a leaked key alone cannot drain the account.
5. **Passive iceberg detection** — no probing orders (would cost spread + tip off the whale).
6. **Kill switch requires real 30s+ window age** — prevents startup false-positives from tick jitter.
