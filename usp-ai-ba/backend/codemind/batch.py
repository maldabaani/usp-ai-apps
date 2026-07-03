"""Anthropic Message Batches API path for BATCH execution mode.

Not yet ported -- scheduled as Phase F5 in the CodeMind merge plan (highest
risk alongside F4's orchestration change, since no test -- Java or Python --
exercises the real Batches API today). SYNC mode (the default, matching
Java's jsprocessor.execution-mode: SYNC default) is fully functional in
codemind/orchestrator.py and does not go through this module.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codemind.models import SourceFile
    from codemind.orchestrator import ExtractionJob


async def run_batch(job: "ExtractionJob", files: list["SourceFile"]) -> None:
    raise NotImplementedError("BATCH execution mode is not yet ported (Phase F5)")
