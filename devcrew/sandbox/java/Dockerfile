# DevCrew sandbox: Temurin 21 + Maven 3.9. Build from devcrew/sandbox:
#   docker build -f java/Dockerfile -t devcrew-sandbox-java .
ARG MAVEN_IMAGE=maven:3.9-eclipse-temurin-21
ARG GATE_TOOLS_IMAGE=devcrew-sandbox-gate-tools:latest
FROM ${GATE_TOOLS_IMAGE} AS gatetools
FROM ${MAVEN_IMAGE}

# /cache/m2 is mounted from the shared `devcrew-m2` volume (dependency cache, not agent memory).
ENV HOME=/home/sandbox \
    MAVEN_OPTS="-Dmaven.repo.local=/cache/m2/repository -Duser.home=/home/sandbox"

RUN mkdir -p /cache/m2/repository /home/sandbox /workspace \
    && chmod -R a+rwX /cache \
    && chmod 1777 /home/sandbox /workspace

# Quality-gate tools (gitleaks, osv-scanner) from the gate-tools image (see gatetools/).
COPY --from=gatetools /gate-tools/ /tmp/gate-tools/
RUN tar -xzf /tmp/gate-tools/gitleaks.tar.gz -C /usr/local/bin gitleaks \
    && install -m 0755 /tmp/gate-tools/osv-scanner /usr/local/bin/osv-scanner \
    && rm -rf /tmp/gate-tools

WORKDIR /workspace
ENTRYPOINT []
CMD ["sleep", "infinity"]
