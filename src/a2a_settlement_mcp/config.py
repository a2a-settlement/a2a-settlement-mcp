"""Environment-driven configuration for the A2A Settlement MCP server."""

from __future__ import annotations

import os


def get_exchange_url() -> str:
    """Exchange API base URL (e.g. http://localhost:3000). SDK appends /v1/ paths."""
    return os.environ.get("A2A_EXCHANGE_URL", "http://localhost:3000").rstrip("/")


def get_api_key() -> str:
    """API key for authenticated exchange operations. Empty if unset."""
    return os.environ.get("A2A_API_KEY", "").strip()


def get_dashboard_api_key() -> str:
    """Dashboard API key for admin operations (suspend, force-refund, etc.).

    Falls back to A2A_API_KEY if not set separately.
    """
    key = os.environ.get("A2A_DASHBOARD_API_KEY", "").strip()
    return key or get_api_key()


def get_transport() -> str:
    """MCP transport: 'stdio' or 'sse'."""
    return os.environ.get("A2A_MCP_TRANSPORT", "stdio").lower()


def get_port() -> int:
    """Port for SSE transport."""
    try:
        return int(os.environ.get("A2A_MCP_PORT", "3200"))
    except ValueError:
        return 3200


def get_shim_url() -> str:
    """Security Shim base URL. Empty if shim is not deployed."""
    return os.environ.get("A2A_SHIM_URL", "").rstrip("/")


def get_shim_api_key() -> str:
    """API key for shim operations. Falls back to A2A_API_KEY."""
    key = os.environ.get("A2A_SHIM_API_KEY", "").strip()
    return key or get_api_key()


def get_settlebridge_url() -> str:
    """SettleBridge base URL. Trailing slashes are removed."""
    return os.environ.get("SETTLEBRIDGE_URL", "https://market.settlebridge.ai").rstrip("/")


def get_settlebridge_gateway_token() -> str:
    """mcp-trust-gateway access token. Empty when unset."""
    return os.environ.get("SETTLEBRIDGE_GATEWAY_TOKEN", "").strip()


def get_settlebridge_heartbeat() -> bool:
    """True when SETTLEBRIDGE_HEARTBEAT is on (case-insensitive)."""
    return os.environ.get("SETTLEBRIDGE_HEARTBEAT", "off").strip().lower() == "on"


def get_settlebridge_min_bounty_usd() -> float:
    """Minimum bounty size in USD. Invalid values fall back to 0."""
    raw = os.environ.get("SETTLEBRIDGE_MIN_BOUNTY_USD", "0").strip()
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def get_settlebridge_languages() -> list[str]:
    """Languages advertised by the worker heartbeat. Empty when unset."""
    raw = os.environ.get("SETTLEBRIDGE_LANGUAGES", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def get_settlebridge_max_tokens() -> int | None:
    """Optional token budget for the heartbeat. None when unset or invalid."""
    raw = os.environ.get("SETTLEBRIDGE_MAX_TOKENS", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
