# Running the Apps

This repo has two independent apps, plus a unified "umbrella" deployment that
serves both under one origin behind an nginx gateway (see the last section).
For separate local dev, each app has its own section below.

## code-mind-app (CodeMind — Java/Spring Boot)

Scans a repo and extracts business logic via Claude; browse results / ask questions in a web UI.

**Requirements:** Java 17+, `ANTHROPIC_API_KEY` (Ollama optional for local model/embeddings).

```bash
cd code-mind-app
export ANTHROPIC_API_KEY=sk-ant-...
./mvnw spring-boot:run
```

Open **http://localhost:8085/ui/jobs**, enter a repository path, click **Start Extraction**.

Run tests: `./mvnw test`

---

## usp-ai-ba (StoryForge AI — Python/FastAPI + Angular)

Turns an SDD PDF into an Epic → User Story → Dev/Unit-Test Task hierarchy (Claude + RAG over ChromaDB), exported as `.docx`/ADO/Notion.

**Requirements:** Python 3.11+, Node.js 18+, Ollama (embedding model), `ANTHROPIC_API_KEY`.

### 1. Backend

```bash
cd usp-ai-ba/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env and fill in ANTHROPIC_API_KEY etc.

ollama pull nomic-embed-text
ollama serve                # if not already running

uvicorn api.main:app --reload --port 8000
```

### 2. Frontend (separate terminal)

```bash
cd usp-ai-ba/frontend/storyforge-ui
npm install
npm start                   # ng serve -> http://localhost:4200
```

### 3. One-time ingestion (before first assessment)

```bash
curl -X POST http://localhost:8000/api/ingest/pdfs -H "Content-Type: application/json" \
  -d '{"folder_path": "/path/to/user-manuals"}'

curl -X POST http://localhost:8000/api/ingest/code -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/monorepo"}'
```

Then open **http://localhost:4200** and submit an SDD PDF via the **Assess** page.

> ⚠️ `backend/.env.example` currently has a real-looking `NOTION_API_KEY` value checked in — rotate that token and treat it as compromised; don't reuse it.

---

## Unified deployment (both apps under one origin, with tabs)

`storyforge-ui` now includes a tab bar ("AI BA" / "CodeMind") at the top of the app. The
"CodeMind" tab embeds CodeMind's existing UI in an iframe. In production this is served
through a single nginx gateway so both apps share one origin:

- `/` → Angular shell (StoryForge AI, tab bar, all existing pages)
- `/api/*` → StoryForge FastAPI backend
- `/codemind-app/*` → CodeMind (Spring Boot, running with `server.servlet.context-path=/codemind-app`)

Run everything with Docker Compose from the repo root:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

Open **http://localhost/** — the AI BA tab is the existing StoryForge flow, the CodeMind
tab embeds CodeMind's job UI. Only the `gateway` service publishes a host port (`80`);
`codemind` and `storyforge-backend` are reachable only inside the compose network.

Ollama (used by both apps for embeddings) is expected to run on the host, not in a
container. `OLLAMA_BASE_URL` defaults to `http://host.docker.internal:11434`, mapped via
`extra_hosts: host-gateway` in `docker-compose.yml` so it resolves on Linux too — make
sure `ollama serve` is running on the host before starting the compose stack if any
Ollama-backed feature is needed.

Data persistence: CodeMind's job store/output and StoryForge's ChromaDB/jobs/uploads/exports
are all backed by named Docker volumes (`codemind-jobs`, `codemind-output`, `storyforge-data`),
so they survive `docker compose down` (but not `docker compose down -v`).
