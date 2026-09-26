# DevCrew sandbox for multi-stack projects: Python 3.12 + Temurin 21/Maven + Node 20/Angular CLI
# + Chromium in one image. Build from devcrew/sandbox:
#   docker build -f mixed/Dockerfile -t devcrew-sandbox-mixed .
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG MAVEN_IMAGE=maven:3.9-eclipse-temurin-21
ARG NODE_IMAGE=node:20-bookworm-slim

ARG GATE_TOOLS_IMAGE=devcrew-sandbox-gate-tools:latest
FROM ${GATE_TOOLS_IMAGE} AS gatetools
FROM ${MAVEN_IMAGE} AS java
FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_IMAGE}
COPY --from=java /opt/java/openjdk /opt/java/openjdk
COPY --from=java /usr/share/maven /usr/share/maven
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN ln -s /usr/share/maven/bin/mvn /usr/local/bin/mvn \
    && ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG ANGULAR_CLI_VERSION=19
RUN npm install -g "@angular/cli@${ANGULAR_CLI_VERSION}" && npm cache clean --force

COPY common/chromium-no-sandbox /usr/local/bin/chromium-no-sandbox

ENV JAVA_HOME=/opt/java/openjdk \
    MAVEN_HOME=/usr/share/maven \
    VIRTUAL_ENV=/deps/venv \
    PATH=/deps/venv/bin:/opt/java/openjdk/bin:$PATH \
    HOME=/home/sandbox \
    MAVEN_OPTS="-Dmaven.repo.local=/cache/m2/repository -Duser.home=/home/sandbox" \
    CHROME_BIN=/usr/local/bin/chromium-no-sandbox \
    npm_config_cache=/cache/npm \
    npm_config_update_notifier=false \
    NG_CLI_ANALYTICS=false \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m venv /deps/venv \
    && /deps/venv/bin/pip install "pytest>=8.3" "httpx>=0.27" "ruff>=0.6" \
    && mkdir -p /cache/m2/repository /cache/npm /home/sandbox /workspace \
    && chmod -R a+rwX /deps /cache \
    && chmod 1777 /home/sandbox /workspace

# Quality-gate tools (gitleaks, osv-scanner) from the gate-tools image (see gatetools/).
COPY --from=gatetools /gate-tools/ /tmp/gate-tools/
RUN tar -xzf /tmp/gate-tools/gitleaks.tar.gz -C /usr/local/bin gitleaks \
    && install -m 0755 /tmp/gate-tools/osv-scanner /usr/local/bin/osv-scanner \
    && rm -rf /tmp/gate-tools

WORKDIR /workspace
CMD ["sleep", "infinity"]
