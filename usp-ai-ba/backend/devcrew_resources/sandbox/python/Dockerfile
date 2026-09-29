# DevCrew sandbox: Python 3.12 + pytest. Build from devcrew/sandbox:
#   docker build -f python/Dockerfile -t devcrew-sandbox-python .
ARG PYTHON_IMAGE=python:3.12-slim
ARG GATE_TOOLS_IMAGE=devcrew-sandbox-gate-tools:latest
FROM ${GATE_TOOLS_IMAGE} AS gatetools
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

# Quality-gate tools (gitleaks, osv-scanner) from the gate-tools image (see gatetools/).
COPY --from=gatetools /gate-tools/ /tmp/gate-tools/
RUN tar -xzf /tmp/gate-tools/gitleaks.tar.gz -C /usr/local/bin gitleaks \
    && install -m 0755 /tmp/gate-tools/osv-scanner /usr/local/bin/osv-scanner \
    && rm -rf /tmp/gate-tools

WORKDIR /workspace
CMD ["sleep", "infinity"]
