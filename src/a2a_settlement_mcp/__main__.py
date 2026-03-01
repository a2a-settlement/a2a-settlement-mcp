"""Entry point for the A2A Settlement MCP server."""

from __future__ import annotations

from .config import get_transport
from .server import mcp


def main() -> None:
    """Run the MCP server with configured transport."""
    transport = get_transport()
    if transport == "sse":
        mcp.run(transport="sse", mount_path="/")
    else:
        mcp.run(transport="stdio")
