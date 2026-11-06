"""Read-only SettleBridge account tools. respx only, no network."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
import respx

from a2a_settlement_mcp.account_tools import get_my_ledger, get_solver_setup_status

BASE = "https://market.settlebridge.ai"
LEDGER = f"{BASE}/api/v1/me/ledger"
LEDGER_CSV = f"{BASE}/api/v1/me/ledger.csv"
SETUP = f"{BASE}/api/v1/github/solver/setup-status"
API_KEY = "ate_account-key-SUPERSECRET"
GATEWAY_SECRET = "ate_gateway-secret-SUPERSECRET"
HOSTED = (
    "Set SETTLEBRIDGE_API_KEY or SETTLEBRIDGE_GATEWAY_CLIENT_SECRET to your own "
    "exchange API key. The hosted MCP server has no key, so run this tool from "
    "your own MCP client."
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SETTLEBRIDGE_URL", raising=False)
    monkeypatch.delenv("SETTLEBRIDGE_API_KEY", raising=False)
    monkeypatch.delenv("SETTLEBRIDGE_GATEWAY_CLIENT_SECRET", raising=False)


def test_tools_are_registered() -> None:
    from a2a_settlement_mcp.server import mcp

    names = [tool.name for tool in mcp._tool_manager._tools.values()]
    assert "get_my_ledger" in names
    assert "get_solver_setup_status" in names


def test_ledger_sends_the_api_key_as_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_API_KEY", API_KEY)
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_SECRET", GATEWAY_SECRET)
    raw = '{"available": 4, "held": 1, "committed": 2, "free_to_fund": 1}\n'
    with respx.mock(assert_all_called=True) as router:
        route = router.get(LEDGER).mock(return_value=httpx.Response(200, content=raw.encode()))
        text = get_my_ledger()
    assert text == raw
    authorization = route.calls[0].request.headers["Authorization"]
    assert authorization == f"Bearer {API_KEY}"
    assert GATEWAY_SECRET not in authorization


def test_gateway_secret_is_used_when_the_api_key_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_SECRET", GATEWAY_SECRET)
    with respx.mock(assert_all_called=True) as router:
        route = router.get(LEDGER).mock(return_value=httpx.Response(200, json={"available": 1}))
        data = json.loads(get_my_ledger())
    assert data == {"available": 1}
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {GATEWAY_SECRET}"


def test_blank_api_key_falls_back_to_the_gateway_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_API_KEY", "   ")
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_CLIENT_SECRET", GATEWAY_SECRET)
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SETUP).mock(return_value=httpx.Response(200, json={"ready": False}))
        data = json.loads(get_solver_setup_status())
    assert data == {"ready": False}
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {GATEWAY_SECRET}"


def test_neither_key_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_API_KEY", API_KEY)
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY_SECRET)
    monkeypatch.setenv("A2A_DASHBOARD_API_KEY", "ate_dashboard-key-SUPERSECRET")
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={}))
        ledger = json.loads(get_my_ledger())
        status = json.loads(get_solver_setup_status())
    assert route.call_count == 0
    assert ledger == {"error": HOSTED}
    assert status == {"error": HOSTED}
    assert API_KEY not in ledger["error"]
    assert GATEWAY_SECRET not in status["error"]


@pytest.mark.parametrize(
    ("caller", "url"),
    [(get_my_ledger, LEDGER), (get_solver_setup_status, SETUP)],
)
def test_http_401_returns_only_the_status_code(
    monkeypatch: pytest.MonkeyPatch, caller: Callable[[], str], url: str
) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_API_KEY", API_KEY)
    with respx.mock() as router:
        router.get(url).mock(return_value=httpx.Response(401, text=f"nope {API_KEY}"))
        text = caller()
    assert json.loads(text) == {"error": "SettleBridge returned HTTP 401"}
    assert API_KEY not in text


@pytest.mark.parametrize(
    ("caller", "url"),
    [(get_my_ledger, LEDGER), (get_solver_setup_status, SETUP)],
)
def test_connection_error_hides_the_key(
    monkeypatch: pytest.MonkeyPatch, caller: Callable[[], str], url: str
) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_API_KEY", API_KEY)
    with respx.mock() as router:
        router.get(url).mock(side_effect=httpx.ConnectError(API_KEY))
        text = caller()
    assert json.loads(text) == {"error": "SettleBridge is not responding"}
    assert API_KEY not in text


def test_csv_true_requests_the_csv_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_API_KEY", API_KEY)
    body = 'bounty,amount,fee\n"acme/widget",5,1\n'
    with respx.mock(assert_all_called=False) as router:
        json_route = router.get(LEDGER).mock(return_value=httpx.Response(500, text=API_KEY))
        csv_route = router.get(LEDGER_CSV).mock(return_value=httpx.Response(200, text=body))
        data = json.loads(get_my_ledger(csv=True))
    assert json_route.call_count == 0
    assert csv_route.call_count == 1
    assert str(csv_route.calls[0].request.url) == LEDGER_CSV
    assert csv_route.calls[0].request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert data == {"csv": body}


def test_setup_status_hits_its_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_API_KEY", API_KEY)
    raw = (
        '{"next_steps":[{"code":"link_github"}],"reputation":0.4,'
        '"financial_scope":true,"eligible_bounties":2,'
        '"mcp_config":{"env":{"SETTLEBRIDGE_API_KEY":"<your exchange API key>"}}}'
    )
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SETUP).mock(return_value=httpx.Response(200, content=raw.encode()))
        text = get_solver_setup_status()
    assert text == raw
    assert str(route.calls[0].request.url) == SETUP
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {API_KEY}"


def test_redirect_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_API_KEY", API_KEY)
    with respx.mock(assert_all_called=False) as router:
        router.get(LEDGER).mock(
            return_value=httpx.Response(
                302, headers={"location": "https://evil.test/collect"}, text=API_KEY
            )
        )
        stolen = router.get("https://evil.test/collect").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        text = get_my_ledger()
    assert stolen.call_count == 0
    assert json.loads(text) == {"error": "SettleBridge returned HTTP 302"}
    assert API_KEY not in text


def test_settlebridge_url_comes_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTLEBRIDGE_URL", "https://ledger.test/base/")
    monkeypatch.setenv("SETTLEBRIDGE_API_KEY", API_KEY)
    url = "https://ledger.test/base/api/v1/me/ledger"
    with respx.mock(assert_all_called=True) as router:
        route = router.get(url).mock(return_value=httpx.Response(200, content=b'{"available":9}'))
        assert get_my_ledger() == '{"available":9}'
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {API_KEY}"
