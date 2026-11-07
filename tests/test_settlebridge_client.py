"""Tests for the SettleBridge HTTP client and bounty config."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx

from a2a_settlement_mcp.config import (
    get_settlebridge_gateway_token,
    get_settlebridge_heartbeat,
    get_settlebridge_languages,
    get_settlebridge_max_tokens,
    get_settlebridge_min_bounty_usd,
    get_settlebridge_url,
)
from a2a_settlement_mcp.settlebridge_client import (
    SettleBridgeClient,
    SettleBridgeError,
    parse_uuid,
)

BASE = "https://market.settlebridge.ai"
ONBOARD = f"{BASE}/api/v1/github/repos/acme/widget/onboard"
_CHECKS = [{"name": "test", "app_id": 15368}]
_ONBOARD_FLAGS = ("auto_dispatch_alerts", "auto_merge_acceptable", "strict_tests")
GATEWAY = "gateway-token-SUPERSECRET"
LEASE = "lease-token-SUPERSECRET"
BOUNTY_ID = "11111111-1111-4111-8111-111111111111"
LEASE_ID = "33333333-3333-4333-8333-333333333333"


def _client(base_url: str = BASE, token: str = GATEWAY) -> SettleBridgeClient:
    return SettleBridgeClient(base_url, token)


def test_settlebridge_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config defaults to the hosted market and strips a trailing slash."""
    monkeypatch.delenv("SETTLEBRIDGE_URL", raising=False)
    assert get_settlebridge_url() == "https://market.settlebridge.ai"
    monkeypatch.setenv("SETTLEBRIDGE_URL", "https://example.test/market/")
    assert get_settlebridge_url() == "https://example.test/market"


def test_gateway_token_and_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token is stripped. Heartbeat is on only for the value on."""
    monkeypatch.delenv("SETTLEBRIDGE_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("SETTLEBRIDGE_HEARTBEAT", raising=False)
    assert get_settlebridge_gateway_token() == ""
    assert get_settlebridge_heartbeat() is False
    monkeypatch.setenv("SETTLEBRIDGE_GATEWAY_TOKEN", "  gw-token  ")
    monkeypatch.setenv("SETTLEBRIDGE_HEARTBEAT", "ON")
    assert get_settlebridge_gateway_token() == "gw-token"
    assert get_settlebridge_heartbeat() is True
    monkeypatch.setenv("SETTLEBRIDGE_HEARTBEAT", "off")
    assert get_settlebridge_heartbeat() is False


def test_min_bounty_languages_and_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Numeric and list settings fall back when unset or invalid."""
    monkeypatch.delenv("SETTLEBRIDGE_MIN_BOUNTY_USD", raising=False)
    monkeypatch.delenv("SETTLEBRIDGE_LANGUAGES", raising=False)
    monkeypatch.delenv("SETTLEBRIDGE_MAX_TOKENS", raising=False)
    assert get_settlebridge_min_bounty_usd() == 0
    assert get_settlebridge_languages() == []
    assert get_settlebridge_max_tokens() is None
    monkeypatch.setenv("SETTLEBRIDGE_MIN_BOUNTY_USD", "12.5")
    monkeypatch.setenv("SETTLEBRIDGE_LANGUAGES", "Python, Go,")
    monkeypatch.setenv("SETTLEBRIDGE_MAX_TOKENS", "8000")
    assert get_settlebridge_min_bounty_usd() == 12.5
    assert get_settlebridge_languages() == ["Python", "Go"]
    assert get_settlebridge_max_tokens() == 8000
    monkeypatch.setenv("SETTLEBRIDGE_MIN_BOUNTY_USD", "nope")
    monkeypatch.setenv("SETTLEBRIDGE_MAX_TOKENS", "x")
    assert get_settlebridge_min_bounty_usd() == 0
    assert get_settlebridge_max_tokens() is None


def test_parse_uuid_rejects_non_uuids() -> None:
    """Ids that are not UUIDs are rejected before they can reach a URL."""
    with pytest.raises(ValueError, match="UUID"):
        parse_uuid("not-a-uuid", field="bounty_id")
    with pytest.raises(ValueError, match="UUID"):
        parse_uuid("../etc/passwd", field="lease_id")
    with pytest.raises(ValueError, match="UUID"):
        parse_uuid("", field="bounty_id")


def test_invalid_uuid_does_not_call_network() -> None:
    """Client validation happens before the HTTP client is used."""
    client = _client()
    with respx.mock(assert_all_called=False) as router:
        with pytest.raises(ValueError, match="UUID"):
            client.claim_lease("not-a-uuid")
        with pytest.raises(ValueError, match="UUID"):
            client.get_bounty_status("bounties/../admin")
        assert router.calls.call_count == 0


def test_list_bounties_query_and_trailing_slash() -> None:
    """Listing sends language and limit, and a trailing slash is not doubled."""
    with respx.mock:
        route = respx.get(f"{BASE}/api/v1/github/bounties").mock(
            return_value=httpx.Response(200, json={"bounties": []})
        )
        result = _client(BASE + "/").list_bounties(language="python", limit=5)
        request = route.calls[0].request
        assert request.url.params["language"] == "python"
        assert request.url.params["limit"] == "5"
        assert "max_difficulty" not in request.url.params
        assert str(request.url).startswith(f"{BASE}/api/v1/github/bounties?")
    assert result == {"bounties": []}


def test_lease_requests_send_bearer_and_lease_headers() -> None:
    """Lease operations send the gateway token and the lease token as headers."""
    renewed = {"lease_id": LEASE_ID, "work_expires_at": "t", "hard_expires_at": "t"}
    with respx.mock:
        route = respx.post(f"{BASE}/api/v1/github/leases/{LEASE_ID}/renew").mock(
            return_value=httpx.Response(200, json=renewed)
        )
        result = _client().renew_lease(LEASE_ID, LEASE)
        request = route.calls[0].request
        assert request.headers["Authorization"] == f"Bearer {GATEWAY}"
        assert request.headers["X-Lease-Token"] == LEASE
        assert GATEWAY not in str(request.url)
        assert LEASE not in str(request.url)
    assert result == renewed


def test_uppercase_uuid_is_canonical_in_the_path() -> None:
    """A valid UUID is normalized before it is placed in the path."""
    raw = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"
    canonical = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    with respx.mock:
        route = respx.post(url__regex=r".*/claim-lease$").mock(
            return_value=httpx.Response(201, json={"lease_id": LEASE_ID, "lease_token": LEASE})
        )
        _client().claim_lease(raw)
        assert route.calls[0].request.url.path.endswith(f"/{canonical}/claim-lease")


def test_submit_body_and_status_path() -> None:
    """Submit sends the pull request URL and trace id. Status is a public GET."""
    with respx.mock:
        submit_route = respx.post(f"{BASE}/api/v1/github/leases/{LEASE_ID}/submit").mock(
            return_value=httpx.Response(
                200,
                json={
                    "lease_id": LEASE_ID,
                    "status": "submitted",
                    "pr_number": 7,
                    "hold_expires_at": "t",
                },
            )
        )
        status_route = respx.get(f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/status").mock(
            return_value=httpx.Response(200, json={"bounty_id": BOUNTY_ID, "state": "open"})
        )
        submitted = _client().submit_patch(
            LEASE_ID,
            LEASE,
            "https://github.com/acme/widgets/pull/7",
            "trace-1",
        )
        status = SettleBridgeClient(BASE).get_bounty_status(BOUNTY_ID)
        body = json.loads(submit_route.calls[0].request.content)
        assert body["pr_url"] == "https://github.com/acme/widgets/pull/7"
        assert body["otel_trace_id"] == "trace-1"
        status_headers = {key.lower() for key in status_route.calls[0].request.headers}
    assert submitted["status"] == "submitted"
    assert status["state"] == "open"
    assert "authorization" not in status_headers


def test_heartbeat_omits_unset_max_tokens() -> None:
    """Heartbeat sends availability, languages, and the minimum. Max is optional."""
    with respx.mock:
        route = respx.post(f"{BASE}/api/v1/workers/heartbeat").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "AVAILABLE_IDLE",
                    "ttl_s": 60,
                    "released_leases": [],
                    "extended_leases": [],
                    "presence": {},
                },
            )
        )
        _client().heartbeat(
            status="AVAILABLE_IDLE",
            languages=["python"],
            min_bounty_usd=0,
        )
        _client().heartbeat(
            status="BUSY_PREEMPTED",
            languages=[],
            min_bounty_usd=4,
            max_tokens=100,
        )
        first = json.loads(route.calls[0].request.content)
        second = json.loads(route.calls[1].request.content)
    assert "max_tokens" not in first
    assert first["status"] == "AVAILABLE_IDLE"
    assert first["capabilities"]["languages"] == ["python"]
    assert second["max_tokens"] == 100
    assert second["status"] == "BUSY_PREEMPTED"


def test_json_error_body_is_parsed() -> None:
    """Non-2xx JSON is raised as SettleBridgeError and is not put in the message."""
    body = {
        "error": "trust_insufficient",
        "evaluations": [{"check": "kya", "ok": False}],
        "upgrade_path": "https://market.settlebridge.ai/trust",
    }
    with respx.mock:
        respx.post(f"{BASE}/api/v1/github/bounties/{BOUNTY_ID}/claim-lease").mock(
            return_value=httpx.Response(403, json=body)
        )
        with pytest.raises(SettleBridgeError) as exc_info:
            _client().claim_lease(BOUNTY_ID)
    assert exc_info.value.status_code == 403
    assert exc_info.value.body == body
    assert GATEWAY not in str(exc_info.value)
    assert GATEWAY not in repr(exc_info.value)


def test_non_json_error_body_is_text() -> None:
    """A non-JSON error body is kept as text."""
    with respx.mock:
        respx.post(f"{BASE}/api/v1/github/leases/{LEASE_ID}/release").mock(
            return_value=httpx.Response(502, text="upstream down")
        )
        with pytest.raises(SettleBridgeError) as exc_info:
            _client().release_lease(LEASE_ID, LEASE)
    assert exc_info.value.status_code == 502
    assert exc_info.value.body == "upstream down"


def test_client_timeout_is_30_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sync client uses a 30 second httpx timeout."""
    seen: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, timeout: object = None) -> None:
            seen["timeout"] = timeout

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
            return httpx.Response(200, json={"bounties": []})

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    _client().list_bounties()
    assert seen["timeout"] == 30


def test_onboard_repo_omits_unset_flags() -> None:
    """Omitted onboarding flags stay out of the body, matching the previous request."""
    with respx.mock:
        route = respx.post(ONBOARD).mock(return_value=httpx.Response(200, json={"changed": False}))
        result = _client().onboard_repo("acme/widget", checks=_CHECKS)
        body = json.loads(route.calls[0].request.content)
    assert body == {"checks": _CHECKS, "dry_run": True}
    assert result == {"changed": False}


@pytest.mark.parametrize("flag", _ONBOARD_FLAGS)
@pytest.mark.parametrize("value", [True, False])
def test_onboard_repo_passes_bool_flags(flag: str, value: bool) -> None:
    """Each onboarding flag is sent as given, including false."""
    with respx.mock:
        route = respx.post(ONBOARD).mock(return_value=httpx.Response(200, json={"changed": True}))
        _client().onboard_repo("acme/widget", checks=_CHECKS, dry_run=False, **{flag: value})
        body = json.loads(route.calls[0].request.content)
    assert body == {"checks": _CHECKS, "dry_run": False, flag: value}


@pytest.mark.parametrize("flag", _ONBOARD_FLAGS)
@pytest.mark.parametrize("value", [0, 1, "true", "false"])
def test_onboard_repo_rejects_non_bool_flags(flag: str, value: object) -> None:
    """A non-bool flag is rejected before any request is sent."""
    with respx.mock(assert_all_called=False) as router:
        with pytest.raises(ValueError, match=f"{flag} must be true or false"):
            _client().onboard_repo("acme/widget", checks=_CHECKS, **{flag: value})
        assert router.calls.call_count == 0


def test_tokens_are_redacted_from_log_records(caplog: pytest.LogCaptureFixture) -> None:
    """Gateway and lease tokens never appear in log records."""
    with respx.mock:
        route = respx.post(f"{BASE}/api/v1/github/leases/{LEASE_ID}/renew").mock(
            return_value=httpx.Response(200, json={"lease_id": LEASE_ID})
        )
        with caplog.at_level(logging.DEBUG):
            _client().renew_lease(LEASE_ID, LEASE)
            logging.getLogger("httpx").warning("Authorization: Bearer %s lease %s", GATEWAY, LEASE)
            logging.getLogger("a2a_settlement_mcp.settlebridge_client").warning(
                "X-Lease-Token %s",
                LEASE,
            )
        assert route.called
    assert GATEWAY not in caplog.text
    assert LEASE not in caplog.text
    assert "[redacted]" in caplog.text
