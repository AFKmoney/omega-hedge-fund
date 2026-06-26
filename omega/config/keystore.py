"""
KeyStore — persistent local credential manager for OMEGA.

Stores API keys, secrets, passphrases, and the TOTP secret in a single local
file (NEVER in git) so you configure them once and every OMEGA command picks
them up automatically — no more retyping env vars every session.

Security model:
    - The file lives in ~/.omega/data/keys.json (outside the git repo)
    - Sensitive values are obfuscated with a machine-derived key (XOR + base64)
      so a casual file viewer doesn't see plaintext secrets. This is NOT strong
      encryption — for real secrets management use a vault / OS keychain. The
      obfuscation stops accidental exposure (cat, screenshot, backup) while
      keeping the UX simple.
    - The file mode is set to owner-only on POSIX.
    - .gitignore excludes the whole ~/.omega/ tree (it's outside the repo anyway).

Two key profiles:
    - "trading": OKX/Binance keys with Trade permission (no withdrawal)
    - "withdrawal": separate OKX key WITH withdrawal permission (used only by
      the WalletManager). Keeping them separate means a compromised trading key
      still cannot withdraw.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import socket
from pathlib import Path
from typing import Dict, Optional

from omega.utils.logger import get_logger

logger = get_logger("omega.config.keystore")


def _keystore_path() -> Path:
    """Default path: ~/.omega/data/keys.json"""
    from omega.config.settings import _default_data_dir
    p = _default_data_dir() / "keys.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _machine_key() -> bytes:
    """Derive a machine-specific key for obfuscation. Tied to hostname + user
    so the file is useless if copied to another machine."""
    seed = f"{platform.node()}-{os.getlogin() if hasattr(os, 'getlogin') else 'user'}-omega-v1"
    return hashlib.sha256(seed.encode()).digest()


def _obfuscate(value: str) -> str:
    """XOR + base64. Stops casual plaintext exposure."""
    if not value:
        return ""
    key = _machine_key()
    data = bytes(b ^ key[i % len(key)] for i, b in enumerate(value.encode()))
    return base64.b64encode(data).decode()


def _deobfuscate(token: str) -> str:
    if not token:
        return ""
    try:
        key = _machine_key()
        data = base64.b64decode(token)
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data)).decode()
    except Exception:
        return ""


# The known credential fields, grouped by profile.
_FIELDS = {
    "trading": {
        "okx_api_key": "OKX API key (Trade permission)",
        "okx_api_secret": "OKX API secret",
        "okx_passphrase": "OKX passphrase",
        "binance_api_key": "Binance API key (fallback venue)",
        "binance_api_secret": "Binance API secret",
    },
    "withdrawal": {
        "okx_withdraw_key": "OKX API key WITH withdrawal permission (separate key)",
        "okx_withdraw_secret": "OKX withdrawal key secret",
        "okx_withdraw_passphrase": "OKX withdrawal key passphrase",
        "totp_secret": "TOTP base32 secret (from Google Authenticator setup)",
    },
    "settings": {
        "okx_demo": "Use OKX demo/paper trading (true/false)",
        "binance_testnet": "Use Binance testnet (true/false)",
        "daily_cap_usd": "Daily withdrawal cap in USD",
        "symbols": "Comma-separated trading symbols",
    },
}


class KeyStore:
    """Persistent credential store."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or _keystore_path()
        self._data: Dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            # Deobfuscate every value
            self._data = {k: _deobfuscate(v) for k, v in raw.items()}
        except Exception as exc:
            logger.warning(f"Failed to load keystore: {exc}")
            self._data = {}

    def _save(self) -> None:
        obf = {k: _obfuscate(v) for k, v in self._data.items()}
        self.path.write_text(json.dumps(obf, indent=2), encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except Exception:
            pass  # Windows ignores chmod

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str, default: str = "") -> str:
        """Get a credential value. Falls back to env var, then default."""
        val = self._data.get(key, "")
        if val:
            return val
        # Env var fallback
        env_map = {
            "okx_api_key": "OKX_API_KEY",
            "okx_api_secret": "OKX_API_SECRET",
            "okx_passphrase": "OKX_PASSPHRASE",
            "okx_withdraw_key": "OKX_WITHDRAW_KEY",
            "okx_withdraw_secret": "OKX_WITHDRAW_SECRET",
            "okx_withdraw_passphrase": "OKX_WITHDRAW_PASSPHRASE",
            "binance_api_key": "BINANCE_API_KEY",
            "binance_api_secret": "BINANCE_API_SECRET",
            "totp_secret": "OMEGA_TOTP_SECRET",
        }
        env_name = env_map.get(key)
        if env_name:
            return os.getenv(env_name, default)
        return default

    def set(self, key: str, value: str) -> None:
        """Set a credential value and persist."""
        self._data[key] = value
        self._save()

    def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            self._save()
            return True
        return False

    def has(self, key: str) -> bool:
        return bool(self.get(key))

    def list_profiles(self) -> Dict[str, Dict[str, str]]:
        """Return the field schema with current set/unset status."""
        out = {}
        for profile, fields in _FIELDS.items():
            out[profile] = {}
            for field, desc in fields.items():
                val = self._data.get(field, "")
                out[profile][field] = {
                    "description": desc,
                    "set": bool(val),
                    "preview": (val[:4] + "..." + val[-2:]) if len(val) > 8 else ("***" if val else ""),
                }
        return out

    def apply_to_env(self) -> None:
        """Push all stored credentials into os.environ so the rest of OMEGA
        (which reads env vars) picks them up automatically."""
        env_map = {
            "okx_api_key": "OKX_API_KEY",
            "okx_api_secret": "OKX_API_SECRET",
            "okx_passphrase": "OKX_PASSPHRASE",
            "okx_withdraw_key": "OKX_WITHDRAW_KEY",
            "okx_withdraw_secret": "OKX_WITHDRAW_SECRET",
            "okx_withdraw_passphrase": "OKX_WITHDRAW_PASSPHRASE",
            "binance_api_key": "BINANCE_API_KEY",
            "binance_api_secret": "BINANCE_API_SECRET",
            "totp_secret": "OMEGA_TOTP_SECRET",
        }
        for key, env_name in env_map.items():
            val = self._data.get(key, "")
            if val:
                os.environ[env_name] = val
        # Settings (non-secret)
        if self._data.get("okx_demo"):
            os.environ["OKX_DEMO"] = self._data["okx_demo"]
        if self._data.get("binance_testnet"):
            os.environ["BINANCE_TESTNET"] = self._data["binance_testnet"]
        if self._data.get("daily_cap_usd"):
            os.environ["OMEGA_DAILY_CAP_USD"] = self._data["daily_cap_usd"]
        if self._data.get("symbols"):
            os.environ["OMEGA_SYMBOLS"] = self._data["symbols"]

    def status(self) -> dict:
        """Summary of what's configured."""
        profiles = self.list_profiles()
        trading_ready = all(
            profiles["trading"][f]["set"]
            for f in ("okx_api_key", "okx_api_secret", "okx_passphrase")
        ) or all(
            profiles["trading"][f]["set"]
            for f in ("binance_api_key", "binance_api_secret")
        )
        withdrawal_ready = all(
            profiles["withdrawal"][f]["set"]
            for f in ("okx_withdraw_key", "okx_withdraw_secret",
                      "okx_withdraw_passphrase", "totp_secret")
        )
        return {
            "path": str(self.path),
            "trading_ready": trading_ready,
            "withdrawal_ready": withdrawal_ready,
            "profiles": profiles,
        }


# Module-level singleton for convenience
_singleton: Optional[KeyStore] = None


def get_keystore() -> KeyStore:
    global _singleton
    if _singleton is None:
        _singleton = KeyStore()
    return _singleton
