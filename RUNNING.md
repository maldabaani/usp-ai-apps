# Running the Apps

This repo has two independent apps. Each has its own section below — run them separately (different terminals/ports, no shared build).

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
curl -X POST http://localhost:8000/ingest/pdfs -H "Content-Type: application/json" \
  -d '{"folder_path": "/path/to/user-manuals"}'

curl -X POST http://localhost:8000/ingest/code -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/monorepo"}'
```

Then open **http://localhost:4200** and submit an SDD PDF via the **Assess** page.

> ⚠️ `backend/.env.example` currently has a real-looking `NOTION_API_KEY` value checked in — rotate that token and treat it as compromised; don't reuse it.
