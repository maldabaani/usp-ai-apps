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
- [ ] **BL-005** (P1, from P8) First delivery to real GitHub with your token: create-repo (user
  and org), push to an empty repo, PR creation. Tests used a mocked REST API and bare git
  repositories on disk; the dev sandbox did not push anywhere (outward-facing action).
- [ ] **BL-090** (P1, from P9) Run the full benchmark (12 tasks) against the real Ollama
  models and commit or record the baseline `benchmarks/results/*.md`. So far the runner was
  only exercised with the scripted fake model; the real sandbox scored the hidden suites for
  Python and Java reference solutions (100%) and the bare template (1/9).
- [ ] **BL-091** (P2, from P9) The Angular hidden suites were checked in headless Chromium
  on the host, not inside `devcrew-sandbox-node` (image not buildable in the dev sandbox,
  see BL-003).
- [ ] **BL-092** (P2, from P9) The P9 end-to-end demo (UI → plan/design approval → question
  answered in the UI → final approval → PR) ran with the fake Ollama server and a local fake
  GitHub API (git remote over `file://`). It needs repeating with real Ollama and GitHub
  (BL-002, BL-005). The node/mixed sandbox images were stand-in tags for that demo only.

## Robustness

- [x] **BL-010** (P2, from P2) A run that fails on an infrastructure error (Ollama down, Docker
  error) is marked `failed` but is resumable from its checkpoint (`RunDriver.continue_run`).
  *P6: runs that were mid-flight when the backend stopped are now continued automatically on
  startup.* Still open: a "retry" for runs already marked `failed` (not in the specified API;
  would be `POST /runs/{id}/retry` + a UI button). *Done in P14: `POST /runs/{id}/retry` and a Retry button continue a failed run from its last checkpoint.*
- [x] **BL-011** (P2, from P3) Installed-dependency tracking (`Sandbox._installed`) is in memory:
  after a backend restart the next command re-runs the install step once. Persist the manifest
  hash (e.g. in a marker file inside the dependency volume) if installs get slow. *Done in P15: install hashes are kept in `WORKSPACES_DIR/.sandbox-state`.*
- [ ] **BL-012** (P2, from P3) Java `install_cmd` works around `dependency:go-offline` resolving
  different versions than the real build (it resolves deps/plugins and fetches surefire's JUnit
  provider explicitly). Re-verify on Spring Boot upgrades; consider a Maven settings/offline
  profile instead.
- [x] **BL-013** (P2, from P4) Indexing failures are non-fatal (error event, search serves the
  previous index, empty retrieval sections if Chroma is down). Consider surfacing a degraded
  "RAG unavailable" status in the UI. *Done in P16: the run page shows "code search unavailable" (with the error) when indexing or search fails, from the RAG service or, after a restart, the event log.*
- [x] **BL-014** (P3, from P1) *Decided in P8:* `GITHUB_TOKEN` stays non-critical at startup
  (only delivery needs it, and a missing or invalid token becomes a "retry / finish without PR"
  decision at delivery time). `/health` is fully green only with a valid token.

## Security / sandbox

- [ ] **BL-020** (P2, from P3) Sandbox processes run as the backend's uid:gid, which is root
  inside the compose backend container (mitigated: all capabilities dropped, no-new-privileges,
  read-only rootfs, no network). Consider running the backend container as a non-root user with
  docker-socket group access.
- [x] **BL-021** (P3, from P3) When the backend runs on the host as non-root, Docker creates
  root-owned empty `node_modules` mount points inside workspaces; deleting a workspace then
  needs root. Pre-create the directories as the backend user before mounting. *Done in P15: the backend creates the mount points.*
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
- [x] **BL-052** (P2, from P5) Tasks are tested on their own branch only; breakages caused by the
  combination of parallel tasks surface in the final integration test run. Consider running the
  integration test suite after each wave and routing failures to the Coordinator. *Done in P14: the tests run after every wave; a failure adds a WAVEFIX task.*
- [ ] **BL-053** (P2, from P5) Coordinator decisions (replan/split quality, when to escalate) are
  only exercised with scripted models; evaluate with the real model (see BL-002) and tune
  `prompts/coordinator.md`.
- [x] **BL-054** (P3, from P5) *Decided in P8:* task branches stay local (for per-task diffs in the
  UI) and are never pushed; only the integration branch is delivered. Worktrees are removed
  after merge; a run's workspace (and its local branches) is kept for inspection.
- [ ] **BL-055** (P3, from P5) "Routing agent questions" is implemented as: the target role
  flags `needs_human`, the Coordinator's routing rule sends it to the human. There is no
  separate Coordinator LLM call to pick the target (the Developer chooses architect/planner).

## API / runtime

- [ ] **BL-060** (P2, from P6) Cancelling a busy run cancels the asyncio drive immediately, but
  work already handed to threads (a running `docker exec`, a git command) finishes in the
  background; the run's containers are removed by the cleanup, which ends in-flight sandbox
  commands. Consider explicit cancellation tokens if this causes surprises.
- [ ] **BL-061** (P3, from P6) Event ids are global across runs, so per-run ids have gaps; SSE
  clients must treat ids as opaque, increasing cursors (they do: `Last-Event-ID`).
- [ ] **BL-062** (P3, from P6) One backend process only: background drives, the event bus and
  repository locks are in-process. Running several backend replicas would need a job queue and
  cross-process pub/sub (out of scope: single local user).

## GitHub delivery

- [x] **BL-080** (P2, from P8) Delivery to a non-empty repository is refused (greenfield only).
  Supporting "deliver into an existing repo" would need a base-branch choice and rebasing the
  scaffold, which is out of the specified scope. *Done in P11: existing repositories get a PR against their default branch.*
- [x] **BL-081** (P3, from P8) After a final rejection the follow-up task is merged into the same
  branch; if a PR was already opened (e.g. delivery retried later), the same PR is reused and
  its body is not refreshed. Consider updating the PR body on re-delivery. *Done in P15: every re-delivery refreshes the PR title and body.*
- [ ] **BL-082** (P3, from P8) Only github.com-style hosts were considered; GitHub Enterprise
  should work via `GITHUB_API_URL` / `GITHUB_GIT_URL` but is untested.

## Web UI

- [x] **BL-070** (P2, from P7) The UI's backend URL is fixed at build time
  (`frontend/src/environments/*.ts`, default `http://localhost:8080`). Add a runtime
  `config.json` if the backend ever runs on another host or port. *Done in P16: the UI reads `config.json` at startup; the container writes it from `DEVCREW_API_URL`.*
- [ ] **BL-071** (P2, from P7) UI verified with scripted models only (Playwright run through the
  whole flow). Check rendering with real model output: very long design docs and large diffs
  (the diff viewer renders every line; add virtual scrolling if needed).
- [x] **BL-072** (P3, from P7) The timeline keeps every event of the run in memory and the file
  explorer is a flat, indented list. Both are fine for local runs; window/tree them if runs grow
  large. *Done in P16: the timeline is virtualized (CDK) with a filter and a detail view; Files is a folder tree with a filter.*
- [ ] **BL-073** (P3, from P7) Frontend unit tests cover the SSE client, API client, diff/DAG
  helpers, action panel, new-run form and run detail; there is no committed browser E2E test
  (the Playwright flow was run manually). *P9: the same flow, extended to the PR link, was
  run again for the end-to-end demo. P10: rewritten for the workflow graph and run again;
  still not committed (Playwright is not a project dependency).*

## Benchmark

- [ ] **BL-093** (P2, from P9) Hidden tests check the contract pinned in each request, so a
  model that deviates slightly (e.g. 400 instead of 422, a different `data-testid`) loses
  those tests. That is intended: the tasks test instruction following as well. Revisit if
  results turn out dominated by such near-misses.
- [ ] **BL-094** (P3, from P9) Benchmark tasks run one at a time: per-run token counts rely
  on that (the gateway's usage tracker is swapped per task). Running tasks concurrently
  would need per-run usage tracking.
- [ ] **BL-095** (P3, from P9) The benchmark uses in-memory checkpoints and events. An
  interrupted benchmark cannot resume a run, but results are rewritten after every task.
- [ ] **BL-096** (P3, from P9) The reference solutions used to validate the hidden suites
  are not committed. Add them with a `--validate-hidden` mode if the suites change often.

## Workflow view (Phase 10)

- [x] **BL-100** (P2, from P10) Requirements documents are limited to `MAX_REQUEST_CHARS`
  (20,000 by default) because the Planner and Architect receive the whole text. Longer
  documents would need a summarization or chunked-planning step, or a larger `num_ctx`. *Done in P15: documents up to `MAX_DOCUMENT_CHARS` are condensed part by part, and the Planner can use `search_requirements`.*
- [ ] **BL-101** (P3, from P10) Only `.md`/`.txt` uploads (decided). `.docx`/`.pdf` would need
  server-side parsing dependencies.
- [x] **BL-102** (P3, from P10) `GET /runs/{id}/workflow` recomputes the graph from the full
  event log on every (batched) refresh. That is fine for local runs; cache or build it
  incrementally if runs reach many thousands of events. *Done in P15: per-run event lists are cached in memory and extended incrementally.*
- [x] **BL-103** (P3, from P10) The Overview of a large plan (10+ tasks) is small. Consider
  collapsing finished stages, a minimap (ngx-vflow has one) or grouping tasks by wave. *Done in P16: Compact mode (default) folds finished stages, finished task waves and PR rounds into one node each (click to open); a minimap toggle.*
- [ ] **BL-104** (P2, from P10) The workflow UI was verified with the scripted fake model (a
  live Playwright walk-through from document upload to PR, including the plan assessment and
  a developer question). Check it with real model timings and larger plans (with BL-002).

## Roadmap: phases 11-13 (agreed in the P10 gap brainstorm)

These phases loosen two locked decisions, with your approval: DevCrew may work on existing
repositories ("greenfield only" no longer applies), and it may perform the GitHub actions listed
below (read issues and PRs, comment, push follow-up commits to its own PR branches). It still
never pushes to the default branch of an existing repo, and never pushes before your approval.

- **Phase 11: existing repositories + quality gates** *(done in P11)*
  - Work on existing Python/Java (Maven)/Angular repos. The stack is auto-detected; other repos
    are refused.
  - Per-run mode: "Full" (plan -> Architect assesses the existing design -> development) or
    "Quick fix" (one change-plan approval, no Architect).
  - Delivery: a branch and a PR against the repo's default branch.
  - Gates before the PR:
    - secret scanning, always blocking;
    - dependency vulnerabilities;
    - a test coverage threshold.
  - A failed gate goes back to the Developer; if it still fails, the Coordinator asks you:
    allow (noted in the PR body) or stop.
- **Phase 12: GitHub automation** *(done in P12)*
  - Issue intake: polling of watched repos for issues labelled `devcrew`, plus manual import
    of an issue URL or number. DevCrew comments on the issue (started, waiting for approval,
    PR link), and the PR says "Fixes #N".
  - PR follow-up on review comments (from allow-listed users), CI failures and merge conflicts.
    Small, clear asks are fixed and pushed automatically; larger ones become a "needs you" step
    with the proposed change. Bounded number of rounds.
- **Phase 13: steering running work** *(done in P13)*
  - A chat on the run or on a task node. Messages go to the Coordinator and are applied at the
    next safe point.
  - Pause/Resume.

- [ ] **BL-117** (P3, from the P10 gap review) Static analysis (SAST: semgrep/bandit) as an
  additional gate; not selected for Phase 11.

## Existing repositories and gates (Phase 11)

- [ ] **BL-120** (P1, from P11) Rebuild all sandbox images with the gate tools
  (`scripts/build_sandbox_images.sh`). The Python and Java images were rebuilt and verified
  here; node/mixed still cannot be built in the dev sandbox (BL-003).
- [ ] **BL-121** (P2, from P11) The dependency scan was verified only up to the network call:
  osv.dev and deps.dev are blocked in the dev sandbox, so the scan reported `error` there.
  - Check a real finding end to end, and the osv-scanner v2 JSON parsing, on your machine.
  - If your network blocks those hosts, allow them or set `GATES_ENABLED` accordingly.
- [x] **BL-122** (P3, from P11) Coverage for existing Python repositories uses `--cov=.`, which
  also counts test files. Set a narrower `coverage_cmd` in `.devcrew.yaml` if that skews
  results; consider detecting the source package. *Done in P15: coverage measures the detected source packages.*
- [ ] **BL-123** (P3, from P11) Existing repositories:
  - Only one project per stack (a second one needs `.devcrew.yaml`).
  - Detection looks at the root and one level below.
  - Gradle, Poetry-only or pnpm/yarn-lock-only setups use defaults that may not fit.
- [x] **BL-124** (P2, from P11) The per-task secret scan only covers files changed by the
  developer before QA runs. Tests QA adds are scanned at integration, not per task. *Done in P14: the per-task scan also covers the tests QA wrote.*
- [x] **BL-125** (P3, from P11) A coverage baseline and a dependency scan of the base branch
  run once per existing-repository run (one extra test run). Consider caching them per base
  commit. *Done in P15: the baseline is cached per base commit.*

## GitHub automation (Phase 12)

- [ ] **BL-130** (P1, from P12) Verified only against the fake GitHub, which follows GitHub's
  documented REST and GraphQL shapes, and in a live UI walk-through against that fake. Check
  these with a real repository and token:
  - label events;
  - the collaborator permission endpoint;
  - check runs and the job-log redirect;
  - `resolveReviewThread`;
  - the token scopes.
- [x] **BL-131** (P2, from P12) API usage:
  - Every tick (`GITHUB_POLL_TICK_S`), each watching run makes about 6 REST calls: pull,
    three comment lists, check runs, permissions.
  - With many watching runs this approaches the 5,000/hour rate limit.
  - Use conditional requests (ETag / `If-None-Match`) and a per-run PR poll interval. *Done in P15: conditional requests (ETag / 304) and a wait that doubles for quiet PRs (`PR_POLL_MAX_INTERVAL_S`).*
- [x] **BL-132** (P2, from P12) Comments are handled once, by id: an edited review comment is
  not processed again. Only check runs are read, not legacy commit statuses. Logs are fetched
  only for GitHub Actions jobs; other checks contribute their name only. *Done in P15: edited comments are handled again; commit statuses and other CI apps' summaries and details links are read.*
- [x] **BL-133** (P3, from P12) A failing gate inside a follow-up round adds a `GATEFIX` task
  that the graph attaches to the main pipeline rather than to the round. The round still
  pushes after the fix. *Done in P15: round-scoped ids (`R<n>-GATEFIX<k>`); this also fixed an id collision.*
- [x] **BL-134** (P3, from P12) The issue-comment sync loads the state of every issue run on
  each tick, finished ones included. Limit it to runs that changed since the last sync. *Done in P15: the sync reads run rows only and calls GitHub only on changes.*
- [x] **BL-135** (P3, from P12) Removing the label does not cancel a run that already started;
  cancel it in the UI. A declined big change is not proposed again unless the reviewer writes
  a new comment. *Done in P15: removing the label cancels the run before plan approval (`CANCEL_ON_UNLABEL`). A declined change is still not proposed again.*
- [x] **BL-136** (P3, from P12) The Overview of a run with many rounds is one long row.
  Collapse finished rounds (with BL-103). *Done in P16 (with BL-103).*

## Steering (Phase 13)

- [x] **BL-140** (P2, from P13) Pause only works between waves. A running task finishes its
  current iterations first, up to `MAX_DEV_ITERATIONS` with review and QA. Pausing between a
  task's developer iterations needs per-task interrupts inside the parallel wave. *Done in P14: tasks pause before their next developer iteration.*
- [x] **BL-141** (P2, from P13) Messages sent during the integration tests, gates, final
  approval or PR watching wait for the next wave, so they often expire. Consider:
  - turning them into final-approval feedback;
  - using them in the next follow-up round. *Done in P14: final-approval feedback and follow-up rounds while watching.*
- [ ] **BL-142** (P2, from P13) The Coordinator's message sorting (note, add, cancel, answer)
  was verified only with the scripted fake model. Check the quality with the real models
  (with BL-002).
- [x] **BL-143** (P3, from P13) Notes accumulate without limit and are required prompt
  sections. Many notes eat the context budget; cap or summarize them. *Done in P14: the Coordinator merges notes above `MAX_NOTES_CHARS`; if that fails, the newest notes are kept.*
- [x] **BL-144** (P3, from P13) Messages cannot be edited or withdrawn. Cancelling applies only
  to tasks that have not started; stopping a running task still needs the escalation flow. *Done in P14: waiting messages can be edited or withdrawn. Cancelling a running task still needs the escalation flow.*

## Run control (Phase 14)

- [ ] **BL-150** (P2, from P14) The budget is checked before each wave, so a long wave can go
  past it. Token counts are what Ollama reports; it leaves out prompt tokens it served from
  cache, so real usage can be higher than shown.
- [x] **BL-151** (P2, from P14) Tests after each wave: in an existing repository whose tests
  already fail on the base branch, every wave adds a fix task (up to `MAX_WAVE_FIX_TASKS`).
  Compare against a base-branch test run, as the coverage gate does. *Done in P15: a base-branch test baseline; only new failures add a fix task.*
- [ ] **BL-152** (P3, from P14) Retry continues failed runs only. "Re-run from a chosen step"
  (checkpoint time travel) was not built: the workspace's git state would not rewind with it.
  "Run again" (a new run with the same request) covers the common case.
- [x] **BL-153** (P3, from P14) A task pauses before its next developer turn. Review and QA of
  the current attempt finish first. *Done in P15: tasks also pause before review and QA.*
- [x] **BL-154** (P3, from P14) Usage is recomputed from the event log on every request and
  before every wave. Cache running totals if runs grow very large. *Done in P15: usage and budgets read the cached event list.*

## Real-run robustness (Phase 15)

- [ ] **BL-160** (P2, from P15) The failing-test parsers cover pytest, Maven Surefire and Karma
  output. Other runners, or unusual formats, fall back to comparing only whether the base
  branch passed: if it already failed, wave failures are treated as pre-existing.
- [ ] **BL-161** (P3, from P15) Condensing long documents uses the Planner model part by part.
  The digest quality with real models is unverified (with BL-002). Very large documents take
  one model call per 20,000 characters.
- [ ] **BL-162** (P3, from P15) The baseline and install-state caches are plain files under
  `WORKSPACES_DIR` with no eviction. Delete `.baseline-cache` to force fresh baselines.
- [ ] **BL-163** (P3, from P15) The ETag cache lives in memory, so it restarts empty with the
  backend. The quiet-PR wait also resets on restart.

## UI and preview (Phase 16)

- [ ] **BL-170** (P2, from P16) The live preview was verified with the Python template in real
  Docker (install, start, `/health` through the proxy, no outbound network, cleanup). The Java
  (`spring-boot:run`, offline Maven) and Angular (`ng serve`) previews are configured but not
  run here (the node image was not built in this environment).
- [ ] **BL-171** (P3, from P16) A preview runs until you stop it, the run ends, or the backend
  restarts (leftover previews are removed at startup). There is no idle timeout and no limit on
  previews of different runs at the same time. A preview started after a run finished recreates
  the run's dependency volumes; stopping it removes them again.
- [ ] **BL-172** (P3, from P16) Model routing (strong model for hard tasks) and a fallback model
  on repeated failures (rest of BL-112).
- [ ] **BL-173** (P3, from P16) A browser the agents can drive against the preview for end-to-end
  checks (rest of BL-110).
- [ ] **BL-174** (P3, from P16) Browser notifications only fire while the run page is open in a
  background tab (no service worker, no email/Slack). Presets live in the browser's
  localStorage (not shared between browsers).
- [ ] **BL-175** (P3, from P16) Line comments exist only at final approval (the whole run's
  diff); not on task diffs during development, and they are not posted to GitHub.

## Parity with commercial coding agents (from the P10 gap review)

Deferred by the user. Phase 11 covers existing repos, issue triggers, PR follow-up, mid-run
steering and security gates.

- [x] **BL-110** (P2) Live app preview: start the generated app in the sandbox with a port
  exposed to the host and show it in the UI; optionally a browser the agent can drive for
  end-to-end checks. *Done in P16: "Preview" runs the template's `preview_cmd` (or `.devcrew.yaml`'s) in the sandbox on an internal Docker network, behind a proxy published on 127.0.0.1 only. A browser the agent drives is not done (BL-173).*
- [x] **BL-111** (P2) Cost and usage in the UI: tokens per run, per role and per task, wall
  time, and budgets (stop or ask after N tokens or minutes). *Done in P14: Usage tab, token and working-time budgets. There is no money cost, because the models are local.*
- [x] **BL-112** (P3) Model choice per run from the UI, routing (strong model for hard tasks,
  small model for easy ones) and a fallback model on repeated failures. *P16: model per role for a run (New run → Models; validated against Ollama). Routing and a fallback model are not done (BL-172).*
- [ ] **BL-113** (P3) Memory across runs: repository rules files (`AGENTS.md`-style) and
  lessons learned. *Changes a locked decision ("no cross-run memory"): needs your approval.*
- [ ] **BL-114** (P3) Teams and enterprise: authentication/SSO, roles, several users, audit
  log. *Changes a locked decision ("no auth", single user): needs your approval.*
- [ ] **BL-115** (P3) More stacks and delivery targets: other languages/templates, generated
  Dockerfile and CI workflow, DB migrations, deploy to a preview environment.
- [ ] **BL-116** (P3) Conveniences:
  - re-run or fork a run from any step (*P14: Retry for failed runs and "Run again" with the
    same request are done; see BL-152*);
  - turn diff comments in the UI into developer feedback (*done in P16: line comments at final
    approval become the follow-up task's feedback*);
  - run presets or templates (*done in P16: presets in this browser*);
  - notifications (desktop, email, Slack) when input is needed (*P16: browser notifications;
    email and Slack are not done*);
  - export a run report (*done in P16: Markdown report*);
  - mobile-friendly approvals.

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
- [x] **BL-047** (from P6) API details beyond the spec: `GET /runs/{id}` includes graph state
  excerpts and `pending` interrupts; `resume` accepts an optional `interrupt_id`; file/diff
  `ref` values are limited to `integration`, `main` and `task:<id>`; `?last_event_id=` query
  parameter as an alternative to the `Last-Event-ID` header; compose ports bound to
  `127.0.0.1`.
- [x] **BL-048** (from P7) UI uses Angular 19 (spec: 17+), no icon/web fonts (system UI font, no
  CDN requests), health badge tooltip via `title`; the frontend compose service is no longer
  behind a profile (the acceptance criteria need it on `docker compose up`).
- [x] **BL-049** (from P8) Additions: settings `GITHUB_GIT_URL`, `GITHUB_DELIVERY_ENABLED`; state
  fields `final_approved`, `delivery_error`; a `delivery_failed` node (retry / finish without
  PR); created repositories are private and empty (`auto_init: false`).
- [x] **BL-097** (from P9) Additions:
  - `app/benchmark/` package; `app/preflight.py`, shared by both CLI scripts.
  - An automatic escalation policy for benchmark runs (retry with the standard answer up to
    `--max-escalations`, then give up). The spec only defines auto-approval and
    auto-answers.
  - The benchmark forces GitHub delivery off.
  - Extra metrics: Coordinator calls, task counts, per-role tokens, and the project's own
    integration test result.
- [x] **BL-105** (from P10) Additions:
  - Endpoints `GET /runs/{id}/workflow` and `GET /config`.
  - Setting `MAX_REQUEST_CHARS`, replacing the hard-coded 20,000 limit.
  - Optional `Design.plan_assessment`.
  - Frontend dependencies `ngx-vflow` and its d3 peers (`d3-drag`, `d3-selection`, `d3-zoom`
    plus their types).
  - A dark "neon" theme (Material dark theme; design tokens in `styles.scss`) and inline-SVG
    robot icons.
  - The workflow graph replaces the old Tasks tab and task-DAG view.
- [x] **BL-126** (from P11) Additions:
  - Run fields `target`/`mode`/`base_branch`/`repo_info`/`gates`, and new statuses
    `preparing` and `checking`.
  - New graph nodes `prepare_repo` and `gates`.
  - `Design.existing_projects` and the `ExistingDesignDraft` Architect output
    (`prompts/architect_existing.md`).
  - `.devcrew.yaml`.
  - Template `coverage_cmd` entries, `pytest-cov` in the Python template, and the JaCoCo
    prefetch in the Java install step.
  - Settings `GATES_ENABLED`, `GATE_COVERAGE_MIN`, `GATE_COVERAGE_TOLERANCE`,
    `MAX_GATE_FIX_ROUNDS`.
  - The `devcrew-sandbox-gate-tools` image.
  - The locked "greenfield only" rule is lifted for existing repositories, with your approval.
- [x] **BL-137** (from P12) Additions:
  - Tables `watched_repos` and `issue_runs` (migration `0002`).
  - Run status `watching_pr`, interrupt kind `watch` and resume action `update` (the
    poller only).
  - Graph nodes `watch_pr`, `followup`, `approve_followup` and `report_followup`.
  - Run fields `issue` and `followup`.
  - Settings `WATCH_PRS`, `MAX_PR_ROUNDS`, `GITHUB_POLL_TICK_S`, `DEFAULT_POLL_INTERVAL_S`,
    `MAX_ISSUE_RUNS`.
  - Endpoints `/watched-repos`, `/issue-runs` and `/issues/import`.
  - The Settings page (`/settings`).
  - GitHub actions approved for P12: issue comments, PR replies, resolving review threads, and
    non-force pushes to DevCrew's own PR branch.
- [x] **BL-145** (from P13) Additions:
  - Table `run_messages` and column `runs.pause_requested` (migration `0003`).
  - Run status `paused`, task status `cancelled`, interrupt kind `pause` and event type
    `message`.
  - Graph node `pause`; the scheduler, Planner, Architect, developer and reviewer read
    messages.
  - Prompt `prompts/steer.md`.
  - Run fields `human_notes` / `steer_applied`.
  - Endpoints `/runs/{id}/messages` and `/runs/{id}/pause`.
  - Fixed a Phase 11 bug: runs in `preparing` or `checking` were not recovered after a
    backend restart.
- [x] **BL-155** (from P14) Additions:
  - Event type `llm_usage` and the per-call usage hook in the LLM gateway.
  - Graph nodes `budget` (interrupt kind `budget`) and task-level `hold`.
  - Run fields `budget`, `budget_limit`, `wave_checked`, `wave_fixes`.
  - Message status `withdrawn`.
  - Prompt `prompts/notes_merge.md`.
  - Settings `RUN_TOKEN_BUDGET`, `RUN_TIME_BUDGET_MIN`, `WAVE_TESTS_ENABLED`,
    `MAX_WAVE_FIX_TASKS`, `MAX_NOTES_CHARS`.
  - Endpoints `/runs/{id}/usage`, `/runs/{id}/retry`, and message `PATCH` / `DELETE`.
  - UI: Usage tab, budget fields, Retry / Run again buttons, message editing, the notes list.
- [x] **BL-164** (from P15) Additions:
  - Settings `PR_POLL_MAX_INTERVAL_S`, `CANCEL_ON_UNLABEL`, `MAX_DOCUMENT_CHARS`.
  - Run field `request_digest` and the Planner tool `search_requirements`.
  - Prompt `prompts/condense_requirements.md`.
  - Baseline field `tests` (base-branch failing tests).
  - Task field `hold_next`.
  - `GET /config` returns `max_document_chars`.
