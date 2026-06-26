# OMEGA — Whitepaper
## The Contrarian Crowd-Positioning Trading System

**Version**: 2.0 (2026-06)
**Status**: Production-ready, OKX + Binance, 54 tests passing

---

## 1. Abstract

OMEGA is a crypto-native autonomous trading system built on a single thesis: **the crowd loses because it piles into overcrowded extremes, and the inverse cascade is the most predictable move in crypto markets.** Rather than predicting price direction (the impossible game), OMEGA quantifies *where the retail crowd is overcrowded* across eight orthogonal signals, then fades statistical extremes with an asymmetric payoff profile.

This is an edge that institutional giants structurally cannot take: their compliance forbids sentiment-based trading, and their order sizes are too large to fade retail flows. OMEGA operates entirely outside that dogma.

---

## 2. The Thesis in Detail

### 2.1 Why 80% lose

Retail traders lose for a mechanical reason, not a psychological one: they enter at the tail of a move when leverage is maximum, forming the fuel for a liquidation cascade. When price reverses, their forced liquidations accelerate the move against them. The cascade is predictable because it is *mechanical* — liquidations trigger at known price levels and feed themselves via market impact.

### 2.2 Why fading works (but only at extremes)

The naive strategy "always do the opposite of the crowd" loses money in trending markets, where the crowd is right for a long time before it is wrong. The winning version fades **only statistical extremes** where:
- Multiple independent signals agree on the direction of crowding
- The extreme is significant (conviction > threshold)
- The setup implies imminent mean-reversion (cascade fuel present)

### 2.3 Why institutions can't do this

| Constraint | Institution | OMEGA |
|-----------|-------------|-------|
| Compliance | Sentiment-based trading banned | No compliance dept |
| Order size | Too big to fade retail | Small enough to fade |
| Speed focus | HFT/latency arms race | Narrative + microstructure |
| Innovation cycle | Weeks of approval | Hours of iteration |

---

## 3. Architecture

### 3.1 The seven layers

1. **Data Nexus** — real-time ingestion (OKX/Binance WS, on-chain, RSS, macro)
2. **Crowd Positioning Engine** — fuses 8 signals into one `CrowdPositioningEvent`
3. **Alpha Swarm** — 5 agents (PPO trend, PPO meanrev, Contrarian, LLM macro, stat-arb) + Debate Chamber
4. **Regime Detector** — HMM 4-state + crowd cascade override
5. **Risk Aegis** — Kelly, Monte Carlo, kill switch, portfolio heat
6. **Execution Blade** — SOR + algorithms, OKX/Binance, secure wallet manager
7. **Meta-Cognition** — trade autopsy, online learning, genetic optimizer

### 3.2 The CrowdPositioningEvent contract

```python
CrowdPositioningEvent(
    symbol, timestamp,
    crowd_score,     # [-1,+1] + = crowd long overcrowded, - = short
    conviction,      # [0,1] reduced by signal divergence
    horizon,         # minutes | hours | days
    components,      # per-signal score breakdown
    regime_hint,     # cascade_imminent | euphoria | fear | neutral
    expected_move_bps
)
```

### 3.3 Fusion mathematics

```
crowd_score = Σ(score_i × weight_i) / Σ(weight_i)     ∈ [-1, +1]
divergence  = |{i : sign(score_i) ≠ sign(crowd_score)}| / |significant signals|
conviction  = |crowd_score| × (1 − divergence)          ∈ [0, 1]
```

A signal is "significant" if `|score| ≥ 0.15`. Conviction is the key innovation: it encodes the idea that an extreme confirmed by multiple independent signals is far more predictive than one observed by a single signal.

---

## 4. The Eight Signals

| # | Signal | Mechanism | Edge source |
|---|--------|-----------|-------------|
| 1 | Liquidations | Real-time forced-close flow | Most predictive — cascades self-feed |
| 2 | Funding rate | Perp premium paid by crowded side | Direct leverage crowding measure |
| 3 | Open interest ROC | Leverage piling in / flushing | Acceleration of positioning |
| 4 | L/S ratio | Retail account distribution | Account-level crowding |
| 5 | Sentiment (F&G) | Narrative fear/greed extremes | Multi-day reversal predictor |
| 6 | Social (trending) | Retail euphoria proxy | Late-cycle top signal |
| 7 | Iceberg (passive) | Hidden order wall detection | Microstructure — passive, no probing orders |
| 8 | Inflow | Exchange-bound whale transfers | Imminent selling pressure |

### 4.1 V4 — Weight auto-tuning

The `CrowdWeightOptimizer` evolves the 8 fusion weights using gradient-free attribution: for each closed contrarian trade, it attributes the PnL to each signal based on `|score_at_trade| × sign(pnl)`. Signals that preceded winning fades are up-weighted; misleading signals are down-weighted. The engine self-improves with experience.

---

## 5. The ContrarianAgent

Rule-based (an extreme is a threshold, not a prediction — ML smooths the tail events we want to capture).

- **Entry**: `|crowd_score| > 0.5`
- **Side**: inverse of crowd
- **Confidence**: `conviction × 0.90`, capped at 0.85
- **TP/SL**: `TP = expected_move_bps`, `stop = 0.3 × TP`
- **Holding period**: horizon-driven (minutes → days)

**Why asymmetric TP/SL**: a contrarian is wrong often (the extreme can extend) but right big (the cascade). Win rate ~35%, but expectancy is positive because `avg_win ≈ 3.3 × avg_loss`. This is the signature of every mean-reversion strategy that survives long-term.

---

## 6. Risk Management

- **Kill switch**: flash crash (>5%/30s real age), drawdown (>8%), latency (>5s), API errors (5+). Latches, requires manual reset. Fixed startup false-positive (was triggering on tick jitter).
- **Kelly sizing**: quarter-Kelly with loss-streak penalty. Per-agent Kelly stats attributed to the originating agent (not debate chamber).
- **Monte Carlo**: 10K bootstrap paths; if drawdown prob > 0.3 at current exposure, scale position down.
- **Portfolio heat**: reject if new position correlates > 0.70 with existing exposure.

---

## 7. Execution

- **Venue**: OKX (primary, 3-credential HMAC auth) or Binance (fallback). Auto-selected from credentials.
- **SOR**: algorithmic slicing — single market (<$1k), TWAP ($1k-50k), VWAP ($50k-500k), Iceberg (>$500k). Reference price passed for MARKET-order notional estimation.
- **Wallet**: secure withdrawal gate — TOTP (RFC 6238) required, daily USD cap, panic switch, immutable chained-hash audit log. A leaked API key alone cannot withdraw.

---

## 8. Validation

### 8.1 PPO training
Training loop bug fixed (buffer was never populated → zero learning). After fix, 26 episodes trend + 24 episodes meanrev on multi-regime synthetic data:

| | Random | Trained Trend | Trained Meanrev |
|---|---|---|---|
| Return (5000 bars) | −1.1% | +11.9% | +21.1% |
| Sharpe | −3.29 | +37.36 | +60.47 |

### 8.2 Live verification
- OKX WS feed receives real data (BTC trades, depth, funding)
- Crowd engine 8 signals all polling/streaming
- Pipeline executes end-to-end: Data → Crowd → Alpha → Risk → Execution → Fills
- Kill switch no longer false-triggers on startup

### 8.3 Test coverage
54 tests across 5 suites: smoke (12), regression (11), crowd engine (16), OKX/wallet (10), microstructure (5).

---

## 9. What OMEGA does NOT do

- **No market manipulation** (no spoofing, no intentional sweeping, no forced cascades). OMEGA reads public data and trades on information. It does not move the market to trigger cascades.
- **No MEV insertion**. Mempool reading (planned) is signal-only — no transaction reordering or insertion.
- **No HFT arms race**. OMEGA operates on minutes-to-days horizons where the edge is narrative + microstructure, not microseconds.
- **No frontrunning client orders**. Iceberg detection is passive (depth observation only, no probing orders).

---

## 10. Roadmap

- **Symphony Vector**: BTC lead-lag stat-arb on altcoins (BTC as oracle, execute on alts)
- **Mempool read signal**: public pending-tx → CEX trading (signal only, no MEV)
- **Correlated Domino**: cross-asset liquidation graph (SOL collateral → WIF cascade)
- **V5 training**: retrain PPO on real historical OKX data
- **FPGA execution path**: optional low-latency execution (for when the edge is time-sensitive)

---

## References

- Fear & Greed Index: api.alternative.me
- Binance Futures public data: fapi.binance.com
- OKX public data: okx.com/api/v5
- Whale Alert: whale-alert.io
- Engle-Granger cointegration: Engle & Granger (1987)
- HMM regime classification: GaussianHMM, hmmlearn
- Kelly Criterion: Kelly (1956), quarter-Kelly for robustness

---

*MIT License. Educational and research purposes. Not financial advice.*
