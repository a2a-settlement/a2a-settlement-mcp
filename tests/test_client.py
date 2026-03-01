"""Tests for the exchange client wrapper and config."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from a2a_settlement_mcp.client import get_exchange_client
from a2a_settlement_mcp.config import (
    get_api_key,
    get_exchange_url,
    get_port,
    get_transport,
)


def test_get_exchange_url_default() -> None:
    """Config returns default URL when env not set."""
    with patch.dict(os.environ, {}, clear=True):
        assert get_exchange_url() == "http://localhost:3000"


def test_get_exchange_url_from_env() -> None:
    """Config reads A2A_EXCHANGE_URL from env."""
    with patch.dict(os.environ, {"A2A_EXCHANGE_URL": "https://exchange.example.com"}):
        assert get_exchange_url() == "https://exchange.example.com"


def test_get_api_key_default() -> None:
    """Config returns empty string when A2A_API_KEY not set."""
    with patch.dict(os.environ, {}, clear=True):
        assert get_api_key() == ""


def test_get_api_key_from_env() -> None:
    """Config reads A2A_API_KEY from env."""
    with patch.dict(os.environ, {"A2A_API_KEY": "ate_test123"}):
        assert get_api_key() == "ate_test123"


def test_get_transport_default() -> None:
    """Config returns stdio when A2A_MCP_TRANSPORT not set."""
    with patch.dict(os.environ, {}, clear=True):
        assert get_transport() == "stdio"


def test_get_port_default() -> None:
    """Config returns 3200 when A2A_MCP_PORT not set."""
    with patch.dict(os.environ, {}, clear=True):
        assert get_port() == 3200


def test_get_port_from_env() -> None:
    """Config reads A2A_MCP_PORT from env."""
    with patch.dict(os.environ, {"A2A_MCP_PORT": "8080"}):
        assert get_port() == 8080


def test_get_exchange_client_builds_correctly() -> None:
    """Client factory builds SettlementExchangeClient with config values."""
    with patch.dict(
        os.environ,
        {"A2A_EXCHANGE_URL": "http://test:3000", "A2A_API_KEY": "ate_key"},
        clear=True,
    ):
        client = get_exchange_client()
        assert client.base_url == "http://test:3000"
        assert client.api_key == "ate_key"


def test_get_exchange_client_without_api_key() -> None:
    """Client can be built without API key for public endpoints."""
    with patch.dict(os.environ, {"A2A_EXCHANGE_URL": "http://test:3000"}, clear=True):
        client = get_exchange_client(api_key="")
        assert client.api_key is None or client.api_key == ""


def test_get_exchange_client_override_api_key() -> None:
    """api_key override works for get_exchange_client."""
    with patch.dict(
        os.environ,
        {"A2A_EXCHANGE_URL": "http://test:3000", "A2A_API_KEY": "ate_default"},
        clear=True,
    ):
        client = get_exchange_client(api_key="ate_override")
        assert client.api_key == "ate_override"
