# Cursor Configuration

Add the A2A Settlement MCP server to Cursor's MCP config.

**Config file:** `.cursor/mcp.json` in your project (or user config)

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
- Exchange must be running at `A2A_EXCHANGE_URL`
- `A2A_API_KEY` is required for authenticated operations
