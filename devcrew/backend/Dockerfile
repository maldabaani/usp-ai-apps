FROM python:3.12-slim

# git: worktrees/branches for runs. No compilers or LLM runtimes here: Ollama runs on the host,
# generated code only ever runs inside the sandbox containers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY app/__init__.py app/__init__.py
RUN pip install --no-cache-dir -e .

COPY . .

EXPOSE 8080
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8080"]
