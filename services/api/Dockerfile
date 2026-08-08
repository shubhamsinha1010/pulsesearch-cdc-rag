# Build context is the repository root so we can install the shared kernel.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends librdkafka1 \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch to keep the image lean and free of CUDA wheels.
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.2"

COPY services/common /opt/common
RUN pip install /opt/common

COPY services/api/requirements.txt .
RUN pip install -r requirements.txt

# Bake the query-embedding model in (must match the worker's model).
ARG EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')"

COPY services/api/app ./app

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /opt/hf
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
