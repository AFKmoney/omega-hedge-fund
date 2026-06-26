"""
OMEGA Web Server — REST API + WebSocket live stream.

Serves the glassmorphism frontend and exposes every OMEGA function via a clean
REST API so the GUI controls 100% of the backend. Runs on http://localhost:8080.

Endpoints:
    GET  /                          — the GUI (glassmorphism dashboard)
    GET  /api/status                — system overview (venue, agents, crowd, risk)
    GET  /api/balance               — account balance
    GET  /api/positions             — open positions
    GET  /api/crowd/signals         — 8 crowd signals live values
    GET  /api/swarm/agents          — alpha swarm agents + stats
    GET  /api/risk                  — risk aegis state
    GET  /api/execution             — execution stats + recent fills
    GET  /api/wallet                — wallet manager status
    GET  /api/profiles              — credential profiles
    POST /api/profiles/use          — switch active profile
    POST /api/profiles/add          — create profile
    DELETE /api/profiles/{name}     — delete profile
    POST /api/wallet/withdraw       — withdraw (TOTP required)
    POST /api/wallet/panic          — freeze withdrawals
    POST /api/wallet/unfreeze       — unfreeze (TOTP)
    POST /api/wallet/set-cap        — change daily cap
    POST /api/trading/start         — start live trading
    POST /api/trading/stop          — stop live trading
    GET  /api/keys                  — keystore status (masked)
    POST /api/keys/set              — set a key
    DELETE /api/keys/{key}          — delete a key
    WS   /ws/live                   — real-time push (crowd events, fills, prices)
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web, WSMsgType

from omega.config.keystore import get_keystore
from omega.config.profiles import get_profile_manager
from omega.config.settings import load_settings
from omega.utils.logger import get_logger

logger = get_logger("omega.web")

_STATIC_DIR = Path(__file__).resolve().parent / "static"


class OmegaWebServer:
    """Web server exposing the full OMEGA backend via REST + WS."""

    def __init__(self, port: int = 8080, host: str = "0.0.0.0") -> None:
        self.port = port
        self.host = host
        self.app = web.Application(client_max_size=10 * 1024 * 1024)
        self._orchestrator = None
        self._ws_clients: List[web.WebSocketResponse] = []
        self._setup_routes()

    @property
    def orch(self):
        """Lazy-load the orchestrator (so the server starts fast)."""
        if self._orchestrator is None:
            from omega import OmegaOrchestrator
            self._orchestrator = OmegaOrchestrator(load_settings())
        return self._orchestrator

    def _setup_routes(self) -> None:
        # Static GUI
        self.app.router.add_get("/", self._serve_index)
        self.app.router.add_static("/", str(_STATIC_DIR), show_index=False)
        # REST API
        self.app.router.add_get("/api/status", self._api_status)
        self.app.router.add_get("/api/balance", self._api_balance)
        self.app.router.add_get("/api/positions", self._api_positions)
        self.app.router.add_get("/api/crowd/signals", self._api_crowd)
        self.app.router.add_get("/api/swarm/agents", self._api_swarm)
        self.app.router.add_get("/api/risk", self._api_risk)
        self.app.router.add_get("/api/execution", self._api_execution)
        self.app.router.add_get("/api/wallet", self._api_wallet_get)
        self.app.router.add_post("/api/wallet/withdraw", self._api_wallet_withdraw)
        self.app.router.add_post("/api/wallet/panic", self._api_wallet_panic)
        self.app.router.add_post("/api/wallet/unfreeze", self._api_wallet_unfreeze)
        self.app.router.add_post("/api/wallet/set-cap", self._api_wallet_set_cap)
        self.app.router.add_get("/api/profiles", self._api_profiles_get)
        self.app.router.add_post("/api/profiles/use", self._api_profiles_use)
        self.app.router.add_post("/api/profiles/add", self._api_profiles_add)
        self.app.router.add_delete("/api/profiles/{name}", self._api_profiles_delete)
        self.app.router.add_get("/api/keys", self._api_keys_get)
        self.app.router.add_post("/api/keys/set", self._api_keys_set)
        self.app.router.add_delete("/api/keys/{key}", self._api_keys_delete)
        self.app.router.add_post("/api/trading/start", self._api_trading_start)
        self.app.router.add_post("/api/trading/stop", self._api_trading_stop)
        self.app.router.add_get("/ws/live", self._ws_live)

    # ------------------------------------------------------------------
    # Static GUI
    # ------------------------------------------------------------------

    async def _serve_index(self, request: web.Request) -> web.Response:
        index = _STATIC_DIR / "index.html"
        if not index.exists():
            return web.Response(text="Frontend not built. Run the build step.", status=500)
        return web.Response(text=index.read_text(encoding="utf-8"), content_type="text/html")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_executor(self):
        try:
            return self.orch.execution_blade.sor.get_venue(self.orch.execution_blade.venue_name)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # API endpoints
    # ------------------------------------------------------------------

    async def _api_status(self, request: web.Request) -> web.Response:
        try:
            s = self.orch.settings
            stats = self.orch.stats()
            return web.json_response({
                "running": self.orch._running,
                "venue": s.venue,
                "okx_demo": s.okx_demo,
                "signals_processed": stats.get("signals_processed", 0),
                "orders_sent": stats.get("orders_sent", 0),
                "fills_received": stats.get("fills_received", 0),
                "crowd_regime": stats.get("crowd_regime", "neutral"),
                "crowd_engine_events": stats.get("crowd_engine", {}).get("events_emitted", 0),
            })
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

    async def _api_balance(self, request: web.Request) -> web.Response:
        ex = await self._get_executor()
        if ex is None:
            return web.json_response({"error": "no venue configured"}, status=400)
        try:
            bal = await ex.get_balance("USDT")
            return web.json_response({"usdt": bal, "venue": ex.venue})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

    async def _api_positions(self, request: web.Request) -> web.Response:
        ex = await self._get_executor()
        if ex is None:
            return web.json_response([], status=200)
        try:
            positions = await ex.get_positions()
            return web.json_response(positions)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

    async def _api_crowd(self, request: web.Request) -> web.Response:
        try:
            stats = self.orch.crowd_engine.stats()
            return web.json_response(stats)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

    async def _api_swarm(self, request: web.Request) -> web.Response:
        try:
            return web.json_response({
                "agents": [a.stats() if hasattr(a, "stats") else {"name": a.name}
                           for a in self.orch.alpha_swarm.agents],
                "weights": self.orch.weight_router.weights_for(self.orch._last_regime)
                           if hasattr(self.orch.weight_router, "weights_for") else {},
            })
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

    async def _api_risk(self, request: web.Request) -> web.Response:
        try:
            return web.json_response(self.orch.risk_aegis.stats())
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

    async def _api_execution(self, request: web.Request) -> web.Response:
        try:
            return web.json_response(self.orch.execution_blade.stats())
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

    async def _api_wallet_get(self, request: web.Request) -> web.Response:
        wm = self.orch.wallet_manager
        if wm is None:
            return web.json_response({"error": "wallet not configured (OKX only)"}, status=200)
        return web.json_response(wm.status())

    async def _api_wallet_withdraw(self, request: web.Request) -> web.Response:
        wm = self.orch.wallet_manager
        if wm is None:
            return web.json_response({"ok": False, "error": "wallet not configured"}, status=400)
        data = await request.json()
        result = await wm.withdraw(
            ccy=data["ccy"], amt=float(data["amount"]),
            to_addr=data["address"], chain=data["chain"],
            totp_code=data["totp"],
        )
        return web.json_response(result)

    async def _api_wallet_panic(self, request: web.Request) -> web.Response:
        wm = self.orch.wallet_manager
        if wm is None:
            return web.json_response({"ok": False, "error": "wallet not configured"}, status=400)
        wm.panic()
        return web.json_response({"ok": True, "panic": True})

    async def _api_wallet_unfreeze(self, request: web.Request) -> web.Response:
        wm = self.orch.wallet_manager
        if wm is None:
            return web.json_response({"ok": False}, status=400)
        data = await request.json()
        ok = wm.unfreeze(data.get("totp", ""))
        return web.json_response({"ok": ok})

    async def _api_wallet_set_cap(self, request: web.Request) -> web.Response:
        wm = self.orch.wallet_manager
        if wm is None:
            return web.json_response({"ok": False}, status=400)
        data = await request.json()
        ok = wm.set_daily_cap(float(data["cap"]), data.get("totp", ""))
        return web.json_response({"ok": ok, "cap": wm.daily_cap_usd})

    async def _api_profiles_get(self, request: web.Request) -> web.Response:
        pm = get_profile_manager()
        return web.json_response({"profiles": pm.list_all(), "active": pm.active_name})

    async def _api_profiles_use(self, request: web.Request) -> web.Response:
        pm = get_profile_manager()
        data = await request.json()
        ok = pm.use(data.get("name", ""))
        return web.json_response({"ok": ok})

    async def _api_profiles_add(self, request: web.Request) -> web.Response:
        pm = get_profile_manager()
        data = await request.json()
        ok = pm.add(data.get("name", ""), data.get("fields", {}))
        return web.json_response({"ok": ok})

    async def _api_profiles_delete(self, request: web.Request) -> web.Response:
        pm = get_profile_manager()
        name = request.match_info["name"]
        ok = pm.delete(name)
        return web.json_response({"ok": ok})

    async def _api_keys_get(self, request: web.Request) -> web.Response:
        ks = get_keystore()
        return web.json_response(ks.status())

    async def _api_keys_set(self, request: web.Request) -> web.Response:
        ks = get_keystore()
        data = await request.json()
        ks.set(data["key"], data["value"])
        return web.json_response({"ok": True})

    async def _api_keys_delete(self, request: web.Request) -> web.Response:
        ks = get_keystore()
        key = request.match_info["key"]
        ok = ks.delete(key)
        return web.json_response({"ok": ok})

    async def _api_trading_start(self, request: web.Request) -> web.Response:
        try:
            if not self.orch._running:
                asyncio.create_task(self.orch.start())
                return web.json_response({"ok": True, "running": True})
            return web.json_response({"ok": True, "running": True, "msg": "already running"})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    async def _api_trading_stop(self, request: web.Request) -> web.Response:
        try:
            if self.orch._running:
                await self.orch.stop()
            return web.json_response({"ok": True, "running": False})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    # ------------------------------------------------------------------
    # WebSocket live
    # ------------------------------------------------------------------

    async def _ws_live(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.append(ws)
        try:
            # Push a status snapshot every 2s
            while True:
                try:
                    snapshot = await self._live_snapshot()
                    await ws.send_json(snapshot)
                except Exception:
                    pass
                await asyncio.sleep(2)
                # Drain any incoming
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=0.01)
                    if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break
                except asyncio.TimeoutError:
                    pass
        finally:
            if ws in self._ws_clients:
                self._ws_clients.remove(ws)
        return ws

    async def _live_snapshot(self) -> Dict[str, Any]:
        """Build a compact snapshot for the live WS push."""
        out: Dict[str, Any] = {}
        try:
            stats = self.orch.stats()
            out["status"] = {
                "running": self.orch._running,
                "signals": stats.get("signals_processed", 0),
                "orders": stats.get("orders_sent", 0),
                "fills": stats.get("fills_received", 0),
                "crowd_regime": stats.get("crowd_regime", "neutral"),
            }
            out["crowd"] = stats.get("crowd_engine", {})
            out["risk"] = stats.get("risk_aegis", {})
            ex = await self._get_executor()
            if ex is not None:
                out["balance"] = await ex.get_balance("USDT")
        except Exception:
            pass
        return out

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(self) -> None:
        logger.info(f"OMEGA web server starting on http://localhost:{self.port}")
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info(f"OMEGA dashboard: http://localhost:{self.port}")
        # Keep running
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="OMEGA web dashboard")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", type=str, default="0.0.0.0")
    args = p.parse_args()
    server = OmegaWebServer(port=args.port, host=args.host)
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
