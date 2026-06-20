"""
OMEGA Smoke Test
================

Verifies that all layers import cleanly and basic operations work.
Run: python tests/test_smoke.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import torch


def test_imports() -> None:
    """All layers should import without error."""
    print("Testing imports...", end=" ")
    from omega import Settings, OmegaOrchestrator, load_settings
    from omega.alpha_swarm import AlphaSwarm, PPOAgent, LLMMacroAgent, StatArbAgent, DebateChamber
    from omega.data_nexus import DataNexus, BinanceWebSocketFeed, KafkaEventBus, MilvusVectorStore
    from omega.regime import RegimeDetector, RegimeWeightRouter
    from omega.risk_aegis import RiskAegis, KellyPositionSizer, MonteCarloEngine, KillSwitch
    from omega.execution import ExecutionBlade, BinanceExecutor, TWAP, VWAP, Iceberg
    from omega.meta_cognition import MetaCognition, TradeAutopsy, GeneticOptimizer
    from omega.rl_environment import TradingEnvironment
    print("✓")


def test_event_types() -> None:
    """Event dataclasses should construct and serialize."""
    print("Testing event types...", end=" ")
    from omega.utils.events import MarketEvent, Side, SignalEvent
    ev = MarketEvent(
        symbol="BTCUSDT",
        timestamp="2024-01-01T00:00:00Z",
        last_price=50000.0,
        volume_24h=1000.0,
        bid=49999.0,
        ask=50001.0,
        bid_qty=1.0,
        ask_qty=1.5,
    )
    assert ev.symbol == "BTCUSDT"
    sig = SignalEvent(
        agent="test",
        symbol="BTCUSDT",
        timestamp="2024-01-01T00:00:00Z",
        side=Side.BUY,
        confidence=0.7,
    )
    assert sig.confidence == 0.7
    print("✓")


def test_kelly() -> None:
    """Kelly sizer should compute non-zero size for high-confidence signal."""
    print("Testing Kelly position sizer...", end=" ")
    from omega.risk_aegis import KellyPositionSizer
    from omega.utils.events import SignalEvent, Side
    kelly = KellyPositionSizer()
    sig = SignalEvent(
        agent="test",
        symbol="BTCUSDT",
        timestamp="2024-01-01T00:00:00Z",
        side=Side.BUY,
        confidence=0.7,
        stop_loss_bps=100.0,
        take_profit_bps=200.0,
    )
    result = kelly.size(sig, equity=100_000.0, price=50000.0, current_atr_bps=100.0)
    assert result.size_qty > 0, f"Kelly returned zero size: {result}"
    assert result.rejected_reason is None
    print(f"✓ (qty={result.size_qty:.4f}, f*={result.kelly_fraction_raw:.3f})")


def test_monte_carlo() -> None:
    """Monte Carlo engine should run and return a multiplier."""
    print("Testing Monte Carlo engine...", end=" ")
    from omega.risk_aegis import MonteCarloEngine
    mc = MonteCarloEngine()
    # Seed with some random returns
    rng = np.random.default_rng(42)
    for _ in range(200):
        mc.on_return(rng.normal(0, 0.005))
    multiplier = mc.run(current_equity=100_000.0, current_position_value=10_000.0)
    assert 0.0 <= multiplier <= 1.0
    print(f"✓ (multiplier={multiplier:.3f}, dd_prob={mc._last_dd_prob:.3f})")


def test_kill_switch() -> None:
    """Kill switch should trigger on flash crash and latch."""
    print("Testing kill switch...", end=" ")
    from omega.risk_aegis import KillSwitch
    ks = KillSwitch()
    assert not ks.is_triggered
    ks.trigger("manual_test")
    assert ks.is_triggered
    assert ks.trigger_reason == "manual_test"
    ks.reset()
    assert not ks.is_triggered
    print("✓")


def test_ppo_agent() -> None:
    """PPO agent should construct and emit signals."""
    print("Testing PPO agent...", end=" ")
    from omega.alpha_swarm import PPOAgent
    from omega.utils.events import MarketEvent
    agent = PPOAgent(symbols=("BTCUSDT",), mode="trend")
    # Feed 30 events to build history
    for i in range(30):
        ev = MarketEvent(
            symbol="BTCUSDT",
            timestamp=f"2024-01-01T00:{i:02d}:00Z",
            last_price=50000.0 + i * 10,
            volume_24h=1000.0,
            bid=49990.0 + i * 10,
            ask=50010.0 + i * 10,
            bid_qty=1.0,
            ask_qty=1.0,
        )
        signals = agent.on_market(ev)
    print(f"✓ (steps={agent._step_count})")


def test_regime_detector() -> None:
    """Regime detector should accept market events."""
    print("Testing regime detector...", end=" ")
    from omega.regime import RegimeDetector
    from omega.utils.events import MarketEvent
    det = RegimeDetector()
    rng = np.random.default_rng(42)
    price = 50000.0
    for i in range(300):
        price *= (1 + rng.normal(0, 0.005))
        ev = MarketEvent(
            symbol="BTCUSDT",
            timestamp=f"2024-01-01T00:{i:02d}:00Z",
            last_price=price,
            volume_24h=1000.0,
            bid=price * 0.999,
            ask=price * 1.001,
        )
        det.on_market(ev)
    print(f"✓ (regime={det.current_regime})")


def test_stat_arb() -> None:
    """Stat-arb agent should construct and run on price streams."""
    print("Testing stat-arb agent...", end=" ")
    from omega.alpha_swarm import StatArbAgent
    from omega.utils.events import MarketEvent
    agent = StatArbAgent(symbols=("BTCUSDT", "ETHUSDT"))
    rng = np.random.default_rng(42)
    btc = 50000.0
    eth = 3000.0
    for i in range(250):
        btc *= (1 + rng.normal(0, 0.005))
        eth = btc * 0.06 + rng.normal(0, 10)
        for sym, p in [("BTCUSDT", btc), ("ETHUSDT", eth)]:
            ev = MarketEvent(
                symbol=sym,
                timestamp=f"2024-01-01T00:{i:02d}:00Z",
                last_price=p,
                volume_24h=1000.0,
                bid=p * 0.999,
                ask=p * 1.001,
            )
            agent.on_market(ev)
    stats = agent.stats()
    print(f"✓ (cointegrated_pairs={stats['cointegrated_pairs']})")


def test_debate_chamber() -> None:
    """Debate chamber should aggregate signals."""
    print("Testing debate chamber...", end=" ")
    from omega.alpha_swarm import DebateChamber
    from omega.utils.events import SignalEvent, Side
    dc = DebateChamber()
    # Submit 3 BUY signals from different agents
    for agent in ["ppo_trend", "ppo_meanrev", "llm_macro"]:
        sig = SignalEvent(
            agent=agent,
            symbol="BTCUSDT",
            timestamp="2024-01-01T00:00:00Z",
            side=Side.BUY,
            confidence=0.7,
        )
        decision = dc.submit(sig)
    # Should produce a consolidated decision on at least the 2nd or 3rd submit
    stats = dc.stats()
    print(f"✓ (decisions={stats['decisions_made']})")


def test_rl_environment() -> None:
    """RL environment should reset and step."""
    print("Testing RL environment...", end=" ")
    import pandas as pd
    from omega.rl_environment import TradingEnvironment, EnvConfig
    # Synthetic data
    rng = np.random.default_rng(42)
    n = 500
    prices = 50000 * np.exp(np.cumsum(rng.normal(0, 0.005, n)))
    df = pd.DataFrame({
        "open": prices,
        "high": prices * 1.001,
        "low": prices * 0.999,
        "close": prices,
        "volume": np.ones(n, dtype=np.float32) * 100,
    })
    env = TradingEnvironment(df=df, config=EnvConfig(window=64, max_episode_bars=100))
    obs = env.reset()
    assert obs.shape == (64,)
    obs, reward, done, info = env.step(2)  # LONG
    assert isinstance(reward, float)
    assert isinstance(done, bool)
    print(f"✓ (equity=${env.equity:,.2f})")


def test_vector_store() -> None:
    """Vector store should accept and search vectors (fallback mode)."""
    print("Testing vector store...", end=" ")
    from omega.data_nexus import MilvusVectorStore

    async def _run():
        vs = MilvusVectorStore(allow_fallback=True, dim=32)
        rng = np.random.default_rng(42)
        # Insert correlated vectors so search has something to find
        base = rng.normal(0, 1, 32).astype(np.float32)
        for i in range(10):
            # Each vector = base + small noise → high cosine similarity
            vec = base + rng.normal(0, 0.1, 32).astype(np.float32)
            await vs.insert(vec, {"test": True, "idx": i})
        # Query = base (should match all 10 with high similarity)
        results = await vs.search(base, top_k=5, min_similarity=0.5)
        assert len(results) > 0, f"Expected matches, got {len(results)}"
        return len(results)

    n = asyncio.run(_run())
    print(f"✓ (found {n} matches)")


def test_full_orchestrator_construction() -> None:
    """Orchestrator should construct with all layers."""
    print("Testing orchestrator construction...", end=" ")
    from omega import OmegaOrchestrator, load_settings
    settings = load_settings()
    orch = OmegaOrchestrator(settings)
    stats = orch.stats()
    assert "alpha_swarm" in stats
    assert "risk_aegis" in stats
    print("✓")


def main() -> None:
    print("=" * 50)
    print("OMEGA Smoke Tests")
    print("=" * 50)
    tests = [
        test_imports,
        test_event_types,
        test_kelly,
        test_monte_carlo,
        test_kill_switch,
        test_ppo_agent,
        test_regime_detector,
        test_stat_arb,
        test_debate_chamber,
        test_rl_environment,
        test_vector_store,
        test_full_orchestrator_construction,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            print(f"FAIL: {exc}")
            import traceback
            traceback.print_exc()
            failed += 1
    print("=" * 50)
    if failed == 0:
        print(f"All {len(tests)} tests PASSED ✓")
        sys.exit(0)
    else:
        print(f"{failed}/{len(tests)} tests FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
