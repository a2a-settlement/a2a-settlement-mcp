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
