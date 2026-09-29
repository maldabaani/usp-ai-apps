"""Command policy for sandboxed execution.

Commands only ever run inside Docker; this policy is an extra guard against the obviously
dangerous (denylist) and keeps working directories inside the run's workspace (allowlist).

The denylist inspects every shell segment, including nested `sh -c "..."`, `$(...)` and
backtick bodies, so wrapping a command does not hide it.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath

DENIED_PROGRAMS = {"docker", "docker-compose", "podman", "sudo", "su", "doas", "nsenter"}
SHELLS = {"sh", "bash", "zsh", "dash", "ash", "ksh"}
FETCHERS = {"curl", "wget"}
DANGEROUS_RM_TARGETS = {"/", "/*", "~", "~/", "~/*", "$HOME", "${HOME}", "/workspace/.."}
SEPARATORS = {";", "&&", "||", "|", "&", "\n", "(", ")", "|&"}
SUBSHELL_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`|<\(([^()]*)\)")
MAX_COMMAND_LENGTH = 10_000


class PolicyViolation(ValueError):
    pass


def _tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError as exc:  # unbalanced quotes
        raise PolicyViolation(f"cannot parse command: {exc}") from exc


def _segments(tokens: list[str]) -> list[tuple[list[str], str | None]]:
    """Split tokens into simple commands; each carries the operator that follows it."""
    segments: list[tuple[list[str], str | None]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in SEPARATORS or (tok and set(tok) <= set(";&|()")):
            segments.append((current, tok))
            current = []
        else:
            current.append(tok)
    segments.append((current, None))
    return [(seg, op) for seg, op in segments if seg]


def _program(segment: list[str]) -> tuple[str, list[str]]:
    """Skip env assignments and wrappers (env, time, nice, timeout N, command, exec)."""
    i = 0
    while i < len(segment):
        tok = segment[i]
        if "=" in tok and not tok.startswith("-") and tok.split("=", 1)[0].isidentifier():
            i += 1
            continue
        name = PurePosixPath(tok).name
        if name in {"env", "time", "nice", "command", "exec", "nohup", "xargs", "stdbuf"}:
            i += 1
            while i < len(segment) and segment[i].startswith("-"):
                i += 1
            continue
        if name == "timeout":
            i += 1
            while i < len(segment) and (segment[i].startswith("-") or segment[i][:1].isdigit()):
                i += 1
            continue
        return name, segment[i + 1 :]
    return "", []


def _check_rm(args: list[str]) -> None:
    flags = "".join(a.lstrip("-") for a in args if a.startswith("-") and not a.startswith("--"))
    long_flags = {a for a in args if a.startswith("--")}
    recursive = "r" in flags.lower() or "--recursive" in long_flags
    force = "f" in flags or "--force" in long_flags
    if "--no-preserve-root" in long_flags:
        raise PolicyViolation("rm --no-preserve-root is not allowed")
    targets = {a for a in args if not a.startswith("-")}
    if recursive and force and targets & DANGEROUS_RM_TARGETS:
        raise PolicyViolation("recursive forced deletion of / or $HOME is not allowed")


def check_command(command: str, _depth: int = 0) -> None:
    """Raise PolicyViolation if the command is denied."""
    if not command.strip():
        raise PolicyViolation("empty command")
    if len(command) > MAX_COMMAND_LENGTH:
        raise PolicyViolation("command too long")
    if _depth > 5:
        raise PolicyViolation("command nesting too deep")

    for match in SUBSHELL_RE.finditer(command):
        inner = next(g for g in match.groups() if g is not None)
        if match.group(0).startswith("<(") and _first_program(inner) in FETCHERS:
            raise PolicyViolation("executing downloaded scripts (curl|sh) is not allowed")
        if inner.strip():
            check_command(inner, _depth + 1)

    segments = _segments(_tokens(command))
    for index, (segment, op) in enumerate(segments):
        program, args = _program(segment)
        if program in DENIED_PROGRAMS:
            raise PolicyViolation(f"'{program}' is not allowed in the sandbox")
        if program == "rm":
            _check_rm(args)
        if program in SHELLS:
            if any(a.startswith("$(") or a.startswith("`") for a in args) and any(
                f in " ".join(args) for f in FETCHERS
            ):
                raise PolicyViolation("executing downloaded scripts (curl|sh) is not allowed")
            if "-c" in args and args.index("-c") + 1 < len(args):
                check_command(args[args.index("-c") + 1], _depth + 1)
        if program in FETCHERS and op in ("|", "|&"):
            nxt = segments[index + 1][0] if index + 1 < len(segments) else []
            nxt_program, _ = _program(nxt)
            if nxt_program in SHELLS or nxt_program in {"python", "python3", "node", "perl"}:
                raise PolicyViolation("piping downloads into an interpreter is not allowed")


def _first_program(command: str) -> str:
    try:
        segments = _segments(_tokens(command))
    except PolicyViolation:
        return ""
    return _program(segments[0][0])[0] if segments else ""


def check_workdir(workdir: Path, allowed_root: Path) -> Path:
    """The sandbox only mounts directories inside WORKSPACES_DIR."""
    resolved = workdir.resolve()
    root = allowed_root.resolve()
    if resolved == root or root not in resolved.parents:
        raise PolicyViolation(f"{workdir} is outside the workspaces directory {allowed_root}")
    return resolved


def check_relative_cwd(cwd: str) -> str:
    """Validate a cwd relative to /workspace inside the container."""
    path = PurePosixPath(cwd.strip() or ".")
    if path.is_absolute() or ".." in path.parts:
        raise PolicyViolation(f"cwd must be a relative path inside the project: {cwd}")
    return str(path)
