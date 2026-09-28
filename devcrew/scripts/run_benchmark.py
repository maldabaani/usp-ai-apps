#!/usr/bin/env python3
"""Run the DevCrew benchmark headless and score it with hidden acceptance tests.

    cd devcrew/backend && . .venv/bin/activate
    python ../scripts/run_benchmark.py                          # all 12 tasks
    python ../scripts/run_benchmark.py --stack python           # one stack
    python ../scripts/run_benchmark.py --task py-todo-crud --task java-todo-crud
    python ../scripts/run_benchmark.py --models-config config/models.yaml \\
        --models-config ../benchmarks/models/other.yaml         # compare model setups

Auto mode: every approval is approved, every agent question is answered with "Use your best
judgment and document the assumption.", escalations are retried with that guidance up to
--max-escalations times per run and then given up. Nothing is pushed to GitHub.

Needs Ollama with the configured models, Chroma and the sandbox images (like a normal run);
Postgres is not used (runs are checkpointed in memory). Results are written to
benchmarks/results/<timestamp>.json and .md.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.benchmark.results import BenchmarkReport, ConfigResult, ConfigSummary, write_report
from app.benchmark.runner import BenchmarkRunner
from app.benchmark.tasks import BenchStack, TaskFileError, load_tasks
from app.config import DEVCREW_DIR, Settings
from app.events.bus import EventBus
from app.events.store import InMemoryEventStore
from app.graph.factory import build_deps, build_llm
from app.llm.models_config import Role
from app.preflight import PreflightError, preflight

BENCH_DIR = DEVCREW_DIR / "benchmarks"
REPORTED_SETTINGS = (
    "max_parallel_devs",
    "max_dev_iterations",
    "max_questions_per_task",
    "max_coordinator_actions",
    "rag_enabled",
    "sandbox_enabled",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--stack", action="append", default=[], choices=[s.value for s in BenchStack]
    )
    parser.add_argument("--task", action="append", default=[], metavar="TASK_ID")
    parser.add_argument(
        "--models-config",
        action="append",
        default=[],
        type=Path,
        metavar="YAML",
        help="models.yaml to benchmark; repeat to compare setups (default: MODELS_CONFIG_PATH)",
    )
    parser.add_argument("--max-escalations", type=int, default=2, help="auto-retries per run")
    parser.add_argument("--task-timeout-min", type=float, default=120.0)
    parser.add_argument("--tasks-dir", type=Path, default=BENCH_DIR / "tasks")
    parser.add_argument("--hidden-dir", type=Path, default=BENCH_DIR / "hidden_tests")
    parser.add_argument("--out-dir", type=Path, default=BENCH_DIR / "results")
    parser.add_argument("--list", action="store_true", help="list the selected tasks and exit")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    try:
        tasks = load_tasks(args.tasks_dir, args.hidden_dir, stacks=args.stack, ids=args.task)
    except TaskFileError as exc:
        sys.exit(f"invalid benchmark tasks: {exc}")
    if args.list:
        for t in tasks:
            print(f"{t.id:26} {t.stack:8} d{t.difficulty} {t.expected_tests:3} tests  {t.title}")
        return
    if not tasks:
        sys.exit("no tasks selected")

    env_file = DEVCREW_DIR / ".env"
    base = Settings(_env_file=env_file if env_file.exists() else None)
    # Never deliver from a benchmark, whatever .env says.
    base = base.model_copy(update={"github_delivery_enabled": False})
    configs = [p.resolve() for p in args.models_config] or [base.models_config_path]

    report = BenchmarkReport(
        started_at=datetime.now(UTC),
        settings={k: getattr(base, k) for k in REPORTED_SETTINGS},
    )
    for path in configs:
        settings = base.model_copy(update={"models_config_path": path})
        llm = build_llm(settings)
        try:
            await preflight(settings, llm.models.required_models())
        except PreflightError as exc:
            sys.exit(f"{path}: {exc}")
        bus = EventBus(InMemoryEventStore())
        runner = BenchmarkRunner(
            build_deps(settings, bus, llm=llm),
            max_escalations=args.max_escalations,
            task_timeout_s=args.task_timeout_min * 60,
            echo=lambda msg: print(msg, flush=True),
        )
        label = path.stem
        if any(c.label == label for c in report.configs):
            label = f"{label}-{len(report.configs) + 1}"
        config = ConfigResult(
            label=label,
            models_config=str(path),
            models={role.value: llm.spec(role).model for role in Role},
        )
        report.configs.append(config)
        print(f"== {config.label}: {len(tasks)} tasks", flush=True)
        for task in tasks:
            config.tasks.append(await runner.run_task(task))
            config.summary = ConfigSummary.of(config.tasks)
            write_report(report, args.out_dir)  # keep partial results if interrupted

    report.finished_at = datetime.now(UTC)
    json_path, md_path = write_report(report, args.out_dir)
    print(f"\nresults: {json_path}\nsummary: {md_path}")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
