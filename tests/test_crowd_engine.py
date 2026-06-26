"""
OMEGA Crowd Positioning Engine Tests
=====================================

Unit + integration tests for the contrarian brain (Layer 1.5).
Run: python tests/test_crowd_engine.py

Covers:
    - FundingRateSignal: tanh normalization, sign, saturation
    - LSRatioSignal: long_pct -> score mapping
    - SentimentSignal: Fear&Greed extremes -> score
    - Engine fusion: weighted sum, divergence deflates conviction
    - ContrarianAgent: no signal under threshold, fades extremes, TP/SL asymmetry
    - Swarm integration: on_positioning routes to contrarian
    - Orchestrator integration: crowd event reconfigures regime weights
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# FundingRateSignal
# ---------------------------------------------------------------------------

def test_funding_signal_normalization() -> None:
    """Positive funding -> crowd long overcrowded -> positive score; saturates."""
    print("Testing funding signal normalization...", end=" ")
    from omega.crowd_engine import FundingRateSignal
    sig = FundingRateSignal(threshold=0.0005)
    # No data -> None
    assert sig.reading_for("BTCUSDT") is None
    # Mild positive funding
    sig.update("BTCUSDT", 0.0001)
    r = sig.reading_for("BTCUSDT")
    assert r is not None and 0 < r.score < 0.8, f"mild funding score wrong: {r}"
    # Extreme positive funding (5x threshold) -> saturates near +1
    sig.update("BTCUSDT", 0.0025)
    r = sig.reading_for("BTCUSDT")
    assert r is not None and r.score > 0.98, f"extreme funding should saturate: {r}"
    # Negative funding -> crowd short overcrowded -> negative score
    sig.update("BTCUSDT", -0.0025)
    r = sig.reading_for("BTCUSDT")
    assert r is not None and r.score < -0.98, f"negative funding score wrong: {r}"
    print("✓")


# ---------------------------------------------------------------------------
# LSRatioSignal
# ---------------------------------------------------------------------------

def test_ls_ratio_signal_mapping() -> None:
    """60% long -> score +0.2; 100% long -> +1.0; 50/50 -> 0."""
    print("Testing L/S ratio mapping...", end=" ")
    from omega.crowd_engine import LSRatioSignal
    sig = LSRatioSignal(symbols=("BTCUSDT",))
    # Inject directly (skip the network poll)
    sig._long_pct["BTCUSDT"] = 60.0
    r = sig.reading_for("BTCUSDT")
    assert r is not None and abs(r.score - 0.2) < 1e-6, f"60% long: {r}"
    sig._long_pct["BTCUSDT"] = 100.0
    r = sig.reading_for("BTCUSDT")
    assert r is not None and abs(r.score - 1.0) < 1e-6, f"100% long: {r}"
    sig._long_pct["BTCUSDT"] = 50.0
    r = sig.reading_for("BTCUSDT")
    assert r is not None and abs(r.score) < 1e-6, f"50/50: {r}"
    sig._long_pct["BTCUSDT"] = 20.0  # mostly short
    r = sig.reading_for("BTCUSDT")
    assert r is not None and r.score < -0.5, f"20% long (mostly short): {r}"
    print("✓")


# ---------------------------------------------------------------------------
# SentimentSignal
# ---------------------------------------------------------------------------

def test_sentiment_signal_extremes() -> None:
    """F&G=95 -> greed extreme -> positive; F&G=5 -> fear -> negative; 50 -> 0."""
    print("Testing sentiment extremes...", end=" ")
    from omega.crowd_engine import SentimentSignal
    sig = SentimentSignal()
    sig._fg_value = 95
    r = sig.reading_for("BTCUSDT")
    assert r is not None and r.score > 0.5, f"F&G=95 greed: {r}"
    sig._fg_value = 5
    r = sig.reading_for("BTCUSDT")
    assert r is not None and r.score < -0.5, f"F&G=5 fear: {r}"
    sig._fg_value = 50
    r = sig.reading_for("BTCUSDT")
    assert r is not None and abs(r.score) < 1e-6, f"F&G=50 neutral: {r}"
    print("✓")


# ---------------------------------------------------------------------------
# Engine fusion + conviction
# ---------------------------------------------------------------------------

def test_engine_fusion_agreement_boosts_conviction() -> None:
    """When all 3 signals agree on direction, conviction is high."""
    print("Testing engine fusion: agreement boosts conviction...", end=" ")
    from omega.crowd_engine import CrowdPositioningEngine
    from omega.utils.events import MarketEvent

    eng = CrowdPositioningEngine(symbols=("BTCUSDT",), emit_threshold=0.0, reemit_delta=0.0)
    # All three signals screaming "crowd long overcrowded"
    eng.funding.update("BTCUSDT", 0.0020)       # extreme positive funding
    eng.ls_ratio._long_pct["BTCUSDT"] = 80.0    # 80% long
    eng.sentiment._fg_value = 95                 # extreme greed
    # Trigger compute via a market event carrying the funding rate
    ev = MarketEvent(symbol="BTCUSDT", timestamp="t", last_price=50000.0,
                     volume_24h=1.0, bid=49999.0, ask=50001.0, funding_rate=0.0020)
    crowd = eng.on_market(ev)
    assert crowd is not None, "engine should emit when threshold is 0"
    assert crowd.crowd_score > 0.7, f"agreement should push score high: {crowd.crowd_score}"
    assert crowd.conviction > 0.6, f"agreement should boost conviction: {crowd.conviction}"
    assert crowd.regime_hint == "cascade_imminent", f"high conv + score: {crowd.regime_hint}"
    print(f"✓ (score={crowd.crowd_score:+.2f} conv={crowd.conviction:.2f})")


def test_engine_fusion_divergence_deflates_conviction() -> None:
    """When signals disagree (funding long, sentiment neutral), conviction drops."""
    print("Testing engine fusion: divergence deflates conviction...", end=" ")
    from omega.crowd_engine import CrowdPositioningEngine
    from omega.utils.events import MarketEvent

    eng = CrowdPositioningEngine(symbols=("BTCUSDT",), emit_threshold=0.0, reemit_delta=0.0)
    # Funding says long overcrowded, but L/S and sentiment are neutral
    eng.funding.update("BTCUSDT", 0.0020)
    eng.ls_ratio._long_pct["BTCUSDT"] = 50.0   # neutral
    eng.sentiment._fg_value = 50                # neutral
    ev = MarketEvent(symbol="BTCUSDT", timestamp="t", last_price=50000.0,
                     volume_24h=1.0, bid=49999.0, ask=50001.0, funding_rate=0.0020)
    crowd = eng.on_market(ev)
    assert crowd is not None
    # Score still positive (funding dominates weighted) but conviction LOWER
    # than the agreement case because of divergence.
    assert crowd.conviction < 0.4, (
        f"divergence should deflate conviction, got {crowd.conviction}"
    )
    print(f"✓ (score={crowd.crowd_score:+.2f} conv={crowd.conviction:.2f})")


def test_engine_below_threshold_emits_nothing() -> None:
    """Mild positioning (all neutral) should not emit."""
    print("Testing engine: below threshold emits nothing...", end=" ")
    from omega.crowd_engine import CrowdPositioningEngine
    from omega.utils.events import MarketEvent

    eng = CrowdPositioningEngine(symbols=("BTCUSDT",))  # default emit_threshold=0.20
    eng.funding.update("BTCUSDT", 0.00005)  # tiny
    eng.ls_ratio._long_pct["BTCUSDT"] = 52.0
    eng.sentiment._fg_value = 55
    ev = MarketEvent(symbol="BTCUSDT", timestamp="t", last_price=50000.0,
                     volume_24h=1.0, bid=49999.0, ask=50001.0, funding_rate=0.00005)
    crowd = eng.on_market(ev)
    assert crowd is None, f"mild positioning should not emit, got {crowd}"
    print("✓")


# ---------------------------------------------------------------------------
# ContrarianAgent
# ---------------------------------------------------------------------------

def test_contrarian_no_signal_below_threshold() -> None:
    """A mild crowd score (below 0.5) should produce no signal."""
    print("Testing contrarian: no signal below threshold...", end=" ")
    from omega.alpha_swarm import ContrarianAgent
    from omega.utils.events import CrowdPositioningEvent

    agent = ContrarianAgent(("BTCUSDT",))
    mild = CrowdPositioningEvent(
        symbol="BTCUSDT", timestamp="t", crowd_score=0.3, conviction=0.3,
        horizon="hours", components={"funding": 0.3}, regime_hint="neutral",
    )
    sigs = agent.on_positioning(mild)
    assert sigs == [], f"mild score should not trigger, got {sigs}"
    print("✓")


def test_contrarian_fades_extreme_and_tp_sl_asymmetry() -> None:
    """At an extreme, contrarian takes the opposite side with TP >> stop."""
    print("Testing contrarian: fades extreme + TP/SL asymmetry...", end=" ")
    from omega.alpha_swarm import ContrarianAgent
    from omega.utils.events import CrowdPositioningEvent, Side

    agent = ContrarianAgent(("BTCUSDT",), min_emit_gap_sec=0.0)
    # Crowd long overcrowded -> we SHORT
    extreme_long = CrowdPositioningEvent(
        symbol="BTCUSDT", timestamp="t", crowd_score=0.8, conviction=0.8,
        horizon="hours", expected_move_bps=300.0, components={"funding": 0.8},
        regime_hint="cascade_imminent",
    )
    sigs = agent.on_positioning(extreme_long)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.side == Side.SELL, f"crowd long -> should SELL, got {sig.side}"
    assert sig.confidence <= 0.85, f"confidence capped: {sig.confidence}"
    assert sig.take_profit_bps > sig.stop_loss_bps, "TP must exceed stop"
    # Asymmetry: stop is ~30% of TP
    ratio = sig.stop_loss_bps / sig.take_profit_bps
    assert abs(ratio - 0.30) < 0.05, f"stop/TP ratio should be ~0.30, got {ratio}"
    # Crowd short overcrowded -> we BUY
    extreme_short = CrowdPositioningEvent(
        symbol="BTCUSDT", timestamp="t", crowd_score=-0.8, conviction=0.8,
        horizon="hours", expected_move_bps=300.0, components={"funding": -0.8},
        regime_hint="cascade_imminent",
    )
    sigs = agent.on_positioning(extreme_short)
    assert len(sigs) == 1 and sigs[0].side == Side.BUY, "crowd short -> should BUY"
    print(f"✓ (SHORT tp={sig.take_profit_bps:.0f} stop={sig.stop_loss_bps:.0f})")


# ---------------------------------------------------------------------------
# Integration: AlphaSwarm routes positioning to contrarian
# ---------------------------------------------------------------------------

def test_swarm_routes_positioning_to_contrarian() -> None:
    """AlphaSwarm.on_positioning should produce a debated SELL signal when the
    crowd is long-overcrowded."""
    print("Testing swarm integration: positioning -> contrarian signal...", end=" ")
    from omega.alpha_swarm import AlphaSwarm
    from omega.config.settings import AlphaSwarmSettings, RegimeSettings
    from omega.utils.events import CrowdPositioningEvent

    # Build settings with quorum=1 so a single contrarian signal can produce a
    # decision in this isolated test (normally quorum=2 requires multiple agents).
    alpha_settings = AlphaSwarmSettings(debate_quorum=1)
    swarm = AlphaSwarm(
        symbols=("BTCUSDT",), alpha_settings=alpha_settings,
        regime_settings=RegimeSettings(),
    )
    extreme = CrowdPositioningEvent(
        symbol="BTCUSDT", timestamp="t", crowd_score=0.85, conviction=0.85,
        horizon="hours", expected_move_bps=300.0, components={"funding": 0.85},
        regime_hint="cascade_imminent",
    )
    decisions = swarm.on_positioning(extreme)
    assert len(decisions) >= 1, f"expected a debated decision, got {decisions}"
    assert decisions[0].side.value == "SELL", "crowd long -> debated SELL"
    print("✓")


# ---------------------------------------------------------------------------
# Integration: Orchestrator wires crowd engine
# ---------------------------------------------------------------------------

def test_orchestrator_has_crowd_engine() -> None:
    """Orchestrator must own a CrowdPositioningEngine and a contrarian agent."""
    print("Testing orchestrator wiring...", end=" ")
    from omega import OmegaOrchestrator, load_settings
    orch = OmegaOrchestrator(load_settings())
    assert hasattr(orch, "crowd_engine"), "orchestrator missing crowd_engine"
    agent_names = [a.name for a in orch.alpha_swarm.agents]
    assert "contrarian" in agent_names, f"contrarian agent missing: {agent_names}"
    assert "crowd_engine" in orch.stats(), "crowd_engine not in stats"
    assert "crowd_regime" in orch.stats(), "crowd_regime not in stats"
    print("✓")


# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 50)
    print("OMEGA Crowd Positioning Engine Tests")
    print("=" * 50)
    tests = [
        test_funding_signal_normalization,
        test_ls_ratio_signal_mapping,
        test_sentiment_signal_extremes,
        test_engine_fusion_agreement_boosts_conviction,
        test_engine_fusion_divergence_deflates_conviction,
        test_engine_below_threshold_emits_nothing,
        test_contrarian_no_signal_below_threshold,
        test_contrarian_fades_extreme_and_tp_sl_asymmetry,
        test_swarm_routes_positioning_to_contrarian,
        test_orchestrator_has_crowd_engine,
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
        print(f"All {len(tests)} crowd engine tests PASSED ✓")
        sys.exit(0)
    print(f"{failed}/{len(tests)} crowd engine tests FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()
