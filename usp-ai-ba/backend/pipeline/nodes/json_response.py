"""Shared helpers for parsing Claude's text/JSON responses across pipeline nodes."""
from __future__ import annotations

import json

from json_repair import repair_json


def extract_text(content) -> str:
    """Flatten an Anthropic message's content (str or content-block list) into plain text."""
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "") for block in content if isinstance(block, dict)
    )


def extract_json(raw_text: str):
    """Parse the first complete JSON value out of raw_text.

    Strips markdown code fences if present, then decodes starting at the first
    '{' or '[' and stops at the matching close -- ignoring any trailing text
    the model appends after the JSON despite being told to respond with only
    JSON. A plain json.loads() rejects that trailing text as "Extra data".

    If the JSON itself is malformed (e.g. Claude writes an unescaped quote
    inside a string value, like `the "status" field`), falls back to
    json_repair, which specifically targets this class of LLM JSON mistakes.
    """
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned

    start = next((i for i, ch in enumerate(cleaned) if ch in "{["), None)
    if start is None:
        raise ValueError("No JSON object or array found in response")
    candidate = cleaned[start:]

    try:
        return json.JSONDecoder().raw_decode(candidate)[0]
    except json.JSONDecodeError:
        return json.loads(repair_json(candidate))
