"""
ProfileManager — named credential profiles with instant switching.

Lets you keep multiple key sets (live-okx, demo-okx, binance-fallback) and
switch between them without retyping. Profiles are stored in
~/.omega/data/profiles.json (obfuscated, never in git).

A profile is a named collection of all credential fields (trading + withdrawal
+ settings). One profile is marked "active" at a time and is what the KeyStore
exposes to the rest of OMEGA.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from omega.config.keystore import KeyStore, _FIELDS, _obfuscate, _deobfuscate, _keystore_path
from omega.utils.logger import get_logger

logger = get_logger("omega.config.profiles")


def _profiles_path() -> Path:
    return _keystore_path().parent / "profiles.json"


class ProfileManager:
    """Named credential profiles with instant switching."""

    # The union of all fields a profile can hold
    ALL_FIELDS = [f for profile in _FIELDS.values() for f in profile]

    def __init__(self) -> None:
        self.path = _profiles_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._profiles: Dict[str, Dict[str, str]] = {}
        self._active: str = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._active = raw.get("active", "")
            self._profiles = {
                name: {k: _deobfuscate(v) for k, v in fields.items()}
                for name, fields in raw.get("profiles", {}).items()
            }
        except Exception as exc:
            logger.warning(f"Failed to load profiles: {exc}")

    def _save(self) -> None:
        obf = {
            name: {k: _obfuscate(v) for k, v in fields.items()}
            for name, fields in self._profiles.items()
        }
        self.path.write_text(
            json.dumps({"active": self._active, "profiles": obf}, indent=2),
            encoding="utf-8",
        )
        try:
            self.path.chmod(0o600)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, name: str, fields: Dict[str, str]) -> bool:
        """Create or overwrite a profile."""
        name = name.strip().lower()
        if not name:
            return False
        # Only keep known fields
        clean = {k: v for k, v in fields.items() if k in self.ALL_FIELDS and v}
        self._profiles[name] = clean
        if not self._active:
            self._active = name
        self._save()
        logger.info(f"Profile '{name}' saved ({len(clean)} fields)")
        return True

    def delete(self, name: str) -> bool:
        name = name.strip().lower()
        if name not in self._profiles:
            return False
        del self._profiles[name]
        if self._active == name:
            self._active = next(iter(self._profiles), "")
            self._apply_active()
        self._save()
        return True

    def use(self, name: str) -> bool:
        """Switch the active profile."""
        name = name.strip().lower()
        if name not in self._profiles:
            return False
        self._active = name
        self._save()
        self._apply_active()
        return True

    def show(self, name: str) -> Optional[Dict[str, str]]:
        name = name.strip().lower()
        profile = self._profiles.get(name)
        if not profile:
            return None
        # Mask secrets for display
        out = {}
        for k, v in profile.items():
            if any(tag in k for tag in ("secret", "passphrase", "totp", "key")) and len(v) > 8:
                out[k] = v[:4] + "..." + v[-2:]
            else:
                out[k] = v
        return out

    def list_all(self) -> List[Dict]:
        out = []
        for name, fields in self._profiles.items():
            venue = "okx" if fields.get("okx_api_key") else (
                "binance" if fields.get("binance_api_key") else "none")
            out.append({
                "name": name,
                "active": name == self._active,
                "field_count": len(fields),
                "venue": venue,
                "demo": fields.get("okx_demo", "false") == "true",
            })
        return out

    @property
    def active_name(self) -> str:
        return self._active

    def get_active(self) -> Optional[Dict[str, str]]:
        return self._profiles.get(self._active)

    # ------------------------------------------------------------------
    # Export / import
    # ------------------------------------------------------------------

    def export_profile(self, name: str, filepath: str) -> bool:
        """Export a profile to a plaintext text file (user asked for this).
        WARNING: the file contains real secrets — handle accordingly."""
        name = name.strip().lower()
        profile = self._profiles.get(name)
        if not profile:
            return False
        lines = [f"# OMEGA profile: {name}", f"# Exported: {__import__('datetime').datetime.now()}", ""]
        for k, v in profile.items():
            lines.append(f"{k}={v}")
        Path(filepath).write_text("\n".join(lines), encoding="utf-8")
        return True

    def import_profile(self, name: str, filepath: str) -> bool:
        """Import a profile from a plaintext file (key=value lines)."""
        name = name.strip().lower()
        if not name:
            return False
        try:
            content = Path(filepath).read_text(encoding="utf-8")
        except Exception as exc:
            logger.error(f"Cannot read import file: {exc}")
            return False
        fields = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                if k in self.ALL_FIELDS:
                    fields[k] = v.strip()
        if not fields:
            return False
        return self.add(name, fields)

    # ------------------------------------------------------------------
    # Apply active profile to the keystore + env
    # ------------------------------------------------------------------

    def _apply_active(self) -> None:
        """Write the active profile into the keystore so the rest of OMEGA
        picks it up."""
        profile = self._profiles.get(self._active)
        ks = KeyStore()
        # Clear current keystore secrets first (so switching fully replaces)
        for profile_fields in _FIELDS.values():
            for f in profile_fields:
                ks.delete(f)
        if profile:
            for k, v in profile.items():
                ks.set(k, v)
        ks.apply_to_env()
        logger.info(f"Active profile switched to '{self._active}'")


# Singleton
_singleton: Optional[ProfileManager] = None


def get_profile_manager() -> ProfileManager:
    global _singleton
    if _singleton is None:
        _singleton = ProfileManager()
    return _singleton
