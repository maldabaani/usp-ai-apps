# DevCrew sandbox: Node 20 + Angular CLI + Chromium. Build from devcrew/sandbox:
#   docker build -f node/Dockerfile -t devcrew-sandbox-node .
ARG NODE_IMAGE=node:20-bookworm-slim
FROM ${NODE_IMAGE}

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG ANGULAR_CLI_VERSION=19
RUN npm install -g "@angular/cli@${ANGULAR_CLI_VERSION}" && npm cache clean --force

COPY common/chromium-no-sandbox /usr/local/bin/chromium-no-sandbox

# node_modules is a per-run volume; /cache/npm is the shared `devcrew-npm-cache` volume.
ENV HOME=/home/sandbox \
    CHROME_BIN=/usr/local/bin/chromium-no-sandbox \
    npm_config_cache=/cache/npm \
    npm_config_update_notifier=false \
    NG_CLI_ANALYTICS=false

RUN mkdir -p /cache/npm /home/sandbox /workspace \
    && chmod -R a+rwX /cache \
    && chmod 1777 /home/sandbox /workspace

WORKDIR /workspace
CMD ["sleep", "infinity"]
