"""
Exchange registry — centralized catalogue of all supported trading platforms.

Each entry defines how to talk to an exchange:
    - REST base URL + WS URL
    - Auth scheme (API key locations, signing method)
    - Symbol format (BTCUSDT vs BTC-USDT vs XBTUSD)
    - Rate limits
    - Canadian availability + legal notes
    - Capabilities (spot/perp/funding/liquidations)

Adding a new exchange = one entry here + one executor class. The orchestrator
auto-discovers which exchanges have credentials configured and activates them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ExchangeSpec:
    """Static specification of a trading venue."""
    name: str
    # Endpoints
    rest_url: str
    ws_url: str
    # Auth
    auth_type: str           # "okx_passphrase" | "hmac_query" | "hmac_header" | "cdp"
    key_env: str             # env var for API key
    secret_env: str          # env var for API secret
    passphrase_env: str = "" # OKX-style 3rd credential
    # Symbol translation: OMEGA uses "BTCUSDT" internally
    symbol_format: str = "concat"  # "concat" (BTCUSDT) | "dash" (BTC-USDT) | "dash_swap" (BTC-USDT-SWAP) | "xbt" (XBTUSD)
    # Capabilities
    has_spot: bool = True
    has_perp: bool = False
    has_funding: bool = False
    has_liquidations: bool = False
    has_withdrawals: bool = False
    # Rate limit (requests per minute)
    rate_limit_per_min: int = 600
    # Regulatory
    canada_available: bool = True
    canada_notes: str = ""
    # Signing specifics
    signing_notes: str = ""


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

EXCHANGES: Dict[str, ExchangeSpec] = {
    "okx": ExchangeSpec(
        name="OKX",
        rest_url="https://www.okx.com",
        ws_url="wss://ws.okx.com:8443/ws/v5/public",
        auth_type="okx_passphrase",
        key_env="OKX_API_KEY",
        secret_env="OKX_API_SECRET",
        passphrase_env="OKX_PASSPHRASE",
        symbol_format="dash_swap",
        has_spot=True, has_perp=True, has_funding=True,
        has_liquidations=True, has_withdrawals=True,
        canada_available=True,
        canada_notes="Available in Canada (Ontario restricted on some pairs). Full perp + funding.",
        signing_notes="base64(HMAC-SHA256(secret, timestamp+method+path+body)) in OK-ACCESS-SIGN header",
    ),
    "binance": ExchangeSpec(
        name="Binance",
        rest_url="https://api.binance.com",
        ws_url="wss://stream.binance.com:9443/stream",
        auth_type="hmac_query",
        key_env="BINANCE_API_KEY",
        secret_env="BINANCE_API_SECRET",
        symbol_format="concat",
        has_spot=True, has_perp=True, has_funding=True,
        has_liquidations=True, has_withdrawals=True,
        canada_available=False,
        canada_notes="Binance.com is NOT available in Ontario. Binance global works elsewhere in Canada. Use as fallback only.",
        signing_notes="HMAC-SHA256 query string signature, key in X-MBX-APIKEY header",
    ),
    "kraken": ExchangeSpec(
        name="Kraken",
        rest_url="https://api.kraken.com",
        ws_url="wss://ws.kraken.com",
        auth_type="hmac_sha512",
        key_env="KRAKEN_API_KEY",
        secret_env="KRAKEN_API_SECRET",
        symbol_format="xbt",   # BTC -> XBT
        has_spot=True, has_perp=False, has_funding=False,
        has_liquidations=False, has_withdrawals=True,
        canada_available=True,
        canada_notes="Fully available in Canada including Ontario. One of the most trusted. Spot only via standard API (futures separate).",
        signing_notes="HMAC-SHA512 with base64-decoded secret, SHA256(url path + sha256(nonce + postdata))",
    ),
    "coinbase": ExchangeSpec(
        name="Coinbase Exchange",
        rest_url="https://api.exchange.coinbase.com",
        ws_url="wss://ws-feed.exchange.coinbase.com",
        auth_type="cdp",
        key_env="COINBASE_API_KEY",
        secret_env="COINBASE_API_SECRET",
        passphrase_env="COINBASE_PASSPHRASE",
        symbol_format="dash",   # BTC-USD
        has_spot=True, has_perp=False, has_funding=False,
        has_liquidations=False, has_withdrawals=True,
        canada_available=True,
        canada_notes="Coinbase Exchange available in Canada. Public CDP API for market data. Spot only on standard API.",
        signing_notes="base64(HMAC-SHA256(secret, timestamp+method+path+body)), CB-ACCESS-KEY/SIGN/TIMESTAMP/PASSPHRASE headers",
    ),
    "bybit": ExchangeSpec(
        name="Bybit",
        rest_url="https://api.bybit.com",
        ws_url="wss://stream.bybit.com/v5/public/linear",
        auth_type="hmac_query",
        key_env="BYBIT_API_KEY",
        secret_env="BYBIT_API_SECRET",
        symbol_format="concat",
        has_spot=True, has_perp=True, has_funding=True,
        has_liquidations=True, has_withdrawals=True,
        canada_available=True,
        canada_notes="Available in Canada (Ontario restricted). Full perp + funding + liquidations. Strong API.",
        signing_notes="HMAC-SHA256(timestamp+key+recv_window+param), X-API-KEY header",
    ),
    "kucoin": ExchangeSpec(
        name="KuCoin",
        rest_url="https://api.kucoin.com",
        ws_url="wss://ws-api.kucoin.com",
        auth_type="hmac_header",
        key_env="KUCOIN_API_KEY",
        secret_env="KUCOIN_API_SECRET",
        passphrase_env="KUCOIN_PASSPHRASE",
        symbol_format="dash",   # BTC-USDT
        has_spot=True, has_perp=True, has_funding=True,
        has_liquidations=False, has_withdrawals=True,
        canada_available=True,
        canada_notes="Available in Canada. Spot + futures. Good altcoin coverage.",
        signing_notes="base64(HMAC-SHA256(secret, timestamp+method+path+body)), KC-API headers",
    ),
    "mexc": ExchangeSpec(
        name="MEXC",
        rest_url="https://api.mexc.com",
        ws_url="wss://wbs-api.mexc.com/ws",
        auth_type="hmac_header",
        key_env="MEXC_API_KEY",
        secret_env="MEXC_API_SECRET",
        symbol_format="concat",
        has_spot=True, has_perp=False, has_funding=False,
        has_liquidations=False, has_withdrawals=True,
        canada_available=True,
        canada_notes="Available in Canada. Spot only on standard API. Best altcoin/meme coin coverage.",
        signing_notes="HMAC-SHA256 signature in headers, X-MEXC-APIKEY header",
    ),
    "gemini": ExchangeSpec(
        name="Gemini",
        rest_url="https://api.gemini.com",
        ws_url="wss://api.gemini.com/v2/marketdata",
        auth_type="hmac_header",
        key_env="GEMINI_API_KEY",
        secret_env="GEMINI_API_SECRET",
        symbol_format="concat_lower",  # btcusd
        has_spot=True, has_perp=False, has_funding=False,
        has_liquidations=False, has_withdrawals=True,
        canada_available=True,
        canada_notes="Fully available in Canada. NYDFS-regulated trust company. Spot only. Very reliable API.",
        signing_notes="base64(HMAC-SHA384(secret, base64(payload))), X-GEMINI-APIKEY + X-GEMINI-PAYLOAD + X-GEMINI-SIGNATURE headers",
    ),
    "crypto_com": ExchangeSpec(
        name="Crypto.com Exchange",
        rest_url="https://api.crypto.com/exchange/v1",
        ws_url="wss://stream.crypto.com/v2/market",
        auth_type="hmac_header",
        key_env="CRYPTOCOM_API_KEY",
        secret_env="CRYPTOCOM_API_SECRET",
        symbol_format="underscore",  # BTC_USDT
        has_spot=True, has_perp=True, has_funding=True,
        has_liquidations=False, has_withdrawals=True,
        canada_available=True,
        canada_notes="Fully available in Canada (OSFI registered). Spot + perp. CAD on/off ramp native.",
        signing_notes="HMAC-SHA256 signature of sorted params, APIKEY header",
    ),
    "gate": ExchangeSpec(
        name="Gate.io",
        rest_url="https://api.gateio.ws/api/v4",
        ws_url="wss://api.gateio.ws/ws/v4/",
        auth_type="hmac_header",
        key_env="GATE_API_KEY",
        secret_env="GATE_API_SECRET",
        symbol_format="underscore",  # BTC_USDT
        has_spot=True, has_perp=True, has_funding=True,
        has_liquidations=False, has_withdrawals=True,
        canada_available=True,
        canada_notes="Available in Canada. Spot + futures. Wide altcoin selection.",
        signing_notes="HMAC-SHA512(signature), KEY + SIGN + TIMESTAMP headers",
    ),
}


def translate_symbol(symbol: str, fmt: str, is_perp: bool = False) -> str:
    """Translate OMEGA's internal 'BTCUSDT' to the exchange's format."""
    s = symbol.upper().replace("-", "")
    quotes = ("USDT", "USDC", "USD", "BUSD")
    base = quote = ""
    for q in quotes:
        if s.endswith(q):
            base = s[:-len(q)]
            quote = q
            break
    if not base:
        return symbol  # unknown format, return as-is

    if fmt == "concat":
        return f"{base}{quote}"
    if fmt == "dash":
        return f"{base}-{quote}"
    if fmt == "dash_swap":
        return f"{base}-{quote}-SWAP" if is_perp else f"{base}-{quote}"
    if fmt == "xbt":
        base = base.replace("BTC", "XBT")
        return f"{base}{quote.replace('USDT','USD')}"
    if fmt == "concat_lower":
        return f"{base}{quote}".lower()
    if fmt == "underscore":
        return f"{base}_{quote}"
    return symbol


def available_in_canada() -> Dict[str, ExchangeSpec]:
    """Return exchanges legally available in Canada."""
    return {k: v for k, v in EXCHANGES.items() if v.canada_available}


def with_credentials(env: dict) -> List[str]:
    """Return exchange names whose API key is present in the env."""
    out = []
    for name, spec in EXCHANGES.items():
        if env.get(spec.key_env):
            out.append(name)
    return out
