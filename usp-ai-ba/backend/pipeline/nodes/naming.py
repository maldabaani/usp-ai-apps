"""Shared Epic naming convention, used by every output mode (ADO, Notion, document)
so an Epic's name is always the PPM Number + PPM Name + System Name concatenation
for the job, rather than the LLM's per-story epic_title (which stays in each
Epic/story's description/body content, just not as its title anymore).
"""
from __future__ import annotations


def build_epic_title(ppm_number: str, ppm_name: str, system_name: str) -> str:
    return f"{ppm_number} | {ppm_name} | {system_name}"
