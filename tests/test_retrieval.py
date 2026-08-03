"""DA-206 -- chunk boundaries land on real function/class boundaries."""

from __future__ import annotations

from agent.retrieval import chunk_repo, chunk_source


def test_function_chunk_boundaries_match_the_real_function():
    source = "def foo(a, b):\n    return a + b\n"
    chunks = chunk_source(source, file_path="calc.py")

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.name == "foo"
    assert chunk.kind == "function"
    assert chunk.start_line == 1
    assert chunk.end_line == 2
    assert chunk.text == "def foo(a, b):\n    return a + b"


def test_multiple_top_level_defs_each_get_their_own_chunk():
    source = "def foo():\n    pass\n\n\ndef bar():\n    pass\n"
    chunks = chunk_source(source, file_path="m.py")

    assert [(c.name, c.start_line, c.end_line) for c in chunks] == [
        ("foo", 1, 2),
        ("bar", 5, 6),
    ]


def test_class_chunk_includes_its_methods_as_one_unit():
    source = (
        "class Bar:\n"
        "    def one(self):\n"
        "        return 1\n"
        "\n"
        "    def two(self):\n"
        "        return 2\n"
    )
    chunks = chunk_source(source, file_path="m.py")

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.kind == "class"
    assert chunk.name == "Bar"
    # Methods are not split out as their own top-level chunks...
    assert "def one" in chunk.text
    assert "def two" in chunk.text
    # ...and the chunk spans the whole class body.
    assert chunk.start_line == 1
    assert chunk.end_line == 6


def test_async_function_is_chunked_as_a_function():
    source = "async def fetch():\n    pass\n"
    chunks = chunk_source(source, file_path="m.py")

    assert len(chunks) == 1
    assert chunks[0].kind == "function"
    assert chunks[0].name == "fetch"
    assert chunks[0].text.startswith("async def fetch")


def test_nested_helper_function_is_not_extracted_separately():
    source = "def outer():\n    def helper():\n        return 1\n    return helper()\n"
    chunks = chunk_source(source, file_path="m.py")

    assert len(chunks) == 1
    assert chunks[0].name == "outer"
    assert "def helper" in chunks[0].text


def test_chunk_repo_walks_files_with_relative_paths(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def a():\n    pass\n")
    (tmp_path / "pkg" / "b.py").write_text("class B:\n    pass\n")
    (tmp_path / "pkg" / "__pycache__").mkdir()
    (tmp_path / "pkg" / "__pycache__" / "junk.py").write_text("def junk():\n    pass\n")

    chunks = chunk_repo(tmp_path)

    assert {c.file_path for c in chunks} == {"pkg/a.py", "pkg/b.py"}
    assert {c.name for c in chunks} == {"a", "B"}


def test_empty_file_yields_no_chunks():
    assert chunk_source("", file_path="empty.py") == []


def test_module_level_code_outside_defs_is_not_chunked():
    source = "x = 1\nprint(x)\n\ndef f():\n    pass\n"
    chunks = chunk_source(source, file_path="m.py")

    assert len(chunks) == 1
    assert chunks[0].name == "f"
