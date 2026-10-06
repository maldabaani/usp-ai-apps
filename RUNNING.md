# Running the App

This repo hosts one app, StoryForge AI (Python/FastAPI + Angular): an SDD-to-
user-stories assessment pipeline, a unified code+manuals ingestion pipeline
(one ChromaDB corpus), and two standing Q&A pages (Ask Technical/Ask
Business) grounded in that same corpus.

## Authentication (read this first)

The FastAPI backend issues a JWT on `POST /auth/login` and every route checks
it. There's no user management UI yet: the first time the backend starts
with no `users.json`, it seeds a default `admin` account with a **random,
one-time password**, printed once to the server log (never the literal
`"admin"`) — grep the startup log for `created default admin account` to
find it. That account is flagged `must_change_password`, surfaced in the
login response, so log in with the printed password and immediately call
`POST /auth/change-password` (`{"current_password", "new_password"}`,
needs the JWT from login) to rotate it. To add further real accounts, the
only way today is calling `user_store.create_user(username, password,
role)` directly (e.g. from a `python -c` one-liner run inside the backend's
venv, with `JOBS_DIR` pointed at the same directory the running server
uses).

`JWT_SECRET` signs that token. Leave it unset for local dev and the backend
auto-generates and persists one under `JOBS_DIR/.jwt_secret`, so restarting
doesn't invalidate your login. Set it explicitly (e.g.
`export JWT_SECRET=$(openssl rand -hex 32)`) before exposing the app beyond
local testing, or when running via Docker Compose (see "Unified deployment"
below).

## Running locally

**Requirements:** Python 3.12 (raised from 3.11 once DevCrew merged in --
see below; DevCrew's own dependencies pin `>=3.12,<3.13`), Node.js 18+,
Ollama running locally with `nomic-embed-text` (embeddings) and
`qwen2.5:14b` (StoryForge's clarify/generate nodes, and optionally
ingestion's LLM-summary enrichment tier if you enable Ollama there — see
below) pulled. `ANTHROPIC_API_KEY` is required for ingestion's default
(Claude) enrichment agent and Ask Technical/Business (Claude is the
default there too); StoryForge's own assessment pipeline doesn't call
Claude anywhere -- **neither does DevCrew's**: its Planner/Architect/
Developer/Reviewer/QA agents are hard-locked to local Ollama by design
(paired with Docker sandboxing and deterministic quality gates, since
these agents write and execute real code), sharing the same
`qwen2.5:14b` default as StoryForge's own nodes -- pulling it once covers
both. `ANTHROPIC_API_KEY` is never read anywhere in `devcrew/`.

**Fastest path:** run `./dev-up.sh` from the repo root (after the one-time
setup below). Ctrl+C stops both processes.

### 1. Backend

```bash
cd usp-ai-ba/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in ANTHROPIC_API_KEY if you want ingestion's LLM-summary enrichment
# tier and Ask Technical/Business (Claude is the default agent for both).
# StoryForge's own SDD-to-stories pipeline doesn't need it -- see the note
# in .env.example.

ollama pull nomic-embed-text
ollama pull qwen2.5:14b
ollama serve                # if not already running

uvicorn api.main:app --reload --port 8000
```

> `backend/.env.example` previously had a real-looking `NOTION_API_KEY` value checked in
> (and in git history, across a few commits). That token has been removed from the working
> tree and must be treated as compromised regardless — it was rotated/revoked in Notion's
> integration settings as part of this change. If your clone predates this fix, don't reuse
> that old value, and ask whoever maintains the remote whether a history rewrite
> (`git filter-repo --replace-text`) has been coordinated yet.

### 2. Frontend (separate terminal)

```bash
cd usp-ai-ba/frontend/storyforge-ui
npm install
npm start                   # ng serve -> http://localhost:4200
```

Open **http://localhost:4200**. The landing page has five cards:

- **AI Business Analyst** — turns an SDD PDF into an Epic → User Story →
  Dev/Unit-Test Task hierarchy (local Ollama model + RAG over ChromaDB),
  exported as `.docx`/ADO/Notion. Run the one-time ingestion below before
  your first assessment.
- **Ingestion** — index a code repository and/or user manuals into the
  shared corpus (`/ingestion`).
- **Ask Technical** / **Ask Business** — standing Q&A pages over that same
  corpus, for the dev team and business team respectively.
- **DevCrew Runs** — the multi-agent Planner/Architect/Developer/Reviewer/QA
  team's own run list and live workflow view. Runs get here two ways: an
  epic dispatched from an assessment's Status page via **Send to DevCrew**
  (skips Planner/Architect, starts straight at Developer -- the normal
  path), or a free-text run created directly against `POST
  /api/devcrew/runs` (full Planner → Architect flow, for ad hoc requests
  not tied to a StoryForge assessment). Either way, DevCrew needs Postgres
  and a reachable Docker daemon -- see "DevCrew requirements" below.

### 3. One-time ingestion (before first assessment)

```bash
curl -X POST http://localhost:8000/api/ingest/documents -H "Content-Type: application/json" \
  -d '{"folder_path": "/path/to/user-manuals"}'

curl -X POST http://localhost:8000/api/ingest/code -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/monorepo"}'
```

---

## Ingestion/Ask configuration notes

Ingestion's enrichment agent and Ask's model are both configurable from the
**Settings** page in the Angular shell, or via environment variables
documented in `.env.example`. A few things worth calling out:

- Ingestion's LLM-summary enrichment defaults to Claude only. Set
  `INGEST_OLLAMA_ENABLED=true` (or the equivalent Settings toggle) to add
  Ollama as a second agent — work is then split **round-robin** between the
  two, not primary/fallback.
- Ask Technical/Business's model is controlled separately via `ASK_QA_MODEL`
  (`claude` or `ollama`).
- All RAG retrieval (ingestion + StoryForge's own pipeline + Ask) uses one
  shared embedding model (`nomic-embed-text` by default, via
  `OLLAMA_EMBED_MODEL`) — there's no per-feature embedding toggle.

## Ollama server tuning (matters more now that one process drives all load)

Since StoryForge's own RAG/generation and ingestion's enrichment fan-out
share one Ollama server, a few server-level env vars (set before `ollama
serve`) are worth tuning for throughput:

- `OLLAMA_NUM_PARALLEL` — concurrent requests per loaded model. Ingestion's
  per-run `max_concurrency` (defaults to 8) should stay at or below
  `OLLAMA_NUM_PARALLEL × (number of active agents)` to avoid requests
  queuing up on the Ollama side.
- `OLLAMA_MAX_LOADED_MODELS` — keep both the generation model (`qwen2.5:14b`)
  and the embedding model (`nomic-embed-text`) resident at once, if you use
  both, to avoid reload latency swapping between them.
- `OLLAMA_KEEP_ALIVE` — how long an idle model stays loaded; raise it if you
  see reload latency between bursts of activity.
- `OLLAMA_MAX_QUEUE` — caps how many requests queue once `OLLAMA_NUM_PARALLEL`
  is saturated, rather than piling up unbounded.
- GPU layer offload and quantization level are configured per-model (see
  Ollama's own docs) — worth revisiting if extraction throughput is CPU-bound.

---

## DevCrew requirements

DevCrew (the multi-agent Planner/Architect/Developer/Reviewer/QA pipeline,
mounted under `/api/devcrew`) needs two things StoryForge's own features
don't:

- **Postgres** for its own `runs`/`run_events`/`watched_repos`/
  `issue_runs`/`run_messages` tables (`devcrew_alembic/` migrations; keep
  this separate from StoryForge's own SQLite job checkpoints -- see
  `CLAUDE.md`'s "Job registries" note). Point `DEVCREW_DATABASE_URL` at it
  (`.env`), or use the `devcrew-postgres` Compose service below.
- **A reachable Docker daemon** -- each Developer task runs inside its own
  sandboxed, network-isolated container (`network_mode=none`, `cap_drop:
  ALL`, read-only rootfs), so DevCrew needs `/var/run/docker.sock` (or
  equivalent) reachable from wherever the backend runs.

`./dev-up.sh` checks both at startup and warns (doesn't fail) if either is
missing -- StoryForge's own pages keep working either way, but DevCrew's
routes and "Send to DevCrew" won't until both are up. `GET
/api/devcrew/health` reports the live status of Postgres, Docker, Ollama,
the sandbox images, and the GitHub token, if you need to check by hand.

GitHub PR delivery (DevCrew's final step) also needs `DEVCREW_GITHUB_TOKEN`
set to a PAT with `repo` scope -- optional until you actually want a run to
open a real pull request.

---

## Unified deployment (Docker Compose)

`storyforge-ui`'s landing page ("/") has the same cards described above. In
production this is served through a single nginx gateway in front of one
backend container, over **TLS** (self-signed by default — internal/VPN
deployment, no public DNS assumed):

- `/` → Angular shell (all pages)
- `/api/*` → the FastAPI backend

### Day-0 checklist (do this once, before the first `docker compose up`)

These are required, not optional — `docker-compose.yml` will refuse to start
without `DEVCREW_DB_PASSWORD` (compose's `:?` required-variable syntax), and
skipping the others leaves real gaps (a predictable default credential, an
unencrypted login, sandboxed DevCrew runs that can't build their images).

1. **Generate secrets.**
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   export JWT_SECRET=$(openssl rand -hex 32)
   export DEVCREW_DB_PASSWORD=$(openssl rand -hex 16)
   ```
   Put these in `usp-ai-ba/backend/.env` (not just the shell) so they survive
   a new shell session, and `chmod 600 usp-ai-ba/backend/.env`.
2. **Generate the gateway's TLS certificate** (self-signed; see "TLS" below
   for when to use a real CA instead):
   ```bash
   mkdir -p deploy/tls
   openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
     -keyout deploy/tls/server.key -out deploy/tls/server.crt \
     -subj "/CN=usp-ai-apps.internal"
   ```
   `deploy/tls/` is gitignored — this never gets committed.
3. **Build DevCrew's sandbox images** (needed before any run that actually
   executes code — see "DevCrew requirements" above):
   ```bash
   ./scripts/build_sandbox_images.sh
   ```
4. **Confirm the backend container's non-root user can reach the Docker
   socket.** The backend `Dockerfile` creates `appuser` in a group matching
   `docker.sock`'s GID (default `999`, the common value on Debian/Ubuntu).
   Check your host's actual value and rebuild with an override if it
   differs:
   ```bash
   stat -c '%g' /var/run/docker.sock
   # if not 999:
   docker compose build --build-arg DOCKER_GID=<that number> storyforge-backend devcrew-migrate
   ```
5. **Pre-create/own the named volumes** so the non-root container isn't
   handed a root-owned `/data` it can't write to. Docker creates a named
   volume root-owned (`755`) on first use, which a non-root `appuser` can't
   write into. Fix it once via `docker compose run` (which already knows
   the real, project-prefixed volume names, so there's nothing to guess):
   ```bash
   docker compose run --rm --user root --entrypoint "" storyforge-backend \
     chown -R 1000:1000 /data /devcrew-workspaces
   ```
   (`1000` is `appuser`'s UID in the backend `Dockerfile` — confirm it
   matches if you ever change that.) This also builds the image and brings
   `devcrew-postgres`/`devcrew-migrate` up as a side effect of satisfying
   `storyforge-backend`'s `depends_on`, so it's a reasonable first command
   to run regardless.

### Running it

```bash
docker compose up --build
```

Open **https://localhost/** (self-signed cert — your browser will warn once;
trust it, or import `deploy/tls/server.crt` into your client's trust store
for a clean VPN-wide rollout). Plain `http://localhost/` redirects to
`https://`. Only the `gateway` service publishes host ports (`443`, `80`);
`storyforge-backend` is reachable only inside the compose network.

Migrations run automatically: `devcrew-migrate` (a one-shot service) runs
`alembic upgrade head` and `storyforge-backend` waits for it to finish
before starting — `alembic -c devcrew_alembic.ini upgrade head` by hand is
still available as a fallback/debug path, not the normal one anymore.

Ollama is expected to run on the host, not in a container. It's used by
StoryForge's `clarify_node`/`generate_node` (`qwen2.5:14b`) and by
ingestion's optional Ollama enrichment agent (Claude by default) — make sure
both `nomic-embed-text` and `qwen2.5:14b` are pulled if you enable the
Ollama enrichment agent. `OLLAMA_BASE_URL` defaults to
`http://host.docker.internal:11434`, mapped via `extra_hosts: host-gateway`
in `docker-compose.yml` so it resolves on Linux too — make sure `ollama
serve` is running on the host before starting the compose stack.

Each service has a `restart: unless-stopped` policy, a `healthcheck`, and
memory/CPU limits (`mem_limit`/`cpus` — the plain-`docker compose` syntax;
`deploy.resources.limits` is silently ignored outside Swarm, so don't add
that instead if you ever edit this). Container logs are capped via a shared
`x-logging` anchor (`json-file`, 50MB × 5 files per service) so they don't
grow unbounded on the host.

`GET /health` is a real liveness check (confirms StoryForge's own
checkpointer DB is reachable), used by the backend's own Docker
`HEALTHCHECK`. `GET /api/devcrew/health` (needs a JWT) gives the full
dependency breakdown (Postgres/Chroma/Ollama/Docker/sandbox
images/GitHub); `GET /api/devcrew/health/live` is the unauthenticated
`{"ok": true/false}` version of the same checks, for infra tooling that
can't carry a token.

Data persistence: ChromaDB, the job registries, uploads/exports, and
ingestion's enrichment manifests are all written under `/data` inside the
container, backed by the `storyforge-data` named Docker volume — it
survives `docker compose down` (but not `docker compose down -v`).

DevCrew's own infra comes up automatically with this stack: `docker
compose up` also starts `devcrew-postgres` (data in the `devcrew-pgdata`
volume) and mounts the **host's Docker socket** into `storyforge-backend`
(so DevCrew can launch its own sandboxed task containers as siblings, not
nested inside the backend's container) plus a `devcrew-workspaces` volume
for its per-run git worktrees. Mounting the host socket is a real trust
boundary — the backend container can start other containers on the host —
accepted as part of the DevCrew merge; don't run this compose stack
somewhere that boundary isn't acceptable.

### TLS

The self-signed cert generated above is the right default for an
internal/VPN-only deployment with no public DNS — there's no HTTP-01/DNS-01
challenge path for Let's Encrypt without one. If this ever gets a real
public-facing domain, switch to a real CA (Let's Encrypt via certbot, or
your org's own) and point `deploy/tls/server.crt`/`server.key` at the
issued cert instead; the nginx config doesn't need to change, only the
files it reads.

### Backups

Three things matter: the `devcrew-pgdata` volume (Postgres), the
`storyforge-data` volume (everything under `/data`), and `.env` (lives in
the source tree, not a volume). `scripts/backup.sh` handles all three —
`pg_dump`, a volume tarball, and a copy of `.env`, optionally shipped
off-box via `rsync` if `USP_BACKUP_OFFBOX_TARGET` is set, with a 14-day
local retention by default.

Add to the host's crontab:
```
0 2 * * * /opt/usp-ai-apps/scripts/backup.sh >> /var/log/usp-ai-apps-backup.log 2>&1
0 3 * * 0 docker system prune -af --volumes >> /var/log/usp-ai-apps-prune.log 2>&1
```
The weekly prune only ever removes *unused/dangling* images, containers,
networks and volumes — the three named volumes above are always referenced
by a running compose service, so it never touches them.

**Test the restore path before relying on it** — an untested backup isn't a
backup. The exact steps are documented at the top of `scripts/backup.sh`:
stop the stack, restore the volume tarball, restore the Postgres dump, start
the stack again.
