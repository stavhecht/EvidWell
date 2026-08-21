# ---------- build stage ----------
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build toolchain needed by some C-extension wheels (argon2-cffi, asyncpg).
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# Install production deps into a virtual-env so we can copy the whole tree later
# without dragging build tools into the final image.
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools && \
    /opt/venv/bin/pip install --no-cache-dir .

COPY app/ app/
COPY migrations/ migrations/

# ---------- runtime stage ----------
FROM python:3.12-slim

WORKDIR /app

# asyncpg needs libpq at runtime.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid 1000 evidwell && \
    useradd  --uid 1000 --gid evidwell --no-create-home evidwell

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/app ./app
COPY --from=builder /build/migrations ./migrations

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    # Sensible production defaults (override via docker-compose / env file).
    DEBUG=false

EXPOSE 8000

USER evidwell

# Uvicorn with a single worker by default; scale horizontally with replicas
# rather than pre-fork workers — each worker holds its own asyncpg pool, so
# fewer-but-bigger is cheaper on PG connections.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
