"""Read-only SettleBridge account tools.

These call SettleBridge with the caller's own exchange API key:
``SETTLEBRIDGE_API_KEY`` when it is set, otherwise
``SETTLEBRIDGE_GATEWAY_CLIENT_SECRET``. The hosted MCP server sets neither,
so the tools do nothing there.
"""

from __future__ import annotations

import os

import httpx

from .config import get_settlebridge_url
from .server import _error_result, _json_result, mcp

_KEY_REQUIRED = (
    "Set SETTLEBRIDGE_API_KEY or SETTLEBRIDGE_GATEWAY_CLIENT_SECRET to your own "
    "exchange API key. The hosted MCP server has no key, so run this tool from "
    "your own MCP client."
)

_TIMEOUT_S = 30.0


def _exchange_api_key() -> str:
    """Caller's exchange API key. Empty when this process has neither accepted var.

    No other environment variable is read as a key.
    """
    api_key = os.environ.get("SETTLEBRIDGE_API_KEY", "").strip()
    if api_key:
        return api_key
    return os.environ.get("SETTLEBRIDGE_GATEWAY_CLIENT_SECRET", "").strip()


def _get(path: str, *, csv: bool = False) -> str:
    key = _exchange_api_key()
    if not key:
        return _error_result(_KEY_REQUIRED)
    try:
        response = httpx.get(
            f"{get_settlebridge_url()}{path}",
            headers={"Authorization": f"Bearer {key}"},
            timeout=_TIMEOUT_S,
            follow_redirects=False,
        )
    except httpx.RequestError:
        return _error_result("SettleBridge is not responding")
    # A non-2xx body can echo the API key, so the error carries only the status.
    if response.status_code < 200 or response.status_code >= 300:
        return _error_result(f"SettleBridge returned HTTP {response.status_code}")
    if csv:
        return _json_result({"csv": response.text})
    return response.text


@mcp.tool()
def get_my_ledger(csv: bool = False) -> str:
    """Read this account's SettleBridge wallet ledger. Read-only.

    Uses the caller's own exchange API key (``SETTLEBRIDGE_API_KEY``, or
    ``SETTLEBRIDGE_GATEWAY_CLIENT_SECRET`` when the API key is unset). The
    hosted MCP server has no key, so this does nothing there.

    Reports wallet totals: available, held, committed to open bounties, and
    free to fund. Includes one row per bounty with that bounty's amount and
    fee, and the other held escrows.

    Args:
        csv: When true, return the ledger as CSV text inside ``{"csv": "..."}``.
    """
    path = "/api/v1/me/ledger.csv" if csv else "/api/v1/me/ledger"
    return _get(path, csv=csv)


@mcp.tool()
def get_solver_setup_status() -> str:
    """Read what is left before this account can solve GitHub bounties.

    Uses the caller's own exchange API key (``SETTLEBRIDGE_API_KEY``, or
    ``SETTLEBRIDGE_GATEWAY_CLIENT_SECRET`` when the API key is unset).
    Read-only. The hosted MCP server has no key, so this does nothing there.

    Reports the remaining setup steps, reputation, financial scope, eligible
    bounties, and an MCP config snippet whose secret is a placeholder.
    """
    return _get("/api/v1/github/solver/setup-status")
