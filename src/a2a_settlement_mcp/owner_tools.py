"""MCP tools that let a repo owner set up their GitHub repo for SettleBridge bounties.

These sign in as the owner with ``SETTLEBRIDGE_API_KEY`` (their exchange API
key), not with a gateway token. Steps that need a person, such as installing
the GitHub App or linking a GitHub account, come back as ``next_steps`` with a
link to open. Enabling bounties always takes an explicit ``apply=True`` after a
dry run.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import get_settlebridge_url
from .owner_session import OwnerSessionError, get_owner_session
from .server import _error_result, _json_result, mcp
from .settlebridge_client import (
    SettleBridgeClient,
    SettleBridgeError,
    parse_repo,
    redact_secrets,
)

_KEY_REQUIRED = (
    "SETTLEBRIDGE_API_KEY is required: set it to the repo owner's exchange API key "
    "(starts with ate_) in this MCP server's environment. Create an exchange account "
    "with settlement_register_agent if the owner has none."
)

_SELF_SERVE_OFF = "Self-serve setup is not enabled on this SettleBridge server."

# Steps this tool can satisfy from its own arguments.
_ARGUMENT_STEPS = {"no_pinned_check", "needs_allowed_path"}

_DETAIL_KEYS = (
    "url",
    "candidates",
    "headroom_ate",
    "minimum_ate",
    "path",
    "github_login",
)
_MAX_ERROR_TEXT = 300

_HUMAN_STEPS = (
    "Show the user each step's message and url. Installing the GitHub App and linking "
    "GitHub need them to open the link in a browser while signed in to SettleBridge; "
    "funding is a deliberate step only they should take. Call this tool again afterwards."
)

_CONFIRM = (
    "This was a dry run; nothing changed. Show the user the repo, required checks, "
    "allowed paths and free balance below. Call setup_github_repo again with apply=true "
    "only after they confirm."
)


def _client() -> SettleBridgeClient:
    from .hosted import MISSING_BEARER, is_hosted, owner_session_for_key, read_caller_key

    if is_hosted():
        key, err = read_caller_key()
        if err:
            raise OwnerSessionError(err)
        if not key:
            raise OwnerSessionError(MISSING_BEARER)
        session = owner_session_for_key(key)
        return SettleBridgeClient(get_settlebridge_url(), token_provider=session)
    session = get_owner_session()
    if session is None:
        raise OwnerSessionError(_KEY_REQUIRED)
    return SettleBridgeClient(get_settlebridge_url(), token_provider=session)


def _owner_error(exc: SettleBridgeError) -> str:
    """The SettleBridge error code, message and allowlisted details. The body is untrusted."""
    if exc.status_code == 404:
        body = exc.body if isinstance(exc.body, dict) else {}
        if body.get("detail") == "Not Found":
            return _error_result(_SELF_SERVE_OFF)
    detail = exc.body.get("detail") if isinstance(exc.body, dict) else None
    result: dict[str, Any] = {"status": exc.status_code}
    if isinstance(detail, dict):
        code = detail.get("code")
        message = detail.get("message")
        result["error"] = code if isinstance(code, str) else f"HTTP {exc.status_code}"
        if isinstance(message, str):
            result["message"] = message[:_MAX_ERROR_TEXT]
        result["details"] = {key: detail[key] for key in _DETAIL_KEYS if key in detail}
    elif isinstance(detail, str):
        result["error"] = detail[:_MAX_ERROR_TEXT]
    else:
        result["error"] = f"HTTP {exc.status_code}"
    return redact_secrets(_json_result(result))


def _run(action) -> str:
    try:
        return redact_secrets(_json_result(action()))
    except SettleBridgeError as exc:
        return _owner_error(exc)
    except OwnerSessionError as exc:
        return _error_result(redact_secrets(str(exc)))
    except ValueError as exc:
        return _error_result(str(exc))
    except httpx.RequestError:
        return _error_result(
            f"SettleBridge connection failed: {get_settlebridge_url()} is not responding."
        )


def _parse_check(value: str) -> dict[str, Any]:
    name, sep, app_id = value.strip().rpartition(":")
    if not sep or not name.strip() or not app_id.strip().isdigit():
        raise ValueError("check must be NAME:APP_ID, such as test:15368")
    return {"name": name.strip(), "app_id": int(app_id)}


def _blocking(status: dict[str, Any], *, skip: set[str]) -> list[dict[str, Any]]:
    steps = status.get("next_steps") if isinstance(status.get("next_steps"), list) else []
    return [
        step
        for step in steps
        if isinstance(step, dict) and step.get("blocking") and step.get("code") not in skip
    ]


@mcp.tool()
def get_github_repo_setup_status(repo: str) -> str:
    """Check what is still needed before SettleBridge bounties can run on a GitHub repo.

    Signs in as the repo owner with SETTLEBRIDGE_API_KEY. Returns ``ready`` and
    ``next_steps``, each with a stable ``code``, a message, and a ``url`` when the
    user must open a link: ``link_github``, ``repo_access`` (install the GitHub App
    or use a repo admin's GitHub account), ``funder_no_exchange``,
    ``funder_low_balance``, ``funded_by_other``, ``no_pinned_check`` (with
    ``candidates``) and ``needs_allowed_path``. Read-only.

    Args:
        repo: ``owner/name`` or a https://github.com URL.
    """

    def action() -> Any:
        return _client().repo_setup_status(parse_repo(repo))

    return _run(action)


@mcp.tool()
def setup_github_repo(
    repo: str,
    check: str = "",
    allowed_paths: list[str] | None = None,
    apply: bool = False,
) -> str:
    """Set up a GitHub repo for SettleBridge bounties in one call, as its owner and funder.

    Signs in with SETTLEBRIDGE_API_KEY (the owner's exchange API key), which
    becomes the repo's funder. If steps that need the user remain, returns them
    with links and changes nothing. Otherwise runs onboarding as a dry run, or for
    real with ``apply=True``: that saves the required check and allowed paths,
    enables bounties, and creates the ``bounty:`` and ``tier:`` labels.

    Always call without ``apply`` first and show the user the result. Pass
    ``apply=True`` only after the user confirms. Enabling bounties does not move
    money; each bounty is paid only after a maintainer merges a pull request whose
    required checks pass.

    Args:
        repo: ``owner/name`` or a https://github.com URL.
        check: Required check as ``NAME:APP_ID`` (for example ``test:15368``).
            Needed only when the repo's branch rules don't pin one; pick from the
            ``candidates`` this tool returns.
        allowed_paths: Globs a solver may change, such as ``["src/*", "tests/*"]``.
            Defaults to what SettleBridge detects. Nothing under ``.github/``.
        apply: False for a dry run (default); True to enable bounties.
    """

    def action() -> Any:
        name = parse_repo(repo)
        chosen = _parse_check(check) if check.strip() else None
        paths = [p.strip() for p in allowed_paths if p.strip()] if allowed_paths else None
        client = _client()
        status = client.repo_setup_status(name)
        if not isinstance(status, dict):
            raise SettleBridgeError(502, status)
        skip = set()
        if chosen is not None:
            skip.add("no_pinned_check")
        if paths:
            skip.add("needs_allowed_path")
        blocking = _blocking(status, skip=skip)
        if blocking:
            needs_arguments = all(step.get("code") in _ARGUMENT_STEPS for step in blocking)
            return {
                "ready": False,
                "repo": name,
                "next_steps": blocking,
                "guidance": (
                    "Ask the user which required check and paths to use, then call again "
                    "with check and allowed_paths."
                    if needs_arguments
                    else _HUMAN_STEPS
                ),
            }
        detected = status.get("detected") if isinstance(status.get("detected"), dict) else {}
        checks = [chosen] if chosen is not None else list(detected.get("required_checks") or [])
        result = client.onboard_repo(
            name, checks=checks, allowed_paths=paths, dry_run=not apply
        )
        if not isinstance(result, dict):
            raise SettleBridgeError(502, result)
        policy = (result.get("after") or {}) if result.get("changed") else {}
        summary = {
            "ready": True,
            "repo": name,
            "applied": bool(apply),
            "changed": result.get("changed"),
            "required_checks": checks,
            "allowed_paths": paths or detected.get("allowed_paths"),
            "funder_headroom_ate": result.get("funder_headroom_ate"),
            "after": policy,
            "labels": result.get("labels"),
        }
        if not apply:
            summary["guidance"] = _CONFIRM
        elif result.get("changed"):
            summary["guidance"] = (
                f"Bounties are on for {name}. Add a label such as `bounty: $5` to an issue "
                "with a reproduce command to post a bounty."
            )
        else:
            summary["guidance"] = f"{name} was already set up this way; nothing changed."
        return summary

    return _run(action)
