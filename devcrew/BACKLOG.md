# DevCrew backlog

Open issues, deferred work and deviations collected at the end of each phase. New items are
appended at the bottom of their section; IDs are stable (never reused). When an item is done,
tick it and note the phase/commit.

Priority: **P1** = needed for the acceptance criteria / a real run, **P2** = important quality or
robustness, **P3** = nice to have.

## Verification gaps (things not yet proven on a real machine)

- [ ] **BL-001** (P1, from P1) Build and run the backend image via `docker compose up --build`.
  The dev sandbox could not build it (no apt access from build containers).
- [ ] **BL-002** (P1, from P2) First real run against Ollama `qwen2.5-coder:14b`: all runs so far
  used scripted fake models. Tune prompts, `num_ctx`/`num_predict`, and check tool-calling and
  JSON-schema output quality per role.
- [ ] **BL-003** (P1, from P3) Build `devcrew-sandbox-node` and `devcrew-sandbox-mixed` and run
  the Angular template's `ng test` inside the node image (only verified on the host so far;
  `deb.debian.org` was blocked in the dev sandbox).
- [ ] **BL-004** (P2, from P4) Measure retrieval quality with the real `nomic-embed-text`
  (tests use a hashing embedder). Candidate input for the Phase 9 benchmark.

## Robustness

- [ ] **BL-010** (P2, from P2) A run that fails on an infrastructure error (Ollama down, Docker
  error) is marked `failed` but is resumable from its checkpoint (`RunDriver.continue_run`).
  Expose "retry" in the API/UI (Phase 6/7) and auto-continue non-terminal runs on backend start.
- [ ] **BL-011** (P2, from P3) Installed-dependency tracking (`Sandbox._installed`) is in memory:
  after a backend restart the next command re-runs the install step once. Persist the manifest
  hash (e.g. in a marker file inside the dependency volume) if installs get slow.
- [ ] **BL-012** (P2, from P3) Java `install_cmd` works around `dependency:go-offline` resolving
  different versions than the real build (it resolves deps/plugins and fetches surefire's JUnit
  provider explicitly). Re-verify on Spring Boot upgrades; consider a Maven settings/offline
  profile instead.
- [ ] **BL-013** (P2, from P4) Indexing failures are non-fatal (error event, search serves the
  previous index, empty retrieval sections if Chroma is down). Consider surfacing a degraded
  "RAG unavailable" status in the UI.
- [ ] **BL-014** (P3, from P1) Health check treats `GITHUB_TOKEN` as non-critical; `/health` is
  only "all green" with a valid token. Revisit once GitHub delivery exists (Phase 8).

## Security / sandbox

- [ ] **BL-020** (P2, from P3) Sandbox processes run as the backend's uid:gid, which is root
  inside the compose backend container (mitigated: all capabilities dropped, no-new-privileges,
  read-only rootfs, no network). Consider running the backend container as a non-root user with
  docker-socket group access.
- [ ] **BL-021** (P3, from P3) When the backend runs on the host as non-root, Docker creates
  root-owned empty `node_modules` mount points inside workspaces; deleting a workspace then
  needs root. Pre-create the directories as the backend user before mounting.
- [ ] **BL-022** (P3, from P3) Chromium runs with `--no-sandbox` inside the node/mixed images (the
  container is the sandbox). Revisit if a seccomp profile allowing Chromium's sandbox is added.
- [ ] **BL-023** (P3, from P3) The mixed sandbox image is large (Python + JDK + Maven + Node +
  Chromium). Consider per-component containers for mixed projects instead.

## Parallel execution / Coordinator

- [ ] **BL-050** (P2, from P5) Scheduling runs in waves (LangGraph supersteps): a new task starts
  only when the whole wave has finished, so a slow task or a pending human question in one lane
  delays dependents of the other lanes. Consider dynamic dispatch (e.g. one lane per worker
  with a queue) if waves turn out to waste time on real runs.
- [ ] **BL-051** (P2, from P5) `RunDriver.pending_interrupts` filters out answered interrupts of
  already-finished parallel tasks by checking `task.result` (they stay in
  `snapshot.interrupts` until the wave ends). Relies on LangGraph snapshot behavior; covered by
  in-memory and Postgres tests, re-check on LangGraph upgrades.
- [ ] **BL-052** (P2, from P5) Tasks are tested on their own branch only; breakages caused by the
  combination of parallel tasks surface in the final integration test run. Consider running the
  integration test suite after each wave and routing failures to the Coordinator.
- [ ] **BL-053** (P2, from P5) Coordinator decisions (replan/split quality, when to escalate) are
  only exercised with scripted models; evaluate with the real model (see BL-002) and tune
  `prompts/coordinator.md`.
- [ ] **BL-054** (P3, from P5) Task branches are kept after merge (for per-task diffs in the UI /
  PR); worktrees are removed. Decide a branch cleanup policy after delivery (Phase 8).
- [ ] **BL-055** (P3, from P5) "Routing agent questions" is implemented as: the target role
  flags `needs_human`, the Coordinator's routing rule sends it to the human. There is no
  separate Coordinator LLM call to pick the target (the Developer chooses architect/planner).

## Retrieval (RAG)

- [ ] **BL-030** (P2, from P4) The index mirrors the integration branch only: code of in-flight
  tasks is not searchable (agents use `read_file` on their own worktree). Consider a per-task
  overlay index if agents need it.
- [ ] **BL-031** (P3, from P4) Java/TypeScript chunking is heuristic (brace matching, no parser).
  Consider tree-sitter if chunk boundaries turn out poor on real projects.

## Deviations from the spec (decided, documented; revisit only if needed)

- [x] **BL-040** (from P1) Chroma runs as a docker-compose service and the backend uses
  `HttpClient` (server-side persistence) rather than an embedded `PersistentClient`.
- [x] **BL-041** (from P1) Extra event types: `answer`, `status`, `awaiting_input`.
- [x] **BL-042** (from P2) Graph state holds no `events` list; events live in the persisted
  event log (`run_events`) which powers SSE replay. `RunDriver.state()` merges live task state
  from in-flight subgraphs.
- [x] **BL-043** (from P2) Additions: `story_ids` on plan tasks, per-role `structured_format`
  in `models.yaml`, `MAX_AGENT_STEPS`.
- [x] **BL-044** (from P3) Additions: `install_cmd` in `template.yaml`; `components` on the design
  for `mixed` stacks; `sandbox_images` health check; settings `SANDBOX_ENABLED`,
  `SANDBOX_INSTALL_TIMEOUT_S`, `SANDBOX_PIDS_LIMIT`, `SANDBOX_IMAGE_PREFIX`.
- [x] **BL-045** (from P4) Settings `RAG_*`; rules files are indexed as `source="rules"` next
  to workspace chunks in the same per-run collection.
- [x] **BL-046** (from P5) Settings `MAX_CONFLICT_ROUNDS`, `MAX_COORDINATOR_ACTIONS`; task state
  adds `wave`, `lane`, `worktree`, `conflict_rounds`, `coordinator_actions`; new task status
  `split`. Graph state stores the run status as a plain string, and tests run with
  `LANGGRAPH_STRICT_MSGPACK=true` so any non-JSON value in a checkpoint fails loudly.
