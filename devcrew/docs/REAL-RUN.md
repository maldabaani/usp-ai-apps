# First run against a real model

The first DevCrew run driven by a real local model instead of the scripted fakes. It was stopped
by hand before the end, after about 3.9 hours. The Coordinator had split two tasks and a third
round of splits was starting. This page records what happened, what was fixed, and what is still
open.

## Setup

| | |
|---|---|
| Machine | Cloud container: 4 CPU cores, 15 GB RAM, **no GPU** |
| Model | `qwen2.5:3b` (Q4_K_M) for every role; `nomic-embed-text` for code search |
| Ollama | 0.34 in Docker (`ollama/ollama`), `OLLAMA_NUM_PARALLEL=2` |
| Settings | `MAX_PARALLEL_DEVS=1`, `LLM_REQUEST_TIMEOUT_S=1800`, `num_ctx` 16384, `num_predict` 4096 |
| GitHub | The fake GitHub server from the tests (nothing was pushed to real GitHub) |
| Sandbox | The real Docker sandbox (`devcrew-sandbox-python`) |

The Ollama registry (`registry.ollama.ai`, `ollama.com`) was blocked by the environment's network
policy. The GGUF weights came from Docker Hub's model artifacts (`ai/qwen2.5:3B-Q4_K_M`,
`ai/nomic-embed-text-v1.5`) and were imported with `ollama create` (`FROM <file>.gguf`).

`models.yaml` asks for `qwen2.5-coder:14b` on a GPU. A 14B model is not usable on this machine, so
this run tests the **plumbing and the failure handling** with a weak model. It does not measure
the output quality DevCrew is designed for (that is still BL-002).

Measured speed on this CPU:
- **3B:** reads prompts at about 55 tokens/s and writes at about 11 tokens/s.
- **7B:** reads at about 23 tokens/s and writes at about 5 tokens/s.

Both models called tools correctly and followed JSON schemas in a quick check.

## The request

```markdown
# Bookmarks API
- A bookmark has an id (int), a url (valid http/https URL), a title (1-200 characters) and tags.
- Endpoints: create, list (optionally filtered by one tag), get by id, delete by id.
- Unknown ids return 404. Invalid input returns 422. Store bookmarks in memory.
- Every endpoint has pytest tests.
```

## What happened

1. **Planner.**
   - It asked a question the request already answered, then proposed 6 sequential tasks with
     files at the repository root (`main.py`, `models.py`).
   - I rejected it with feedback (3 tasks under `app/`). The revision was **identical to the
     rejected plan** (fixed, see below).
   - After the fix it asked another redundant question, then produced exactly the requested plan.
2. **Architect.**
   - It asked which template to use, and twice whether to call its own `read_rules` tool.
   - After the question limit it asked the same question **27 more times** until the step limit
     (fixed).
   - The design was usable (python-fastapi, three modules). Its data model used Pydantic v1
     `class Config: orm_mode = True`, which the rules forbid.
3. **T1** (schemas and store): three attempts.
   - The developer read the design document 4 lines per call (fixed).
   - QA's tests imported names that did not exist.
   - One QA answer hit the output limit in the middle of a `write_file` call. Ollama answered
     HTTP 500, and **the whole run failed** (fixed).
   - **Retry** continued the run from its checkpoint, as designed.
   - After the third failed attempt the Coordinator **split** T1 into T1-a and T1-b.
4. **T1-a merged**: review approved, its tests passed in the sandbox, and the secret scan was
   clean.
5. **T1-b** failed three attempts and was split again (T1-b-a, T1-b-b). The split tasks drifted
   toward "use `ConfigDict`" (the reviewer's complaint about the design's v1 config) instead of
   the store itself. The run was stopped here; T2 (router) and T3 (tests) never started.

**Usage at the stop:**

| Role | Calls | Tokens | Model time |
|---|---:|---:|---:|
| Reviewer | 64 | 360,570 | 55 min |
| Developer | 48 | 173,277 | 66 min |
| Architect | 41 | 153,549 | 12 min |
| QA | 22 | 64,322 | 38 min |
| Planner | 5 | 12,695 | 9 min |
| Coordinator | 3 | 5,792 | 2 min |
| **Total** | **183** | **770,205** (718k in, 52k out) | **3.0 h** |

Wall time was 3.9 h; 22 minutes of it was spent waiting for me. There were 10 questions: 6 to me
and the rest routed between agents.

## Fixed during the run

All fixes have regression tests (backend: 380 tests pass).

| # | Problem | Fix |
|---|---|---|
| 1 | A rejected plan came back unchanged: the revision prompt listed the feedback **before** the rejected plan, and a small model copies what it reads last. Replaying the same prompt with the order swapped applied the feedback. | Planner and Architect revision prompts put the rejected artifact first (labelled "rejected: do not repeat it unchanged") and the feedback last. |
| 2 | After the question limit, the Architect asked the same question 27 more times (about 140k prompt tokens, 10 minutes). | The agent loop withdraws a tool that is called twice in a row with the same arguments and the same result, and tells the model to continue without it. |
| 3 | A tool call cut off by `num_predict` made Ollama answer HTTP 500, and the exception failed the run. | Treated as a malformed tool call: one corrective retry ("write smaller files"), then the normal escalation. Other model errors still raise. |
| 4 | The failed run showed the Architect's earlier, already-handled error instead of the exception that stopped it. | A failed run reports the exception that stopped it. |
| 5 | The developer paged through a 62-line file 4 lines per call (16 model calls). | `read_file` always returns at least 100 lines. |

Retry after a crash, the Coordinator's split, sandboxed tests, the secret scan and merging all
worked with real model output.

## Still open

Each finding is tracked in the [backlog](../BACKLOG.md) (BL-180 to BL-189).

- **Too many questions (BL-180).** The small model asked about things the request or its own
  tools answer. The per-node limit (3) contains it, but each question waits for a human.
- **The Reviewer is the main cost (BL-181).** It used 47% of all tokens. Single review calls
  took up to 11 minutes on CPU: a large prompt (diff, rules, design) and a long answer.
- **The Planner does not know the template layout (BL-182).** It placed files at the
  repository root because the Architect chooses the template only after planning.
- **The design contradicted the rules (BL-183).** The Architect's example code used Pydantic
  v1 config, and the Reviewer then kept asking for `ConfigDict`. That churn also steered the
  Coordinator's split.
- **QA test quality (BL-184).** QA wrote tests against names that did not exist, and it edited
  the template's existing `tests/test_health.py`.
- **Split drift (BL-185).** The Coordinator's sub-tasks followed the latest review complaint
  instead of the original task goal.
- **Parallel duplicate tool calls (BL-186).** The Reviewer issued the same `search_codebase`
  call several times in one answer. They are cheap, but the new repeat guard only covers
  consecutive steps.
- **Repeated questions, contradictory answers (BL-188).** One developer question was asked three
  times across attempts. The Architect agent answered "no", then "yes", before it came to me.
- **Unlimited split depth (BL-189).** Every sub-task gets its own Coordinator budget, so T1-b
  was split again (T1-b-a, T1-b-b) and could have gone on.
- **Speed on CPU (BL-187).** A small API did not finish in 4 hours. CPU-only is a smoke test,
  not a way to use DevCrew; the intended setup (14B on a GPU) is still unverified (BL-002).
