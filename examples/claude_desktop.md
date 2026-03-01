# Claude Desktop Configuration

Add the A2A Settlement MCP server to your Claude Desktop config.

**Config file location:**
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

**Configuration:**

```json
{
  "mcpServers": {
    "a2a-settlement": {
      "command": "python",
      "args": ["-m", "a2a_settlement_mcp"],
      "env": {
        "A2A_EXCHANGE_URL": "http://localhost:3000",
        "A2A_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

If you installed via pip:

```json
{
  "mcpServers": {
    "a2a-settlement": {
      "command": "a2a-settlement-mcp",
      "args": [],
      "env": {
        "A2A_EXCHANGE_URL": "http://localhost:3000",
        "A2A_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

**Requirements:**
- Exchange must be running at `A2A_EXCHANGE_URL` (e.g. `docker compose up` in the a2a-settlement repo)
- `A2A_API_KEY` is required for balance, escrow, fund, and history operations. Register an agent first to get a key.
