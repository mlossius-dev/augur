# Augur application container
FROM python:3.12-slim-bookworm

WORKDIR /app

# System deps for asyncpg compilation and health checks
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies before copying source so layer is cached
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[dev]"

# Copy application source
COPY src/ src/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "augur.main:app", "--host", "0.0.0.0", "--port", "8000"]
