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

    def __init__(self, port: int = 8080, host: str = "127.0.0.1") -> None:
        self.port = port
        self.host = host
        self.app = web.Application(client_max_size=10 * 1024 * 1024)
        self._orchestrator = None
        self._ws_clients: List[web.WebSocketResponse] = []
        # Live OHLC buffer for the chart (symbol -> list of candles)
        self._ohlc: Dict[str, List[dict]] = {}
        self._current_candle: Dict[str, dict] = {}
        self._candle_interval_sec = 15  # 15-second candles for the chart
        self._mode = os.getenv("OMEGA_PAPER", "").lower() in ("1", "true", "yes")
        # Feed OHLC from the orchestrator's market events
        self._ohlc_task = None
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
        # Multi-venue + Web3
        self.app.router.add_get("/api/markets", self._api_markets)
        self.app.router.add_get("/api/exchanges", self._api_exchanges)
        self.app.router.add_get("/api/web3/balance", self._api_web3_balance)
        self.app.router.add_get("/ws/live", self._ws_live)
        # Chart data + mode
        self.app.router.add_get("/api/chart/{symbol}", self._api_chart)
        self.app.router.add_get("/api/mode", self._api_mode_get)
        self.app.router.add_post("/api/mode", self._api_mode_set)
        # Breakthroughs + settings + symbols
        self.app.router.add_get("/api/breakthroughs", self._api_breakthroughs)
        self.app.router.add_get("/api/settings", self._api_settings_get)
        self.app.router.add_post("/api/settings", self._api_settings_set)
        self.app.router.add_get("/api/symbols", self._api_symbols_get)
        self.app.router.add_post("/api/symbols", self._api_symbols_set)
        # AutoPilot
        self.app.router.add_get("/api/autopilot", self._api_autopilot_get)
        self.app.router.add_post("/api/autopilot/toggle", self._api_autopilot_toggle)

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
            # get_positions is OKX-only; BinanceExecutor lacks it
            getter = getattr(ex, "get_positions", None)
            if getter is None:
                return web.json_response([], status=200)
            positions = await getter()
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
            return web.json_response({"ok": False, "error": "wallet not configured (OKX only)"}, status=200)
        wm.panic()
        return web.json_response({"ok": True, "panic": True})

    async def _api_wallet_unfreeze(self, request: web.Request) -> web.Response:
        wm = self.orch.wallet_manager
        if wm is None:
            return web.json_response({"ok": False, "error": "wallet not configured"}, status=200)
        data = await request.json()
        ok = wm.unfreeze(data.get("totp", ""))
        return web.json_response({"ok": ok})

    async def _api_wallet_set_cap(self, request: web.Request) -> web.Response:
        wm = self.orch.wallet_manager
        if wm is None:
            return web.json_response({"ok": False, "error": "wallet not configured"}, status=200)
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
    # Multi-venue + Web3
    # ------------------------------------------------------------------

    async def _api_exchanges(self, request: web.Request) -> web.Response:
        from omega.config.exchanges import EXCHANGES
        out = []
        for name, spec in EXCHANGES.items():
            out.append({
                "name": name, "display": spec.name,
                "canada": spec.canada_available,
                "notes": spec.canada_notes,
                "has_api": bool(spec.rest_url),
                "spot": spec.has_spot, "perp": spec.has_perp,
                "funding": spec.has_funding, "withdrawals": spec.has_withdrawals,
            })
        return web.json_response(out)

    async def _api_markets(self, request: web.Request) -> web.Response:
        """Cross-venue price aggregator. Returns BTC prices across all exchanges."""
        from omega.data_nexus.multi_venue import MultiVenueAggregator
        symbols = request.query.get("symbols", "BTCUSDT,ETHUSDT").split(",")
        agg = MultiVenueAggregator()
        for s in symbols:
            agg.track(s.strip())
        await agg.start()
        await asyncio.sleep(8)  # one poll cycle
        summaries = {s: agg.price_summary(s.strip()) for s in symbols}
        await agg.stop()
        return web.json_response(summaries)

    async def _api_web3_balance(self, request: web.Request) -> web.Response:
        """Read Web3 wallet balances (MetaMask-compatible)."""
        addr = request.query.get("address", "")
        chain = request.query.get("chain", "ethereum")
        if not addr:
            return web.json_response({"error": "provide ?address=0x..."}, status=400)
        from omega.web3.wallet import Web3Wallet
        w = Web3Wallet(address=addr)
        try:
            balances = await w.get_all_balances(chain)
            await w.close()
            return web.json_response({
                "address": addr, "chain": chain,
                "balances": [{"symbol": b.symbol, "balance": b.balance} for b in balances],
            })
        except Exception as exc:
            await w.close()
            return web.json_response({"error": str(exc)}, status=500)

    # ------------------------------------------------------------------
    # Chart data + mode switcher
    # ------------------------------------------------------------------

    def feed_price(self, symbol: str, price: float) -> None:
        """Feed a price tick into the OHLC buffer for the chart."""
        import time
        now = time.time()
        candle_start = int(now // self._candle_interval_sec) * self._candle_interval_sec
        cur = self._current_candle.get(symbol)
        if cur is None or cur["t"] < candle_start:
            # New candle
            if cur is not None:
                self._ohlc.setdefault(symbol, []).append(cur)
                # Keep last 200 candles
                if len(self._ohlc[symbol]) > 200:
                    self._ohlc[symbol] = self._ohlc[symbol][-200:]
            self._current_candle[symbol] = {
                "t": candle_start, "o": price, "h": price, "l": price, "c": price,
            }
        else:
            cur["h"] = max(cur["h"], price)
            cur["l"] = min(cur["l"], price)
            cur["c"] = price

    async def _api_chart(self, request: web.Request) -> web.Response:
        """Return OHLC history for the chart."""
        symbol = request.match_info["symbol"].upper().replace("-", "")
        # Feed the latest known price into the candle buffer so the chart has
        # data even when no WS client is connected (the REST poll path).
        try:
            px = self.orch.risk_aegis.portfolio_heat._last_prices.get(symbol, 0)
            if px > 0:
                self.feed_price(symbol, px)
        except Exception:
            pass
        candles = list(self._ohlc.get(symbol, []))
        # Include the current forming candle
        cur = self._current_candle.get(symbol)
        if cur:
            candles.append(cur.copy())
        return web.json_response({"symbol": symbol, "interval_sec": self._candle_interval_sec,
                                  "candles": candles[-200:]})

    async def _api_mode_get(self, request: web.Request) -> web.Response:
        """Return current trading mode."""
        paper = os.getenv("OMEGA_PAPER", "").lower() in ("1", "true", "yes")
        demo = os.getenv("OKX_DEMO", "").lower() in ("1", "true", "yes")
        mode = "paper" if paper else ("testnet" if demo else "live")
        return web.json_response({"mode": mode, "paper": paper, "demo": demo,
                                  "description": {
                                      "paper": "Real market data, orders LOGGED only (not submitted)",
                                      "testnet": "OKX demo server with fake money, full execution",
                                      "live": "REAL MONEY on OKX mainnet",
                                  }.get(mode, "?")})

    async def _api_mode_set(self, request: web.Request) -> web.Response:
        """Switch trading mode. Requires restart to take full effect, but sets
        the env var so the next start uses the new mode."""
        data = await request.json()
        mode = data.get("mode", "paper")
        if mode == "paper":
            os.environ["OMEGA_PAPER"] = "true"
            os.environ["OKX_DEMO"] = "false"
            self._mode = True
        elif mode == "testnet":
            os.environ["OMEGA_PAPER"] = "false"
            os.environ["OKX_DEMO"] = "true"
            self._mode = False
        elif mode == "live":
            os.environ["OMEGA_PAPER"] = "false"
            os.environ["OKX_DEMO"] = "false"
            self._mode = False
        else:
            return web.json_response({"ok": False, "error": "unknown mode"}, status=400)
        return web.json_response({"ok": True, "mode": mode,
                                  "msg": "Mode set. Restart trading for it to take effect."})

    # ------------------------------------------------------------------
    # Breakthroughs + settings + symbols
    # ------------------------------------------------------------------

    def _init_breakthroughs(self):
        """Lazily instantiate the 25 breakthrough modules."""
        if not hasattr(self, "_breakthroughs"):
            try:
                from omega.breakthroughs import (
                    CascadePredictor, FundingForecast, WhaleTracker,
                    GammaExposureSignal, DepegAlert, ToxicFlowDetector,
                    SmartMoneyDivergence, VolatilityForecast, CorrelationBreakdown,
                    FlashCrashScanner, VolumeProfile, TimeOfDayAlpha,
                    BTCDominanceSignal, ExchangeReserves, MultiTimeframeSignal,
                    StablecoinFlow, MempoolMonitor, BridgeTracker,
                    EconomicCalendar, StressIndex, CrossVenueArbitrage,
                    AdaptiveRiskManager, DeFiYieldScanner, SentimentNLP,
                    PortfolioOptimizer,
                )
                self._breakthroughs = {
                    "cascade_predictor": CascadePredictor(),
                    "funding_forecast": FundingForecast(),
                    "whale_tracker": WhaleTracker(),
                    "gamma_signal": GammaExposureSignal(),
                    "depeg_alert": DepegAlert(),
                    "toxic_flow": ToxicFlowDetector(),
                    "smart_money": SmartMoneyDivergence(),
                    "vol_forecast": VolatilityForecast(),
                    "correlation": CorrelationBreakdown(),
                    "flash_crash": FlashCrashScanner(),
                    "volume_profile": VolumeProfile(),
                    "time_of_day": TimeOfDayAlpha(),
                    "btc_dominance": BTCDominanceSignal(),
                    "exchange_reserves": ExchangeReserves(),
                    "multi_timeframe": MultiTimeframeSignal(),
                    "stablecoin_flow": StablecoinFlow(),
                    "mempool": MempoolMonitor(),
                    "bridge_tracker": BridgeTracker(),
                    "econ_calendar": EconomicCalendar(),
                    "stress_index": StressIndex(),
                    "cross_venue_arb": CrossVenueArbitrage(),
                    "adaptive_risk": AdaptiveRiskManager(),
                    "defi_yield": DeFiYieldScanner(),
                    "sentiment_nlp": SentimentNLP(),
                    "portfolio_opt": PortfolioOptimizer(),
                }
            except Exception as exc:
                logger.warning(f"Breakthroughs init failed: {exc}")
                self._breakthroughs = {}

    async def _api_breakthroughs(self, request: web.Request) -> web.Response:
        """Return all 25 breakthrough module stats."""
        self._init_breakthroughs()
        out = {}
        for name, mod in self._breakthroughs.items():
            try:
                out[name] = mod.stats()
            except Exception:
                out[name] = {"name": name, "error": "stats failed"}
        return web.json_response(out)

    async def _api_settings_get(self, request: web.Request) -> web.Response:
        """Return current editable settings (risk + execution params)."""
        s = self.orch.settings
        return web.json_response({
            "risk": {
                "per_trade_pct": s.risk.max_per_trade_risk_pct,
                "kelly_fraction": s.risk.kelly_fraction,
                "max_drawdown_pct": s.risk.max_portfolio_drawdown_pct,
                "min_confidence": s.risk.min_signal_confidence,
                "max_positions": getattr(s.risk, "max_positions", 8),
            },
            "execution": {
                "venue": s.venue,
                "min_notional": os.getenv("OMEGA_MIN_NOTIONAL_USD", "2.0"),
            },
            "symbols": list(s.data_nexus.symbols),
        })

    async def _api_settings_set(self, request: web.Request) -> web.Response:
        """Update settings. Some require trading restart to take effect."""
        data = await request.json()
        changed = []
        s = self.orch.settings
        # We can't mutate frozen dataclasses, but we set env vars so the next
        # restart picks them up. Live values that the orchestrator reads each
        # signal cycle also get updated in place where possible.
        if "per_trade_pct" in data:
            os.environ["OMEGA_RISK_PER_TRADE_PCT"] = str(data["per_trade_pct"])
            try: s.risk.max_per_trade_risk_pct = float(data["per_trade_pct"])
            except: pass
            changed.append("per_trade_pct")
        if "kelly_fraction" in data:
            os.environ["OMEGA_RISK_KELLY_FRACTION"] = str(data["kelly_fraction"])
            try: s.risk.kelly_fraction = float(data["kelly_fraction"])
            except: pass
            changed.append("kelly_fraction")
        if "max_drawdown_pct" in data:
            os.environ["OMEGA_RISK_MAX_DRAWDOWN_PCT"] = str(data["max_drawdown_pct"])
            try: s.risk.max_portfolio_drawdown_pct = float(data["max_drawdown_pct"])
            except: pass
            changed.append("max_drawdown_pct")
        if "min_confidence" in data:
            try: s.risk.min_signal_confidence = float(data["min_confidence"])
            except: pass
            changed.append("min_confidence")
        if "min_notional" in data:
            os.environ["OMEGA_MIN_NOTIONAL_USD"] = str(data["min_notional"])
            changed.append("min_notional")
        return web.json_response({"ok": True, "changed": changed,
                                  "msg": "Settings updated (some need restart)"})

    async def _api_symbols_get(self, request: web.Request) -> web.Response:
        """Return the active trading symbols."""
        return web.json_response({
            "symbols": list(self.orch.settings.data_nexus.symbols),
        })

    async def _api_symbols_set(self, request: web.Request) -> web.Response:
        """Set trading symbols. Requires restart to take full effect."""
        data = await request.json()
        syms = data.get("symbols", [])
        if syms:
            os.environ["OMEGA_SYMBOLS"] = ",".join(syms)
            return web.json_response({"ok": True, "symbols": syms,
                                      "msg": "Symbols set — restart trading to apply"})
        return web.json_response({"ok": False, "error": "no symbols"}, status=400)

    async def _api_autopilot_get(self, request: web.Request) -> web.Response:
        """Return AutoPilot status and automation toggles."""
        try:
            return web.json_response(self.orch.autopilot.stats())
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

    async def _api_autopilot_toggle(self, request: web.Request) -> web.Response:
        """Toggle an individual automation switch."""
        data = await request.json()
        name = data.get("name", "")
        value = bool(data.get("value", False))
        try:
            ok = self.orch.autopilot.set_toggle(name, value)
            return web.json_response({"ok": ok, "toggles": self.orch.autopilot.stats()["toggles"]})
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
            # Feed live prices into the OHLC chart buffer
            try:
                for sym in self.orch.settings.data_nexus.symbols:
                    px = self.orch.risk_aegis.portfolio_heat._last_prices.get(sym, 0)
                    if px > 0:
                        self.feed_price(sym, px)
                        out.setdefault("prices", {})[sym] = px
            except Exception:
                pass
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
        try:
            await site.start()
        except OSError as exc:
            logger.error(
                f"Cannot bind port {self.port} ({exc}). "
                f"Is another instance running? Try --port {self.port + 1}"
            )
            raise
        url = f"http://{'localhost' if self.host in ('0.0.0.0','127.0.0.1') else self.host}:{self.port}"
        logger.info(f"OMEGA dashboard ready: {url}")
        print(f"\n  ╔══════════════════════════════════════╗")
        print(f"  ║  OMEGA Dashboard: {url:<22}║")
        print(f"  ╚══════════════════════════════════════╝\n")
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
