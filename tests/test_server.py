"""Tests for MCP server tool handlers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from a2a_settlement_mcp.server import (
    mcp,
    settlement_check_balance,
    settlement_check_reputation,
    settlement_create_escrow,
    settlement_file_dispute,
    settlement_fund_agent,
    settlement_get_escrow,
    settlement_get_history,
    settlement_list_agents,
    settlement_refund_escrow,
    settlement_register_agent,
    settlement_release_escrow,
)


def _parse_result(result: str) -> dict:
    """Parse JSON result, handling error wrapper."""
    return json.loads(result)


def test_settlement_list_agents_success() -> None:
    """settlement_list_agents returns directory data on success."""
    mock_response = {"agents": [{"id": "a1", "bot_name": "Agent1"}], "total": 1}
    with patch("a2a_settlement_mcp.server.get_exchange_client") as mock_factory:
        mock_client = MagicMock()
        mock_client.directory.return_value = mock_response
        mock_factory.return_value = mock_client

        result = settlement_list_agents()
        data = _parse_result(result)
        assert "error" not in data
        assert data["agents"] == mock_response["agents"]
        mock_client.directory.assert_called_once_with(skill=None, limit=50)


def test_settlement_check_reputation_success() -> None:
    """settlement_check_reputation returns reputation on success."""
    mock_response = {"id": "a1", "reputation": 0.85, "bot_name": "Test"}
    with patch("a2a_settlement_mcp.server.get_exchange_client") as mock_factory:
        mock_client = MagicMock()
        mock_client.get_account.return_value = mock_response
        mock_factory.return_value = mock_client

        result = settlement_check_reputation(agent_id="a1")
        data = _parse_result(result)
        assert "error" not in data
        assert data["reputation"] == 0.85
        assert data["agent_id"] == "a1"


def test_settlement_check_balance_requires_auth() -> None:
    """settlement_check_balance returns error when A2A_API_KEY not set."""
    with patch("a2a_settlement_mcp.server.get_api_key", return_value=""):
        result = settlement_check_balance(agent_id="any")
        data = _parse_result(result)
        assert "error" in data
        assert "A2A_API_KEY" in data["error"]


def test_settlement_check_balance_success() -> None:
    """settlement_check_balance returns balance when auth and agent_id match."""
    mock_balance = {"account_id": "acc-1", "available": 100}
    with patch("a2a_settlement_mcp.server.get_api_key", return_value="ate_key"):
        with patch("a2a_settlement_mcp.server._get_configured_account_id", return_value="acc-1"):
            with patch("a2a_settlement_mcp.server.get_exchange_client") as mock_factory:
                mock_client = MagicMock()
                mock_client.get_balance.return_value = mock_balance
                mock_factory.return_value = mock_client

                result = settlement_check_balance(agent_id="acc-1")
                data = _parse_result(result)
                assert "error" not in data
                assert data["available"] == 100


def test_settlement_check_balance_agent_id_mismatch() -> None:
    """settlement_check_balance returns error when agent_id does not match configured."""
    with patch("a2a_settlement_mcp.server.get_api_key", return_value="ate_key"):
        with patch("a2a_settlement_mcp.server._get_configured_account_id", return_value="acc-1"):
            result = settlement_check_balance(agent_id="other-agent")
            data = _parse_result(result)
            assert "error" in data
            assert "must match" in data["error"]


def test_settlement_fund_agent_requires_auth() -> None:
    """settlement_fund_agent returns error when A2A_API_KEY not set."""
    with patch("a2a_settlement_mcp.server.get_api_key", return_value=""):
        result = settlement_fund_agent(agent_id="acc-1", amount=10)
        data = _parse_result(result)
        assert "error" in data
        assert "A2A_API_KEY" in data["error"]


def test_settlement_create_escrow_requires_auth() -> None:
    """settlement_create_escrow returns error when A2A_API_KEY not set."""
    with patch("a2a_settlement_mcp.server.get_api_key", return_value=""):
        result = settlement_create_escrow(
            provider_id="prov-1",
            amount=10,
            task_description="Test task",
        )
        data = _parse_result(result)
        assert "error" in data
        assert "A2A_API_KEY" in data["error"]


def test_settlement_create_escrow_success() -> None:
    """settlement_create_escrow calls create_escrow with correct params."""
    mock_escrow = {"escrow_id": "e1", "status": "held", "amount": 10}
    with patch("a2a_settlement_mcp.server.get_api_key", return_value="ate_key"):
        with patch("a2a_settlement_mcp.server.get_exchange_client") as mock_factory:
            mock_client = MagicMock()
            mock_client.create_escrow.return_value = mock_escrow
            mock_factory.return_value = mock_client

            result = settlement_create_escrow(
                provider_id="prov-1",
                amount=10,
                task_description="Analyze sentiment",
            )
            data = _parse_result(result)
            assert "error" not in data
            assert data["escrow_id"] == "e1"
            mock_client.create_escrow.assert_called_once()
            call_kw = mock_client.create_escrow.call_args[1]
            assert call_kw["provider_id"] == "prov-1"
            assert call_kw["amount"] == 10
            assert call_kw["deliverables"] == [{"description": "Analyze sentiment"}]


def test_settlement_release_escrow_requires_auth() -> None:
    """settlement_release_escrow returns error when A2A_API_KEY not set."""
    with patch("a2a_settlement_mcp.server.get_api_key", return_value=""):
        result = settlement_release_escrow(escrow_id="e1")
        data = _parse_result(result)
        assert "error" in data


def test_settlement_refund_escrow_success() -> None:
    """settlement_refund_escrow calls refund_escrow with correct params."""
    mock_result = {"escrow_id": "e1", "status": "refunded"}
    with patch("a2a_settlement_mcp.server.get_api_key", return_value="ate_key"):
        with patch("a2a_settlement_mcp.server.get_exchange_client") as mock_factory:
            mock_client = MagicMock()
            mock_client.refund_escrow.return_value = mock_result
            mock_factory.return_value = mock_client

            result = settlement_refund_escrow(escrow_id="e1", reason="Task failed")
            data = _parse_result(result)
            assert "error" not in data
            mock_client.refund_escrow.assert_called_once_with(
                escrow_id="e1", reason="Task failed"
            )


def test_settlement_file_dispute_success() -> None:
    """settlement_file_dispute calls dispute_escrow with correct params."""
    mock_result = {"escrow_id": "e1", "status": "disputed"}
    with patch("a2a_settlement_mcp.server.get_api_key", return_value="ate_key"):
        with patch("a2a_settlement_mcp.server.get_exchange_client") as mock_factory:
            mock_client = MagicMock()
            mock_client.dispute_escrow.return_value = mock_result
            mock_factory.return_value = mock_client

            result = settlement_file_dispute(escrow_id="e1", reason="Incomplete delivery")
            data = _parse_result(result)
            assert "error" not in data
            mock_client.dispute_escrow.assert_called_once_with(
                escrow_id="e1", reason="Incomplete delivery"
            )


def test_settlement_register_agent_success() -> None:
    """settlement_register_agent calls register_account with mapped params."""
    mock_response = {"account_id": "a1", "api_key": "ate_xxx", "bot_name": "Agent1"}
    with patch("a2a_settlement_mcp.server.get_exchange_client") as mock_factory:
        mock_client = MagicMock()
        mock_client.register_account.return_value = mock_response
        mock_factory.return_value = mock_client

        result = settlement_register_agent(name="Agent1")
        data = _parse_result(result)
        assert "error" not in data
        mock_client.register_account.assert_called_once()
        call_kw = mock_client.register_account.call_args[1]
        assert call_kw["bot_name"] == "Agent1"
        assert call_kw["developer_id"] == "mcp"
        assert call_kw["developer_name"] == "MCP User"
        assert call_kw["contact_email"] == "noreply@localhost"


def test_settlement_list_agents_exchange_error() -> None:
    """settlement_list_agents returns error message on HTTP error."""
    import httpx

    with patch("a2a_settlement_mcp.server.get_exchange_client") as mock_factory:
        mock_client = MagicMock()
        mock_client.directory.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        mock_client.directory.side_effect.response.json.return_value = {
            "error": {"message": "Not found"}
        }
        mock_factory.return_value = mock_client

        result = settlement_list_agents()
        data = _parse_result(result)
        assert "error" in data


def test_list_tools_returns_all_eleven() -> None:
    """Server lists all 11 tools with correct names."""
    tools = mcp._tool_manager._tools
    tool_names = [t.name for t in tools.values()]
    expected = [
        "settlement_register_agent",
        "settlement_fund_agent",
        "settlement_check_balance",
        "settlement_list_agents",
        "settlement_create_escrow",
        "settlement_get_escrow",
        "settlement_release_escrow",
        "settlement_refund_escrow",
        "settlement_file_dispute",
        "settlement_check_reputation",
        "settlement_get_history",
    ]
    for name in expected:
        assert name in tool_names, f"Missing tool: {name}"
    assert len(tool_names) == 11
