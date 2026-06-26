"""
OMEGA Microstructure + On-chain Signal Tests
=============================================

Tests for the IcebergDetectionSignal (passive depth microstructure) and
OnChainInflowSignal (exchange-bound whale transfers).
Run: python tests/test_microstructure.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# IcebergDetectionSignal
# ---------------------------------------------------------------------------

def test_iceberg_detects_refill_pattern() -> None:
    """A wall that dips then refills repeatedly at a stable price must be
    flagged as an iceberg (positive score on the bid side)."""
    print("Testing iceberg refill detection...", end=" ")
    from omega.crowd_engine import IcebergDetectionSignal
    from omega.utils.events import MarketEvent
    sig = IcebergDetectionSignal(wall_qty_threshold=5.0, refill_threshold=2)
    # Simulate a bid wall at 50000 that gets consumed then refills 4 times
    base_px = 50000.0
    for _ in range(4):
        # Wall present
        sig.update_from_market(MarketEvent(
            symbol="BTCUSDT", timestamp="t", last_price=base_px,
            volume_24h=0, bid=base_px, ask=base_px + 1,
            bid_qty=10.0, ask_qty=1.0,
        ))
        # Wall consumed (qty drops)
        sig.update_from_market(MarketEvent(
            symbol="BTCUSDT", timestamp="t", last_price=base_px,
            volume_24h=0, bid=base_px, ask=base_px + 1,
            bid_qty=1.0, ask_qty=1.0,
        ))
        # Wall refills
        sig.update_from_market(MarketEvent(
            symbol="BTCUSDT", timestamp="t", last_price=base_px,
            volume_24h=0, bid=base_px, ask=base_px + 1,
            bid_qty=10.0, ask_qty=1.0,
        ))
    r = sig.reading_for("BTCUSDT")
    assert r is not None, "should produce a reading after enough snapshots"
    assert r.score > 0, f"bid iceberg should give positive score: {r}"
    print(f"✓ (score={r.score:+.2f})")


def test_iceberg_neutral_on_stable_book() -> None:
    """A stable book with no consume-refill pattern should be near-zero."""
    print("Testing iceberg neutral on stable book...", end=" ")
    from omega.crowd_engine import IcebergDetectionSignal
    from omega.utils.events import MarketEvent
    sig = IcebergDetectionSignal()
    for _ in range(20):
        sig.update_from_market(MarketEvent(
            symbol="BTCUSDT", timestamp="t", last_price=50000,
            volume_24h=0, bid=50000, ask=50001, bid_qty=2.0, ask_qty=2.0,
        ))
    r = sig.reading_for("BTCUSDT")
    # Score should be ~0 (balanced, no refills)
    if r is not None:
        assert abs(r.score) < 0.3, f"stable book should be ~neutral: {r}"
    print("✓")


# ---------------------------------------------------------------------------
# OnChainInflowSignal
# ---------------------------------------------------------------------------

def test_inflow_no_key_returns_neutral() -> None:
    """Without an API key, the signal should return None (neutral)."""
    print("Testing inflow neutral without key...", end=" ")
    from omega.crowd_engine import OnChainInflowSignal
    sig = OnChainInflowSignal(symbols=("BTCUSDT",), whale_alert_key="")
    r = sig.reading_for("BTCUSDT")
    assert r is None, f"no key + no data -> None, got {r}"
    print("✓")


def test_inflow_score_from_large_transfers() -> None:
    """Large inflows recorded directly should produce a positive score."""
    print("Testing inflow score from transfers...", end=" ")
    from omega.crowd_engine import OnChainInflowSignal
    sig = OnChainInflowSignal(symbols=("BTCUSDT",), whale_alert_key="test")
    # Inject a large inflow ($60M = saturation)
    now = time.time()
    sig._inflows.append((now, 60_000_000.0, True))
    r = sig.reading_for("BTCUSDT")
    assert r is not None and r.score > 0.5, f"$60M inflow should saturate: {r}"
    # Small inflow
    sig._inflows.clear()
    sig._inflows.append((now, 5_000_000.0, True))
    r2 = sig.reading_for("BTCUSDT")
    assert r2 is not None and 0 < r2.score < 0.5, f"$5M should be mild: {r2}"
    print(f"✓ ($60M={r.score:.2f}, $5M={r2.score:.2f})")


def test_engine_includes_8_signals() -> None:
    """The crowd engine must now have 8 signals including iceberg + inflow."""
    print("Testing engine has 8 signals...", end=" ")
    from omega.crowd_engine import CrowdPositioningEngine
    eng = CrowdPositioningEngine(symbols=("BTCUSDT",))
    names = [s.name for s in eng.signals]
    assert "iceberg" in names, f"iceberg missing: {names}"
    assert "inflow" in names, f"inflow missing: {names}"
    assert len(names) == 8, f"expected 8 signals, got {len(names)}: {names}"
    print(f"✓ ({len(names)} signals: {', '.join(names)})")


# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 50)
    print("OMEGA Microstructure + On-chain Tests")
    print("=" * 50)
    tests = [
        test_iceberg_detects_refill_pattern,
        test_iceberg_neutral_on_stable_book,
        test_inflow_no_key_returns_neutral,
        test_inflow_score_from_large_transfers,
        test_engine_includes_8_signals,
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
        print(f"All {len(tests)} microstructure tests PASSED ✓")
        sys.exit(0)
    print(f"{failed}/{len(tests)} microstructure tests FAILED")
    sys.exit(1)


if __name__ == "__main__":
    main()
