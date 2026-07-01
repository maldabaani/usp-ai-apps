# CodeMind — CLAUDE.md

Developer reference for AI coding assistants working on this codebase.

## Project identity

| Key | Value |
|---|---|
| Artifact | `com.jslogicextractor:codemind:0.1.0` |
| Spring Boot | 3.5.0 |
| Spring AI | 1.1.7 |
| Java | 17 |
| Default port | `8085` |

## Build & test

```bash
# Build (skip tests)
./mvnw package -DskipTests

# Full test suite (82 tests, ~10 s)
./mvnw test

# Run locally
./mvnw spring-boot:run
```

Set `ANTHROPIC_API_KEY` before running. Everything else has safe defaults.

## Module layout

```
src/main/java/com/jslogicextractor/
├── agent/          LogicExtractionAgent interface + Claude/Ollama impls
├── batch/          Anthropic Message Batches API client + service
├── config/         @ConfigurationProperties records (all under jsprocessor.*)
├── filter/         NonSubstantiveFileFilter — skips test/generated/config files
├── incremental/    ManifestService — tracks processed files to skip on re-run
├── orchestration/  ExtractionJob, JobPhase, JobRegistry, JobStore, JobStarter,
│                   JobSnapshot, JsRepositoryProcessingOrchestrator
├── output/         ExtractionResultWriter, OutputFileSnapshotService
├── prompt/         LogicExtractionPromptTemplates (loads .st from classpath)
├── qa/             ExtractionQaService, QaChatClientConfig, OllamaEmbeddingModelConfig
├── scanner/        RepositoryScannerService, LargeFileChunker, Language enum
├── watch/          InputDirectoryWatcher (WatchService-based)
└── web/            REST controllers + Thymeleaf UI controllers + request/response records
```

## Key types

### ExtractionJob

Mutable in-memory job object. All fields except the `volatile boolean cancelRequested`
are written only by the orchestrator thread; reading from the polling REST endpoint
is safe because the fields are effectively published via the volatile write.

```
id, repositoryRoot, outputDirectory, maxConcurrency, executionMode, incremental
phase (JobPhase), totalFiles, processedFiles, succeededFiles, failedFiles, skippedFiles
createdAt, startedAt, finishedAt, failureReason
cancelRequested (volatile)
```

Transitions: `PENDING → SCANNING → FILTERING → PROCESSING → COMPLETED | CANCELLED | FAILED`

### JobPhase enum

`PENDING, SCANNING, FILTERING, PROCESSING, COMPLETED, CANCELLED, FAILED`

Cancel is soft: `requestCancel()` sets the volatile flag; each file in the orchestrator
checks `isCancelRequested()` before starting and skips the queue. In-flight Ollama/Claude
calls run to completion. Orchestrator calls `markCancelled()` after the loop drains.

### ExtractionResult record

Written as JSON to `{outputDir}/{sourceRelativePath}.json`:

```json
{
  "relativePath": "src/auth/login.ts",
  "agentName": "claude-sonnet-4-5-20250929",
  "success": true,
  "skipped": false,
  "content": "{\"file\":\"...\",\"summary\":\"...\",\"rules\":[...],\"dependencies\":[...]}",
  "errorMessage": null,
  "durationMillis": 1240,
  "promptTokens": 820,
  "completionTokens": 412
}
```

`content` is itself a JSON string — the raw model output. Viewer/export code parses
it a second time to extract `summary`, `rules`, `dependencies`.

### AgentSelector

Round-robins across all registered `LogicExtractionAgent` beans. In default config
only the Claude agent is active. Enable `jsprocessor.ollama.enabled=true` to add the
Ollama agent; both then share the load equally.

### ExtractionQaService

Two retrieval paths, selected at runtime:

1. **Vector search** — requires `jsprocessor.embedding.enabled=true` and a running
   Ollama embedding model (`nomic-embed-text` by default). Builds an in-memory
   `SimpleVectorStore`, loads all extraction result documents, runs cosine similarity.

2. **Keyword overlap fallback** — scores each output file by how many question tokens
   appear in the file's raw JSON. Used when no `EmbeddingModel` bean is present or
   when an embedding call fails.

The answer is streamed back as an SSE flux from either the Claude API or a local
Ollama model (`jsprocessor.qa.model=claude|ollama`).

### OutputFileSnapshotService

Reads output files from disk. Key methods:

- `recentFiles(job, limit)` — returns the N most-recently-modified `.json` files
- `readOutputFile(job, relativePath)` — path-traversal-guarded single file read
- `listFailedFiles(job)` — scans all output JSONs, returns those with `success=false`

### BatchExtractionService

Submits files to the Anthropic Message Batches API in chunks (≤10 000 requests /
≤200 MB per batch). Groups files by language for prompt-cache efficiency. Polls
every 30 s with a 26 h timeout. Writes results to the same output layout as SYNC mode.

### LargeFileChunker

Splits files exceeding `jsprocessor.max-file-size-bytes` (300 KB default) at safe line
boundaries into `part-0001`, `part-0002`, … virtual files rather than skipping them.
Enabled by default; disable via `jsprocessor.chunking.enabled=false`.

### InputDirectoryWatcher

Watches a single directory (non-recursive) via `java.nio.file.WatchService`. A
configurable quiet period (500 ms default) debounces rapid multi-event drops. Each
new file triggers one job. Subdirectory events are ignored. Disabled by default.

## Configuration reference

All properties live under `jsprocessor.*` in `application.yml`. Each has an env-var
override shown in `${ENV_VAR:default}` notation.

| Property | Env var | Default | Notes |
|---|---|---|---|
| `included-extensions` | — | `.js,.jsx,.mjs,.cjs,.ts,.tsx,.py,.pyw,.java,.kt,.kts,.go,.cs,.rb,.rs,.php` | Comma-separated |
| `excluded-directory-names` | — | `node_modules,.git,dist,build,…` | Prune list |
| `max-file-size-bytes` | — | `300000` | Files larger than this are chunked |
| `max-concurrent-requests` | — | `8` | SYNC thread pool size |
| `skip-existing-results` | — | `true` | Incremental re-run support |
| `execution-mode` | `JSPROCESSOR_EXECUTION_MODE` | `SYNC` | `SYNC` or `BATCH` |
| `chunking.enabled` | `JSPROCESSOR_CHUNKING_ENABLED` | `true` | |
| `chunking.max-lines-per-chunk` | `JSPROCESSOR_CHUNKING_MAX_LINES` | `1800` | |
| `ollama.enabled` | `JSPROCESSOR_OLLAMA_ENABLED` | `false` | Add Ollama as second agent |
| `ollama.model` | `OLLAMA_MODEL` | `qwen2.5:14b` | |
| `ollama.num-ctx` | `OLLAMA_NUM_CTX` | `8192` | Context window; raise for large files |
| `qa.model` | `JSPROCESSOR_QA_MODEL` | `claude` | `claude` or `ollama` |
| `embedding.enabled` | `JSPROCESSOR_EMBEDDING_ENABLED` | `false` | Enables vector search |
| `embedding.model` | `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | |
| `watch.enabled` | `JSPROCESSOR_WATCH_ENABLED` | `false` | Directory watcher |
| `watch.directory` | `JSPROCESSOR_WATCH_DIRECTORY` | `./watch-input` | |

## REST API surface

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/extraction-jobs` | Start a new job |
| `GET` | `/api/v1/extraction-jobs` | List all jobs |
| `GET` | `/api/v1/extraction-jobs/{id}` | Get job status |
| `POST` | `/api/v1/extraction-jobs/{id}/cancel` | Request cancellation |
| `DELETE` | `/api/v1/extraction-jobs/{id}` | Delete job + output files |
| `DELETE` | `/api/v1/extraction-jobs` | Clear all jobs + data |
| `GET` | `/api/v1/extraction-jobs/{id}/output-files` | Recent output files (50 max) |
| `GET` | `/api/v1/extraction-jobs/{id}/output-file?relativePath=…` | Read single output file |
| `GET` | `/api/v1/extraction-jobs/{id}/failed-files` | List failed files with error |
| `GET` | `/api/v1/extraction-jobs/{id}/export` | Download merged JSON export |
| `POST` | `/api/v1/extraction-jobs/{id}/qa` | One-shot Q&A (non-streaming) |
| `POST` | `/api/v1/extraction-jobs/{id}/qa/stream` | SSE streaming Q&A (per-job) |
| `POST` | `/api/v1/ask/stream` | SSE streaming Q&A (all completed jobs) |

## UI routes

| Path | Description |
|---|---|
| `/ui/jobs` | Jobs list + start form |
| `/ui/jobs/{id}` | Job progress (stepper, stats, file feed, viewer modal) |
| `/ui/jobs/{id}/ask` | Ask Agent for a single job |
| `/ui/ask` | Ask All — cross-job search across all completed jobs |

## Persistence

Jobs are persisted to `~/.js-logic-extractor/jobs/{uuid}.json` by `JobStore`.
They are reloaded on startup; terminal phases (`COMPLETED`, `FAILED`, `CANCELLED`)
are treated as read-only snapshots.

Output files are written to `{outputDirectory}/{sourceRelativePath}.json` by
`FileSystemExtractionResultWriter`. The output directory defaults to `./output/{jobId}`.
The manifest file (`manifest.json` at the output root) tracks which source files have
been processed for incremental re-runs, and is only written when the job completes.

## Thymeleaf security note

Thymeleaf 3.1+ rejects string variable expressions inside event-handler attributes
(`th:onclick`, `th:onchange`, etc.). Pass data via `th:attr="data-*=..."` and read
it from JavaScript using `element.dataset.*`.

## Test suite

82 tests across 14 test classes. All tests are unit/slice tests with Mockito mocks
— no external processes (Anthropic API, Ollama) are called. The `MockMvc` web layer
tests use `@WebMvcTest` with `@MockitoBean` injections.

Run with `./mvnw test`. Expected result: 82 passed, 0 failed, 0 skipped.
