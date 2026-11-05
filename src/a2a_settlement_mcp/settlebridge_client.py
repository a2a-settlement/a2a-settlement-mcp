"""Sync HTTP client for the SettleBridge GitHub bounty API.

Gateway tokens and lease tokens are sent only as headers. They are never
written to logs: a log-record factory redacts any known secret before a
handler, including the stdio transport's logs, can emit it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from typing import Any

import httpx

_secrets_lock = threading.Lock()
_known_secrets: set[str] = set()
_redaction_installed = False
_factory_guard = threading.local()

# Short strings are not redacted so ordinary log text cannot be blanked.
_MIN_SECRET_LEN = 8


class SettleBridgeError(Exception):
    """Non-2xx response from SettleBridge.

    ``body`` is parsed JSON when the response is JSON, otherwise the raw text.
    The exception message intentionally omits ``body`` and credentials.
    """

    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"SettleBridge request failed with HTTP {status_code}")


def remember_secret(value: str) -> None:
    """Remember a credential so later log records can be redacted."""
    if not isinstance(value, str):
        return
    secret = value.strip()
    if len(secret) < _MIN_SECRET_LEN:
        return
    with _secrets_lock:
        _known_secrets.add(secret)


def parse_uuid(value: str, *, field: str) -> str:
    """Return the canonical UUID text or raise ValueError.

    Callers must use the returned value in URLs so a raw id cannot change the path.
    """
    if not isinstance(value, str):
        raise ValueError(f"Invalid {field}: must be a UUID")
    text = value.strip()
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"Invalid {field}: must be a UUID") from exc
    return str(parsed)


def _secrets_snapshot() -> tuple[str, ...]:
    found: list[str] = []
    env_token = os.environ.get("SETTLEBRIDGE_GATEWAY_TOKEN", "").strip()
    if len(env_token) >= _MIN_SECRET_LEN:
        found.append(env_token)
    with _secrets_lock:
        found.extend(secret for secret in _known_secrets if len(secret) >= _MIN_SECRET_LEN)
    unique: list[str] = []
    seen: set[str] = set()
    for secret in found:
        if secret not in seen:
            seen.add(secret)
            unique.append(secret)
    return tuple(unique)


def _redact_text(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, "[redacted]")
    return text


def redact_secrets(text: str) -> str:
    """Replace every remembered credential in ``text`` with ``[redacted]``."""
    return _redact_text(text, _secrets_snapshot())


def _redact_record(record: logging.LogRecord) -> None:
    secrets = _secrets_snapshot()
    if not secrets:
        return
    try:
        rendered = record.getMessage()
    except Exception:
        rendered = str(record.msg)
    if any(secret in rendered for secret in secrets):
        record.msg = _redact_text(rendered, secrets)
        record.args = ()
    if isinstance(record.exc_text, str) and record.exc_text:
        record.exc_text = _redact_text(record.exc_text, secrets)
    exc = record.exc_info[1] if record.exc_info else None
    if exc is not None and any(secret in str(exc) for secret in secrets):
        record.exc_info = None
        if not record.exc_text:
            record.exc_text = "[redacted exception]"


def _install_log_redaction() -> None:
    """Redact known credentials from every log record in this process."""
    global _redaction_installed
    if _redaction_installed:
        return
    _redaction_installed = True
    original = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = original(*args, **kwargs)
        if getattr(_factory_guard, "active", False):
            return record
        _factory_guard.active = True
        try:
            _redact_record(record)
        except Exception:
            return record
        finally:
            _factory_guard.active = False
        return record

    logging.setLogRecordFactory(factory)


_install_log_redaction()


def _reject_header_break(value: str) -> None:
    """Refuse CR/LF so a credential cannot be written into the request line."""
    if "\n" in value or "\r" in value:
        raise ValueError("credential header value is invalid")


def _response_body(response: httpx.Response) -> Any:
    if not response.content:
        return {}
    try:
        return response.json()
    except json.JSONDecodeError:
        return response.text


class SettleBridgeClient:
    """Small synchronous SettleBridge client. One method per bounty endpoint."""

    def __init__(self, base_url: str, gateway_token: str = "", *, timeout: float = 30) -> None:
        self._base_url = base_url.rstrip("/")
        self._gateway_token = gateway_token.strip()
        self._timeout = timeout
        if self._gateway_token:
            remember_secret(self._gateway_token)

    def list_bounties(self, *, language: str = "", limit: int = 20) -> Any:
        """GET /api/v1/github/bounties. Public; auth is sent only when configured."""
        return self._request(
            "GET",
            "/api/v1/github/bounties",
            params={"language": language, "limit": str(limit)},
        )

    def claim_lease(self, bounty_id: str) -> Any:
        """POST /api/v1/github/bounties/{bounty_id}/claim-lease."""
        bounty = parse_uuid(bounty_id, field="bounty_id")
        return self._request("POST", f"/api/v1/github/bounties/{bounty}/claim-lease")

    def renew_lease(self, lease_id: str, lease_token: str) -> Any:
        """POST /api/v1/github/leases/{lease_id}/renew."""
        lease = parse_uuid(lease_id, field="lease_id")
        return self._request(
            "POST",
            f"/api/v1/github/leases/{lease}/renew",
            lease_token=lease_token,
        )

    def release_lease(self, lease_id: str, lease_token: str) -> Any:
        """POST /api/v1/github/leases/{lease_id}/release."""
        lease = parse_uuid(lease_id, field="lease_id")
        return self._request(
            "POST",
            f"/api/v1/github/leases/{lease}/release",
            lease_token=lease_token,
        )

    def submit_patch(
        self,
        lease_id: str,
        lease_token: str,
        pr_url: str,
        otel_trace_id: str = "",
    ) -> Any:
        """POST /api/v1/github/leases/{lease_id}/submit.

        SettleBridge loads the diff from GitHub. This client does not send one.
        """
        lease = parse_uuid(lease_id, field="lease_id")
        body: dict[str, Any] = {"pr_url": pr_url}
        if otel_trace_id:
            body["otel_trace_id"] = otel_trace_id
        return self._request(
            "POST",
            f"/api/v1/github/leases/{lease}/submit",
            json_body=body,
            lease_token=lease_token,
        )

    def get_bounty_status(self, bounty_id: str) -> Any:
        """GET /api/v1/github/bounties/{bounty_id}/status. Public."""
        bounty = parse_uuid(bounty_id, field="bounty_id")
        return self._request("GET", f"/api/v1/github/bounties/{bounty}/status")

    def heartbeat(
        self,
        *,
        status: str,
        languages: list[str],
        min_bounty_usd: float,
        max_tokens: int | None = None,
    ) -> Any:
        """POST /api/v1/workers/heartbeat."""
        if status not in {"AVAILABLE_IDLE", "BUSY_PREEMPTED"}:
            raise ValueError("status must be AVAILABLE_IDLE or BUSY_PREEMPTED")
        body: dict[str, Any] = {
            "status": status,
            "capabilities": {"languages": list(languages)},
            "min_bounty_usd": float(min_bounty_usd),
        }
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)
        return self._request("POST", "/api/v1/workers/heartbeat", json_body=body)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        lease_token: str | None = None,
    ) -> Any:
        if lease_token is not None:
            lease_token = lease_token.strip()
            if not lease_token:
                raise ValueError("lease_token is required")
            _reject_header_break(lease_token)
            remember_secret(lease_token)
        headers: dict[str, str] = {}
        if self._gateway_token:
            _reject_header_break(self._gateway_token)
            headers["Authorization"] = f"Bearer {self._gateway_token}"
        if lease_token is not None:
            headers["X-Lease-Token"] = lease_token
        with httpx.Client(timeout=self._timeout) as http:
            response = http.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=json_body,
                headers=headers,
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise SettleBridgeError(response.status_code, _response_body(response))
        if not response.content:
            return {}
        try:
            return response.json()
        except json.JSONDecodeError:
            raise SettleBridgeError(response.status_code, response.text) from None
