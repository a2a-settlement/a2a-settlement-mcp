"""Sign in to SettleBridge as a repo owner with their exchange API key.

The owner tools call SettleBridge routes that need a signed-in user, not a
gateway token. With ``SETTLEBRIDGE_API_KEY`` set, this module posts the key to
``/api/auth/exchange-login`` (which creates the SettleBridge user on first use),
caches the session token, and signs in again shortly before it expires or after
SettleBridge rejects it. The key and every session token are registered for log
redaction and never appear in error messages.

Never set ``SETTLEBRIDGE_API_KEY`` on a shared, hosted MCP server: every client
of that server would act as the key's owner.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import httpx

from a2a_settlement_mcp.config import get_settlebridge_api_key, get_settlebridge_url
from a2a_settlement_mcp.settlebridge_client import remember_secret

# SettleBridge sessions last 24 hours; sign in again well before that.
SESSION_LIFETIME_S = 23 * 3600.0


class OwnerSessionError(Exception):
    """SettleBridge sign-in failed. The message is safe to show a model."""


class OwnerSessionProvider:
    """Thread-safe cache of one SettleBridge session token."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._token = ""
        self._refresh_at = 0.0
        remember_secret(api_key)

    @property
    def key(self) -> tuple[str, str]:
        return (self._base_url, self._api_key)

    def get(self) -> str:
        with self._lock:
            if self._token and self._clock() < self._refresh_at:
                return self._token
            self._token = self._sign_in()
            self._refresh_at = self._clock() + SESSION_LIFETIME_S
            return self._token

    def invalidate(self, token: str) -> None:
        with self._lock:
            if token and token == self._token:
                self._token = ""
                self._refresh_at = 0.0

    def _sign_in(self) -> str:
        url = f"{self._base_url}/api/auth/exchange-login"
        try:
            with httpx.Client(timeout=self._timeout) as http:
                response = http.post(url, json={"api_key": self._api_key})
        except httpx.RequestError:
            raise OwnerSessionError(
                f"SettleBridge sign-in failed: {self._base_url} is not responding."
            ) from None
        if response.status_code in (400, 401, 403):
            raise OwnerSessionError(
                f"SettleBridge rejected SETTLEBRIDGE_API_KEY (HTTP {response.status_code}). "
                "It must be your exchange API key, starting with ate_."
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise OwnerSessionError(
                f"SettleBridge sign-in failed with HTTP {response.status_code}."
            )
        try:
            body = response.json()
        except ValueError:
            raise OwnerSessionError("SettleBridge sign-in response was not JSON.") from None
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise OwnerSessionError("SettleBridge sign-in response had no access_token.")
        token = token.strip()
        remember_secret(token)
        return token


_provider_lock = threading.Lock()
_provider: OwnerSessionProvider | None = None


def get_owner_session() -> OwnerSessionProvider | None:
    """The session for the current environment, or None without ``SETTLEBRIDGE_API_KEY``."""
    global _provider
    api_key = get_settlebridge_api_key()
    if not api_key:
        return None
    key = (get_settlebridge_url(), api_key)
    with _provider_lock:
        if _provider is None or _provider.key != key:
            _provider = OwnerSessionProvider(*key)
        return _provider


def reset_owner_session() -> None:
    """Forget the cached session. Used by tests."""
    global _provider
    with _provider_lock:
        _provider = None
