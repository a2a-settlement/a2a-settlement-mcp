"""Read-only SettleBridge account tools.

Locally these call SettleBridge with the caller's own exchange API key:
``SETTLEBRIDGE_API_KEY`` when it is set, otherwise
``SETTLEBRIDGE_GATEWAY_CLIENT_SECRET``. On the hosted server they use the
exchange API key from ``Authorization: Bearer``.
"""

from __future__ import annotations

import os

import httpx

from .config import get_settlebridge_url
from .server import _error_result, _json_result, mcp
from .settlebridge_client import remember_secret

_KEY_REQUIRED = (
    "Set SETTLEBRIDGE_API_KEY or SETTLEBRIDGE_GATEWAY_CLIENT_SECRET to your own "
    "exchange API key. The hosted MCP server has no key, so run this tool from "
    "your own MCP client."
)

_TIMEOUT_S = 30.0


def _exchange_api_key() -> tuple[str, str | None]:
    """``(key, error)``. Hosted mode never reads environment credentials.

    Locally, no environment variable other than the two accepted ones is a key.
    """
    from .hosted import MISSING_BEARER, is_hosted, read_caller_key

    if is_hosted():
        key, err = read_caller_key()
        if err:
            return "", err
        if not key:
            return "", MISSING_BEARER
        return key, None
    api_key = os.environ.get("SETTLEBRIDGE_API_KEY", "").strip()
    if api_key:
        return api_key, None
    secret = os.environ.get("SETTLEBRIDGE_GATEWAY_CLIENT_SECRET", "").strip()
    if secret:
        return secret, None
    return "", _KEY_REQUIRED


def _get(path: str, *, csv: bool = False) -> str:
    key, err = _exchange_api_key()
    if err or not key:
        return _error_result(err or _KEY_REQUIRED)
    remember_secret(key)
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
    ``SETTLEBRIDGE_GATEWAY_CLIENT_SECRET`` when the API key is unset).
    On the hosted server, send that key as ``Authorization: Bearer``.

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
    Read-only. On the hosted server, send that key as ``Authorization: Bearer``.

    Reports the remaining setup steps, reputation, financial scope, eligible
    bounties, and an MCP config snippet whose secret is a placeholder.
    """
    return _get("/api/v1/github/solver/setup-status")
