"""MCP tools for SettleBridge GitHub bounties.

Listed bounties are balance checked; funds not reserved. Payment happens only
after the maintainer merges the pull request and required checks pass on that
merged commit, with no open policy violations. Submitting a pull request is
not payment.

The heartbeat thread is a daemon and logs through ``logging`` only. It must
not write to stdout: that stream is the stdio MCP transport.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import httpx

from .config import (
    get_settlebridge_gateway_token,
    get_settlebridge_heartbeat,
    get_settlebridge_languages,
    get_settlebridge_max_tokens,
    get_settlebridge_min_bounty_usd,
    get_settlebridge_url,
)
from .server import _error_result, _json_result, mcp
from .settlebridge_client import (
    SettleBridgeClient,
    SettleBridgeError,
    parse_uuid,
    redact_secrets,
    remember_secret,
)

logger = logging.getLogger(__name__)

_DEFAULT_HEARTBEAT_INTERVAL_S = 60.0

FUNDS_NOTE = (
    "Listed bounties are balance checked; funds not reserved. "
    "Payment requires the maintainer to merge the pull request and "
    "required checks to pass on that merged commit, with no open policy violations."
)

SUBMISSION_NOTE = (
    "Submission is not payment. Payment happens only after the maintainer "
    "merges the pull request and required checks pass on that merged commit, "
    "with no open policy violations."
)

_TOKEN_REQUIRED = (
    "SETTLEBRIDGE_GATEWAY_TOKEN is required. "
    "Set an mcp-trust-gateway access token in the server environment."
)

_LEASE_TOKEN_REQUIRED = (
    "lease_token is required. Pass lease_token, or claim the lease in this process."
)

_MODES = {"idle": "AVAILABLE_IDLE", "busy": "BUSY_PREEMPTED"}

_state_lock = threading.Lock()
_thread_lock = threading.Lock()
_leases: dict[str, dict[str, str]] = {}
_worker: dict[str, Any] = {
    "status": "AVAILABLE_IDLE",
    "min_bounty_usd": None,
    "max_tokens": None,
}
_stop = threading.Event()
_stop.set()
_thread: threading.Thread | None = None


def _new_client() -> SettleBridgeClient:
    return SettleBridgeClient(
        get_settlebridge_url(),
        get_settlebridge_gateway_token(),
    )


def _require_token() -> str | None:
    if not get_settlebridge_gateway_token():
        return _error_result(_TOKEN_REQUIRED)
    return None


_ERROR_DETAIL_KEYS = ("error", "error_type", "tool", "evaluations", "upgrade_path", "retry_after_s")
_MAX_ERROR_TEXT = 200


def _http_error(exc: SettleBridgeError) -> str:
    """Return the SettleBridge error code, HTTP status, and allowlisted details.

    The response body is untrusted, so only known fields are passed to the model
    and known credentials are scrubbed from them.
    """
    body = exc.body
    details: dict[str, Any] = {}
    if isinstance(body, dict):
        details = {key: body[key] for key in _ERROR_DETAIL_KEYS if key in body}
    error: object = details.get("error")
    if not isinstance(error, str) or not error:
        if isinstance(body, str) and body.strip():
            error = body.strip()[:_MAX_ERROR_TEXT]
        else:
            error = f"HTTP {exc.status_code}"
    result = {"error": error, "status": exc.status_code, "details": details}
    return redact_secrets(_json_result(result))


def _connection_error() -> str:
    return _error_result(
        f"SettleBridge connection failed: {get_settlebridge_url()} is not responding."
    )


def _run(action: Callable[[], Any]) -> str:
    _ensure_heartbeat()
    try:
        return _json_result(action())
    except SettleBridgeError as exc:
        return _http_error(exc)
    except ValueError as exc:
        return _error_result(str(exc))
    except httpx.RequestError:
        return _connection_error()


def _remember_lease(payload: dict[str, Any], requested_bounty_id: str) -> None:
    lease_id = payload.get("lease_id")
    token = payload.get("lease_token")
    if not isinstance(lease_id, str) or not isinstance(token, str) or not token.strip():
        return
    try:
        lease_key = parse_uuid(lease_id, field="lease_id")
    except ValueError:
        lease_key = lease_id
    bounty_raw = payload.get("bounty_id") or requested_bounty_id
    try:
        bounty_key = parse_uuid(str(bounty_raw), field="bounty_id")
    except ValueError:
        bounty_key = str(bounty_raw)
    remember_secret(token)
    with _state_lock:
        _leases[lease_key] = {"lease_token": token, "bounty_id": bounty_key}


def _forget_lease(lease_id: str) -> None:
    with _state_lock:
        _leases.pop(lease_id, None)


def _resolve_token(lease_id: str, explicit: str) -> str:
    token = explicit.strip()
    if token:
        remember_secret(token)
        return token
    with _state_lock:
        record = _leases.get(lease_id)
    if not record:
        raise ValueError(_LEASE_TOKEN_REQUIRED)
    return record["lease_token"]


def _resolve_submit_lease(
    bounty_id: str,
    lease_id: str,
    lease_token: str,
) -> tuple[str, str]:
    if lease_id.strip():
        lease_key = parse_uuid(lease_id, field="lease_id")
        return lease_key, _resolve_token(lease_key, lease_token)
    with _state_lock:
        matches = [
            stored_id
            for stored_id, record in _leases.items()
            if record.get("bounty_id") == bounty_id
        ]
    if not matches:
        raise ValueError(
            "No stored lease for this bounty_id. Pass lease_id and lease_token."
        )
    if len(matches) > 1:
        raise ValueError("Ambiguous lease for this bounty_id. Pass lease_id.")
    lease_key = matches[0]
    return lease_key, _resolve_token(lease_key, lease_token)


def _bounty_meets_minimum(bounty: dict[str, Any], minimum: float) -> bool:
    if minimum <= 0:
        return True
    try:
        amount = float(bounty.get("amount_usd"))
    except (TypeError, ValueError):
        return False
    return amount >= minimum


def _claim_guidance(payload: dict[str, Any]) -> str:
    issue = payload.get("issue_number")
    fixes = f"Fixes #{issue}" if issue is not None else "Fixes #<issue_number>"
    repo = payload.get("repo") or "the bounty repository"
    return (
        f"Open a pull request on {repo} whose body contains `{fixes}`, "
        "authored by the GitHub account linked to your SettleBridge user, "
        "then call submit_github_patch. SettleBridge fetches the diff from GitHub. "
        "Submission is not payment."
    )


def _heartbeat_configured() -> bool:
    return get_settlebridge_heartbeat() and bool(get_settlebridge_gateway_token())


def _worker_settings() -> tuple[str, float, int | None]:
    with _state_lock:
        status = str(_worker["status"])
        min_override = _worker["min_bounty_usd"]
        max_override = _worker["max_tokens"]
    if min_override is None:
        minimum = get_settlebridge_min_bounty_usd()
    else:
        minimum = float(min_override)
    if max_override is None:
        max_tokens = get_settlebridge_max_tokens()
    else:
        max_tokens = int(max_override)
    return status, minimum, max_tokens


def _send_heartbeat() -> Any:
    token = get_settlebridge_gateway_token()
    if not token:
        return {}
    status, minimum, max_tokens = _worker_settings()
    return _new_client().heartbeat(
        status=status,
        languages=get_settlebridge_languages(),
        min_bounty_usd=minimum,
        max_tokens=max_tokens,
    )


def _log_heartbeat_failure(exc: Exception) -> None:
    """Log a heartbeat failure without the exception text (it may echo a token)."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        logger.warning("SettleBridge heartbeat failed with HTTP %s", status)
        return
    logger.warning("SettleBridge heartbeat failed (%s)", type(exc).__name__)


def _heartbeat_loop(stop: threading.Event, interval_s: float) -> None:
    while not stop.is_set():
        try:
            _send_heartbeat()
        except Exception as exc:
            _log_heartbeat_failure(exc)
        if stop.wait(interval_s):
            return


def start_heartbeat(interval_s: float = _DEFAULT_HEARTBEAT_INTERVAL_S) -> None:
    """Start the daemon heartbeat when it is enabled and a gateway token is set.

    Safe to call more than once. Does nothing when SETTLEBRIDGE_HEARTBEAT is
    off or SETTLEBRIDGE_GATEWAY_TOKEN is empty. Never writes to stdout.
    ``interval_s`` overrides the 60 second period (tests inject a short one).
    """
    global _thread, _stop
    if interval_s <= 0:
        raise ValueError("interval_s must be positive")
    if not _heartbeat_configured():
        return
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return
        stop = threading.Event()
        _stop = stop
        thread = threading.Thread(
            target=_heartbeat_loop,
            args=(stop, interval_s),
            name="settlebridge-heartbeat",
            daemon=True,
        )
        _thread = thread
        thread.start()


def stop_heartbeat() -> None:
    """Stop the background heartbeat thread if it is running."""
    global _thread
    with _thread_lock:
        stop = _stop
        thread = _thread
        _thread = None
    stop.set()
    if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
        thread.join(timeout=5)


def _ensure_heartbeat() -> None:
    try:
        start_heartbeat()
    except Exception as exc:
        _log_heartbeat_failure(exc)


def reset_local_state() -> None:
    """Clear in-process leases and worker settings. Used by tests."""
    stop_heartbeat()
    with _state_lock:
        _leases.clear()
        _worker["status"] = "AVAILABLE_IDLE"
        _worker["min_bounty_usd"] = None
        _worker["max_tokens"] = None


@mcp.tool()
def list_open_github_bounties(
    language: str = "",
    max_difficulty: str = "",
    limit: int = 20,
) -> str:
    """List open GitHub bounties on SettleBridge.

    Listed bounties are balance checked; funds not reserved. Payment requires
    the maintainer to merge the pull request, required checks to pass on that
    merged commit, and no open policy violations. Nothing is paid when a
    bounty is listed or when a pull request is submitted.

    Results are filtered in this process by SETTLEBRIDGE_MIN_BOUNTY_USD.

    Args:
        language: Optional language filter forwarded to SettleBridge.
        max_difficulty: Accepted for forward compatibility. Not yet supported
            by the server; ignored.
        limit: Maximum number of bounties to request from SettleBridge.
    """

    def action() -> dict[str, Any]:
        payload = _new_client().list_bounties(language=language or "", limit=limit)
        if not isinstance(payload, dict):
            payload = {"bounties": payload}
        raw = payload.get("bounties") or []
        bounties = raw if isinstance(raw, list) else []
        minimum = get_settlebridge_min_bounty_usd()
        filtered = [
            bounty
            for bounty in bounties
            if isinstance(bounty, dict) and _bounty_meets_minimum(bounty, minimum)
        ]
        result = dict(payload)
        result["bounties"] = filtered
        note = FUNDS_NOTE
        if (max_difficulty or "").strip():
            note = (
                f"{note} max_difficulty is not yet supported by the server and was ignored."
            )
        result["note"] = note
        return result

    return _run(action)


@mcp.tool()
def claim_github_bounty_lease(bounty_id: str) -> str:
    """Claim an exclusive lease on a GitHub bounty.

    Requires SETTLEBRIDGE_GATEWAY_TOKEN (an mcp-trust-gateway access token).
    The lease token is stored in this process and also returned so a restarted
    agent can pass it to later tools. The response includes the frozen spec
    and test command.

    Next step: open a pull request on the bounty repo whose body contains
    `Fixes #<issue_number>`, authored by the GitHub account linked to your
    SettleBridge user, then call submit_github_patch. SettleBridge fetches
    the diff from GitHub.

    Listed bounties are balance checked; funds not reserved. Claiming a lease
    does not pay the bounty. Payment happens only after the maintainer merges
    the pull request and required checks pass on that commit, with no open
    policy violations. Submission is not payment.
    """
    missing = _require_token()
    if missing:
        return missing

    def action() -> dict[str, Any]:
        bounty_key = parse_uuid(bounty_id, field="bounty_id")
        result = _new_client().claim_lease(bounty_key)
        if not isinstance(result, dict):
            raise SettleBridgeError(502, result)
        _remember_lease(result, bounty_key)
        merged = dict(result)
        merged["guidance"] = _claim_guidance(merged)
        merged["note"] = FUNDS_NOTE
        return merged

    return _run(action)


@mcp.tool()
def renew_github_lease(lease_id: str, lease_token: str = "") -> str:
    """Keep a bounty lease alive until its work deadline.

    ``lease_token`` may be omitted when this process claimed the lease.
    Requires SETTLEBRIDGE_GATEWAY_TOKEN. Renewing a lease is not payment.
    Payment happens only after the maintainer merges the pull request and
    required checks pass on that merged commit.
    """
    missing = _require_token()
    if missing:
        return missing

    def action() -> Any:
        lease_key = parse_uuid(lease_id, field="lease_id")
        token = _resolve_token(lease_key, lease_token)
        return _new_client().renew_lease(lease_key, token)

    return _run(action)


@mcp.tool()
def release_github_lease(lease_id: str, lease_token: str = "") -> str:
    """Release an exclusive bounty lease so another agent can claim it.

    ``lease_token`` may be omitted when this process claimed the lease.
    Requires SETTLEBRIDGE_GATEWAY_TOKEN. Releasing a lease is not payment.
    """
    missing = _require_token()
    if missing:
        return missing

    def action() -> Any:
        lease_key = parse_uuid(lease_id, field="lease_id")
        token = _resolve_token(lease_key, lease_token)
        result = _new_client().release_lease(lease_key, token)
        _forget_lease(lease_key)
        return result

    return _run(action)


@mcp.tool()
def submit_github_patch(
    bounty_id: str,
    pr_url: str,
    lease_id: str = "",
    lease_token: str = "",
    otel_trace_id: str = "",
) -> str:
    """Submit a pull request against a leased GitHub bounty.

    There is no git_diff parameter. SettleBridge fetches the diff from GitHub
    itself. When ``lease_id`` is omitted, the single lease stored in this
    process for ``bounty_id`` is used.

    Submission is not payment. Payment happens only after the maintainer
    merges the pull request and required checks pass on that merged commit,
    with no open policy violations. Listed bounties are balance checked;
    funds not reserved.
    """
    missing = _require_token()
    if missing:
        return missing

    def action() -> dict[str, Any]:
        bounty_key = parse_uuid(bounty_id, field="bounty_id")
        lease_key, token = _resolve_submit_lease(bounty_key, lease_id, lease_token)
        result = _new_client().submit_patch(lease_key, token, pr_url, otel_trace_id)
        merged = dict(result) if isinstance(result, dict) else {"result": result}
        merged["note"] = SUBMISSION_NOTE
        return merged

    return _run(action)


@mcp.tool()
def get_github_bounty_status(bounty_id: str) -> str:
    """Fetch public status for a GitHub bounty.

    Returns bounty state, the bound pull request, assessment, and settlement
    when SettleBridge has them. A submitted pull request is not payment.
    Payment happens only after the maintainer merges and required checks pass
    on that commit. Listed bounties are balance checked; funds not reserved.
    """

    def action() -> Any:
        return _new_client().get_bounty_status(bounty_id)

    return _run(action)


@mcp.tool()
def set_worker_availability(
    mode: str,
    min_bounty_usd: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Advertise this worker as idle or busy and send one heartbeat.

    ``mode`` is ``idle`` (AVAILABLE_IDLE) or ``busy`` (BUSY_PREEMPTED),
    case-insensitive. Saved settings are reused by the background heartbeat.
    Requires SETTLEBRIDGE_GATEWAY_TOKEN. This does not claim or pay a bounty.
    Listed bounties are balance checked; funds not reserved.
    """
    mode_key = (mode or "").strip().lower()
    if mode_key not in _MODES:
        return _error_result("mode must be 'idle' or 'busy'")
    missing = _require_token()
    if missing:
        return missing
    with _state_lock:
        _worker["status"] = _MODES[mode_key]
        if min_bounty_usd is not None:
            _worker["min_bounty_usd"] = float(min_bounty_usd)
        if max_tokens is not None:
            _worker["max_tokens"] = int(max_tokens)
    return _run(_send_heartbeat)


# Server import of this module is process startup. No-op unless configured.
start_heartbeat()
