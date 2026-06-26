"""B21 — CrossVenueArbitrage: detects profitable cross-exchange price gaps.

When BTC trades at $59,655 on Kraken and $59,756 on MEXC, there's a 14.8 bps
arbitrage opportunity. We scan all venue pairs and flag any gap > fees + slippage
threshold. The execution requires accounts on both venues (the arbitrage is
real but capital-intensive).
"""
from __future__ import annotations
from typing import Dict, List
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.arb")

class CrossVenueArbitrage:
    """Scans for cross-exchange arbitrage opportunities."""
    def __init__(self, min_profit_bps: float = 15.0) -> None:
        self.min_profit_bps = min_profit_bps
        self._opportunities: List[dict] = []

    def scan(self, venue_prices: Dict[str, float]) -> List[dict]:
        """Find all profitable venue pairs. venue_prices = {venue: price}."""
        self._opportunities = []
        venues = [(v, p) for v, p in venue_prices.items() if p > 0]
        for i in range(len(venues)):
            for j in range(i + 1, len(venues)):
                v1, p1 = venues[i]
                v2, p2 = venues[j]
                if p1 == p2:
                    continue
                buy_venue, buy_price = (v1, p1) if p1 < p2 else (v2, p2)
                sell_venue, sell_price = (v2, p2) if p1 < p2 else (v1, p1)
                spread_bps = (sell_price - buy_price) / buy_price * 10000
                # Subtract estimated fees (~10 bps each side = 20 bps total)
                net_bps = spread_bps - 20.0
                if net_bps > self.min_profit_bps:
                    opp = {
                        "buy_venue": buy_venue, "buy_price": buy_price,
                        "sell_venue": sell_venue, "sell_price": sell_price,
                        "spread_bps": round(spread_bps, 1),
                        "net_profit_bps": round(net_bps, 1),
                    }
                    self._opportunities.append(opp)
                    logger.info(
                        f"ARB: buy {buy_venue}@${buy_price:.0f} sell {sell_venue}@${sell_price:.0f} "
                        f"net={net_bps:.1f}bps"
                    )
        return self._opportunities

    def stats(self) -> dict:
        return {"name": "cross_venue_arb",
                "opportunities": self._opportunities[:5],
                "count": len(self._opportunities)}
