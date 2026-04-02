# ---- Stage 1: Builder ----
FROM python:3.14-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install Poetry
RUN pip install poetry && \
    poetry config virtualenvs.in-project true

WORKDIR /app

# Copy dependency manifests and README (required by Poetry) first (layer caching)
COPY pyproject.toml poetry.lock README.md ./

# Install only main dependencies (no dev)
RUN poetry install --only main --no-root --no-interaction

# Copy source and install the waverider package itself
COPY src/ src/
RUN poetry install --only main --no-interaction


# ---- Stage 2: Runtime ----
FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user
RUN groupadd --gid 1000 waverider && \
    useradd --uid 1000 --gid waverider --create-home waverider

WORKDIR /app

# Copy the virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code — data/ is mounted at runtime via docker-compose volume
COPY src/ src/
COPY scripts/ scripts/
COPY indices/ indices/

# Ensure the venv is on PATH
ENV PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/.venv"

# Default env vars — override via docker-compose or .env
ENV NEO4J_URI="bolt://neo4j:7687" \
    NEO4J_USER="neo4j" \
    OLLAMA_HOST="http://host.docker.internal:11434" \
    MCP_TRANSPORT="sse" \
    MCP_HOST="0.0.0.0" \
    MCP_PORT="8000"

EXPOSE 8000

# Ensure data dir exists for volume mount, switch to non-root user
RUN mkdir -p /app/data && chown -R waverider:waverider /app
USER waverider

ENTRYPOINT ["python", "-m", "waverider.mcp_server"]
