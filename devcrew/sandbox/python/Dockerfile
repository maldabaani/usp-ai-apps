# DevCrew sandbox: Python 3.12 + pytest. Build from devcrew/sandbox:
#   docker build -f python/Dockerfile -t devcrew-sandbox-python .
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

# /deps/venv is re-mounted as a per-run volume (initialized from this image), so dependencies
# installed in the network-enabled install step are visible to every offline command of the run.
ENV VIRTUAL_ENV=/deps/venv \
    PATH=/deps/venv/bin:$PATH \
    HOME=/home/sandbox \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m venv /deps/venv \
    && pip install "pytest>=8.3" "httpx>=0.27" "ruff>=0.6" \
    && mkdir -p /home/sandbox /workspace \
    && chmod -R a+rwX /deps \
    && chmod 1777 /home/sandbox /workspace

WORKDIR /workspace
CMD ["sleep", "infinity"]
