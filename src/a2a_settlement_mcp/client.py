"""Thin wrapper around SettlementExchangeClient for the MCP server."""

from __future__ import annotations

from a2a_settlement.client import SettlementExchangeClient

from .config import get_api_key, get_exchange_url


def get_exchange_client(*, api_key: str | None = None) -> SettlementExchangeClient:
    """Build a SettlementExchangeClient from config.

    Args:
        api_key: Override the configured API key. Pass ``""`` for public endpoints
            (register, directory, get_account). Omit it for auth-required ops.
            In hosted mode the omitted key is the caller's bearer token, never
            an environment variable.
    """
    from .hosted import caller_exchange_key, is_hosted
    from .settlebridge_client import remember_secret

    base_url = get_exchange_url()
    if api_key is not None:
        key = api_key
    elif is_hosted():
        key = caller_exchange_key()
    else:
        key = get_api_key()
    if key:
        remember_secret(key)
    return SettlementExchangeClient(base_url=base_url, api_key=key or None)
