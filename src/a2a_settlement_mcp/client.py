"""Thin wrapper around SettlementExchangeClient for the MCP server."""

from __future__ import annotations

from a2a_settlement.client import SettlementExchangeClient

from .config import get_api_key, get_exchange_url


def get_exchange_client(*, api_key: str | None = None) -> SettlementExchangeClient:
    """Build a SettlementExchangeClient from config.

    Args:
        api_key: Override the configured API key. Use None for public endpoints
            (register, directory, get_account). Use config default for auth-required ops.
    """
    base_url = get_exchange_url()
    key = api_key if api_key is not None else get_api_key()
    return SettlementExchangeClient(base_url=base_url, api_key=key or None)
