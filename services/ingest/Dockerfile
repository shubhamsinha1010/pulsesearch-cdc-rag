# Build context is the repository root so we can install the shared kernel.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the shared kernel first for better layer caching.
COPY services/common /opt/common
RUN pip install /opt/common

COPY services/ingest/requirements.txt .
RUN pip install -r requirements.txt

COPY services/ingest/app ./app

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["python", "-m", "app.main"]
