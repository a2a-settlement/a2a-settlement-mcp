"""Tests that the documented launch commands actually start a serving MCP server."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}


def _handshake(argv: list[str]) -> dict:
    """Send an initialize request over stdio and return the parsed JSON-RPC response."""
    env = {**os.environ, "A2A_MCP_TRANSPORT": "stdio", "A2A_EXCHANGE_URL": "http://localhost:3000"}
    proc = subprocess.run(
        argv,
        input=json.dumps(INITIALIZE) + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, f"server produced no stdio response (stderr: {proc.stderr[-2000:]})"
    return json.loads(lines[0])


def test_module_invocation_serves_stdio() -> None:
    """`python -m a2a_settlement_mcp` (the config documented for MCP clients) responds."""
    response = _handshake([sys.executable, "-m", "a2a_settlement_mcp"])
    assert response["result"]["serverInfo"]["name"] == "a2a-settlement"


def test_console_script_serves_stdio() -> None:
    """The `a2a-settlement-mcp` console script used by the Dockerfile responds."""
    script = shutil.which("a2a-settlement-mcp") or os.path.join(
        os.path.dirname(sys.executable), "a2a-settlement-mcp"
    )
    if not os.path.exists(script):
        pytest.skip("console script not installed in this environment")
    response = _handshake([script])
    assert response["result"]["serverInfo"]["name"] == "a2a-settlement"
