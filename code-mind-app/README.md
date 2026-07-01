# CodeMind

Async Spring AI agent that extracts and documents business logic from multi-language codebases at scale.

CodeMind scans a repository, sends each source file to Claude (or a local Ollama model) with a structured prompt, and stores the extracted rules, summaries, and dependencies as JSON. A built-in web UI lets you watch jobs run in real time, browse extracted results, and ask natural-language questions about the code once extraction is complete.

## Features

- **Multi-language extraction** — JavaScript, TypeScript, Python, Java, Kotlin, Go, C#, Ruby, Rust, PHP
- **Two execution modes** — SYNC (parallel Spring AI calls) or BATCH (Anthropic Message Batches API, flat 50% token discount)
- **Hybrid agent setup** — Claude as the primary agent; optionally add a local Ollama model as a second agent with automatic round-robin load balancing
- **Large-file chunking** — files exceeding 300 KB are split at line boundaries into numbered part chunks instead of being skipped
- **Incremental re-runs** — a manifest file tracks already-processed files; re-running only processes new or changed files
- **Cancel & delete** — stop an in-flight job gracefully or delete a completed job along with all its output files
- **Export** — download all successful extractions for a job as a single merged JSON file
- **Ask Agent (per-job)** — natural-language Q&A over a single job's extracted results, streamed via SSE
- **Ask All (cross-job)** — query across all completed jobs simultaneously from one chat interface
- **Vector search** — real semantic retrieval via an Ollama embedding model (optional); falls back to keyword-overlap if unavailable
- **Directory watcher** — drop a path into a watched folder and a job starts automatically
- **Live progress UI** — stepper, stats cards, real-time file feed, clickable viewer modal, failed-file panel, cancel/export buttons
- **Persistent job store** — jobs survive restarts; terminal states are reloaded as read-only snapshots

## Requirements

- Java 17+
- Maven wrapper included (`./mvnw`)
- `ANTHROPIC_API_KEY` environment variable
- (Optional) [Ollama](https://ollama.com) for the local agent, local Q&A, or vector embeddings

## Quick start

```bash
git clone <repo-url>
cd js-logic-extractor

export ANTHROPIC_API_KEY=sk-ant-...
./mvnw spring-boot:run
```

Open `http://localhost:8085/ui/jobs`, enter a repository path, and click **Start Extraction**.

## How it works

```
POST /api/v1/extraction-jobs  --(202, jobId)-->  caller
        |
        v
JobRegistry.register()   -- resolves output dir, concurrency, execution mode
        |
        v
JsRepositoryProcessingOrchestrator.run(job)        ← dispatched off the HTTP thread
        |
        +-- RepositoryScannerService.scan(root)    -- walks the tree, filters by extension /
        |                                              excluded dirs / max size. Files over
        |                                              max-file-size-bytes are split by
        |                                              LargeFileChunker into part-NNNN SourceFiles
        |
        +-- NonSubstantiveFileFilter               -- skips .d.ts, test/spec, barrel files
        |                                              before any model call; recorded as skipped
        |
        +-- job.executionMode() branches:
        |
        |   SYNC (default) -- bounded thread pool (maxConcurrency), one CompletableFuture per file
        |   |   AgentSelector.next()              -- round-robins across LogicExtractionAgent beans
        |   |   agent.extract(file)               -- prompt → model → parse usage
        |   |   ExtractionResultWriter.write()    -- one JSON file per source file
        |   |   CompletableFuture.allOf().join()
        |   |
        |   BATCH -- BatchExtractionService.runBatch(job, files)
        |       Groups files by language (prompt-cache efficiency), submits chunks of up to
        |       10 000 requests / 200 MB, polls every 30 s, writes results same as SYNC
        |
        v
phase → COMPLETED | CANCELLED | FAILED
```

## Execution modes

### SYNC (default)

Per-file Spring AI `ChatClient` calls through a bounded thread pool. Lower latency per
file, normal token pricing. Best for small/interactive runs and incremental re-runs.

```bash
JSPROCESSOR_EXECUTION_MODE=SYNC ./mvnw spring-boot:run
```

### BATCH

Uses the [Anthropic Message Batches API](https://docs.anthropic.com/en/api/creating-message-batches).
Files are grouped by language for prompt-cache efficiency, submitted in chunks (≤10 000 requests /
≤200 MB per chunk), and polled every 30 s up to a 26 h timeout. The flat 50% token discount makes
BATCH the right choice for large repositories (1 000–100 000+ files).

```bash
JSPROCESSOR_EXECUTION_MODE=BATCH ./mvnw spring-boot:run
```

BATCH mode bypasses Spring AI entirely and talks directly to Anthropic via the
`anthropic-java-client-okhttp` SDK (Spring AI 1.1.7 has no Batches API support).

## Splitting oversized files

Files over `jsprocessor.max-file-size-bytes` (300 KB default) are split at line
boundaries into `<originalPath>/part-0001.<ext>`, `part-0002.<ext>`, … by `LargeFileChunker`.
Each chunk flows through the pipeline normally and lands at `output/<originalPath>/part-0001.<ext>.json`.

Cuts prefer boundaries where bracket depth (`{}/()/ []`) returns to zero and the line
isn't inside a string or block comment. A hard cap (2× `max-lines-per-chunk`) forces a
cut if no safe boundary appears. A single minified line can't be split and is sent as-is
with a warning logged.

Set `jsprocessor.chunking.enabled=false` to revert to skipping oversized files.

## Optional Ollama agent

Enable a local Ollama model alongside Claude for SYNC-mode extraction. Both agents share
files equally via round-robin scheduling.

```bash
ollama pull qwen2.5:14b

JSPROCESSOR_OLLAMA_ENABLED=true \
OLLAMA_MODEL=qwen2.5:14b \
./mvnw spring-boot:run
```

The Ollama agent uses an 8 192-token context window (`OLLAMA_NUM_CTX`) and a 1 500-token
output limit (`OLLAMA_MAX_TOKENS`). Raise `OLLAMA_NUM_CTX` for large files.

> BATCH mode has no Ollama equivalent — it always uses the Anthropic Batches API.

## Ask Agent (Q&A over extracted results)

Once a job has completed, open `/ui/jobs/{id}/ask` or stream via
`POST /api/v1/extraction-jobs/{id}/qa/stream` to ask natural-language questions.
`ExtractionQaService` retrieves relevant extracted-logic documents and feeds them to the
answer model as grounded context, returning the answer plus the source files it drew from.

**Ask All** at `/ui/ask` (or `POST /api/v1/ask/stream`) searches across all completed
jobs simultaneously.

### Retrieval tiers

1. **Vector search** — cosine similarity via an ephemeral `SimpleVectorStore` when
   `jsprocessor.embedding.enabled=true` and a running Ollama embedding model is reachable.

2. **Keyword overlap** — zero-infrastructure fallback; scores files by how many question
   tokens appear in their raw JSON. Used when embeddings are disabled or any embedding
   call fails — the endpoint never hard-fails due to embedding infrastructure being down.

Enable vector search:

```bash
ollama pull nomic-embed-text

JSPROCESSOR_EMBEDDING_ENABLED=true \
./mvnw spring-boot:run
```

Fully local pipeline (embedding + answers via Ollama, no Anthropic calls for Q&A):

```bash
JSPROCESSOR_EMBEDDING_ENABLED=true \
JSPROCESSOR_QA_MODEL=ollama \
JSPROCESSOR_QA_OLLAMA_MODEL=qwen2.5:14b \
./mvnw spring-boot:run
```

## Directory watcher

Drop any path into a watched directory and CodeMind starts a job automatically:

```bash
JSPROCESSOR_WATCH_ENABLED=true \
JSPROCESSOR_WATCH_DIRECTORY=/tmp/watch-input \
./mvnw spring-boot:run

echo "/path/to/my/repo" > /tmp/watch-input/trigger.txt
```

`InputDirectoryWatcher` monitors the directory non-recursively via `WatchService`. A
configurable quiet period (500 ms default) debounces rapid multi-event drops. Subdirectory
events are ignored — only individual files trigger jobs.

## Output format

Each source file produces a JSON file at `{outputDirectory}/{sourceRelativePath}.json`:

```json
{
  "relativePath": "src/auth/login.ts",
  "agentName": "claude-sonnet-4-5-20250929",
  "success": true,
  "skipped": false,
  "content": "{\"file\":\"src/auth/login.ts\",\"summary\":\"Handles JWT-based login...\",\"rules\":[{\"name\":\"Rate limit\",\"description\":\"...\",\"conditions\":[\"...\"],\"actions\":[\"...\"]}],\"dependencies\":[\"bcrypt\",\"jsonwebtoken\"]}",
  "errorMessage": null,
  "durationMillis": 1240,
  "promptTokens": 820,
  "completionTokens": 412
}
```

`content` is the raw model output — a JSON string with `file`, `summary`, `rules`, and `dependencies`.

The **Export** button on the progress page (or `GET /api/v1/extraction-jobs/{id}/export`)
downloads a merged JSON of all successful extractions:

```json
{
  "jobId": "...",
  "repositoryRoot": "/path/to/repo",
  "exportedAt": "2025-10-01T12:00:00Z",
  "totalExtracted": 312,
  "files": [
    { "file": "src/auth/login.ts", "summary": "...", "rules": [...], "dependencies": [...] }
  ]
}
```

## REST API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/extraction-jobs` | Start a new extraction job |
| `GET` | `/api/v1/extraction-jobs` | List all jobs |
| `GET` | `/api/v1/extraction-jobs/{id}` | Get job status + stats |
| `POST` | `/api/v1/extraction-jobs/{id}/cancel` | Request graceful cancellation |
| `DELETE` | `/api/v1/extraction-jobs/{id}` | Delete job + output files |
| `DELETE` | `/api/v1/extraction-jobs` | Clear all jobs and data |
| `GET` | `/api/v1/extraction-jobs/{id}/output-files` | Recent output files (last 50) |
| `GET` | `/api/v1/extraction-jobs/{id}/output-file?relativePath=…` | Read a single output file |
| `GET` | `/api/v1/extraction-jobs/{id}/failed-files` | Failed files with error details |
| `GET` | `/api/v1/extraction-jobs/{id}/export` | Download merged JSON export |
| `POST` | `/api/v1/extraction-jobs/{id}/qa` | One-shot Q&A (non-streaming) |
| `POST` | `/api/v1/extraction-jobs/{id}/qa/stream` | SSE streaming Q&A for one job |
| `POST` | `/api/v1/ask/stream` | SSE streaming Q&A across all completed jobs |

**Start a job:**

```bash
curl -X POST http://localhost:8085/api/v1/extraction-jobs \
  -H 'Content-Type: application/json' \
  -d '{"repositoryPath": "/path/to/repo", "maxConcurrency": 10}'
```

**BATCH mode:**

```bash
curl -X POST http://localhost:8085/api/v1/extraction-jobs \
  -H 'Content-Type: application/json' \
  -d '{"repositoryPath": "/path/to/repo", "executionMode": "BATCH"}'
```

**Ask a question (SSE stream):**

```bash
curl -N -X POST http://localhost:8085/api/v1/extraction-jobs/{id}/qa/stream \
  -H 'Content-Type: application/json' \
  -d '{"question": "How is authentication handled?"}'
```

**Ask across all completed jobs:**

```bash
curl -N -X POST http://localhost:8085/api/v1/ask/stream \
  -H 'Content-Type: application/json' \
  -d '{"question": "Where is rate limiting applied?"}'
```

## Web UI

| Page | Path | Description |
|---|---|---|
| Jobs | `/ui/jobs` | Start new jobs, list existing jobs, delete or navigate to any job |
| Progress | `/ui/jobs/{id}` | Live stepper, stats, file feed, viewer modal, failed-file panel, cancel/export |
| Ask | `/ui/jobs/{id}/ask` | Chat interface for Q&A over a single completed job |
| Ask All | `/ui/ask` | Chat interface querying across all completed jobs simultaneously |

The **file viewer modal** (click any row in the feed or failed-file panel) shows:
- **Summary** — one-paragraph description of the file's business logic
- **Business rules** — structured list with name, description, conditions, actions
- **Dependencies** — extracted import/library references
- **Meta** — extraction time, agent name, token usage

## Resilience

- Transient failures (HTTP 429/5xx) are retried with exponential backoff inside Spring AI
  (`spring.ai.retry.*`, default: 5 attempts, initial 2 s, ×2 multiplier, max 30 s).
- A failure that survives retries is recorded against that one file only — the orchestrator
  isolates failures per file, so one bad file never aborts the whole job.
- Incremental re-runs (`jsprocessor.skip-existing-results=true`, default) skip files that
  already have an output JSON — safe to resume a partial run against the same output directory.
- Cancel is soft: the orchestrator checks the cancel flag before each file; in-flight
  model calls complete normally before the job transitions to CANCELLED.

## Configuration

All settings live under `jsprocessor.*` in `application.yml` and can be overridden with
the corresponding environment variables.

| Property | Env var | Default | Notes |
|---|---|---|---|
| `included-extensions` | — | `.js,.jsx,.mjs,.cjs,.ts,.tsx,.py,.pyw,.java,.kt,.kts,.go,.cs,.rb,.rs,.php` | Comma-separated |
| `excluded-directory-names` | — | `node_modules,.git,dist,build,coverage,out,.next,.turbo,vendor,…` | Prune list |
| `max-file-size-bytes` | — | `300000` | Files above this are chunked (or skipped if chunking disabled) |
| `max-concurrent-requests` | — | `8` | SYNC thread pool size |
| `skip-existing-results` | — | `true` | Skip files that already have output JSON |
| `execution-mode` | `JSPROCESSOR_EXECUTION_MODE` | `SYNC` | `SYNC` or `BATCH` |
| `chunking.enabled` | `JSPROCESSOR_CHUNKING_ENABLED` | `true` | Split vs. skip oversized files |
| `chunking.max-lines-per-chunk` | `JSPROCESSOR_CHUNKING_MAX_LINES` | `1800` | Target lines per chunk |
| `batch.poll-interval` | — | `30s` | How often to poll BATCH status |
| `batch.poll-timeout` | — | `26h` | Max wait for a batch to complete |
| `batch.max-requests-per-batch` | — | `10000` | Requests per batch chunk |
| `batch.max-batch-bytes` | — | `200000000` | Bytes per batch chunk |
| `ollama.enabled` | `JSPROCESSOR_OLLAMA_ENABLED` | `false` | Enable local Ollama agent (SYNC only) |
| `ollama.base-url` | `OLLAMA_BASE_URL` | `http://localhost:11434` | |
| `ollama.model` | `OLLAMA_MODEL` | `qwen2.5:14b` | Must be pulled in Ollama |
| `ollama.max-tokens` | `OLLAMA_MAX_TOKENS` | `1500` | Output token limit |
| `ollama.num-ctx` | `OLLAMA_NUM_CTX` | `8192` | Context window; raise for large files |
| `qa.model` | `JSPROCESSOR_QA_MODEL` | `claude` | `claude` or `ollama` for answer generation |
| `qa.ollama-model` | `JSPROCESSOR_QA_OLLAMA_MODEL` | `qwen2.5:14b` | |
| `embedding.enabled` | `JSPROCESSOR_EMBEDDING_ENABLED` | `false` | Enable vector search |
| `embedding.base-url` | `OLLAMA_EMBEDDING_BASE_URL` | `http://localhost:11434` | |
| `embedding.model` | `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Must be pulled in Ollama |
| `watch.enabled` | `JSPROCESSOR_WATCH_ENABLED` | `false` | Enable directory watcher |
| `watch.directory` | `JSPROCESSOR_WATCH_DIRECTORY` | `./watch-input` | |
| `watch.quiet-period-millis` | `JSPROCESSOR_WATCH_QUIET_PERIOD_MILLIS` | `500` | Debounce delay |

Anthropic / server settings:

| Env var | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-5-20250929` | Used for both SYNC and BATCH extraction and Claude Q&A |
| `ANTHROPIC_MAX_TOKENS` | `4096` | |
| `SERVER_PORT` | `8085` | |

## Tests

```bash
./mvnw test
```

82 tests, ~10 s, no external services required. Covers:

- Repository scanning (extension filters, exclusion rules, max-size handling)
- `LargeFileChunker` — target line count, safe-boundary selection, hard-cap forced cuts, content round-trip fidelity, no-line-breaks edge case
- `NonSubstantiveFileFilter` — `.d.ts`, test/spec, barrel file detection
- Prompt template rendering (including `<`/`>` delimiter choice so JSON schemas in the prompt don't need escaping)
- Round-robin agent dispatch (`AgentSelector`)
- Orchestrator concurrency bound and per-file fault isolation (SYNC mode)
- `BatchExtractionService` — result mapping, chunk-level fault isolation, stuck-batch timeout (against a mocked Anthropic SDK client)
- `OllamaLogicExtractionAgent` — extraction, usage parsing, failure handling
- Job-control REST endpoints (start, status, cancel, delete, export, Q&A stream, failed files)
- UI controller (Thymeleaf template rendering, jobs list, empty state)
- Directory watcher (file drop, quiet period, subdirectory ignored)

No Anthropic API or Ollama calls are made during the test suite — all model dependencies are mocked.

## Tech stack

| Layer | Technology |
|---|---|
| Framework | Spring Boot 3.5.0 |
| AI integration | Spring AI 1.1.7 |
| Anthropic SDK | anthropic-java-client-okhttp 2.42.0 |
| Templating | Thymeleaf 3 |
| Serialization | Jackson |
| Build | Maven (wrapper included) |
| Java | 17 |
