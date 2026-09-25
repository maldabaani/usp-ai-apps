# DevCrew sandbox: Temurin 21 + Maven 3.9. Build from devcrew/sandbox:
#   docker build -f java/Dockerfile -t devcrew-sandbox-java .
ARG MAVEN_IMAGE=maven:3.9-eclipse-temurin-21
FROM ${MAVEN_IMAGE}

# /cache/m2 is mounted from the shared `devcrew-m2` volume (dependency cache, not agent memory).
ENV HOME=/home/sandbox \
    MAVEN_OPTS="-Dmaven.repo.local=/cache/m2/repository -Duser.home=/home/sandbox"

RUN mkdir -p /cache/m2/repository /home/sandbox /workspace \
    && chmod -R a+rwX /cache \
    && chmod 1777 /home/sandbox /workspace

WORKDIR /workspace
ENTRYPOINT []
CMD ["sleep", "infinity"]
