"""Repo owner setup tools and SettleBridge sign-in. respx only, no network."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import httpx
import pytest
import respx

from a2a_settlement_mcp.owner_session import (
    SESSION_LIFETIME_S,
    OwnerSessionError,
    OwnerSessionProvider,
    reset_owner_session,
)
from a2a_settlement_mcp.owner_tools import get_github_repo_setup_status, setup_github_repo
from a2a_settlement_mcp.settlebridge_client import parse_repo

BASE = "https://market.settlebridge.ai"
KEY = "ate_owner-key-SUPERSECRET"
LOGIN = f"{BASE}/api/auth/exchange-login"
STATUS = f"{BASE}/api/v1/github/repos/acme/widget/setup-status"
ONBOARD = f"{BASE}/api/v1/github/repos/acme/widget/onboard"
CHECK = {"name": "test", "app_id": 15368}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("SETTLEBRIDGE_URL", raising=False)
    monkeypatch.setenv("SETTLEBRIDGE_API_KEY", KEY)
    reset_owner_session()
    yield
    reset_owner_session()


def _status(steps=(), *, required=(CHECK,), paths=("src/*",)) -> dict:
    return {
        "repo": "acme/widget",
        "ready": not any(step["blocking"] for step in steps),
        "repo_access": True,
        "next_steps": list(steps),
        "detected": {
            "required_checks": list(required),
            "candidate_checks": [CHECK],
            "allowed_paths": list(paths),
        },
    }


def _step(code: str, blocking: bool = True, **extra) -> dict:
    return {"code": code, "message": code.replace("_", " "), "blocking": blocking, **extra}


def _login(router: respx.MockRouter, token: str = "jwt-session-token-1") -> respx.Route:
    return router.post(LOGIN).mock(
        return_value=httpx.Response(200, json={"access_token": token, "token_type": "bearer"})
    )


def _onboarded(changed: bool = True) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "changed": changed,
            "funder_headroom_ate": 7389,
            "after": {"enabled": True},
            "labels": None,
            "dry_run": True,
        },
    )


# parse_repo


@pytest.mark.parametrize(
    "value,expected",
    [
        ("acme/widget", "acme/widget"),
        (" acme/widget ", "acme/widget"),
        ("https://github.com/acme/widget", "acme/widget"),
        ("https://github.com/acme/widget.git", "acme/widget"),
        ("a.b/c_d-e", "a.b/c_d-e"),
    ],
)
def test_parse_repo_accepts(value: str, expected: str) -> None:
    assert parse_repo(value) == expected


@pytest.mark.parametrize(
    "value", ["acme", "acme/widget/extra", "../x", "acme/..", "acme/wid get", "acme/w?x=1", ""]
)
def test_parse_repo_rejects(value: str) -> None:
    with pytest.raises(ValueError):
        parse_repo(value)


# Session


def test_session_signs_in_once_and_reuses_the_token() -> None:
    clock = [0.0]
    provider = OwnerSessionProvider(BASE, KEY, clock=lambda: clock[0])
    with respx.mock(assert_all_called=True) as router:
        route = _login(router)
        assert provider.get() == "jwt-session-token-1"
        assert provider.get() == "jwt-session-token-1"
        assert route.call_count == 1
        assert json.loads(route.calls[0].request.content) == {"api_key": KEY}
        clock[0] = SESSION_LIFETIME_S + 1
        provider.get()
        assert route.call_count == 2


def test_session_rejected_key_is_a_safe_error() -> None:
    provider = OwnerSessionProvider(BASE, KEY)
    with respx.mock() as router:
        router.post(LOGIN).mock(return_value=httpx.Response(401, json={"detail": KEY}))
        with pytest.raises(OwnerSessionError) as excinfo:
            provider.get()
    assert KEY not in str(excinfo.value)
    assert "SETTLEBRIDGE_API_KEY" in str(excinfo.value)


def test_expired_session_signs_in_again_after_401() -> None:
    with respx.mock() as router:
        login = router.post(LOGIN).mock(
            side_effect=[
                httpx.Response(200, json={"access_token": "jwt-old-session"}),
                httpx.Response(200, json={"access_token": "jwt-new-session"}),
            ]
        )
        status = router.get(STATUS).mock(
            side_effect=[httpx.Response(401, json={"detail": "expired"}), httpx.Response(200, json=_status())]
        )
        data = json.loads(get_github_repo_setup_status("acme/widget"))
    assert data["ready"] is True
    assert login.call_count == 2
    assert status.calls[1].request.headers["Authorization"] == "Bearer jwt-new-session"


def test_missing_api_key_explains_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SETTLEBRIDGE_API_KEY")
    data = json.loads(setup_github_repo("acme/widget"))
    assert "SETTLEBRIDGE_API_KEY is required" in data["error"]


# Tools


def test_status_tool_sends_the_session_token() -> None:
    with respx.mock(assert_all_called=True) as router:
        _login(router)
        route = router.get(STATUS).mock(return_value=httpx.Response(200, json=_status()))
        data = json.loads(get_github_repo_setup_status("https://github.com/acme/widget"))
    assert data["repo"] == "acme/widget"
    assert route.calls[0].request.headers["Authorization"] == "Bearer jwt-session-token-1"


def test_setup_returns_human_steps_without_onboarding() -> None:
    steps = [_step("repo_access", url="https://github.com/apps/x/installations/new"), _step("funder_low_balance")]
    with respx.mock(assert_all_called=False) as router:
        _login(router)
        router.get(STATUS).mock(return_value=httpx.Response(200, json=_status(steps)))
        onboard = router.post(ONBOARD)
        data = json.loads(setup_github_repo("acme/widget", apply=True))
    assert data["ready"] is False
    assert [step["code"] for step in data["next_steps"]] == ["repo_access", "funder_low_balance"]
    assert data["next_steps"][0]["url"].startswith("https://github.com/apps/")
    assert "browser" in data["guidance"]
    assert onboard.call_count == 0


def test_setup_asks_for_a_check_when_none_is_pinned() -> None:
    steps = [_step("no_pinned_check", candidates=[CHECK])]
    with respx.mock() as router:
        _login(router)
        router.get(STATUS).mock(
            return_value=httpx.Response(200, json=_status(steps, required=()))
        )
        data = json.loads(setup_github_repo("acme/widget"))
    assert data["ready"] is False
    assert data["next_steps"][0]["candidates"] == [CHECK]
    assert "check and allowed_paths" in data["guidance"]


def test_setup_dry_run_by_default_with_detected_values() -> None:
    with respx.mock() as router:
        _login(router)
        router.get(STATUS).mock(return_value=httpx.Response(200, json=_status()))
        onboard = router.post(ONBOARD).mock(return_value=_onboarded())
        data = json.loads(setup_github_repo("acme/widget"))
    body = json.loads(onboard.calls[0].request.content)
    assert body == {"checks": [CHECK], "dry_run": True}
    assert data["applied"] is False
    assert data["allowed_paths"] == ["src/*"]
    assert "apply=true" in data["guidance"]


def test_setup_apply_with_chosen_check_and_paths() -> None:
    steps = [_step("no_pinned_check", candidates=[CHECK])]
    with respx.mock() as router:
        _login(router)
        router.get(STATUS).mock(
            return_value=httpx.Response(200, json=_status(steps, required=()))
        )
        onboard = router.post(ONBOARD).mock(return_value=_onboarded())
        data = json.loads(
            setup_github_repo(
                "acme/widget", check="test:15368", allowed_paths=["pkg/*", " "], apply=True
            )
        )
    body = json.loads(onboard.calls[0].request.content)
    assert body == {"checks": [CHECK], "allowed_paths": ["pkg/*"], "dry_run": False}
    assert data["applied"] is True and "Bounties are on" in data["guidance"]


def test_setup_ignores_non_blocking_steps() -> None:
    with respx.mock() as router:
        _login(router)
        router.get(STATUS).mock(
            return_value=httpx.Response(200, json=_status([_step("already_enabled", blocking=False)]))
        )
        router.post(ONBOARD).mock(return_value=_onboarded(changed=False))
        data = json.loads(setup_github_repo("acme/widget", apply=True))
    assert data["ready"] is True and "nothing changed" in data["guidance"]


@pytest.mark.parametrize("check", ["test", "test:abc", ":15368"])
def test_setup_rejects_a_malformed_check(check: str) -> None:
    data = json.loads(setup_github_repo("acme/widget", check=check))
    assert "NAME:APP_ID" in data["error"]


def test_coded_errors_pass_through_allowlisted_details() -> None:
    detail = {
        "code": "funder_low_balance",
        "message": "funder has 50 ATE free",
        "headroom_ate": 50,
        "minimum_ate": 101,
        "secret": "should not pass",
    }
    with respx.mock() as router:
        _login(router)
        router.get(STATUS).mock(return_value=httpx.Response(200, json=_status()))
        router.post(ONBOARD).mock(return_value=httpx.Response(422, json={"detail": detail}))
        data = json.loads(setup_github_repo("acme/widget"))
    assert data["error"] == "funder_low_balance" and data["status"] == 422
    assert data["details"] == {"headroom_ate": 50, "minimum_ate": 101}


def test_self_serve_off_is_reported() -> None:
    with respx.mock() as router:
        _login(router)
        router.get(STATUS).mock(return_value=httpx.Response(404, json={"detail": "Not Found"}))
        data = json.loads(get_github_repo_setup_status("acme/widget"))
    assert "not enabled" in data["error"]


def test_key_and_session_are_redacted_from_logs_and_results(caplog) -> None:
    with respx.mock() as router:
        _login(router, token="jwt-session-token-SECRET")
        router.get(STATUS).mock(
            return_value=httpx.Response(
                403, json={"detail": {"code": "repo_access", "message": f"echo {KEY} jwt-session-token-SECRET"}}
            )
        )
        with caplog.at_level(logging.INFO):
            logging.getLogger("test").info("leak %s", KEY)
            text = get_github_repo_setup_status("acme/widget")
    assert KEY not in text and "jwt-session-token-SECRET" not in text
    assert KEY not in caplog.text
