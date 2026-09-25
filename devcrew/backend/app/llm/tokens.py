"""Conservative token estimation (no tokenizer dependency).

Code-heavy text tokenizes at roughly 3-4 characters per token for Qwen-family models; we use 3
so that estimates err on the side of staying inside num_ctx.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence

from langchain_core.messages import BaseMessage

CHARS_PER_TOKEN = 3.0
MESSAGE_OVERHEAD_TOKENS = 8


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def message_tokens(message: BaseMessage) -> int:
    content = message.content if isinstance(message.content, str) else json.dumps(message.content)
    extra = ""
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        extra = json.dumps(tool_calls, default=str)
    return estimate_tokens(content) + estimate_tokens(extra) + MESSAGE_OVERHEAD_TOKENS


def messages_tokens(messages: Sequence[BaseMessage]) -> int:
    return sum(message_tokens(m) for m in messages)


def truncate_to_tokens(text: str, max_tokens: int, marker: str = "[truncated]") -> str:
    """Keep whole lines from the start of `text` so it fits in `max_tokens`."""
    if estimate_tokens(text) <= max_tokens:
        return text
    budget_chars = max(0, int(max_tokens * CHARS_PER_TOKEN) - len(marker) - 32)
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) > budget_chars:
            break
        kept.append(line)
        used += len(line)
    if not kept and budget_chars > 0:
        kept = [text[:budget_chars]]
    dropped = len(lines) - len(kept)
    suffix = f"\n... {marker} ({dropped} more lines)\n" if dropped > 0 else f"\n... {marker}\n"
    return "".join(kept) + suffix


def tail_text(text: str, max_tokens: int) -> str:
    """Keep the END of `text` (for logs, where the failure summary is at the bottom)."""
    max_chars = int(max_tokens * CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    cut = text[-max_chars:]
    newline = cut.find("\n")
    if 0 <= newline < 200:
        cut = cut[newline + 1 :]
    return "[... earlier output truncated]\n" + cut
