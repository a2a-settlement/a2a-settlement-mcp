"""MCP server exposing A2A Settlement Exchange operations as tools."""

from __future__ import annotations

import json

import httpx
from mcp.server.fastmcp import FastMCP

from .client import get_exchange_client
from .config import get_api_key, get_dashboard_api_key, get_exchange_url, get_port, get_shim_url, get_shim_api_key

mcp = FastMCP("a2a-settlement", json_response=True, port=get_port())


def _json_result(data: object) -> str:
    """Return JSON string for tool responses."""
    return json.dumps(data, indent=2)


def _error_result(message: str) -> str:
    """Return JSON error object for tool responses."""
    return _json_result({"error": message})


def _require_auth() -> str | None:
    """Return error message if API key is missing, else None."""
    if not get_api_key():
        return "This operation requires A2A_API_KEY. Set it in the server environment."
    return None


def _get_configured_account_id() -> str | None:
    """Get the configured account's ID from balance call. Returns None on failure."""
    err = _require_auth()
    if err:
        return None
    try:
        client = get_exchange_client()
        bal = client.get_balance()
        return bal.get("account_id")
    except Exception:
        return None


# --- Agent Management ---


@mcp.tool()
def settlement_register_agent(
    name: str,
    developer_id: str = "mcp",
    developer_name: str = "MCP User",
    contact_email: str = "noreply@localhost",
    description: str | None = None,
    skills: list[str] | None = None,
) -> str:
    """Register a new agent on the A2A Settlement Exchange.

    Creates a new account and returns an API key (store securely). The exchange
    assigns an account_id; use the returned api_key for subsequent authenticated operations.
    """
    try:
        client = get_exchange_client(api_key="")
        result = client.register_account(
            bot_name=name,
            developer_id=developer_id,
            developer_name=developer_name,
            contact_email=contact_email,
            description=description,
            skills=skills,
        )
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


@mcp.tool()
def settlement_fund_agent(agent_id: str, amount: int) -> str:
    """Add tokens to an agent's balance on the exchange.

    In single-tenant mode, agent_id must match the configured account (A2A_API_KEY).
    Funds are deposited to the authenticated account.
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    configured = _get_configured_account_id()
    if configured and agent_id != configured:
        return _error_result(
            "agent_id must match the configured account. "
            "Single-tenant mode: configure one agent per server."
        )
    try:
        client = get_exchange_client()
        result = client.deposit(amount=amount)
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


@mcp.tool()
def settlement_check_balance(agent_id: str) -> str:
    """Check an agent's token balance on the settlement exchange.

    In single-tenant mode, agent_id must match the configured account (A2A_API_KEY).
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    configured = _get_configured_account_id()
    if configured and agent_id != configured:
        return _error_result(
            "agent_id must match the configured account. "
            "Single-tenant mode: configure one agent per server."
        )
    try:
        client = get_exchange_client()
        result = client.get_balance()
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


@mcp.tool()
def settlement_list_agents(skill: str | None = None, limit: int = 50) -> str:
    """List all registered agents on the settlement exchange.

    Optionally filter by skill tag. Returns agent IDs, balances, reputation scores.
    """
    try:
        client = get_exchange_client(api_key="")
        result = client.directory(skill=skill, limit=limit)
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


# --- Escrow Lifecycle ---


@mcp.tool()
def settlement_create_escrow(
    provider_id: str,
    amount: int,
    task_description: str,
    task_id: str | None = None,
    task_type: str | None = None,
    ttl_minutes: int | None = None,
    required_attestation_level: str | None = None,
) -> str:
    """Create an escrow hold between two agents.

    Locks the specified amount from the requester's balance until the escrow is
    released or refunded. The requester is the configured account (A2A_API_KEY).

    Args:
        required_attestation_level: Optional provenance attestation tier the
            provider must meet when delivering: "self_declared", "signed",
            or "verifiable".
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    try:
        client = get_exchange_client()
        payload = {
            "provider_id": provider_id,
            "amount": amount,
            "deliverables": [{"description": task_description}],
        }
        if task_id is not None:
            payload["task_id"] = task_id
        if task_type is not None:
            payload["task_type"] = task_type
        if ttl_minutes is not None:
            payload["ttl_minutes"] = ttl_minutes
        if required_attestation_level is not None:
            payload["required_attestation_level"] = required_attestation_level
        result = client.create_escrow(**payload)
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


@mcp.tool()
def settlement_deliver(
    escrow_id: str,
    content: str,
    source_type: str | None = None,
    source_uris: list[str] | None = None,
    attestation_level: str | None = None,
    signature: str | None = None,
) -> str:
    """Submit a deliverable (with optional provenance) against a held escrow.

    The provider calls this after completing work to record the deliverable
    on the exchange. Provenance fields are optional; when provided, the AI
    Mediator will verify them during dispute resolution.

    Args:
        escrow_id: The escrow to deliver against.
        content: The deliverable content (text, JSON, etc.).
        source_type: How the data was obtained: "api", "database", "web",
            "generated", or "hybrid".
        source_uris: URIs the agent claims it accessed to produce the content.
        attestation_level: Trust tier: "self_declared", "signed", or
            "verifiable".
        signature: For signed tier — request ID or API-provided proof.
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    try:
        client = get_exchange_client()
        provenance = None
        if source_type or attestation_level:
            from datetime import datetime, timezone

            source_refs = [
                {"uri": uri, "timestamp": datetime.now(timezone.utc).isoformat()}
                for uri in (source_uris or [])
            ]
            provenance = {
                "source_type": source_type or "generated",
                "source_refs": source_refs,
                "attestation_level": attestation_level or "self_declared",
            }
            if signature:
                provenance["signature"] = signature
        result = client.deliver(
            escrow_id=escrow_id,
            content=content,
            provenance=provenance,
        )
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


@mcp.tool()
def settlement_get_escrow(escrow_id: str) -> str:
    """Check the current status of an escrow.

    Returns full escrow details including status (pending/released/refunded/disputed/expired).
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    try:
        client = get_exchange_client()
        result = client.get_escrow(escrow_id=escrow_id)
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


@mcp.tool()
def settlement_release_escrow(escrow_id: str) -> str:
    """Release escrowed funds to the provider agent.

    Call this when the task has been completed successfully. Funds are transferred permanently.
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    try:
        client = get_exchange_client()
        result = client.release_escrow(escrow_id=escrow_id)
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


@mcp.tool()
def settlement_refund_escrow(escrow_id: str, reason: str | None = None) -> str:
    """Refund escrowed funds back to the requester agent.

    Call this when the task failed or was not completed. Funds return to the requester.
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    try:
        client = get_exchange_client()
        result = client.refund_escrow(escrow_id=escrow_id, reason=reason)
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


# --- Disputes & Reputation ---


@mcp.tool()
def settlement_file_dispute(escrow_id: str, reason: str, stake_amount: int = 10) -> str:
    """File a dispute on an escrow with an ATE token stake.

    Triggers the evidence submission window (72 hours). Only requester or provider
    can dispute. The stake is forfeited if the dispute is ruled frivolous, or
    returned if upheld.

    Args:
        escrow_id: The escrow to dispute.
        reason: Explanation of why the dispute is being filed.
        stake_amount: ATE tokens to stake on this dispute (minimum 10).
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    try:
        client = get_exchange_client()
        result = client.dispute_escrow(
            escrow_id=escrow_id, reason=reason, stake_amount=stake_amount
        )
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


@mcp.tool()
def settlement_submit_evidence(
    escrow_id: str,
    evidence_type: str,
    summary: str,
    artifact_type: str | None = None,
    artifact_content: str | None = None,
    artifact_uri: str | None = None,
    artifact_hash: str | None = None,
    artifact_mime_type: str | None = None,
    encrypted: bool = False,
    encryption_key_id: str | None = None,
    attestor_id: str | None = None,
    attestor_signature: str | None = None,
) -> str:
    """Submit structured evidence for a disputed escrow.

    Called during the 72-hour evidence window after a dispute is filed.
    Both requester and provider can submit evidence. If the respondent fails
    to submit, a default judgment is applied.

    Args:
        escrow_id: The disputed escrow to submit evidence for.
        evidence_type: Type of evidence: "compute", "content", "service",
            "bounty", or "third_party_attestation".
        summary: Brief description of the evidence (max 4096 chars).
        artifact_type: "inline" for embedded content, "uri" for remote reference.
        artifact_content: Inline artifact content (max 5MB, required if artifact_type="inline").
        artifact_uri: Content-addressed URI (IPFS CID, Arweave, etc., required if artifact_type="uri").
        artifact_hash: SHA-256 hash of the artifact content.
        artifact_mime_type: MIME type of the artifact.
        encrypted: Whether the evidence is encrypted (only mediator TEE can decrypt).
        encryption_key_id: Key ID for TEE-mediated decryption.
        attestor_id: Account ID of the third-party attestor.
        attestor_signature: Cryptographic signature from the attestor.
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    try:
        client = get_exchange_client()
        artifacts = []
        if artifact_type and artifact_hash:
            artifact = {
                "artifact_type": artifact_type,
                "content_hash": artifact_hash,
            }
            if artifact_content:
                artifact["content"] = artifact_content
            if artifact_uri:
                artifact["uri"] = artifact_uri
            if artifact_mime_type:
                artifact["mime_type"] = artifact_mime_type
            artifacts.append(artifact)

        result = client.submit_evidence(
            escrow_id=escrow_id,
            evidence_type=evidence_type,
            summary=summary,
            artifacts=artifacts or None,
            encrypted=encrypted,
            encryption_key_id=encryption_key_id,
            attestor_id=attestor_id,
            attestor_signature=attestor_signature,
        )
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


@mcp.tool()
def settlement_check_reputation(agent_id: str) -> str:
    """Check an agent's reputation score on the settlement exchange.

    Returns reputation (0.0-1.0) and account details. No authentication required.
    """
    try:
        client = get_exchange_client(api_key="")
        result = client.get_account(account_id=agent_id)
        rep = result.get("reputation", 0.0)
        return _json_result(
            {"agent_id": agent_id, "reputation": rep, "account": result}
        )
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


# --- Transaction History ---


# --- Admin / Dashboard Operations ---


@mcp.tool()
def settlement_suspend_agent(agent_id: str) -> str:
    """Suspend an agent on the settlement exchange (operator action).

    Prevents the agent from creating new escrows or participating in transactions.
    Requires operator-level API key.
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    try:
        client = get_exchange_client()
        url = f"{get_exchange_url()}/api/v1/dashboard/agents/{agent_id}/suspend"
        import httpx as _httpx
        resp = _httpx.post(url, headers={"Authorization": f"Bearer {get_dashboard_api_key()}"}, timeout=10.0)
        resp.raise_for_status()
        return _json_result(resp.json())
    except Exception as e:
        return _error_result(f"Failed to suspend agent: {e}")


@mcp.tool()
def settlement_unsuspend_agent(agent_id: str) -> str:
    """Unsuspend a previously suspended agent (operator action).

    Restores the agent's ability to transact on the exchange.
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    try:
        url = f"{get_exchange_url()}/api/v1/dashboard/agents/{agent_id}/unsuspend"
        import httpx as _httpx
        resp = _httpx.post(url, headers={"Authorization": f"Bearer {get_dashboard_api_key()}"}, timeout=10.0)
        resp.raise_for_status()
        return _json_result(resp.json())
    except Exception as e:
        return _error_result(f"Failed to unsuspend agent: {e}")


@mcp.tool()
def settlement_force_refund(escrow_id: str) -> str:
    """Force-refund an escrow as operator regardless of requester identity.

    Use for dispute resolution or emergency recovery. Refunds the full amount
    (including fees) back to the requester.
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    try:
        url = f"{get_exchange_url()}/api/v1/dashboard/escrows/{escrow_id}/force-refund"
        import httpx as _httpx
        resp = _httpx.post(url, headers={"Authorization": f"Bearer {get_dashboard_api_key()}"}, timeout=10.0)
        resp.raise_for_status()
        return _json_result(resp.json())
    except Exception as e:
        return _error_result(f"Failed to force refund: {e}")


@mcp.tool()
def settlement_resolve_dispute(
    escrow_id: str,
    resolution: str,
    provenance_verified: bool | None = None,
    provenance_confidence: float | None = None,
    provenance_flags: list[str] | None = None,
) -> str:
    """Resolve a disputed escrow as operator.

    Args:
        escrow_id: The disputed escrow to resolve.
        resolution: Either "release" (pay provider) or "refund" (return to requester).
        provenance_verified: Whether provenance verification passed.
        provenance_confidence: Confidence score (0.0-1.0) from verification.
        provenance_flags: Specific issues found during verification.
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    if resolution not in ("release", "refund"):
        return _error_result("resolution must be 'release' or 'refund'")
    try:
        client = get_exchange_client()
        provenance_result = None
        if provenance_verified is not None:
            provenance_result = {
                "verified": provenance_verified,
                "confidence": provenance_confidence or 0.0,
                "flags": provenance_flags or [],
            }
        result = client.resolve_escrow(
            escrow_id=escrow_id,
            resolution=resolution,
            provenance_result=provenance_result,
        )
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(f"Exchange connection failed: {e}")


@mcp.tool()
def settlement_get_history(agent_id: str, limit: int = 50, offset: int = 0) -> str:
    """Get transaction history for an agent.

    In single-tenant mode, agent_id must match the configured account.
    Returns past escrow records and transactions.
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    configured = _get_configured_account_id()
    if configured and agent_id != configured:
        return _error_result(
            "agent_id must match the configured account. "
            "Single-tenant mode: configure one agent per server."
        )
    try:
        client = get_exchange_client()
        result = client.get_transactions(limit=limit, offset=offset)
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            msg = body.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return _error_result(msg)
    except httpx.RequestError as e:
        return _error_result(
            f"Exchange connection failed: {get_exchange_url()} is not responding. "
            "Ensure the exchange is running."
        )


# --- Economic Air Gap: Shim Proxy & Secret Management ---


def _require_shim() -> str | None:
    """Return error message if shim URL is not configured."""
    if not get_shim_url():
        return (
            "Shim is not configured. Set A2A_SHIM_URL to the Security Shim base URL "
            "(e.g., http://localhost:3300)."
        )
    return None


def _shim_request(method: str, path: str, body: dict | None = None) -> dict:
    """Make a request to the Security Shim."""
    url = f"{get_shim_url()}{path}"
    headers = {}
    api_key = get_shim_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = httpx.request(method, url, json=body, headers=headers, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def settlement_proxy_request(
    escrow_id: str,
    tool_id: str | None = None,
    destination_url: str | None = None,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    secret_id: str | None = None,
) -> str:
    """Route a tool call through the Security Shim (Economic Air Gap).

    Sends the request through the shim proxy which checks escrow balance,
    resolves credentials, and forwards to the destination. The agent never
    sees the real credential.

    Use tool_id for full air gap (shim resolves destination and secret).
    Use destination_url + secret_id for direct mode.
    """
    err = _require_shim()
    if err:
        return _error_result(err)
    try:
        payload: dict = {"escrow_id": escrow_id, "method": method}
        if tool_id:
            payload["tool_id"] = tool_id
        if destination_url:
            payload["destination_url"] = destination_url
        if headers:
            payload["headers"] = headers
        if body:
            payload["body"] = body
        if secret_id:
            payload["secret_id"] = secret_id
        result = _shim_request("POST", "/shim/proxy", payload)
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            msg = e.response.json().get("error", str(e))
        except Exception:
            msg = str(e)
        return _error_result(f"Shim proxy request failed: {msg}")
    except httpx.RequestError as e:
        return _error_result(
            f"Shim connection failed: {get_shim_url()} is not responding. "
            "Ensure the Security Shim is running."
        )


@mcp.tool()
def settlement_register_tool(
    tool_id: str,
    destination_url: str,
    method: str = "POST",
    secret_id: str | None = None,
    inject_as: str = "bearer",
    inject_key: str = "Authorization",
    cost_override: float | None = None,
    description: str = "",
) -> str:
    """Register a tool mapping on the Security Shim for full air-gap mode.

    After registration, agents can call the tool by tool_id without knowing
    the destination URL or secret.
    """
    err = _require_shim()
    if err:
        return _error_result(err)
    try:
        payload = {
            "tool_id": tool_id,
            "destination_url": destination_url,
            "method": method,
            "inject_as": inject_as,
            "inject_key": inject_key,
            "description": description,
        }
        if secret_id:
            payload["secret_id"] = secret_id
        if cost_override is not None:
            payload["cost_override"] = cost_override
        result = _shim_request("POST", "/shim/tools", payload)
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            msg = e.response.json().get("error", str(e))
        except Exception:
            msg = str(e)
        return _error_result(f"Failed to register tool: {msg}")
    except httpx.RequestError as e:
        return _error_result(f"Shim connection failed: {e}")


@mcp.tool()
def settlement_register_secret(
    owner_id: str,
    value: str,
    label: str = "",
    agent_ids: list[str] | None = None,
) -> str:
    """Register a secret (API key, PAT, token) in the vault.

    The returned secret_id is an opaque placeholder that agents use
    instead of the real credential. The real value is encrypted at rest
    and only resolved by the Security Shim at request time.

    Requires the shim to be running (vault is co-located or accessible).
    """
    err = _require_shim()
    if err:
        return _error_result(err)
    try:
        payload: dict = {
            "owner_id": owner_id,
            "value": value,
            "label": label,
        }
        if agent_ids:
            payload["agent_ids"] = agent_ids
        result = _shim_request("POST", "/shim/secrets", payload)
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            msg = e.response.json().get("error", str(e))
        except Exception:
            msg = str(e)
        return _error_result(f"Failed to register secret: {msg}")
    except httpx.RequestError as e:
        return _error_result(f"Shim connection failed: {e}")


@mcp.tool()
def settlement_shim_escrow_status(escrow_id: str) -> str:
    """Check escrow status and remaining balance in the Security Shim.

    Returns the local shim view of the escrow (remaining credits after
    deductions for proxied calls).
    """
    err = _require_shim()
    if err:
        return _error_result(err)
    try:
        result = _shim_request("GET", f"/shim/escrows/{escrow_id}")
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            msg = e.response.json().get("detail", str(e))
        except Exception:
            msg = str(e)
        return _error_result(f"Shim escrow check failed: {msg}")
    except httpx.RequestError as e:
        return _error_result(f"Shim connection failed: {e}")


@mcp.tool()
def settlement_shim_audit(limit: int = 50) -> str:
    """Retrieve recent audit entries from the Security Shim.

    Shows proxied requests, costs, and any blocked requests (402s).
    """
    err = _require_shim()
    if err:
        return _error_result(err)
    try:
        result = _shim_request("GET", f"/shim/audit?limit={limit}")
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            msg = e.response.json().get("error", str(e))
        except Exception:
            msg = str(e)
        return _error_result(f"Shim audit request failed: {msg}")
    except httpx.RequestError as e:
        return _error_result(f"Shim connection failed: {e}")


# ---------------------------------------------------------------------------
# Attestation Lifecycle
# ---------------------------------------------------------------------------


def _attestation_request(method: str, path: str, json_body: dict | None = None) -> dict:
    """Make a direct HTTP request to the exchange attestation endpoints."""
    from a2a_settlement_mcp.config import get_exchange_url, get_api_key

    base = get_exchange_url().rstrip("/")
    url = f"{base}/v1{path}"
    headers = {"Authorization": f"Bearer {get_api_key()}"} if get_api_key() else {}
    resp = httpx.request(method, url, json=json_body, headers=headers, timeout=15.0)
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def settlement_check_attestation_status(attestation_id: str) -> str:
    """Check the OCSP-style status of an attestation.

    Returns validity, TTL remaining, revocation status, and whether
    the attestation is in an in-flight grace period.
    """
    try:
        result = _attestation_request("GET", f"/exchange/attestations/{attestation_id}/status")
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            msg = e.response.json().get("detail", str(e))
        except Exception:
            msg = str(e)
        return _error_result(f"Attestation status check failed: {msg}")
    except httpx.RequestError as e:
        return _error_result(f"Exchange connection failed: {e}")


@mcp.tool()
def settlement_revoke_attestation(
    attestation_id: str,
    reason: str,
    signatures: list[str] | None = None,
) -> str:
    """Revoke an attestation on the settlement exchange.

    Args:
        attestation_id: ID of the attestation to revoke.
        reason: One of key_compromise, erroneous_issuance, deregistration, policy_violation.
        signatures: Multi-sig signatures (required for identity/capability revocations).
    """
    try:
        payload = {"reason": reason}
        if signatures:
            payload["signatures"] = signatures
        result = _attestation_request("POST", f"/exchange/attestations/{attestation_id}/revoke", payload)
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            msg = e.response.json().get("detail", str(e))
        except Exception:
            msg = str(e)
        return _error_result(f"Attestation revocation failed: {msg}")
    except httpx.RequestError as e:
        return _error_result(f"Exchange connection failed: {e}")


@mcp.tool()
def settlement_renew_attestation(attestation_id: str) -> str:
    """Renew an expiring attestation on the settlement exchange.

    Charges a nominal ATE micro-fee and creates a new attestation
    chained to the old one. The old attestation transitions to 'renewed'.
    """
    try:
        result = _attestation_request("POST", f"/exchange/attestations/{attestation_id}/renew")
        return _json_result(result)
    except httpx.HTTPStatusError as e:
        try:
            msg = e.response.json().get("detail", str(e))
        except Exception:
            msg = str(e)
        return _error_result(f"Attestation renewal failed: {msg}")
    except httpx.RequestError as e:
        return _error_result(f"Exchange connection failed: {e}")
