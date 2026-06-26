"""B18 — BridgeTracker: monitors cross-chain bridge flows.

When large amounts flow through bridges (Ethereum→Arbitrum, Ethereum→Solana),
it signals capital repositioning. A surge into a chain = upcoming activity on
that chain's DEXes = buy that chain's native token. We track known bridge
contract inflows via on-chain data.
"""
from __future__ import annotations
import time
from collections import deque
from typing import Deque, Dict
from omega.utils.logger import get_logger
logger = get_logger("omega.breakthroughs.bridge")

class BridgeTracker:
    """Tracks cross-chain bridge flow volume."""
    # Known bridge contracts (simplified — extend for production)
    BRIDGES = {
        "arbitrum": "0x0000000000000000000000000000000000000064",  # native bridge
        "optimism": "0x99C9fc46f92E8a1c0deC1b1747d010903E884bE1",
        "polygon": "0xA0c68C638235ee32657e8f720a23ceC1bFc77C77",
    }

    def __init__(self, window_sec: int = 3600) -> None:
        self.window_sec = window_sec
        self._flows: Dict[str, Deque] = {k: deque(maxlen=200) for k in self.BRIDGES}

    def record_flow(self, chain: str, amount_usd: float) -> None:
        if chain in self._flows and amount_usd > 0:
            self._flows[chain].append((time.time(), amount_usd))
            if amount_usd > 5_000_000:
                logger.info(f"Large bridge flow to {chain}: ${amount_usd:,.0f}")

    def get_flow_score(self, chain: str) -> float:
        """Net USD inflow to a chain over the window (normalized)."""
        flows = self._flows.get(chain, deque())
        cutoff = time.time() - self.window_sec
        total = sum(amt for ts, amt in flows if ts > cutoff)
        import math
        return min(1.0, math.tanh(total / 50_000_000))

    def stats(self) -> dict:
        return {"name": "bridge_tracker",
                "flows": {c: round(self.get_flow_score(c), 3) for c in self.BRIDGES}}
