# syntax=docker/dockerfile:1
# Cash-room video check service. GPU-ready base for RTX 50xx (Blackwell, sm_120): CUDA 12.8 + cuDNN.
# Build/run: docker compose up -d --build   (see README, section "Развёртывание")
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /uvx /bin/

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root user with uid/gid 1000 (replaces the stock `ubuntu` user) so bind-mounted ./data and ./models
# stay owned by the usual first host user.
RUN userdel -r ubuntu 2>/dev/null || true \
 && groupadd -g 1000 app && useradd -u 1000 -g app -m -s /bin/bash app \
 && mkdir -p /app/data /models /opt/uv-python \
 && chown -R app:app /app /models /opt/uv-python

ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1

USER app
WORKDIR /app

# Dependency layer: rebuilt only when pyproject.toml / uv.lock / .python-version change.
# Python 3.12 is downloaded by uv into /opt/uv-python.
COPY --chown=app:app pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/home/app/.cache/uv,uid=1000,gid=1000 \
    uv sync --frozen --no-dev --inexact --no-install-project

COPY --chown=app:app app ./app

# Defaults for every config var (app/config.py). API_KEY is deliberately not baked in: set it in .env.
ENV DATA_DIR=/app/data \
    JOB_TIMEOUT_SEC=270 \
    JOB_HARD_CAP_SEC=280 \
    MAX_UPLOAD_MB=200 \
    ANALYZER=app.analyzers.stub:StubAnalyzer \
    STUB_DEBUG=0 \
    MODELS_DIR=/models \
    HF_HOME=/models/hf \
    TORCH_HOME=/models/torch

VOLUME ["/app/data", "/models"]
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# Single process on purpose: the job queue and worker thread live in-process.
# --timeout-keep-alive 75: above the reverse proxies' 60 s idle keep-alive, so the proxy always closes an idle
# connection first (otherwise a request can race uvicorn's default 5 s close and get a 502).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "75"]
