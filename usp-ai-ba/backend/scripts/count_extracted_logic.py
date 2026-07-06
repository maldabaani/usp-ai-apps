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
from pathlib import Path

from codemind import job_store
from codemind.extraction_stats import compute_stats, format_report


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

    stats = compute_stats(output_directory)
    print(f"Output directory: {output_directory}")
    print(format_report(stats))


if __name__ == "__main__":
    main()
