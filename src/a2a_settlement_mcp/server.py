"""MCP server exposing A2A Settlement Exchange operations as tools."""

from __future__ import annotations

import json

import httpx
from mcp.server.fastmcp import FastMCP

from .client import get_exchange_client
from .config import get_api_key, get_dashboard_api_key, get_exchange_url, get_port

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
) -> str:
    """Create an escrow hold between two agents.

    Locks the specified amount from the requester's balance until the escrow is
    released or refunded. The requester is the configured account (A2A_API_KEY).
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
def settlement_file_dispute(escrow_id: str, reason: str) -> str:
    """File a dispute on an escrow.

    Triggers the mediation process. Only requester or provider can dispute.
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    try:
        client = get_exchange_client()
        result = client.dispute_escrow(escrow_id=escrow_id, reason=reason)
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
def settlement_resolve_dispute(escrow_id: str, resolution: str) -> str:
    """Resolve a disputed escrow as operator.

    Args:
        escrow_id: The disputed escrow to resolve.
        resolution: Either "release" (pay provider) or "refund" (return to requester).
    """
    err = _require_auth()
    if err:
        return _error_result(err)
    if resolution not in ("release", "refund"):
        return _error_result("resolution must be 'release' or 'refund'")
    try:
        client = get_exchange_client()
        result = client.resolve_escrow(escrow_id=escrow_id, resolution=resolution)
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
