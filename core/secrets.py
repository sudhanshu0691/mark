"""OS-backed credential access with environment overrides and legacy migration."""
from __future__ import annotations

import json
import os
from pathlib import Path

SERVICE = "mark-desktop"
BASE_DIR = Path(__file__).resolve().parent.parent
LEGACY = BASE_DIR / "config" / "api_keys.json"
RUNTIME_ENV = BASE_DIR / "config" / "runtime.env"
_ENV = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "whatsapp": "MARK_WHATSAPP_TOKEN",
    "smtp_password": "SMTP_PASSWORD",
}
_JSON_KEYS = {
    "groq": "groq_api_key",
    "gemini": "gemini_api_key",
    "whatsapp": "whatsapp_token",
    "smtp_password": "smtp_password",
}
try:
    from dotenv import load_dotenv
    load_dotenv(RUNTIME_ENV, override=False)
    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:
    pass


def get_secret(name: str) -> str:
    key = str(name).strip().lower()
    env_value = os.environ.get(_ENV.get(key, ""), "").strip()
    if env_value:
        return env_value
    try:
        import keyring
        value = keyring.get_password(SERVICE, key)
        if value and value.strip():
            return value.strip()
    except Exception:
        pass
    # Legacy installations remain readable until credentials are migrated.
    try:
        cfg = json.loads(LEGACY.read_text(encoding="utf-8"))
        value = str(cfg.get(_JSON_KEYS.get(key, ""), "")).strip()
        if value:
            return value
    except (OSError, ValueError, TypeError):
        pass
    return ""


def set_secret(name: str, value: str) -> None:
    key = str(name).strip().lower()
    if key not in _ENV:
        raise ValueError("Only configured MARK credential names may be saved")
    if not value.strip():
        delete_secret(key)
        return
    import keyring
    try:
        keyring.set_password(SERVICE, key, value.strip())
    except Exception as exc:
        raise RuntimeError("Windows credential storage is unavailable.") from exc
    if LEGACY.exists():
        cfg = json.loads(LEGACY.read_text(encoding="utf-8"))
        if _JSON_KEYS[key] in cfg:
            del cfg[_JSON_KEYS[key]]
            LEGACY.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def delete_secret(name: str) -> None:
    key = str(name).strip().lower()
    try:
        cfg = json.loads(LEGACY.read_text(encoding="utf-8"))
        cfg.pop(_JSON_KEYS.get(key, ""), None)
        LEGACY.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except (OSError, ValueError, TypeError):
        pass
    try:
        import keyring
        keyring.delete_password(SERVICE, key)
    except Exception:
        pass


def migrate_legacy_gemini_key() -> bool:
    """Deprecated: Gemini credentials now remain in config/api_keys.json."""
    return False

