"""One-off script (not part of the automated test suite/CI): compares
CodeMind's existing per-job Ask feature (codemind/qa.py) against the new
unified Ask Technical endpoint (api/routers/ask.py, over the shared
ingestion corpus) on the same repository and the same set of questions --
Phase I6 of the ingestion-unification plan's parity-validation gate, run
before Phase I7 deletes CodeMind's product surface.

This is NOT a pass/fail check. There is no way to assert "equivalent
answer" programmatically here: the two systems draw from genuinely
different corpora by design (CodeMind: per-file LLM summaries for one job
only; the new pipeline: persistent ChromaDB chunks across every ingested
repo/manual, plus an optional per-file LLM-summary tier). It writes a
side-by-side Markdown report; a human reviewer reads it and judges
whether the new answers are at least as specific, well-grounded, and
correctly cited as the old ones before Phase I7 proceeds.

Usage (from usp-ai-ba/backend, with the venv active and
ANTHROPIC_API_KEY/Ollama configured as usual):

    python -m scripts.compare_ask_old_vs_new /path/to/repo

Optional: --questions /path/to/questions.txt (one question per line;
otherwise a small built-in default set is used) and --output report.md
(default: ask_parity_report.md in the current directory).

Side effect: this ingests the repo into BOTH systems -- a fresh CodeMind
extraction job (codemind/job_registry.py + orchestrator.run) and a fresh
ingestion run into the live ChromaDB collections
(ingestion.ingest_code.ingest_code). The latter is NOT sandboxed to a temp
directory, since the point is comparing against what the new Ask
Technical endpoint would actually answer in production -- re-run
ingestion afterward against your real corpus if this repo isn't meant to
stay indexed.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from api.routers import ask as ask_router
from codemind import job_registry, qa
from codemind.agents.selector import get_agent_selector
from codemind.orchestrator import run as run_codemind_job
from ingestion.ingest_code import ingest_code
from ingestion.retrieval import retrieve_all_collections
from prompts.ask_prompts import TECHNICAL_ASK_SYSTEM_PROMPT

DEFAULT_QUESTIONS = [
    "What does this codebase do at a high level?",
    "How is authentication/authorization handled?",
    "Where is configuration loaded from, and what are the key settings?",
    "How are errors logged or reported?",
    "What external services or APIs does this code integrate with?",
    "Describe the main data models/entities.",
    "How is the codebase's main entry point structured?",
    "What testing conventions does this codebase follow?",
    "Are there any background jobs or asynchronous processing?",
    "How is data persisted (database, files, cache)?",
]


async def _run_codemind_extraction(repo_path: Path) -> Path:
    job = job_registry.register(repo_path, None, None, None, False)
    await run_codemind_job(job, get_agent_selector())
    return job.output_directory


async def _old_answer(output_directory: Path, question: str) -> str:
    result = await qa.ask(output_directory, question)
    return result.answer


async def _new_answer(question: str) -> str:
    retrieved = await retrieve_all_collections(question)
    if not any(retrieved.values()):
        return "(no content retrieved -- ingestion may not have completed yet)"
    context = ask_router._build_context(retrieved)
    chat = ask_router._get_ask_chat()
    system_prompt = TECHNICAL_ASK_SYSTEM_PROMPT.format(context=context)
    response = await chat.ainvoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=question)]
    )
    return response.content


async def _run(repo_path: Path, questions: list[str], output_path: Path) -> None:
    print(f"Running CodeMind extraction against {repo_path} ...")
    output_directory = await _run_codemind_extraction(repo_path)
    print(f"CodeMind extraction complete: {output_directory}")

    print(f"Running unified ingestion against {repo_path} ...")
    await ingest_code(str(repo_path))
    print("Ingestion complete.")

    lines = [f"# Ask parity report: {repo_path}\n"]
    for i, question in enumerate(questions, start=1):
        print(f"[{i}/{len(questions)}] {question}")
        old = await _old_answer(output_directory, question)
        new = await _new_answer(question)
        lines.append(f"## {i}. {question}\n")
        lines.append("### Old (CodeMind per-job Ask)\n")
        lines.append(f"{old}\n")
        lines.append("### New (unified Ask Technical)\n")
        lines.append(f"{new}\n")
        lines.append("---\n")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written to {output_path}.")
    print(
        "Read it and judge: are the new answers at least as specific/well-grounded/"
        "correctly-cited as the old ones? This is a human sign-off gate -- no automated "
        "check substitutes for reading both columns before Phase I7 (deleting CodeMind) proceeds."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_path", help="Path to the repository to ingest into both systems")
    parser.add_argument("--questions", help="Path to a file of questions, one per line")
    parser.add_argument("--output", default="ask_parity_report.md", help="Where to write the report")
    args = parser.parse_args()

    repo_path = Path(args.repo_path).expanduser().resolve()
    if not repo_path.is_dir():
        raise SystemExit(f"Not a directory: {repo_path}")

    if args.questions:
        questions = [
            line.strip() for line in Path(args.questions).read_text().splitlines() if line.strip()
        ]
    else:
        questions = DEFAULT_QUESTIONS

    asyncio.run(_run(repo_path, questions, Path(args.output)))


if __name__ == "__main__":
    main()
