"""Detect the projects of an existing repository and how to install/test them.

Supported: Python (pyproject.toml / setup.py / requirements.txt), Java with Maven (pom.xml) and
Angular (angular.json), at the repository root or one directory below it (e.g. backend/,
frontend/). A `.devcrew.yaml` at the root can list the projects and override any command:

    projects:
      - stack: python
        path: backend
        test_cmd: python -m pytest -q tests/unit
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from app.graph.state import ExistingProject

CONFIG_FILE = ".devcrew.yaml"
JACOCO_VERSION = "0.8.12"
_JACOCO = f"org.jacoco:jacoco-maven-plugin:{JACOCO_VERSION}"
SKIP_DIRS = {"node_modules", "target", "dist", "build", "venv", ".venv", "__pycache__", "docs"}

# Prints "COVERAGE_LINES <percent>" from every JaCoCo CSV report (multi-module builds too).
JACOCO_SUMMARY = (
    "find . -path '*target/site/jacoco/jacoco.csv' -exec cat {} + | "
    'awk -F, \'$1 != "GROUP" {m += $8; c += $9} '
    'END {if (m + c > 0) printf "COVERAGE_LINES %.2f\\n", 100 * c / (m + c)}\''
)

DEFAULTS: dict[str, dict[str, str]] = {
    "python": {
        "install_cmd": (
            'if [ -f pyproject.toml ] || [ -f setup.py ]; then pip install -q -e ".[dev]" '
            "|| pip install -q -e .; fi; "
            "for f in requirements.txt requirements-dev.txt dev-requirements.txt; do "
            'if [ -f "$f" ]; then pip install -q -r "$f"; fi; done; '
            "pip install -q pytest pytest-cov"
        ),
        "build_cmd": "python -m compileall -q .",
        "test_cmd": "python -m pytest -q -p no:cacheprovider",
        "coverage_cmd": "python -m pytest -q -p no:cacheprovider --cov=. --cov-report=term",
    },
    "java": {
        # Running the coverage goals once with no tests downloads every plugin, the surefire
        # provider and the JaCoCo agent, so later (offline) test runs find them in the cache.
        "install_cmd": (
            f"mvn -q -DskipTests dependency:resolve && mvn -q {_JACOCO}:prepare-agent test "
            f"-Dtest=DevCrewNoSuchTest -Dsurefire.failIfNoSpecifiedTests=false {_JACOCO}:report"
        ),
        "build_cmd": "mvn -q -DskipTests package",
        "test_cmd": "mvn -q test",
        "coverage_cmd": f"mvn -q {_JACOCO}:prepare-agent test {_JACOCO}:report && {JACOCO_SUMMARY}",
    },
    "angular": {
        "install_cmd": (
            "if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; "
            "else npm install --no-audit --no-fund; fi"
        ),
        "build_cmd": "npx ng build",
        "test_cmd": "npx ng test --watch=false --browsers=ChromeHeadless",
        "coverage_cmd": "npx ng test --watch=false --browsers=ChromeHeadless --code-coverage",
    },
}

MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("angular", ("angular.json",)),
    ("java", ("pom.xml",)),
    ("python", ("pyproject.toml", "setup.py", "requirements.txt")),
]


class DetectionError(ValueError):
    """The repository cannot be handled (unsupported or ambiguous); the message says why."""


class _ProjectOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stack: str
    path: str = "."
    install_cmd: str | None = None
    build_cmd: str | None = None
    test_cmd: str | None = None
    coverage_cmd: str | None = None


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projects: list[_ProjectOverride]


@dataclass(frozen=True)
class Detection:
    projects: list[ExistingProject]
    source: str  # "detected" | ".devcrew.yaml"
    notes: list[str]


def project_for(stack: str, path: str, **overrides: str | None) -> ExistingProject:
    if stack not in DEFAULTS:
        raise DetectionError(f"unsupported stack '{stack}' (use python, java or angular)")
    commands = {**DEFAULTS[stack], **{k: v for k, v in overrides.items() if v is not None}}
    return ExistingProject.model_validate({"stack": stack, "path": path, **commands})


def _candidates(root: Path) -> list[Path]:
    dirs = [root]
    dirs += sorted(
        p
        for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in SKIP_DIRS
    )
    return dirs


def _stack_of(directory: Path) -> str | None:
    for stack, files in MARKERS:
        if any((directory / f).is_file() for f in files):
            return stack
    return None


def detect_projects(root: Path) -> Detection:
    config = root / CONFIG_FILE
    if config.is_file():
        try:
            data = _Config.model_validate(yaml.safe_load(config.read_text(encoding="utf-8")) or {})
            projects = [
                project_for(p.stack, p.path, **p.model_dump(exclude={"stack", "path"}))
                for p in data.projects
            ]
        except (ValidationError, yaml.YAMLError) as exc:
            raise DetectionError(f"{CONFIG_FILE} is invalid: {exc}") from exc
        if not projects:
            raise DetectionError(f"{CONFIG_FILE} lists no projects")
        return Detection(projects, CONFIG_FILE, [])

    found: dict[str, list[str]] = {}
    notes: list[str] = []
    for directory in _candidates(root):
        rel = "." if directory == root else directory.relative_to(root).as_posix()
        stack = _stack_of(directory)
        if stack:
            found.setdefault(stack, []).append(rel)
        elif (directory / "build.gradle").is_file() or (directory / "build.gradle.kts").is_file():
            notes.append(f"{rel}: Gradle builds are not supported (Maven only)")
        if directory == root and stack in ("java", "angular"):
            break  # a single-stack repository: its sub-directories are its own modules

    if not found:
        hint = (
            "; ".join(notes)
            or "no pyproject.toml/setup.py/requirements.txt, pom.xml or angular.json"
        )
        raise DetectionError(
            f"no supported project found ({hint}). DevCrew supports Python, Java (Maven) and "
            f"Angular; add a {CONFIG_FILE} to point at the project directories."
        )
    projects = []
    for stack, paths in found.items():
        if len(paths) > 1:
            if "." in paths:
                paths = ["."]
            else:
                raise DetectionError(
                    f"several {stack} projects found ({', '.join(paths)}); list the one to work "
                    f"on in {CONFIG_FILE}"
                )
        projects.append(project_for(stack, paths[0]))
    return Detection(sorted(projects, key=lambda p: p.path), "detected", notes)


def repository_map(files: list[str], *, limit: int = 300) -> str:
    """A compact file listing for the Planner/Architect (directories first, then files)."""
    shown = files[:limit]
    more = len(files) - len(shown)
    return "\n".join(shown) + (f"\n… and {more} more files" if more > 0 else "")
