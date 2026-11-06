"""Hosted per-caller credentials. respx and TestClient only; no network."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from a2a_settlement_mcp import github_bounty_tools as bounty_tools
from a2a_settlement_mcp import hosted
from a2a_settlement_mcp.account_tools import get_my_ledger
from a2a_settlement_mcp.gateway_token import _providers
from a2a_settlement_mcp.github_bounty_tools import (
    claim_github_bounty_lease,
    list_open_github_bounties,
    renew_github_lease,
)
from a2a_settlement_mcp.hosted import (
    FORBIDDEN_ENV,
    MALFORMED_BEARER,
    MISSING_BEARER,
    build_app,
    caller_authorization,
    disable_hosted_mode,
    enable_hosted_mode,
    is_hosted,
    reset_hosted_caches,
)
from a2a_settlement_mcp.owner_session import _sessions, reset_owner_session
from a2a_settlement_mcp.owner_tools import get_github_repo_setup_status
from a2a_settlement_mcp.server import settlement_check_balance, settlement_list_agents

EXCHANGE = "http://exchange.test"
TOKEN_URL = "https://gateway.test/oauth/token"
MARKET = "https://market.test"
KEY_A = "ate_caller-a-SECRET-aaaa"
KEY_B = "ate_caller-b-SECRET-bbbb"
ENV_SECRET = "ate_env-secret-SHOULD-NOT-USE"
ACCT_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ACCT_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
TOKEN_A = "gw-token-caller-a-SECRET"
TOKEN_B = "gw-token-caller-b-SECRET"
BOUNTY_ID = "11111111-1111-4111-8111-111111111111"
LEASE_ID = "33333333-3333-4333-8333-333333333333"
_FORBIDDEN_VALUES = (
    *FORBIDDEN_ENV,
    "SETTLEBRIDGE_GATEWAY_CLIENT_ID",
    "SETTLEBRIDGE_GATEWAY_SCOPE",
    "SETTLEBRIDGE_GATEWAY_TOKEN_URL",
    "SETTLEBRIDGE_URL",
    "SETTLEBRIDGE_HEARTBEAT",
    "A2A_EXCHANGE_URL",
    "A2A_DASHBOARD_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _FORBIDDEN_VALUES:
        monkeypatch.delenv(name, raising=False)
    disable_hosted_mode()
    reset_hosted_caches()
    bounty_tools.reset_local_state()
    reset_owner_session()
    yield
    disable_hosted_mode()
    reset_hosted_caches()
    bounty_tools.reset_local_state()
    reset_owner_session()


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_EXCHANGE_URL", EXCHANGE)
    monkeypatch.setenv("SETTLEBRIDGE_URL", MARKET)
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN_URL", TOKEN_URL)
    monkeypatch.setenv("SETTLEBRIDGE_HEARTBEAT", "off")
    enable_hosted_mode()


def _lease(account_note: str) -> dict:
    return {
        "lease_id": LEASE_ID,
        "lease_token": f"lease-token-{account_note}-SECRET",
        "bounty_id": BOUNTY_ID,
        "repo": "acme/widgets",
        "issue_number": 7,
        "spec": {"test_command": "pytest -q"},
        "work_expires_at": "t",
        "hard_expires_at": "t",
    }


def _balance(request: httpx.Request) -> httpx.Response:
    auth = request.headers["authorization"]
    if auth == f"Bearer {KEY_A}":
        return httpx.Response(200, json={"account_id": ACCT_A, "available": 1})
    if auth == f"Bearer {KEY_B}":
        return httpx.Response(200, json={"account_id": ACCT_B, "available": 2})
    return httpx.Response(401, json={"error": auth})


def _token(request: httpx.Request) -> httpx.Response:
    form = parse_qs(request.content.decode())
    secret = form["client_secret"][0]
    scope = form["scope"][0]
    assert scope == "mcp:read"
    assert "mcp:tool:financial" not in scope
    if secret == KEY_A:
        assert form["client_id"] == [ACCT_A]
        token = TOKEN_A
    elif secret == KEY_B:
        assert form["client_id"] == [ACCT_B]
        token = TOKEN_B
    else:
        return httpx.Response(401, json={"error": "invalid_client"})
    return httpx.Response(
        200,
        json={"access_token": token, "token_type": "Bearer", "expires_in": 3600, "scope": scope},
    )


def test_two_callers_get_their_own_gateway_tokens(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("A2A_API_KEY", ENV_SECRET)
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_ID", ACCT_A)
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_SECRET", ENV_SECRET)
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", ENV_SECRET)
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_SCOPE", "mcp:read mcp:tool:financial")
    claim_url = f"{MARKET}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease"
    with respx.mock:
        balance = respx.get(f"{EXCHANGE}/v1/exchange/balance").mock(side_effect=_balance)
        token = respx.post(TOKEN_URL).mock(side_effect=_token)
        claim = respx.post(claim_url).mock(
            side_effect=[
                httpx.Response(201, json=_lease("a")),
                httpx.Response(201, json=_lease("a")),
                httpx.Response(201, json=_lease("b")),
            ]
        )
        with caplog.at_level(logging.DEBUG):
            with caller_authorization(f"Bearer {KEY_A}"):
                first = json.loads(claim_github_bounty_lease(BOUNTY_ID))
                second = json.loads(claim_github_bounty_lease(BOUNTY_ID))
            with caller_authorization(f"Bearer {KEY_B}"):
                third = json.loads(claim_github_bounty_lease(BOUNTY_ID))
            logging.getLogger("hosted-test").info("keys %s %s tokens %s %s", KEY_A, KEY_B, TOKEN_A, TOKEN_B)
    assert first["lease_id"] == LEASE_ID
    assert second["lease_id"] == LEASE_ID
    assert third["lease_id"] == LEASE_ID
    assert [call.request.headers["authorization"] for call in claim.calls] == [
        f"Bearer {TOKEN_A}",
        f"Bearer {TOKEN_A}",
        f"Bearer {TOKEN_B}",
    ]
    assert balance.call_count == 2
    assert token.call_count == 2
    assert ENV_SECRET not in caplog.text
    assert KEY_A not in caplog.text
    assert KEY_B not in caplog.text
    assert TOKEN_A not in caplog.text
    assert TOKEN_B not in caplog.text
    assert all(KEY_A not in cache_id and KEY_B not in cache_id for cache_id in _providers)
    assert all(KEY_A not in cache_id and KEY_B not in cache_id for cache_id in hosted._account_ids._items)


def test_caller_lease_tokens_are_not_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    claim_url = f"{MARKET}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease"
    renew_url = f"{MARKET}/api/v1/github/leases/{LEASE_ID}/renew"
    with respx.mock:
        respx.get(f"{EXCHANGE}/v1/exchange/balance").mock(side_effect=_balance)
        respx.post(TOKEN_URL).mock(side_effect=_token)
        respx.post(claim_url).mock(return_value=httpx.Response(201, json=_lease("a")))
        renew = respx.post(renew_url).mock(return_value=httpx.Response(200, json={"ok": True}))
        with caller_authorization(f"Bearer {KEY_A}"):
            claimed = json.loads(claim_github_bounty_lease(BOUNTY_ID))
        with caller_authorization(f"Bearer {KEY_B}"):
            denied = json.loads(renew_github_lease(LEASE_ID))
    assert claimed["lease_token"]
    assert "lease_token is required" in denied["error"]
    assert claimed["lease_token"] not in denied["error"]
    assert renew.call_count == 0


def test_missing_header_is_a_credential_error_and_public_tools_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("A2A_API_KEY", ENV_SECRET)
    directory = f"{EXCHANGE}/v1/accounts/directory"
    bounties = f"{MARKET}/api/v1/github/bounties"
    with respx.mock:
        agents = respx.get(directory).mock(return_value=httpx.Response(200, json={"agents": [], "total": 0}))
        listed = respx.get(bounties).mock(return_value=httpx.Response(200, json={"bounties": []}))
        balance = respx.get(f"{EXCHANGE}/v1/exchange/balance").mock(
            return_value=httpx.Response(200, json={"account_id": ACCT_A})
        )
        data = json.loads(settlement_check_balance(ACCT_A))
        agents_data = json.loads(settlement_list_agents())
        bounty_data = json.loads(list_open_github_bounties())
    assert data == {"error": MISSING_BEARER}
    assert ENV_SECRET not in data["error"]
    assert balance.call_count == 0
    assert agents_data["agents"] == []
    assert "authorization" not in {name.lower() for name in agents.calls[0].request.headers}
    assert bounty_data["bounties"] == []
    assert "authorization" not in {name.lower() for name in listed.calls[0].request.headers}


def test_exchange_tool_uses_the_caller_key_not_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("A2A_API_KEY", ENV_SECRET)
    with respx.mock:
        route = respx.get(f"{EXCHANGE}/v1/exchange/balance").mock(
            return_value=httpx.Response(200, json={"account_id": ACCT_A, "available": 4})
        )
        with caller_authorization(f"Bearer {KEY_A}"):
            data = json.loads(settlement_check_balance(ACCT_A))
    assert data["available"] == 4
    assert {call.request.headers["authorization"] for call in route.calls} == {f"Bearer {KEY_A}"}
    assert ENV_SECRET not in json.dumps(data)


@pytest.mark.parametrize(
    "header",
    [
        "Bearer",
        "Bearer ",
        "Basic ate_not-a-bearer-key",
        "Bearer bad key",
        "Bearer key\ninjected",
        "Bearer key\r\ninjected",
        "Bearer " + ("k" * 513),
        "  Bearer ate_padded-key-SECRET",
    ],
)
def test_malformed_authorization_is_rejected(
    monkeypatch: pytest.MonkeyPatch, header: str
) -> None:
    _enable(monkeypatch)
    with respx.mock:
        route = respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={}))
        with caller_authorization(header):
            balance = json.loads(settlement_check_balance(ACCT_A))
            listed = json.loads(settlement_list_agents())
    assert balance["error"] == MALFORMED_BEARER
    assert listed["error"] == MALFORMED_BEARER
    assert route.call_count == 0
    leaked = header.replace("Bearer", "").replace("Basic", "").strip()
    if len(leaked) >= 8:
        assert leaked not in balance["error"]
        assert leaked not in listed["error"]


@pytest.mark.parametrize("name", FORBIDDEN_ENV)
def test_startup_refuses_environment_credentials(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, ENV_SECRET)
    with pytest.raises(RuntimeError, match=name) as caught:
        build_app()
    assert ENV_SECRET not in str(caught.value)
    assert is_hosted() is False


def test_account_id_cache_expires_after_ten_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    now = {"t": 1_000.0}
    monkeypatch.setattr(hosted.time, "monotonic", lambda: now["t"])
    with respx.mock:
        route = respx.get(f"{EXCHANGE}/v1/exchange/balance").mock(side_effect=_balance)
        with caller_authorization(f"Bearer {KEY_A}"):
            assert hosted.account_id_for_key(KEY_A) == ACCT_A
            now["t"] += 599
            assert hosted.account_id_for_key(KEY_A) == ACCT_A
            now["t"] += 2
            assert hosted.account_id_for_key(KEY_A) == ACCT_A
    assert route.call_count == 2


def test_account_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hosted, "CACHE_MAX", 2)
    hosted._account_ids.clear()
    hosted._account_ids.set("a" * 64, "one")
    hosted._account_ids.set("b" * 64, "two")
    hosted._account_ids.set("c" * 64, "three")
    assert len(hosted._account_ids) == 2
    assert hosted._account_ids.get("a" * 64) is None
    assert hosted._account_ids.get("c" * 64) == "three"


def test_ledger_and_owner_tools_use_the_caller_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("SETTLEBRIDGE_API_KEY", ENV_SECRET)
    login = f"{MARKET}/api/auth/exchange-login"
    status = f"{MARKET}/api/v1/github/repos/acme/widget/setup-status"
    ledger = f"{MARKET}/api/v1/me/ledger"
    with respx.mock:
        ledger_route = respx.get(ledger).mock(return_value=httpx.Response(200, json={"available": 1}))
        login_route = respx.post(login).mock(
            side_effect=lambda request: httpx.Response(
                200,
                json={"access_token": "jwt-" + json.loads(request.content)["api_key"][-4:], "token_type": "bearer"},
            )
        )
        respx.get(status).mock(
            return_value=httpx.Response(200, json={"repo": "acme/widget", "ready": True, "next_steps": []})
        )
        with caller_authorization(f"Bearer {KEY_A}"):
            ledger_a = json.loads(get_my_ledger())
            owner_a = json.loads(get_github_repo_setup_status("acme/widget"))
            json.loads(get_github_repo_setup_status("acme/widget"))
        with caller_authorization(f"Bearer {KEY_B}"):
            ledger_b = json.loads(get_my_ledger())
            json.loads(get_github_repo_setup_status("acme/widget"))
    assert ledger_a == {"available": 1}
    assert ledger_b == {"available": 1}
    assert owner_a["repo"] == "acme/widget"
    assert [call.request.headers["authorization"] for call in ledger_route.calls] == [
        f"Bearer {KEY_A}",
        f"Bearer {KEY_B}",
    ]
    posted = [json.loads(call.request.content)["api_key"] for call in login_route.calls]
    assert posted == [KEY_A, KEY_B]
    assert ENV_SECRET not in posted
    assert all(KEY_A not in cache_id and KEY_B not in cache_id for cache_id in _sessions)


def test_balance_error_does_not_echo_the_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _enable(monkeypatch)
    with respx.mock:
        respx.get(f"{EXCHANGE}/v1/exchange/balance").mock(
            return_value=httpx.Response(401, text=KEY_A)
        )
        with caplog.at_level(logging.DEBUG):
            with caller_authorization(f"Bearer {KEY_A}"):
                result = claim_github_bounty_lease(BOUNTY_ID)
    assert KEY_A not in result
    assert KEY_A not in caplog.text
    assert "HTTP 401" in result


def test_hosted_app_routes_mcp_and_sse() -> None:
    application = build_app()
    paths = [getattr(route, "path", "") for route in application.routes]
    assert "/mcp" in paths
    assert "/sse" in paths
    assert any(path.startswith("/messages") for path in paths)
    headers = {
        "Host": "127.0.0.1:3200",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(application, raise_server_exceptions=False) as client:
        mcp_response = client.post("/mcp", headers=headers, content=b"{}")
        messages = client.post("/messages/", headers=headers, content=b"{}")
        missing = client.get("/not-a-real-route", headers={"Host": "127.0.0.1:3200"})
        # GET /sse holds the connection open. A rejected Host is answered
        # immediately and still proves the route is mounted.
        sse = client.get("/sse", headers={"Host": "rejected.example"})
    assert mcp_response.status_code != 404
    assert messages.status_code != 404
    assert missing.status_code == 404
    assert sse.status_code != 404
    assert sse.status_code in {403, 421, 500}


def test_local_mode_still_reports_the_environment_key() -> None:
    assert is_hosted() is False
    balance = json.loads(settlement_check_balance("any"))
    claim = json.loads(claim_github_bounty_lease(BOUNTY_ID))
    assert "A2A_API_KEY" in balance["error"]
    assert MISSING_BEARER not in balance["error"]
    assert "SETTLEBRIDGE_GATEWAY_TOKEN" in claim["error"]
    assert MISSING_BEARER not in claim["error"]


def test_remembered_secrets_are_bounded():
    from a2a_settlement_mcp import settlebridge_client as sc

    for i in range(sc._MAX_KNOWN_SECRETS + 50):
        sc.remember_secret(f"ate_bounded_secret_{i:08d}")
    assert len(sc._known_secrets) <= sc._MAX_KNOWN_SECRETS
    assert "ate_bounded_secret_00000000" not in sc._known_secrets
