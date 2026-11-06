"""Tests for SettleBridge GitHub bounty MCP tools. No live network."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator

import httpx
import pytest
import respx

from a2a_settlement_mcp import github_bounty_tools as bounty_tools
from a2a_settlement_mcp.github_bounty_tools import (
    claim_github_bounty_lease,
    get_github_bounty_status,
    list_open_github_bounties,
    release_github_lease,
    renew_github_lease,
    reset_local_state,
    set_worker_availability,
    start_heartbeat,
    stop_heartbeat,
    submit_github_patch,
)
from a2a_settlement_mcp.server import mcp

BASE = "https://market.settlebridge.ai"
GATEWAY = "gateway-token-SUPERSECRET"
LEASE = "lease-token-SUPERSECRET"
LEASE_OTHER = "lease-token-OTHERSECRET"
BOUNTY_ID = "11111111-1111-4111-8111-111111111111"
OTHER_BOUNTY = "22222222-2222-4222-8222-222222222222"
LEASE_ID = "33333333-3333-4333-8333-333333333333"
OTHER_LEASE = "44444444-4444-4444-8444-444444444444"
PR_URL = "https://github.com/acme/widgets/pull/7"

SPEC = {
    "spec_id": "spec-1",
    "version": "1",
    "content_hash": "abc123",
    "test_command": "pytest -q",
    "required_checks": ["test"],
    "policy": {"no_binary": True},
    "payment_trigger": "merge_and_checks",
    "required_kya": "kya",
    "required_ema": "ema",
}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep leases, worker settings, and the heartbeat thread inside each test."""
    monkeypatch.delenv("SETTLEBRIDGE_URL", raising=False)
    monkeypatch.delenv("SETTLEBRIDGE_GATEWAY_TOKEN", raising=False)
    for name in (
        "SETTLEBRIDGE_GATEWAY_CLIENT_ID",
        "SETTLEBRIDGE_GATEWAY_CLIENT_SECRET",
        "SETTLEBRIDGE_GATEWAY_TOKEN_URL",
        "SETTLEBRIDGE_GATEWAY_SCOPE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("SETTLEBRIDGE_HEARTBEAT", raising=False)
    monkeypatch.delenv("SETTLEBRIDGE_MIN_BOUNTY_USD", raising=False)
    monkeypatch.delenv("SETTLEBRIDGE_LANGUAGES", raising=False)
    monkeypatch.delenv("SETTLEBRIDGE_MAX_TOKENS", raising=False)
    reset_local_state()
    yield
    reset_local_state()


def _load(result: str) -> dict:
    return json.loads(result)


def _bounty(bounty_id: str, amount: float, title: str = "Fix the thing") -> dict:
    return {
        "bounty_id": bounty_id,
        "repo": "acme/widgets",
        "issue_number": 42,
        "issue_url": "https://github.com/acme/widgets/issues/42",
        "title": title,
        "amount_ate": int(amount * 100),
        "amount_usd": amount,
        "funding_status": "balance_checked_not_reserved",
        "spec": dict(SPEC),
    }


def _lease(bounty_id: str, lease_id: str, token: str) -> dict:
    return {
        "lease_id": lease_id,
        "lease_token": token,
        "bounty_id": bounty_id,
        "repo": "acme/widgets",
        "issue_number": 42,
        "issue_url": "https://github.com/acme/widgets/issues/42",
        "spec": dict(SPEC),
        "test_command": "pytest -q",
        "work_expires_at": "2026-11-05T18:00:00Z",
        "hard_expires_at": "2026-11-05T20:00:00Z",
    }


def _heartbeat_body() -> dict:
    return {
        "status": "AVAILABLE_IDLE",
        "ttl_s": 60,
        "released_leases": [],
        "extended_leases": [],
        "presence": {},
    }


def test_tool_descriptions_do_not_promise_payment() -> None:
    """Tool descriptions state the funding and payment rules."""
    listing = list_open_github_bounties.__doc__ or ""
    claim = claim_github_bounty_lease.__doc__ or ""
    submit = submit_github_patch.__doc__ or ""
    assert "balance checked; funds not reserved" in listing
    assert "not yet supported" in listing.lower()
    assert "Fixes #<issue_number>" in claim
    assert "git_diff" in submit
    assert "Submission is not payment" in submit
    assert "fetches the diff from GitHub" in submit


def test_github_tools_are_registered() -> None:
    """Importing the server registers the bounty tools on the shared FastMCP instance."""
    names = {tool.name for tool in mcp._tool_manager._tools.values()}
    assert {
        "list_open_github_bounties",
        "claim_github_bounty_lease",
        "renew_github_lease",
        "release_github_lease",
        "submit_github_patch",
        "get_github_bounty_status",
        "set_worker_availability",
    } <= names


def test_list_filters_min_bounty_and_explains_funding(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Listing drops bounties under the configured minimum and states funds are not reserved."""
    monkeypatch.setenv("SETTLEBRIDGE_MIN_BOUNTY_USD", "10")
    listed = [
        _bounty(BOUNTY_ID, 9.5, "too small"),
        _bounty(OTHER_BOUNTY, 10, "exact"),
        _bounty("55555555-5555-4555-8555-555555555555", 25, "large"),
        {"bounty_id": "66666666-6666-4666-8666-666666666666", "title": "missing amount"},
    ]
    with respx.mock:
        route = respx.get(f"{BASE}/api/v1/github/bounties").mock(
            return_value=httpx.Response(200, json={"bounties": listed})
        )
        data = _load(list_open_github_bounties(language="python", max_difficulty="hard", limit=20))
        request = route.calls[0].request
        assert request.url.params["language"] == "python"
        assert request.url.params["limit"] == "20"
        assert "max_difficulty" not in request.url.params
        assert "authorization" not in {key.lower() for key in request.headers}
    assert [item["title"] for item in data["bounties"]] == ["exact", "large"]
    assert data["bounties"][0]["funding_status"] == "balance_checked_not_reserved"
    assert "balance checked; funds not reserved" in data["note"]
    assert "merge" in data["note"]
    assert "required checks" in data["note"]
    assert "no open policy violations" in data["note"]
    assert "not yet supported" in data["note"]
    assert capsys.readouterr().out == ""


def test_claim_stores_token_and_returns_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claim keeps the lease token in process and returns it with the frozen spec."""
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY)
    with respx.mock:
        route = respx.post(f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease").mock(
            return_value=httpx.Response(201, json=_lease(BOUNTY_ID, LEASE_ID, LEASE))
        )
        data = _load(claim_github_bounty_lease(BOUNTY_ID))
        assert route.calls[0].request.headers["Authorization"] == f"Bearer {GATEWAY}"
        assert "x-lease-token" not in {key.lower() for key in route.calls[0].request.headers}
    assert data["lease_token"] == LEASE
    assert data["lease_id"] == LEASE_ID
    assert data["spec"] == SPEC
    assert data["test_command"] == "pytest -q"
    assert "Fixes #42" in data["guidance"]
    assert "acme/widgets" in data["guidance"]
    assert "submit_github_patch" in data["guidance"]
    assert "Submission is not payment" in data["guidance"]
    assert "balance checked; funds not reserved" in data["note"]
    assert bounty_tools._leases[LEASE_ID]["lease_token"] == LEASE


def test_claim_trust_insufficient_is_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 403 trust denial is returned with the server body unchanged."""
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY)
    body = {
        "error": "trust_insufficient",
        "evaluations": [{"check": "kya", "ok": False}],
        "upgrade_path": "https://market.settlebridge.ai/trust",
    }
    with respx.mock:
        respx.post(f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease").mock(
            return_value=httpx.Response(403, json=body)
        )
        data = _load(claim_github_bounty_lease(BOUNTY_ID))
    assert data == {"error": "trust_insufficient", "status": 403, "details": body}
    assert LEASE_ID not in bounty_tools._leases


def test_renew_and_release_use_stored_or_explicit_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renew and release take the stored lease token, or an explicit one after restart."""
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY)
    with respx.mock:
        respx.post(f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease").mock(
            return_value=httpx.Response(201, json=_lease(BOUNTY_ID, LEASE_ID, LEASE))
        )
        renew_route = respx.post(f"{BASE}/api/v1/github/leases/{LEASE_ID}/renew").mock(
            return_value=httpx.Response(
                200,
                json={"lease_id": LEASE_ID, "work_expires_at": "later", "hard_expires_at": "end"},
            )
        )
        release_route = respx.post(f"{BASE}/api/v1/github/leases/{LEASE_ID}/release").mock(
            return_value=httpx.Response(200, json={"lease_id": LEASE_ID, "status": "released"})
        )
        claim_github_bounty_lease(BOUNTY_ID)
        renewed = _load(renew_github_lease(LEASE_ID))
        assert renew_route.calls[0].request.headers["X-Lease-Token"] == LEASE
        renewed_again = _load(renew_github_lease(LEASE_ID, lease_token=LEASE_OTHER))
        assert renew_route.calls[1].request.headers["X-Lease-Token"] == LEASE_OTHER
        released = _load(release_github_lease(LEASE_ID))
        assert release_route.calls[0].request.headers["X-Lease-Token"] == LEASE
    assert renewed["work_expires_at"] == "later"
    assert renewed_again["lease_id"] == LEASE_ID
    assert released["status"] == "released"
    assert LEASE_ID not in bounty_tools._leases

    with respx.mock:
        explicit = respx.post(f"{BASE}/api/v1/github/leases/{OTHER_LEASE}/release").mock(
            return_value=httpx.Response(200, json={"lease_id": OTHER_LEASE, "status": "released"})
        )
        data = _load(release_github_lease(OTHER_LEASE, lease_token=LEASE_OTHER))
        assert explicit.calls[0].request.headers["X-Lease-Token"] == LEASE_OTHER
        assert explicit.calls[0].request.headers["Authorization"] == f"Bearer {GATEWAY}"
    assert data["status"] == "released"


def test_submit_uses_the_stored_lease_for_the_bounty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Submit resolves the single stored lease and does not send a diff."""
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY)
    with respx.mock:
        respx.post(f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease").mock(
            return_value=httpx.Response(201, json=_lease(BOUNTY_ID, LEASE_ID, LEASE))
        )
        route = respx.post(f"{BASE}/api/v1/github/leases/{LEASE_ID}/submit").mock(
            return_value=httpx.Response(
                200,
                json={
                    "lease_id": LEASE_ID,
                    "status": "submitted",
                    "pr_number": 7,
                    "hold_expires_at": "tomorrow",
                },
            )
        )
        claim_github_bounty_lease(BOUNTY_ID)
        data = _load(submit_github_patch(BOUNTY_ID, PR_URL, otel_trace_id="trace-9"))
        request = route.calls[0].request
        body = json.loads(request.content)
        assert body == {"pr_url": PR_URL, "otel_trace_id": "trace-9"}
        assert "git_diff" not in body
        assert request.headers["X-Lease-Token"] == LEASE
    assert data["status"] == "submitted"
    assert "Submission is not payment" in data["note"]
    assert "required checks" in data["note"]


def test_submit_reports_ambiguous_and_missing_leases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Submit asks for a lease id when none or more than one lease is stored."""
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY)
    with respx.mock:
        missing = _load(submit_github_patch(BOUNTY_ID, PR_URL))
    assert "No stored lease" in missing["error"]

    with respx.mock:
        respx.post(f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease").mock(
            side_effect=iter(
                [
                    httpx.Response(201, json=_lease(BOUNTY_ID, LEASE_ID, LEASE)),
                    httpx.Response(201, json=_lease(BOUNTY_ID, OTHER_LEASE, LEASE_OTHER)),
                ]
            )
        )
        claim_github_bounty_lease(BOUNTY_ID)
        claim_github_bounty_lease(BOUNTY_ID)
        ambiguous = _load(submit_github_patch(BOUNTY_ID, PR_URL))
    assert "Ambiguous" in ambiguous["error"]


def test_submit_author_mismatch_is_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 422 from SettleBridge is returned with the server body unchanged."""
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY)
    body = {"error": "pr_author_mismatch", "expected": "octocat", "actual": "bot"}
    with respx.mock:
        respx.post(f"{BASE}/api/v1/github/leases/{LEASE_ID}/submit").mock(
            return_value=httpx.Response(422, json=body)
        )
        data = _load(
            submit_github_patch(
                BOUNTY_ID,
                PR_URL,
                lease_id=LEASE_ID,
                lease_token=LEASE,
            )
        )
    assert data == {
        "error": "pr_author_mismatch",
        "status": 422,
        "details": {"error": "pr_author_mismatch"},
    }


def test_error_body_echoing_tokens_is_not_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlisted fields are dropped and remembered credentials are scrubbed."""
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY)
    body = {
        "error": f"bad token {LEASE}",
        "headers": {"Authorization": f"Bearer {GATEWAY}"},
        "upgrade_path": f"see {GATEWAY}",
    }
    with respx.mock:
        respx.post(f"{BASE}/api/v1/github/leases/{LEASE_ID}/submit").mock(
            return_value=httpx.Response(422, json=body)
        )
        raw = submit_github_patch(BOUNTY_ID, PR_URL, lease_id=LEASE_ID, lease_token=LEASE)
    assert GATEWAY not in raw
    assert LEASE not in raw
    assert "headers" not in _load(raw)["details"]


def test_lease_tools_require_gateway_token() -> None:
    """Lease, submit, and heartbeat tools explain that the gateway token is required."""
    with respx.mock:
        calls = [
            claim_github_bounty_lease(BOUNTY_ID),
            renew_github_lease(LEASE_ID, lease_token=LEASE),
            release_github_lease(LEASE_ID, lease_token=LEASE),
            submit_github_patch(BOUNTY_ID, PR_URL, lease_id=LEASE_ID, lease_token=LEASE),
            set_worker_availability("idle"),
        ]
        for result in calls:
            data = _load(result)
            assert "SETTLEBRIDGE_GATEWAY_TOKEN" in data["error"]


def test_status_is_public_without_a_token() -> None:
    """Status does not require the gateway token."""
    payload = {
        "bounty_id": BOUNTY_ID,
        "state": "submitted",
        "pr_number": 7,
        "evaluated_head_sha": None,
        "assessment": None,
        "settlement": None,
        "lease": None,
    }
    with respx.mock:
        respx.get(f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/status").mock(
            return_value=httpx.Response(200, json=payload)
        )
        assert _load(get_github_bounty_status(BOUNTY_ID)) == payload


@pytest.mark.parametrize(
    ("caller", "args"),
    [
        (claim_github_bounty_lease, ("not-a-uuid",)),
        (claim_github_bounty_lease, ("../etc/passwd",)),
        (renew_github_lease, ("not-a-uuid",)),
        (release_github_lease, ("bad-lease",)),
        (get_github_bounty_status, ("not-a-uuid",)),
        (submit_github_patch, ("not-a-uuid", PR_URL)),
        (submit_github_patch, (BOUNTY_ID, PR_URL, "not-a-lease")),
    ],
)
def test_invalid_uuids_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    caller: object,
    args: tuple,
) -> None:
    """Bounty and lease ids that are not UUIDs never reach SettleBridge."""
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY)
    with respx.mock:
        data = _load(caller(*args))  # type: ignore[operator]
    assert "UUID" in data["error"]


def test_set_worker_availability_maps_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """idle and busy map onto the heartbeat status values and are sent immediately."""
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY)
    monkeypatch.setenv("SETTLEBRIDGE_LANGUAGES", "python, rust")
    monkeypatch.setenv("SETTLEBRIDGE_MIN_BOUNTY_USD", "4")

    def respond(request: httpx.Request) -> httpx.Response:
        incoming = json.loads(request.content)
        body = _heartbeat_body()
        body["status"] = incoming["status"]
        return httpx.Response(200, json=body)

    with respx.mock:
        route = respx.post(f"{BASE}/api/v1/workers/heartbeat").mock(side_effect=respond)
        idle = _load(set_worker_availability("Idle"))
        busy = _load(set_worker_availability("BUSY", min_bounty_usd=15, max_tokens=1000))
        first = json.loads(route.calls[0].request.content)
        second = json.loads(route.calls[1].request.content)
        assert route.calls[0].request.headers["Authorization"] == f"Bearer {GATEWAY}"
    assert idle["status"] == "AVAILABLE_IDLE"
    assert busy["status"] == "BUSY_PREEMPTED"
    assert first["status"] == "AVAILABLE_IDLE"
    assert first["capabilities"]["languages"] == ["python", "rust"]
    assert first["min_bounty_usd"] == 4
    assert "max_tokens" not in first
    assert second["status"] == "BUSY_PREEMPTED"
    assert second["min_bounty_usd"] == 15
    assert second["max_tokens"] == 1000


def test_set_worker_availability_rejects_unknown_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown mode is rejected without calling SettleBridge."""
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY)
    with respx.mock:
        data = _load(set_worker_availability("asleep"))
    assert "idle" in data["error"]
    assert "busy" in data["error"]


def test_heartbeat_thread_sends_and_stops(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The background thread heartbeats with the saved availability, then stops."""
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY)
    monkeypatch.setenv("SETTLEBRIDGE_LANGUAGES", "go")
    monkeypatch.setenv("SETTLEBRIDGE_HEARTBEAT", "off")
    with respx.mock:
        route = respx.post(f"{BASE}/api/v1/workers/heartbeat").mock(
            return_value=httpx.Response(200, json=_heartbeat_body())
        )
        set_worker_availability("busy", min_bounty_usd=12.5, max_tokens=90)
        assert json.loads(route.calls[0].request.content)["status"] == "BUSY_PREEMPTED"
        monkeypatch.setenv("SETTLEBRIDGE_HEARTBEAT", "on")
        start_heartbeat(interval_s=0.05)
        deadline = time.time() + 3
        while route.call_count < 2 and time.time() < deadline:
            time.sleep(0.02)
        assert route.call_count >= 2
        later = json.loads(route.calls[-1].request.content)
        assert later["status"] == "BUSY_PREEMPTED"
        assert later["min_bounty_usd"] == 12.5
        assert later["max_tokens"] == 90
        assert later["capabilities"]["languages"] == ["go"]
        stop_heartbeat()
        settled = route.call_count
        time.sleep(0.3)
        assert route.call_count == settled
    assert capsys.readouterr().out == ""


def test_heartbeat_stays_off_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Heartbeat does not start when the gateway token is missing."""
    monkeypatch.setenv("SETTLEBRIDGE_HEARTBEAT", "on")
    start_heartbeat(interval_s=0.05)
    assert bounty_tools._thread is None


def test_tokens_never_appear_in_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Claim and a failed heartbeat do not write either token to the logs."""
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", GATEWAY)
    monkeypatch.setenv("SETTLEBRIDGE_HEARTBEAT", "on")
    with respx.mock:
        respx.post(f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease").mock(
            return_value=httpx.Response(201, json=_lease(BOUNTY_ID, LEASE_ID, LEASE))
        )
        respx.post(f"{BASE}/api/v1/workers/heartbeat").mock(
            return_value=httpx.Response(
                500,
                json={"error": "boom", "echo_gateway": GATEWAY, "echo_lease": LEASE},
            )
        )
        with caplog.at_level(logging.DEBUG):
            claim_github_bounty_lease(BOUNTY_ID)
            start_heartbeat(interval_s=0.05)
            deadline = time.time() + 3
            while not any("heartbeat failed" in record.message for record in caplog.records):
                if time.time() > deadline:
                    break
                time.sleep(0.02)
            stop_heartbeat()
    text = caplog.text
    assert GATEWAY not in text
    assert LEASE not in text
    assert any("heartbeat failed" in record.message for record in caplog.records)
