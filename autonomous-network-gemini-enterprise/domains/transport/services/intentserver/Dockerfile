FROM python:3.13-slim

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

# Copy dependency files
COPY domains/transport/services/intentserver/pyproject.toml ./
COPY domains/transport/services/intentserver/uv.lock ./
COPY domains/transport/services/intentserver/Readme.md ./

# Copy source code
COPY domains/transport/services/intentserver/src/ ./

# Install dependencies using uv sync
RUN uv sync --frozen

# Environment
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# Run FastAPI server through uv
CMD ["uv", "run", "python", "main.py"]
