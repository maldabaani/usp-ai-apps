"""One-off script: a real, complete tally of extracted logic for one CodeMind
job -- unlike the Ask feature (codemind/qa.py), which only ever answers from
the top 6 highest-scoring files for a given question, this reads every
result the job wrote and sums across all of them.

"Functions" here means extracted *rules* (the `rules` list in each file's
parsed content -- see codemind/prompts.py's expected output shape and the
job-detail page's ExtractedContent interface) -- CodeMind does not parse an
AST or count literal function declarations; it asks the LLM to summarize
business-logic units per file, which is a related but distinct concept from
a literal source-level function count.

Run from usp-ai-ba/backend, with the venv active:

    python -m scripts.count_extracted_logic --job-id <job-id>

or point it directly at an output directory (e.g. if the job snapshot is
gone but the output files remain):

    python -m scripts.count_extracted_logic --output-dir /path/to/output
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from codemind import job_store

_SUMMARY_FILE_NAME = "_summary.json"


def _resolve_output_directory(args: argparse.Namespace) -> Path:
    if args.output_dir:
        return Path(args.output_dir)
    for snapshot in job_store.load_all():
        if snapshot.get("id") == args.job_id:
            return Path(snapshot["output_directory"])
    raise SystemExit(f"No job found with id {args.job_id!r} under JOBS_DIR/codemind_jobs/")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--job-id", help="Job ID as shown in the Progress page URL")
    group.add_argument("--output-dir", help="Job's output directory, if you already know it")
    args = parser.parse_args()

    output_directory = _resolve_output_directory(args)
    if not output_directory.is_dir():
        raise SystemExit(f"Output directory does not exist: {output_directory}")

    total_rules = 0
    per_file: list[tuple[str, int]] = []
    unparseable: list[str] = []
    skipped_or_failed = 0

    for path in sorted(output_directory.rglob("*.json")):
        if path.name == _SUMMARY_FILE_NAME:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unparseable.append(str(path.relative_to(output_directory)))
            continue

        if not data.get("success") or data.get("skipped"):
            skipped_or_failed += 1
            continue

        raw_content = data.get("content") or ""
        cleaned = raw_content.strip()
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            parsed = json.loads(cleaned)
        except ValueError:
            unparseable.append(data.get("relativePath", str(path)))
            continue

        rules = parsed.get("rules") or []
        count = len(rules)
        total_rules += count
        per_file.append((data.get("relativePath", str(path)), count))

    print(f"Output directory: {output_directory}")
    print(f"Files with usable extraction results: {len(per_file)}")
    print(f"Files skipped/failed (no logic extracted): {skipped_or_failed}")
    print(f"Files whose content wasn't valid JSON (excluded from the count): {len(unparseable)}")
    print(f"Total extracted rules across all files: {total_rules}")
    print()
    print("Per-file rule counts:")
    for relative_path, count in sorted(per_file, key=lambda pair: pair[1], reverse=True):
        print(f"  {count:>4}  {relative_path}")
    if unparseable:
        print()
        print("Files excluded (content wasn't valid JSON):")
        for relative_path in unparseable:
            print(f"  {relative_path}")


if __name__ == "__main__":
    main()
