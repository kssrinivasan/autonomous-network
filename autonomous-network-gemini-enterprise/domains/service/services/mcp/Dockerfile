FROM python:3.13-slim

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

# Copy dependency files
COPY domains/transport/services/mcp/pyproject.toml ./

# Copy source code
COPY domains/transport/services/mcp/src/ ./

# Install dependencies using uv sync
RUN uv sync

# Environment
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# Run MCP server through uv
CMD ["uv", "run", "python", "main.py"]
