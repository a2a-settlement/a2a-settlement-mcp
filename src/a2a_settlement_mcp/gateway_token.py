"""Fetch and refresh mcp-trust-gateway access tokens with client credentials.

Gateway tokens expire (one hour by default), so a long agent session can't rely
on a fixed ``SETTLEBRIDGE_GATEWAY_TOKEN``. When ``SETTLEBRIDGE_GATEWAY_CLIENT_ID``
and ``SETTLEBRIDGE_GATEWAY_CLIENT_SECRET`` are set, this module fetches a token,
caches it, and fetches a new one shortly before it expires or after SettleBridge
rejects it. The client secret and every fetched token are registered for log
redaction and never appear in error messages.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Callable

import httpx

from a2a_settlement_mcp.config import (
    get_settlebridge_gateway_client_id,
    get_settlebridge_gateway_client_secret,
    get_settlebridge_gateway_scope,
    get_settlebridge_gateway_token_url,
)
from a2a_settlement_mcp.settlebridge_client import remember_secret

FINANCIAL_SCOPE = "mcp:tool:financial"
# GitHub lease routes require this scope. Do not add mcp:tool:financial here:
# the gateway refuses that scope for accounts that are not allowlisted.
LEASE_SCOPE = "mcp:read"
_REFRESH_MARGIN_S = 60.0
_DEFAULT_EXPIRES_IN_S = 300.0
_MAX_ERROR_TEXT = 100
_PROVIDER_CACHE_MAX = 1000


class GatewayTokenError(Exception):
    """A gateway token could not be fetched. The message is safe to show a model."""


def _gateway_error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"][:_MAX_ERROR_TEXT]
    return ""


class GatewayTokenProvider:
    """Thread-safe cache of one client-credentials gateway token."""

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str,
        *,
        timeout: float = 15,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._timeout = timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._token = ""
        self._refresh_at = 0.0
        remember_secret(client_secret)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self._token_url, self._client_id, self._client_secret, self._scope)

    def get(self) -> str:
        """A token valid for at least the refresh margin, fetching one if needed."""
        with self._lock:
            if self._token and self._clock() < self._refresh_at:
                return self._token
            self._token, lifetime = self._fetch()
            margin = min(_REFRESH_MARGIN_S, lifetime / 2)
            self._refresh_at = self._clock() + lifetime - margin
            return self._token

    def invalidate(self, token: str) -> None:
        """Drop ``token`` so the next ``get`` fetches a new one."""
        with self._lock:
            if token and token == self._token:
                self._token = ""
                self._refresh_at = 0.0

    def _fetch(self) -> tuple[str, float]:
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": self._scope,
        }
        try:
            with httpx.Client(timeout=self._timeout) as http:
                response = http.post(
                    self._token_url, data=data, headers={"Accept": "application/json"}
                )
        except httpx.RequestError:
            raise GatewayTokenError(
                f"Gateway token request failed: {self._token_url} is not responding."
            ) from None
        if response.status_code in (400, 401, 403):
            code = _gateway_error_code(response) or f"HTTP {response.status_code}"
            hint = (
                " Check SETTLEBRIDGE_GATEWAY_CLIENT_ID (exchange account id) and "
                "SETTLEBRIDGE_GATEWAY_CLIENT_SECRET (exchange API key)."
            )
            if FINANCIAL_SCOPE in self._scope.split() and code in {"invalid_scope", "access_denied"}:
                hint = (
                    f" The gateway would not grant {FINANCIAL_SCOPE}. Ask the operator to add "
                    "this exchange account to MCP_TRUST_FINANCIAL_ALLOWLIST."
                )
            raise GatewayTokenError(f"Gateway refused the token request ({code}).{hint}")
        if response.status_code < 200 or response.status_code >= 300:
            raise GatewayTokenError(
                f"Gateway token request failed with HTTP {response.status_code}."
            )
        try:
            body = response.json()
        except ValueError:
            raise GatewayTokenError("Gateway token response was not JSON.") from None
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise GatewayTokenError("Gateway token response had no access_token.")
        token = token.strip()
        remember_secret(token)
        granted = body.get("scope")
        if (
            FINANCIAL_SCOPE in self._scope.split()
            and isinstance(granted, str)
            and FINANCIAL_SCOPE not in granted.split()
        ):
            raise GatewayTokenError(
                f"The gateway issued a token without {FINANCIAL_SCOPE}. Ask the operator to add "
                "this exchange account to MCP_TRUST_FINANCIAL_ALLOWLIST."
            )
        expires_in = body.get("expires_in")
        if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or expires_in <= 0:
            expires_in = _DEFAULT_EXPIRES_IN_S
        return token, float(expires_in)


_provider_lock = threading.Lock()
_providers: OrderedDict[str, GatewayTokenProvider] = OrderedDict()


def _provider_cache_id(token_url: str, client_id: str, client_secret: str, scope: str) -> str:
    """Cache id for one caller. The raw secret is not the key."""
    material = "\0".join((token_url, client_id, client_secret, scope)).encode()
    return hashlib.sha256(material).hexdigest()


def provider_for(
    token_url: str,
    client_id: str,
    client_secret: str,
    scope: str,
    *,
    clock: Callable[[], float] | None = None,
) -> GatewayTokenProvider:
    """Thread-safe LRU of gateway providers, keyed by sha256 of the credentials.

    The same caller reuses one provider so its token refreshes before expiry.
    """
    cache_id = _provider_cache_id(token_url, client_id, client_secret, scope)
    with _provider_lock:
        existing = _providers.get(cache_id)
        if existing is not None:
            _providers.move_to_end(cache_id)
            return existing
    if clock is None:
        created = GatewayTokenProvider(token_url, client_id, client_secret, scope)
    else:
        created = GatewayTokenProvider(token_url, client_id, client_secret, scope, clock=clock)
    with _provider_lock:
        existing = _providers.get(cache_id)
        if existing is not None:
            _providers.move_to_end(cache_id)
            return existing
        _providers[cache_id] = created
        while len(_providers) > _PROVIDER_CACHE_MAX:
            _providers.popitem(last=False)
        return created


def get_provider() -> GatewayTokenProvider | None:
    """The provider for the current environment, or None without client credentials.

    GitHub lease tools use this. The requested scope defaults to ``mcp:read``.
    """
    client_id = get_settlebridge_gateway_client_id()
    client_secret = get_settlebridge_gateway_client_secret()
    if not client_id or not client_secret:
        return None
    return provider_for(
        get_settlebridge_gateway_token_url(),
        client_id,
        client_secret,
        get_settlebridge_gateway_scope(),
    )


def reset_provider() -> None:
    """Forget cached providers and tokens. Used by tests."""
    with _provider_lock:
        _providers.clear()
