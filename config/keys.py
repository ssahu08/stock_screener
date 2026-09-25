"""
config/keys.py — secret LOADER (safe to commit: contains NO secrets)
=====================================================================

Every value is resolved at import time, in this order:
  1. Environment variable of the same name
  2. .streamlit/secrets.toml  (local file, or the file Streamlit
     Community Cloud creates from "App settings -> Secrets")
  3. streamlit.secrets        (authoritative on Streamlit Cloud)
  4. default value

NEVER put real secrets back in this file — it lives in git.
Add them to .streamlit/secrets.toml instead (gitignored), or to the
Secrets box in the Streamlit Community Cloud UI.
"""

from __future__ import annotations

import os

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SECRETS_FILE = os.path.join(_BASE_DIR, ".streamlit", "secrets.toml")


def _load_file_secrets() -> dict:
    """Read .streamlit/secrets.toml directly — works in CLI and Streamlit."""
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        return {}
    if not os.path.exists(_SECRETS_FILE):
        return {}
    try:
        with open(_SECRETS_FILE, "rb") as fh:
            return {str(k): "" if v is None else str(v) for k, v in tomllib.load(fh).items()}
    except Exception:
        return {}


_FILE: dict = _load_file_secrets()
_ST_CACHE: dict = {}


def _st_secret(name: str) -> str:
    """streamlit.secrets lookup (lazy import; cached). Only used as fallback."""
    if name in _ST_CACHE:
        return _ST_CACHE[name]
    value = ""
    try:
        import streamlit as st
        value = str(st.secrets.get(name) or "")
    except Exception:
        value = ""
    _ST_CACHE[name] = value
    return value


def get_secret(name: str, default: str = "") -> str:
    """Resolve one secret by name (env -> file -> st.secrets -> default)."""
    return (
        os.environ.get(name, "")
        or _FILE.get(name, "")
        or _st_secret(name)
        or default
    )


# ------------------------------------------------------------------
# Fyers API (get yours at https://api-dashboard.fyers.in -> my apps)
# ------------------------------------------------------------------
CLIENT_ID = get_secret("CLIENT_ID")
SECRET_KEY = get_secret("SECRET_KEY")
REDIRECT_URI = get_secret(
    "REDIRECT_URI",
    "https://trade.fyers.in/api-login/redirect-uri/index.html",
)

# ------------------------------------------------------------------
# Telegram alerts (bot token from @BotFather)
# ------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = get_secret("TELEGRAM_CHAT_ID")

# ------------------------------------------------------------------
# Third-party data APIs
# ------------------------------------------------------------------
APIFY_TOKEN = get_secret("APIFY_TOKEN")      # https://console.apify.com
FINNHUB_TOKEN = get_secret("FINNHUB_TOKEN")  # https://finnhub.io

# ------------------------------------------------------------------
# Dashboard password gate (keep the app private when hosted)
# ------------------------------------------------------------------
DASHBOARD_PASSWORD = get_secret("DASHBOARD_PASSWORD")
