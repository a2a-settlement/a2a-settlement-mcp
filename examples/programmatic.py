"""Example: Connect to the A2A Settlement MCP server programmatically from Python."""

from __future__ import annotations

import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    """List tools and call settlement_check_reputation."""
    params = StdioServerParameters(
        command="python",
        args=["-m", "a2a_settlement_mcp"],
        env={
            "A2A_EXCHANGE_URL": os.environ.get("A2A_EXCHANGE_URL", "http://localhost:3000"),
            "A2A_API_KEY": os.environ.get("A2A_API_KEY", ""),
        },
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # List available tools
            tools = await session.list_tools()
            print("Available tools:", [t.name for t in tools.tools])

            # Call a tool (no auth required)
            result = await session.call_tool(
                "settlement_list_agents",
                arguments={"limit": 5},
            )
            print("settlement_list_agents result:", result)


if __name__ == "__main__":
    asyncio.run(main())
