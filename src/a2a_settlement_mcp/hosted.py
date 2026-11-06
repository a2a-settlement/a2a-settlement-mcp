"""Hosted multi-tenant ASGI app.

Each MCP caller sends their own exchange API key. The process does not use
server environment credentials. Run it with::

    uvicorn a2a_settlement_mcp.hosted:app --host 127.0.0.1 --port 3200

Building the app turns hosted mode on. SSE stays on the same paths as
``mcp.sse_app('/')`` (``GET /sse`` and ``POST /messages/``). Streamable HTTP
is served at ``/mcp``.

The caller's key is read from the Starlette request FastMCP attaches to the
MCP message (``request_context.request``). That is the POST that carries the
tool call for both streamable HTTP and SSE. An ASGI middleware contextvar is
not used: SSE runs the tool on the ``GET /sse`` task, so a contextvar set on
the POST would be invisible there.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

from a2a_settlement_mcp.config import get_exchange_url, get_settlebridge_gateway_token_url, get_settlebridge_url
from a2a_settlement_mcp.gateway_token import LEASE_SCOPE, GatewayTokenError, GatewayTokenProvider, provider_for
from a2a_settlement_mcp.owner_session import OwnerSessionProvider, session_for_api_key
from a2a_settlement_mcp.settlebridge_client import remember_secret

logger = logging.getLogger(__name__)

MISSING_BEARER = (
    "Send your exchange API key as Authorization: Bearer <key> in your MCP client config."
)
MALFORMED_BEARER = (
    "Authorization header is invalid. "
    "Send your exchange API key as Authorization: Bearer <key> in your MCP client config."
)

# Startup refuses these. Callers send the key per request instead.
FORBIDDEN_ENV = (
    "SETTLEBRIDGE_API_KEY",
    "SETTLEBRIDGE_GATEWAY_CLIENT_SECRET",
    "SETTLEBRIDGE_GATEWAY_TOKEN",
    "A2A_API_KEY",
    "A2A_DASHBOARD_API_KEY",
)

MAX_KEY_LENGTH = 512
ACCOUNT_TTL_S = 600.0
CACHE_MAX = 1000
_BALANCE_TIMEOUT_S = 15.0

_hosted = False
_app: Starlette | None = None
_state_lock = threading.Lock()


class _TtlCache:
    """Thread-safe TTL LRU. Keys are digests, never raw credentials."""

    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def get(self, key: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            value, expires = item
            if now >= expires:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return value

    def set(self, key: str, value: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._items[key] = (value, now + self._ttl)
            self._items.move_to_end(key)
            while len(self._items) > CACHE_MAX:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


_account_ids = _TtlCache(ACCOUNT_TTL_S)


def is_hosted() -> bool:
    """True after the hosted app has been built in this process."""
    return _hosted


def disable_hosted_mode() -> None:
    """Turn hosted mode off. Used by tests so other cases keep using the environment."""
    global _hosted
    _hosted = False


def reset_hosted_caches() -> None:
    """Drop per-key account ids. Used by tests."""
    _account_ids.clear()


def _refuse_env_credentials() -> None:
    present = [name for name in FORBIDDEN_ENV if os.environ.get(name, "").strip()]
    if not present:
        return
    names = ", ".join(present)
    raise RuntimeError(
        "Hosted MCP server refuses to start because credential environment "
        f"variables are set ({names}). Unset them. Callers send their own "
        "exchange API key as Authorization: Bearer <key>."
    )


def enable_hosted_mode() -> None:
    """Refuse server credentials, then serve each caller with their own key."""
    global _hosted
    _refuse_env_credentials()
    with _state_lock:
        if not _hosted:
            logger.info("Hosted MCP mode enabled; each caller sends Authorization: Bearer")
        _hosted = True


def _valid_token(token: str) -> bool:
    if not token or len(token) > MAX_KEY_LENGTH:
        return False
    if any(char.isspace() for char in token):
        return False
    return token.isprintable()


def parse_authorization(value: str | None) -> tuple[str, str | None]:
    """Return ``(key, error)``. An absent header is ``("", None)``."""
    if value is None or value == "":
        return "", None
    if value != value.strip() or "\r" in value or "\n" in value:
        return "", MALFORMED_BEARER
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer" or not _valid_token(token):
        return "", MALFORMED_BEARER
    remember_secret(token)
    return token, None


def _current_request() -> Any:
    """Starlette request for this MCP call, if the transport attached one."""
    from mcp.server.lowlevel.server import request_ctx

    try:
        ctx = request_ctx.get()
    except LookupError:
        return None
    return getattr(ctx, "request", None)


def read_caller_key() -> tuple[str, str | None]:
    """The bearer key on this MCP request. ``("", None)`` when the header is absent."""
    request = _current_request()
    if request is None:
        return "", None
    headers = getattr(request, "headers", None)
    if headers is None:
        return "", None
    raw = headers.get("authorization")
    return parse_authorization(raw if isinstance(raw, str) else None)


def caller_exchange_key() -> str:
    """Validated caller key, or ``""`` when the header is absent or malformed.

    Malformed headers are reported by ``read_caller_key``; this never falls
    back to the process environment.
    """
    key, err = read_caller_key()
    if err or not key:
        return ""
    return key


def _key_digest(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _fetch_account_id(api_key: str) -> str:
    url = f"{get_exchange_url()}/v1/exchange/balance"
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=_BALANCE_TIMEOUT_S,
            follow_redirects=False,
        )
    except httpx.RequestError:
        raise GatewayTokenError(
            f"Exchange balance request failed: {get_exchange_url()} is not responding."
        ) from None
    if response.status_code != 200:
        raise GatewayTokenError(
            f"Exchange refused this API key (HTTP {response.status_code})."
        )
    try:
        body = response.json()
    except ValueError:
        raise GatewayTokenError("Exchange balance response was not JSON.") from None
    account_id = body.get("account_id") if isinstance(body, dict) else None
    if (
        not isinstance(account_id, str)
        or not account_id.strip()
        or account_id.strip() != account_id
        or any(char.isspace() for char in account_id)
        or len(account_id) > 256
        or api_key in account_id
    ):
        raise GatewayTokenError("Exchange balance response had no account_id.")
    return account_id


def account_id_for_key(api_key: str) -> str:
    """Exchange account id for ``api_key``, cached by sha256 for ten minutes."""
    digest = _key_digest(api_key)
    cached = _account_ids.get(digest)
    if cached is not None:
        return cached
    account_id = _fetch_account_id(api_key)
    _account_ids.set(digest, account_id)
    return account_id


def gateway_provider_for_key(api_key: str) -> GatewayTokenProvider:
    """Client-credentials provider for this caller. Scope is ``mcp:read``.

    ``client_id`` is the exchange account id. ``client_secret`` is the caller's
    key. The server environment is not consulted.
    """
    account_id = account_id_for_key(api_key)
    return provider_for(
        get_settlebridge_gateway_token_url(),
        account_id,
        api_key,
        LEASE_SCOPE,
    )


def owner_session_for_key(api_key: str) -> OwnerSessionProvider:
    """SettleBridge owner session for this caller's exchange API key."""
    return session_for_api_key(get_settlebridge_url(), api_key)


def _hosted_security() -> Any:
    from mcp.server.transport_security import TransportSecuritySettings

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
            "mcp.a2a-settlement.org",
            "mcp.a2a-settlement.org:*",
        ],
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
            "https://mcp.a2a-settlement.org",
        ],
    )


def _construct_app() -> Starlette:
    """SSE paths from ``mcp.sse_app('/')``, plus streamable HTTP at ``/mcp``.

    SSE keeps the FastMCP instance's existing transport security. Streamable
    HTTP also allows the public hosted hostname so Caddy can forward
    ``Host: mcp.a2a-settlement.org``.
    """
    from a2a_settlement_mcp.server import mcp

    sse_app = mcp.sse_app("/")
    previous = mcp.settings.transport_security
    mcp.settings.transport_security = _hosted_security()
    try:
        stream_app = mcp.streamable_http_app()
    finally:
        mcp.settings.transport_security = previous
    return Starlette(
        routes=[*stream_app.routes, *sse_app.routes],
        lifespan=stream_app.router.lifespan_context,
    )


def build_app() -> Starlette:
    """Create the hosted ASGI app and turn hosted mode on.

    Raises ``RuntimeError`` when server credential environment variables are set.
    """
    global _app
    enable_hosted_mode()
    if _app is None:
        _app = _construct_app()
    return _app


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI entrypoint: ``uvicorn a2a_settlement_mcp.hosted:app``."""
    await build_app()(scope, receive, send)


@contextmanager
def caller_authorization(header: str | None) -> Iterator[None]:
    """Install a Starlette request as FastMCP's current request. Tests use this.

    ``header`` is the raw ``Authorization`` value. ``None`` omits the header.
    """
    from mcp.server.lowlevel.server import request_ctx
    from mcp.shared.context import RequestContext

    raw_headers: list[tuple[bytes, bytes]] = []
    if header is not None:
        raw_headers.append((b"authorization", header.encode("latin-1")))
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 50000),
        "server": ("mcp.a2a-settlement.org", 443),
    }
    request = Request(scope)
    ctx = RequestContext(
        request_id="hosted-test",
        meta=None,
        session=None,  # type: ignore[arg-type]
        lifespan_context=None,
        request=request,
    )
    token = request_ctx.set(ctx)
    try:
        yield
    finally:
        request_ctx.reset(token)
