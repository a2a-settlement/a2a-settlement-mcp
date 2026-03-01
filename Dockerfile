FROM python:3.11-slim

WORKDIR /app

# Install from source (or use pip install a2a-settlement-mcp when published)
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

# Default to SSE transport for containerized/remote access
ENV A2A_MCP_TRANSPORT=sse
ENV A2A_MCP_PORT=3200

EXPOSE 3200

ENTRYPOINT ["a2a-settlement-mcp"]
