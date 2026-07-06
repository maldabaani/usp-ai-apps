"""One-time ingestion of the codebase into ChromaDB.

Implements the "smart chunking" rules:

- Java: chunked by class AND by method separately. Entity classes (``@Entity``)
  are indexed into BOTH ``sf_codebase`` and ``sf_jpa_entities``.
- TypeScript/Angular: chunked by component, service, and module separately.
- JavaScript/jQuery: chunked by function. Falls back to whole-file chunking
  when no functions are detected.
- Every other supported language (Python, Kotlin, Go, C#, Ruby, Rust, PHP, plus
  JS/TS variants like .jsx/.tsx/.mjs/.cjs/.pyw/.kts): whole-file chunking --
  building bespoke per-symbol chunkers for 13 more languages isn't attempted
  here; ingestion/enrichment's optional LLM-summary tier (see enrich.py) is
  what's meant to compensate for the resulting loss of symbol-level precision,
  by adding an LLM-synthesized per-file summary alongside these raw chunks.
- HTML: skipped entirely.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ingestion.chroma_client import (
    delete_by_source,
    delete_by_source_excluding_type,
    get_vector_store,
    list_distinct_sources,
)
from ingestion.enrichment import enrich
from config import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE_TOKENS = 1500
CHUNK_OVERLAP_TOKENS = 150
CHARS_PER_TOKEN = 4
MAX_CHUNK_CHARS = CHUNK_SIZE_TOKENS * CHARS_PER_TOKEN
OVERLAP_CHARS = CHUNK_OVERLAP_TOKENS * CHARS_PER_TOKEN

# Matches codemind/orchestrator.py's EXCLUDED_DIRECTORY_NAMES -- widened here
# (from just {node_modules, target, dist, .git, .angular, .cache}) as part of
# folding CodeMind's broader language coverage into this pipeline, since the
# 13 newly-added languages have their own common build/cache directories
# (.venv, __pycache__, vendor, bin, obj, ...) that were never relevant before.
SKIP_DIR_NAMES = {
    "node_modules", ".git", "dist", "build", "coverage",
    "out", ".next", ".turbo", "vendor",
    "__pycache__", "target", ".venv", "venv",
    "bin", "obj", ".gradle", ".mypy_cache", ".pytest_cache",
    ".angular", ".cache",
}
JAVA_EXCLUDE_SUFFIXES = ("Test.java", "IT.java")
TS_EXCLUDE_SUFFIXES = (".spec.ts",)
# Matches codemind/orchestrator.py's INCLUDED_EXTENSIONS -- widened here (from
# just {.java, .ts, .js}) to fold in CodeMind's broader per-file LLM
# extraction language coverage, per the "fully unify ingestion" decision (see
# plan file section I). Only .java/.ts/.js get symbol-level chunking below;
# every other extension falls through to chunk_generic_file's whole-file split.
SOURCE_EXTENSIONS = {
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".py", ".pyw", ".java", ".kt", ".kts",
    ".go", ".cs", ".rb", ".rs", ".php",
}

BATCH_SIZE = 64

_overflow_splitter = RecursiveCharacterTextSplitter(
    chunk_size=MAX_CHUNK_CHARS,
    chunk_overlap=OVERLAP_CHARS,
    separators=["\n\n", "\n", " ", ""],
)


@dataclass
class ChunkResult:
    documents: list[Document]
    is_entity: bool = False


def _is_skipped_path(path: Path) -> bool:
    if any(part in SKIP_DIR_NAMES for part in path.parts):
        return True
    name = path.name
    if name.endswith(JAVA_EXCLUDE_SUFFIXES):
        return True
    if name.endswith(TS_EXCLUDE_SUFFIXES):
        return True
    if path.suffix == ".html":
        return True
    return False


def iter_source_files(repo_path: Path):
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in SOURCE_EXTENSIONS:
            continue
        if _is_skipped_path(path):
            continue
        yield path


def _detect_module(relative_path: Path) -> str:
    """First path segment is treated as the Maven module / app name."""
    parts = relative_path.parts
    return parts[0] if parts else "root"


def _split_overflow(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    return _overflow_splitter.split_text(text)


def _document_id(metadata: dict) -> str:
    """Deterministic per-chunk ID so re-ingesting an unchanged file/method
    upserts the same Chroma entry instead of adding a duplicate (see
    chroma_client.delete_by_source for the complementary fix -- a symbol
    that's been renamed/removed changes this ID and would otherwise leave
    the old one behind as an orphan, which delete_by_source clears first).
    Includes every symbol-name field a chunk type might set (class/method/
    function) rather than "whichever is present first", since e.g. two
    methods in the same class share class_name but must still get distinct
    IDs.
    """
    key = "::".join(
        [
            metadata.get("source", ""),
            metadata.get("type", ""),
            metadata.get("class_name", ""),
            metadata.get("method_name", ""),
            metadata.get("function_name", ""),
            str(metadata.get("chunk_part", 0)),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]


def _make_documents(
    text: str,
    base_metadata: dict,
) -> list[Document]:
    pieces = _split_overflow(text)
    ingested_at = time.time()
    docs = []
    for index, piece in enumerate(pieces):
        metadata = dict(base_metadata)
        metadata["ingested_at"] = ingested_at
        if len(pieces) > 1:
            metadata["chunk_part"] = index
        docs.append(Document(page_content=piece, metadata=metadata))
    return docs


# --------------------------------------------------------------------------
# Java
# --------------------------------------------------------------------------

_JAVA_CLASS_RE = re.compile(
    r"(?P<modifiers>(?:public|private|protected|abstract|final|static|\s)*)"
    r"(?P<kind>class|interface|enum)\s+(?P<name>\w+)"
)
_JAVA_METHOD_RE = re.compile(
    r"(?:^|\n)\s*(?:@\w+(?:\([^)]*\))?\s*)*"
    r"(?:public|private|protected)\s+"
    r"(?:static\s+|final\s+|synchronized\s+|abstract\s+)*"
    r"[\w<>\[\],\s?]+?\s+"
    r"(?P<name>\w+)\s*\([^;{}]*\)\s*"
    r"(?:throws\s+[\w,\s]+)?\s*\{",
    re.MULTILINE,
)
_JAVA_ANNOTATION_LAYER_MAP = [
    (re.compile(r"@RestController|@Controller"), "controller"),
    (re.compile(r"@Service"), "service"),
    (re.compile(r"@Repository"), "repository"),
    (re.compile(r"@Entity"), "entity"),
    (re.compile(r"@Configuration|@Component(?!\w)"), "config"),
]


def _java_layer(text: str) -> str:
    for pattern, layer in _JAVA_ANNOTATION_LAYER_MAP:
        if pattern.search(text):
            return layer
    return "service"


def _find_matching_brace(text: str, open_brace_index: int) -> int:
    depth = 0
    for i in range(open_brace_index, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text) - 1


def chunk_java_file(text: str, relative_path: Path, module: str) -> ChunkResult:
    is_entity = bool(re.search(r"@Entity\b", text))
    layer = _java_layer(text)
    language = "java"

    class_match = _JAVA_CLASS_RE.search(text)
    class_name = class_match.group("name") if class_match else relative_path.stem

    base_metadata = {
        "source": str(relative_path),
        "module": module,
        "layer": layer,
        "class_name": class_name,
        "language": language,
    }

    documents: list[Document] = []

    # Whole-class chunk (java_entity for entities, java_class otherwise)
    class_type = "java_entity" if is_entity else "java_class"
    documents.extend(
        _make_documents(text, {**base_metadata, "type": class_type})
    )

    # Per-method chunks
    for match in _JAVA_METHOD_RE.finditer(text):
        open_brace_index = match.end() - 1
        close_brace_index = _find_matching_brace(text, open_brace_index)
        method_start = match.start()
        method_text = text[method_start : close_brace_index + 1].strip()
        if not method_text:
            continue
        method_metadata = {
            **base_metadata,
            "type": "java_method",
            "method_name": match.group("name"),
        }
        documents.extend(_make_documents(method_text, method_metadata))

    return ChunkResult(documents=documents, is_entity=is_entity)


# --------------------------------------------------------------------------
# TypeScript / Angular
# --------------------------------------------------------------------------

_TS_DECORATOR_CLASS_RE = re.compile(
    r"@(?P<decorator>Component|Injectable|NgModule|Directive|Pipe)\s*\("
    r"[^)]*\)\s*"
    r"(?:export\s+)?class\s+(?P<name>\w+)"
)


def _ts_chunk_type(decorator: str) -> str:
    return {
        "Component": "angular_component",
        "Injectable": "angular_service",
        "NgModule": "angular_module",
        "Directive": "angular_directive",
        "Pipe": "angular_pipe",
    }.get(decorator, "angular_component")


def chunk_ts_file(text: str, relative_path: Path, module: str) -> ChunkResult:
    matches = list(_TS_DECORATOR_CLASS_RE.finditer(text))
    base_metadata = {
        "source": str(relative_path),
        "module": module,
        "layer": "frontend",
        "language": "typescript",
    }

    documents: list[Document] = []

    if not matches:
        # Plain service/utility/model file with no Angular decorator detected.
        class_name = relative_path.stem
        documents.extend(
            _make_documents(
                text,
                {
                    **base_metadata,
                    "type": "angular_service",
                    "class_name": class_name,
                },
            )
        )
        return ChunkResult(documents=documents)

    for match in matches:
        decorator_start = text.rfind("@" + match.group("decorator"), 0, match.start() + 1)
        decorator_start = decorator_start if decorator_start != -1 else match.start()
        brace_index = text.find("{", match.end())
        if brace_index == -1:
            continue
        close_brace_index = _find_matching_brace(text, brace_index)
        class_text = text[decorator_start : close_brace_index + 1].strip()
        chunk_metadata = {
            **base_metadata,
            "type": _ts_chunk_type(match.group("decorator")),
            "class_name": match.group("name"),
        }
        documents.extend(_make_documents(class_text, chunk_metadata))

    return ChunkResult(documents=documents)


# --------------------------------------------------------------------------
# JavaScript / jQuery
# --------------------------------------------------------------------------

_JS_FUNCTION_RE = re.compile(
    r"(?:^|\n)\s*"
    r"(?:function\s+(?P<fname>\w+)\s*\([^)]*\)\s*\{"
    r"|(?:const|let|var)\s+(?P<vname>\w+)\s*=\s*(?:async\s*)?(?:function\s*)?\([^)]*\)\s*=>?\s*\{"
    r"|\$\.fn\.(?P<jqname>\w+)\s*=\s*function\s*\([^)]*\)\s*\{)",
    re.MULTILINE,
)


def chunk_js_file(text: str, relative_path: Path, module: str) -> ChunkResult:
    base_metadata = {
        "source": str(relative_path),
        "module": module,
        "layer": "frontend",
        "language": "javascript",
        "class_name": relative_path.stem,
    }

    matches = list(_JS_FUNCTION_RE.finditer(text))
    documents: list[Document] = []

    if not matches:
        documents.extend(_make_documents(text, {**base_metadata, "type": "js_file"}))
        return ChunkResult(documents=documents)

    for match in matches:
        function_name = match.group("fname") or match.group("vname") or match.group("jqname")
        open_brace_index = match.end() - 1
        close_brace_index = _find_matching_brace(text, open_brace_index)
        function_text = text[match.start() : close_brace_index + 1].strip()
        documents.extend(
            _make_documents(
                function_text,
                {
                    **base_metadata,
                    "type": "js_function",
                    "function_name": function_name or "anonymous",
                },
            )
        )

    return ChunkResult(documents=documents)


# --------------------------------------------------------------------------
# Generic whole-file fallback (Python, Kotlin, Go, C#, Ruby, Rust, PHP)
# --------------------------------------------------------------------------

_GENERIC_LANGUAGE_BY_EXTENSION = {
    ".py": "python", ".pyw": "python",
    ".kt": "kotlin", ".kts": "kotlin",
    ".go": "go",
    ".cs": "csharp",
    ".rb": "ruby",
    ".rs": "rust",
    ".php": "php",
}


def chunk_generic_file(text: str, relative_path: Path, module: str, suffix: str) -> ChunkResult:
    """Whole-file chunking for every language without a bespoke symbol-level
    chunker above -- see this module's docstring for why building 13 more
    per-language chunkers wasn't attempted here."""
    language = _GENERIC_LANGUAGE_BY_EXTENSION.get(suffix, suffix.lstrip("."))
    base_metadata = {
        "source": str(relative_path),
        "module": module,
        "layer": "backend",
        "language": language,
        "class_name": relative_path.stem,
    }
    documents = _make_documents(text, {**base_metadata, "type": f"{language}_file"})
    return ChunkResult(documents=documents)


# --------------------------------------------------------------------------
# Dispatch + ingestion entry point
# --------------------------------------------------------------------------


def chunk_file(path: Path, repo_path: Path) -> ChunkResult:
    relative_path = path.relative_to(repo_path)
    module = _detect_module(relative_path)
    text = path.read_text(encoding="utf-8", errors="ignore")

    if path.suffix == ".java":
        return chunk_java_file(text, relative_path, module)
    if path.suffix in (".ts", ".tsx"):
        return chunk_ts_file(text, relative_path, module)
    if path.suffix in (".js", ".jsx", ".mjs", ".cjs"):
        return chunk_js_file(text, relative_path, module)
    return chunk_generic_file(text, relative_path, module, path.suffix)


async def ingest_code(
    repo_path: str,
    progress_callback=None,
    *,
    enable_llm_summary: bool | None = None,
    max_concurrency: int = enrich.DEFAULT_MAX_CONCURRENCY,
) -> dict:
    """Walk the repository, chunk every eligible source file, and embed into
    ChromaDB (tier 1: mechanical, always runs), then optionally run the
    LLM-summary enrichment tier (tier 2: see enrich.py) over the same file
    list.

    Java entity classes are written to both ``sf_codebase`` and
    ``sf_jpa_entities``. Everything else goes only to ``sf_codebase``.

    ``enable_llm_summary`` defaults to settings.INGEST_LLM_SUMMARY_ENABLED
    when not given explicitly (a per-request override, e.g. for a quick
    raw-only re-index of a huge repo without the LLM-cost tier).
    """
    repo = Path(repo_path)
    if not repo.is_dir():
        raise FileNotFoundError(f"Repository path not found: {repo_path}")

    codebase_store = get_vector_store("codebase")
    entities_store = get_vector_store("entities")

    files = sorted(iter_source_files(repo))
    total_files = len(files)

    # Files present in a prior ingestion of this repo but deleted from disk
    # since are never visited by the loop below, so their stale chunks would
    # otherwise never get cleared -- diff against what Chroma already has
    # (acting as its own manifest) and purge those up front.
    current_sources = {str(path.relative_to(repo)) for path in files}
    previously_seen = await list_distinct_sources("codebase")
    for removed_source in previously_seen - current_sources:
        await delete_by_source("codebase", removed_source)
        await delete_by_source("entities", removed_source)

    codebase_batch: list[Document] = []
    entities_batch: list[Document] = []
    files_processed = 0
    chunks_indexed = 0
    entity_chunks_indexed = 0
    errors: list[str] = []

    async def flush(force: bool = False):
        nonlocal codebase_batch, entities_batch, chunks_indexed, entity_chunks_indexed
        if codebase_batch and (force or len(codebase_batch) >= BATCH_SIZE):
            ids = [_document_id(doc.metadata) for doc in codebase_batch]
            await codebase_store.aadd_documents(codebase_batch, ids=ids)
            chunks_indexed += len(codebase_batch)
            codebase_batch = []
        if entities_batch and (force or len(entities_batch) >= BATCH_SIZE):
            ids = [_document_id(doc.metadata) for doc in entities_batch]
            await entities_store.aadd_documents(entities_batch, ids=ids)
            entity_chunks_indexed += len(entities_batch)
            entities_batch = []

    for index, path in enumerate(files, start=1):
        try:
            result = chunk_file(path, repo)
            relative_path = str(path.relative_to(repo))
            # Clear this file's prior raw-chunk set before adding its fresh
            # one -- deterministic IDs alone only upsert chunks that still
            # exist in this run; a renamed/removed method or a deleted file
            # would otherwise leave its old chunks behind as invisible,
            # stale entries (see chroma_client.delete_by_source's
            # docstring). Excludes "llm_summary"-typed documents, which
            # enrich.py separately owns for this same source path -- a
            # blanket delete here would wipe out a summary from a prior run
            # that this run's enrichment pass decides to skip re-writing
            # (unchanged content, per its own manifest).
            await delete_by_source_excluding_type("codebase", relative_path, "llm_summary")
            # Entities has no llm_summary documents to protect (enrich.py
            # only ever writes into "codebase"), so a blanket delete is
            # fine, and is cleared unconditionally (not just when
            # result.is_entity) since a class that *stopped* being an
            # @Entity between runs must have its old entity-collection
            # chunks removed even though this run won't re-add any.
            await delete_by_source("entities", relative_path)
            codebase_batch.extend(result.documents)
            if result.is_entity:
                entities_batch.extend(result.documents)
            files_processed += 1
        except Exception as exc:  # noqa: BLE001 - surfaced to caller via errors list
            logger.exception("Failed to chunk %s", path)
            errors.append(f"{path}: {exc}")

        await flush()

        if progress_callback:
            await progress_callback(index, total_files)

    await flush(force=True)

    resolved_enable_llm_summary = (
        settings.INGEST_LLM_SUMMARY_ENABLED if enable_llm_summary is None else enable_llm_summary
    )
    enrichment_result = await enrich.enrich_repository(
        repo, files, enabled=resolved_enable_llm_summary, max_concurrency=max_concurrency
    )

    return {
        "files_processed": files_processed,
        "files_total": total_files,
        "chunks_indexed": chunks_indexed,
        "entity_chunks_indexed": entity_chunks_indexed,
        "errors": errors + enrichment_result["errors"],
        "llm_summary_enabled": enrichment_result["enabled"],
        "files_summarized": enrichment_result["files_summarized"],
        "files_skipped_unchanged": enrichment_result["files_skipped_unchanged"],
    }
