"""DA-205 -- tree-sitter chunking of Python repos at function/class granularity.

Retrieval needs units smaller than "whole file" (an LLM's context is
finite) but coherent enough to be useful in isolation -- a function or
class body, not an arbitrary line window that might cut either in half.
tree-sitter gives real AST boundaries instead of a naive line-count split.

Scope: only *top-level* functions and classes become chunks. A method
stays embedded in its class's chunk text rather than being split out on
its own, since a method read without its class (sibling methods, shared
attributes) usually isn't independently useful. A nested helper function
defined inside another function is likewise left inside its parent's
chunk text, not extracted separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tree_sitter
import tree_sitter_python as tspython

_LANGUAGE = tree_sitter.Language(tspython.language())
_CHUNK_NODE_TYPES = {"function_definition", "class_definition"}

_IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}


@dataclass(frozen=True)
class Chunk:
    """One retrievable unit: a top-level function or class from a real file."""

    file_path: str
    name: str
    kind: str  # "function" | "class"
    start_line: int  # 1-indexed, inclusive
    end_line: int  # 1-indexed, inclusive
    text: str


def chunk_source(source: str, file_path: str) -> list[Chunk]:
    """Chunk one Python file's source into its top-level functions and classes."""
    source_bytes = source.encode("utf-8")
    parser = tree_sitter.Parser(_LANGUAGE)
    tree = parser.parse(source_bytes)

    chunks: list[Chunk] = []
    for node in tree.root_node.children:
        if node.type not in _CHUNK_NODE_TYPES:
            continue

        name_node = node.child_by_field_name("name")
        name = (
            source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8")
            if name_node is not None
            else "<anonymous>"
        )
        kind = "function" if node.type == "function_definition" else "class"

        chunks.append(
            Chunk(
                file_path=file_path,
                name=name,
                kind=kind,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                text=source_bytes[node.start_byte : node.end_byte].decode("utf-8"),
            )
        )
    return chunks


def chunk_file(path: Path) -> list[Chunk]:
    return chunk_source(path.read_text(encoding="utf-8"), file_path=str(path))


def chunk_repo(repo_root: Path) -> list[Chunk]:
    """Chunk every `.py` file under `repo_root`, skipping VCS/venv/cache dirs.

    Chunk `file_path`s are relative to `repo_root`, so results are stable
    regardless of where the repo happens to be checked out.
    """
    chunks: list[Chunk] = []
    for path in sorted(repo_root.rglob("*.py")):
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        relative = str(path.relative_to(repo_root))
        source = path.read_text(encoding="utf-8")
        chunks.extend(chunk_source(source, file_path=relative))
    return chunks
