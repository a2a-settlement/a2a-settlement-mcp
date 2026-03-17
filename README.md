# a2a-settlement-mcp

[![MIT License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Protocol-green.svg)](https://modelcontextprotocol.io/)

MCP server that gives any AI agent access to the A2A Settlement Exchange. Connect Claude, Cursor, LangGraph, or custom agents to escrow-based settlement without framework-specific integration code.

## Architecture

```
Any MCP Client                        A2A-SE Exchange
(Claude, Cursor, LangGraph,           (http://localhost:3000)
 custom agents, etc.)                        │
        │                                    │
        │  MCP Protocol (stdio/SSE)          │
        ▼                                    │
┌──────────────────┐    HTTP/REST    ┌───────┴────────┐
│  MCP Server      │───────────────►│   Exchange     │
│  (this project)  │                │   API          │
│                  │◄───────────────│                 │
│  Tools:          │                └─────────────────┘
│  - register      │
│  - fund          │
│  - balance       │
│  - escrow        │
│  - dispute       │
│  - reputation    │
│  - history       │
└──────────────────┘
```

## What is MCP?

The [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) is a standard for connecting AI applications to external data and tools. An MCP server exposes **tools** that agents can discover and call. This server exposes A2A Settlement Exchange operations as MCP tools—no SDK installation in the agent's runtime, no framework-specific adapters.

## Available Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `settlement_register_agent` | Register a new agent on the exchange | `name`, `developer_id`, `developer_name`, `contact_email`, `description`, `skills` |
| `settlement_fund_agent` | Add tokens to an agent's balance | `agent_id`, `amount` |
| `settlement_check_balance` | Check an agent's token balance | `agent_id` |
| `settlement_list_agents` | List all registered agents | `skill`, `limit` |
| `settlement_create_escrow` | Create an escrow hold between agents | `provider_id`, `amount`, `task_description`, `task_id`, `task_type`, `ttl_minutes` |
| `settlement_get_escrow` | Check escrow status | `escrow_id` |
| `settlement_release_escrow` | Release escrowed funds to provider | `escrow_id` |
| `settlement_refund_escrow` | Refund escrowed funds to requester | `escrow_id`, `reason` |
| `settlement_file_dispute` | File a dispute on an escrow | `escrow_id`, `reason` |
| `settlement_check_reputation` | Check an agent's reputation score | `agent_id` |
| `settlement_get_history` | Get transaction history for an agent | `agent_id`, `limit`, `offset` |

**Note:** Single-tenant mode. The server uses one `A2A_API_KEY`. For `check_balance`, `fund_agent`, `create_escrow`, `release_escrow`, `refund_escrow`, `file_dispute`, and `get_history`, the `agent_id` must match the configured account.

## Install

```bash
pip install -e .
```

Or from git (includes a2a-settlement SDK from upstream):

```bash
pip install git+https://github.com/a2a-settlement/a2a-settlement-mcp.git
```

## Quick Start

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "a2a-settlement": {
      "command": "python",
      "args": ["-m", "a2a_settlement_mcp"],
      "env": {
        "A2A_EXCHANGE_URL": "http://localhost:3000",
        "A2A_API_KEY": "your-api-key"
      }
    }
  }
}
```

See [examples/claude_desktop.md](examples/claude_desktop.md) for details.

### Cursor

Add to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "a2a-settlement": {
      "command": "python",
      "args": ["-m", "a2a_settlement_mcp"],
      "env": {
        "A2A_EXCHANGE_URL": "http://localhost:3000",
        "A2A_API_KEY": "your-api-key"
      }
    }
  }
}
```

See [examples/cursor_config.md](examples/cursor_config.md) for details.

### Programmatic (Python)

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(
    command="python",
    args=["-m", "a2a_settlement_mcp"],
    env={"A2A_EXCHANGE_URL": "http://localhost:3000", "A2A_API_KEY": "your-key"},
)

async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("settlement_check_balance", {"agent_id": "your-account-id"})
```

See [examples/programmatic.py](examples/programmatic.py) for a full example.

## Configuration

| Variable | Default | Description |
|---------|---------|-------------|
| `A2A_EXCHANGE_URL` | `http://localhost:3000` | Exchange API base URL (no `/v1`) |
| `A2A_API_KEY` | (empty) | API key for authenticated operations |
| `A2A_MCP_TRANSPORT` | `stdio` | `stdio` or `sse` |
| `A2A_MCP_PORT` | `3200` | Port for SSE transport |

Copy [.env.example](.env.example) and adjust as needed.

## Docker

```bash
docker build -t a2a-settlement-mcp .
docker run -e A2A_EXCHANGE_URL=http://host.docker.internal:3000 \
           -e A2A_API_KEY=your-key \
           -p 3200:3200 \
           a2a-settlement-mcp
```

The container defaults to SSE transport on port 3200. Override with env vars.

## SSE Transport (Remote Access)

For HTTP/remote access, run with SSE:

```bash
A2A_MCP_TRANSPORT=sse A2A_MCP_PORT=3200 a2a-settlement-mcp
```

Clients connect to `http://localhost:3200/sse` (or the configured host/port).

## Testing

```bash
pytest tests/ -v
```

## Related Projects

| Project | Description |
|---------|-------------|
| [a2a-settlement](https://github.com/a2a-settlement/a2a-settlement) | Core exchange + SDK |
| [a2a-settlement-auth](https://github.com/a2a-settlement/a2a-settlement-auth) | OAuth economic authorization |
| [a2a-settlement-mediator](https://github.com/a2a-settlement/a2a-settlement-mediator) | AI-powered dispute resolution |
| [a2a-settlement-dashboard](https://github.com/a2a-settlement/a2a-settlement-dashboard) | Human oversight dashboard |
| [settlebridge-ai](https://github.com/a2a-settlement/settlebridge-ai) | SettleBridge Gateway — trust/policy enforcement |
| [mcp-trust-gateway](https://github.com/a2a-settlement/mcp-trust-gateway) | MCP trust layer — complementary: evaluates trust on MCP tool invocations |
| [otel-agent-provenance](https://github.com/a2a-settlement/otel-agent-provenance) | OpenTelemetry provenance conventions |
| [a2a-federation-rfc](https://github.com/a2a-settlement/a2a-federation-rfc) | Federation protocol specification |
| [langgraph-a2a-settlement](https://github.com/a2a-settlement/langgraph-a2a-settlement) | Native LangGraph graph nodes — use for LangGraph workflows |
| [crewai-a2a-settlement](https://github.com/a2a-settlement/crewai-a2a-settlement) | Native CrewAI wrappers |
| [litellm-a2a-settlement](https://github.com/a2a-settlement/litellm-a2a-settlement) | LiteLLM callback hooks |
| [adk-a2a-settlement](https://github.com/a2a-settlement/adk-a2a-settlement) | Google ADK integration |

**When to use MCP vs framework integrations:** MCP works with any MCP client (Claude Desktop, Cursor, custom). For LangGraph, CrewAI, LiteLLM, or ADK, the framework-specific packages offer native patterns (graph nodes, wrappers, callbacks). Use MCP when your agent runtime is framework-agnostic or MCP-only.

- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — Official MCP implementation
- [Model Context Protocol](https://modelcontextprotocol.io/) — Protocol specification

## License

MIT. See [LICENSE](LICENSE).
