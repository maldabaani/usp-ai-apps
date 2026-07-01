"""One-time ingestion of the Maven multi-module monorepo codebase into ChromaDB.

Implements the "smart chunking" rules:

- Java: chunked by class AND by method separately. Entity classes (``@Entity``)
  are indexed into BOTH ``sf_codebase`` and ``sf_jpa_entities``.
- TypeScript/Angular: chunked by component, service, and module separately.
- JavaScript/jQuery: chunked by function. Falls back to whole-file chunking
  when no functions are detected.
- HTML: skipped entirely.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ingestion.chroma_client import get_vector_store

logger = logging.getLogger(__name__)

CHUNK_SIZE_TOKENS = 1500
CHUNK_OVERLAP_TOKENS = 150
CHARS_PER_TOKEN = 4
MAX_CHUNK_CHARS = CHUNK_SIZE_TOKENS * CHARS_PER_TOKEN
OVERLAP_CHARS = CHUNK_OVERLAP_TOKENS * CHARS_PER_TOKEN

SKIP_DIR_NAMES = {"node_modules", "target", "dist", ".git"}
JAVA_EXCLUDE_SUFFIXES = ("Test.java", "IT.java")
TS_EXCLUDE_SUFFIXES = (".spec.ts",)
SOURCE_EXTENSIONS = {".java", ".ts", ".js"}

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


def _make_documents(
    text: str,
    base_metadata: dict,
) -> list[Document]:
    pieces = _split_overflow(text)
    docs = []
    for index, piece in enumerate(pieces):
        metadata = dict(base_metadata)
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
# Dispatch + ingestion entry point
# --------------------------------------------------------------------------


def chunk_file(path: Path, repo_path: Path) -> ChunkResult:
    relative_path = path.relative_to(repo_path)
    module = _detect_module(relative_path)
    text = path.read_text(encoding="utf-8", errors="ignore")

    if path.suffix == ".java":
        return chunk_java_file(text, relative_path, module)
    if path.suffix == ".ts":
        return chunk_ts_file(text, relative_path, module)
    if path.suffix == ".js":
        return chunk_js_file(text, relative_path, module)
    return ChunkResult(documents=[])


async def ingest_code(
    repo_path: str,
    progress_callback=None,
) -> dict:
    """Walk the monorepo, chunk every eligible source file, and embed into ChromaDB.

    Java entity classes are written to both ``sf_codebase`` and
    ``sf_jpa_entities``. Everything else goes only to ``sf_codebase``.
    """
    repo = Path(repo_path)
    if not repo.is_dir():
        raise FileNotFoundError(f"Repository path not found: {repo_path}")

    codebase_store = get_vector_store("codebase")
    entities_store = get_vector_store("entities")

    files = sorted(iter_source_files(repo))
    total_files = len(files)

    codebase_batch: list[Document] = []
    entities_batch: list[Document] = []
    files_processed = 0
    chunks_indexed = 0
    entity_chunks_indexed = 0
    errors: list[str] = []

    async def flush(force: bool = False):
        nonlocal codebase_batch, entities_batch, chunks_indexed, entity_chunks_indexed
        if codebase_batch and (force or len(codebase_batch) >= BATCH_SIZE):
            await codebase_store.aadd_documents(codebase_batch)
            chunks_indexed += len(codebase_batch)
            codebase_batch = []
        if entities_batch and (force or len(entities_batch) >= BATCH_SIZE):
            await entities_store.aadd_documents(entities_batch)
            entity_chunks_indexed += len(entities_batch)
            entities_batch = []

    for index, path in enumerate(files, start=1):
        try:
            result = chunk_file(path, repo)
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

    return {
        "files_processed": files_processed,
        "files_total": total_files,
        "chunks_indexed": chunks_indexed,
        "entity_chunks_indexed": entity_chunks_indexed,
        "errors": errors,
    }
