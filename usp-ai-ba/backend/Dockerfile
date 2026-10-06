FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV CHROMA_PERSIST_PATH=/data/chroma_db \
    JOBS_DIR=/data/jobs \
    UPLOADS_DIR=/data/uploads \
    EXPORTS_DIR=/data/exports \
    CODEMIND_DEFAULT_OUTPUT_DIRECTORY=/data/codemind-output \
    PYTHONDONTWRITEBYTECODE=1

# Non-root user, in a group matching the host's docker.sock GID so it can still spawn
# DevCrew's sandboxed agent containers via the socket docker-compose.yml bind-mounts in.
# 999 is the common docker group GID on Debian/Ubuntu; if your host differs, rebuild with
# --build-arg DOCKER_GID=$(stat -c '%g' /var/run/docker.sock) (see RUNNING.md's Day-0
# checklist -- this is the one place "non-root" and "needs the Docker socket" are in tension).
ARG DOCKER_GID=999
RUN groupadd -g ${DOCKER_GID} docker-sock && useradd -m -u 1000 -G docker-sock appuser
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=8s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)" || exit 1
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
