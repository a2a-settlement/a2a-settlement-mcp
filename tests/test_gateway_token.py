"""Gateway token fetch, refresh, 401 retry, and tool wiring. respx only, no network."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from a2a_settlement_mcp import github_bounty_tools as bounty_tools
from a2a_settlement_mcp.config import (
    DEFAULT_GATEWAY_SCOPE,
    DEFAULT_GATEWAY_TOKEN_URL,
    get_settlebridge_gateway_scope,
    get_settlebridge_gateway_token_url,
    settlebridge_gateway_configured,
)
from a2a_settlement_mcp.gateway_token import (
    GatewayTokenError,
    GatewayTokenProvider,
    get_provider,
    reset_provider,
)
from a2a_settlement_mcp.github_bounty_tools import claim_github_bounty_lease
from a2a_settlement_mcp.settlebridge_client import SettleBridgeClient

TOKEN_URL = "https://gateway.example/oauth/token"
BASE = "https://market.settlebridge.ai"
CLIENT_ID = "f19013bc-a6f9-4902-9654-bdcddbe3955e"
SECRET = "ate_client-secret-SUPERSECRET"
BOUNTY_ID = "11111111-1111-4111-8111-111111111111"
LEASE_ID = "33333333-3333-4333-8333-333333333333"
_ENV = (
    "SETTLEBRIDGE_URL",
    "SETTLEBRIDGE_GATEWAY_TOKEN",
    "SETTLEBRIDGE_GATEWAY_CLIENT_ID",
    "SETTLEBRIDGE_GATEWAY_CLIENT_SECRET",
    "SETTLEBRIDGE_GATEWAY_TOKEN_URL",
    "SETTLEBRIDGE_GATEWAY_SCOPE",
    "SETTLEBRIDGE_HEARTBEAT",
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    bounty_tools.reset_local_state()
    yield
    bounty_tools.reset_local_state()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _token(value: str, expires_in: object = 3600, scope: str = DEFAULT_GATEWAY_SCOPE) -> httpx.Response:
    body: dict = {"access_token": value, "token_type": "Bearer", "scope": scope}
    if expires_in is not None:
        body["expires_in"] = expires_in
    return httpx.Response(200, json=body)


def _provider(clock: Clock | None = None, scope: str = DEFAULT_GATEWAY_SCOPE) -> GatewayTokenProvider:
    return GatewayTokenProvider(TOKEN_URL, CLIENT_ID, SECRET, scope, clock=clock or Clock())


def test_config_defaults_and_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    assert get_settlebridge_gateway_token_url() == DEFAULT_GATEWAY_TOKEN_URL
    assert get_settlebridge_gateway_scope() == "mcp:read mcp:tool:financial"
    assert settlebridge_gateway_configured() is False
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_ID", CLIENT_ID)
    assert settlebridge_gateway_configured() is False
    assert get_provider() is None
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_SECRET", f"  {SECRET}  ")
    assert settlebridge_gateway_configured() is True
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_SCOPE", "  mcp:read   mcp:tool:financial ")
    assert get_settlebridge_gateway_scope() == "mcp:read mcp:tool:financial"


def test_provider_is_reused_until_the_environment_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_SECRET", SECRET)
    first = get_provider()
    assert first is not None and get_provider() is first
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_SCOPE", "mcp:read")
    assert get_provider() is not first
    reset_provider()


def test_fetch_sends_client_credentials_and_caches() -> None:
    with respx.mock:
        route = respx.post(TOKEN_URL).mock(return_value=_token("tok-one-123456"))
        provider = _provider()
        assert provider.get() == "tok-one-123456"
        assert provider.get() == "tok-one-123456"
    assert route.call_count == 1
    form = parse_qs(route.calls[0].request.content.decode())
    assert form == {
        "grant_type": ["client_credentials"],
        "client_id": [CLIENT_ID],
        "client_secret": [SECRET],
        "scope": ["mcp:read mcp:tool:financial"],
    }
    assert SECRET not in str(route.calls[0].request.url)


def test_refreshes_a_minute_before_expiry() -> None:
    clock = Clock()
    with respx.mock:
        route = respx.post(TOKEN_URL).mock(
            side_effect=[_token("tok-one-123456"), _token("tok-two-123456")]
        )
        provider = _provider(clock)
        assert provider.get() == "tok-one-123456"
        clock.now += 3600 - 61
        assert provider.get() == "tok-one-123456"
        clock.now += 2
        assert provider.get() == "tok-two-123456"
    assert route.call_count == 2


def test_short_or_missing_lifetimes() -> None:
    clock = Clock()
    with respx.mock:
        respx.post(TOKEN_URL).mock(
            side_effect=[_token("tok-one-123456", 40), _token("tok-two-123456", None)]
        )
        provider = _provider(clock)
        provider.get()
        clock.now += 21
        assert provider.get() == "tok-two-123456"
        clock.now += 300 - 61
        assert provider.get() == "tok-two-123456"


def test_invalidate_only_drops_the_current_token() -> None:
    with respx.mock:
        route = respx.post(TOKEN_URL).mock(
            side_effect=[_token("tok-one-123456"), _token("tok-two-123456")]
        )
        provider = _provider()
        provider.get()
        provider.invalidate("some-other-token")
        assert provider.get() == "tok-one-123456"
        provider.invalidate("tok-one-123456")
        assert provider.get() == "tok-two-123456"
    assert route.call_count == 2


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(401, json={"error": "invalid_client"}), "invalid_client.*SETTLEBRIDGE_GATEWAY_CLIENT_ID"),
        (httpx.Response(400, json={"error": "invalid_scope"}), "MCP_TRUST_FINANCIAL_ALLOWLIST"),
        (httpx.Response(403, text="nope"), r"HTTP 403.*CLIENT_SECRET"),
        (httpx.Response(503), "HTTP 503"),
        (httpx.Response(200, text="not json"), "not JSON"),
        (httpx.Response(200, json={"token_type": "Bearer"}), "no access_token"),
        (_token("tok-one-123456", scope="mcp:read"), "without mcp:tool:financial"),
    ],
)
def test_fetch_errors_are_safe_and_actionable(response: httpx.Response, expected: str) -> None:
    with respx.mock:
        respx.post(TOKEN_URL).mock(return_value=response)
        with pytest.raises(GatewayTokenError, match=expected) as caught:
            _provider().get()
    assert SECRET not in str(caught.value)
    assert CLIENT_ID not in str(caught.value)


def test_read_only_scope_does_not_require_financial() -> None:
    with respx.mock:
        respx.post(TOKEN_URL).mock(return_value=_token("tok-one-123456", scope="mcp:read"))
        assert _provider(scope="mcp:read").get() == "tok-one-123456"


def test_connection_error_names_the_url_only() -> None:
    with respx.mock:
        respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(GatewayTokenError, match="gateway.example/oauth/token is not responding"):
            _provider().get()


def test_client_retries_once_with_a_new_token_after_401() -> None:
    url = f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease"
    with respx.mock:
        respx.post(TOKEN_URL).mock(
            side_effect=[_token("tok-old-123456"), _token("tok-new-123456")]
        )
        route = respx.post(url).mock(
            side_effect=[httpx.Response(401, json={"error": "invalid_token"}), httpx.Response(201, json={"ok": True})]
        )
        client = SettleBridgeClient(BASE, token_provider=_provider())
        assert client.claim_lease(BOUNTY_ID) == {"ok": True}
    auth = [call.request.headers["Authorization"] for call in route.calls]
    assert auth == ["Bearer tok-old-123456", "Bearer tok-new-123456"]


def test_client_gives_up_after_one_retry() -> None:
    url = f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease"
    with respx.mock:
        respx.post(TOKEN_URL).mock(
            side_effect=[_token("tok-old-123456"), _token("tok-new-123456")]
        )
        route = respx.post(url).mock(return_value=httpx.Response(401, json={"error": "invalid_token"}))
        client = SettleBridgeClient(BASE, token_provider=_provider())
        with pytest.raises(Exception) as caught:
            client.claim_lease(BOUNTY_ID)
    assert getattr(caught.value, "status_code", None) == 401
    assert route.call_count == 2


def test_static_token_is_not_retried() -> None:
    url = f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease"
    with respx.mock:
        route = respx.post(url).mock(return_value=httpx.Response(401, json={"error": "invalid_token"}))
        with pytest.raises(Exception):
            SettleBridgeClient(BASE, "static-token-123456").claim_lease(BOUNTY_ID)
    assert route.call_count == 1


def _lease() -> dict:
    return {
        "lease_id": LEASE_ID,
        "lease_token": "lease-token-SUPERSECRET",
        "bounty_id": BOUNTY_ID,
        "repo": "acme/widgets",
        "issue_number": 42,
        "spec": {"test_command": "pytest -q"},
        "work_expires_at": "t",
        "hard_expires_at": "t",
    }


def test_claim_tool_uses_fetched_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", "static-token-ignored")
    with respx.mock:
        respx.post(DEFAULT_GATEWAY_TOKEN_URL).mock(return_value=_token("tok-fetched-123456"))
        route = respx.post(f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease").mock(
            return_value=httpx.Response(201, json=_lease())
        )
        data = json.loads(claim_github_bounty_lease(BOUNTY_ID))
    assert data["lease_id"] == LEASE_ID
    assert route.calls[0].request.headers["Authorization"] == "Bearer tok-fetched-123456"


def test_claim_tool_reports_token_errors_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_SECRET", SECRET)
    with respx.mock:
        respx.post(DEFAULT_GATEWAY_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_scope"})
        )
        result = claim_github_bounty_lease(BOUNTY_ID)
    assert "MCP_TRUST_FINANCIAL_ALLOWLIST" in result
    assert SECRET not in result


def test_claim_tool_without_credentials_explains_both_options() -> None:
    result = claim_github_bounty_lease(BOUNTY_ID)
    assert "SETTLEBRIDGE_GATEWAY_CLIENT_ID" in result
    assert "SETTLEBRIDGE_GATEWAY_TOKEN" in result


def test_client_secret_and_fetched_token_are_redacted_from_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_SECRET", SECRET)
    with respx.mock:
        respx.post(TOKEN_URL).mock(return_value=_token("tok-fetched-SECRET-123456"))
        _provider().get()
    with caplog.at_level(logging.INFO):
        logging.getLogger("test").info("secret %s token %s", SECRET, "tok-fetched-SECRET-123456")
    assert SECRET not in caplog.text
    assert "tok-fetched-SECRET-123456" not in caplog.text
