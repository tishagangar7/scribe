"""DA-116 -- static safety net for the same bugs DivergenceError catches.

`DivergenceError` (see ADR 0004) catches nondeterminism at *runtime*, after a
run has already been paid for and diverged. This module catches the common
cases *before* a run ever starts, by walking the AST of every `@workflow`
function looking for direct calls to `datetime.now`/`time.time`/`random.*`/
`uuid.uuid4`, or `os.environ` access -- all of which must go through
`ctx.now`/`ctx.random`/`ctx.uuid` instead to be replay-stable.

Not exhaustive: this is a lint, not a type system. `getattr(datetime, "now")()`
or reassigning `now = datetime.now` before calling it will dodge it. It
resolves plain `import x [as y]` and `from x import y [as z]` aliasing, which
covers how these modules are actually imported in practice.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# (module, attribute) -> human-readable name of the banned call.
_BANNED_ATTR_CALLS: dict[tuple[str, str], str] = {
    ("datetime", "now"): "datetime.now()",
    ("datetime", "utcnow"): "datetime.utcnow()",
    ("time", "time"): "time.time()",
}
# Modules where *any* attribute call is banned (random.random(),
# random.randint(), random.choice(), ...).
_BANNED_ANY_ATTR_MODULES: dict[str, str] = {"random": "random"}
_BANNED_UUID_CALL = ("uuid", "uuid4")


@dataclass(frozen=True)
class Violation:
    file: str
    lineno: int
    workflow_name: str
    message: str

    def __str__(self) -> str:
        return (
            f"{self.file}:{self.lineno}: in @workflow {self.workflow_name!r}: "
            f"{self.message}"
        )


class _ImportTracker(ast.NodeVisitor):
    """Maps local names back to the real module they came from."""

    def __init__(self) -> None:
        self.module_alias: dict[str, str] = {}
        self.from_alias: dict[str, tuple[str, str]] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.module_alias[local] = alias.name.split(".")[0]

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            return
        for alias in node.names:
            local = alias.asname or alias.name
            self.from_alias[local] = (node.module, alias.name)


def _is_workflow_decorator(dec: ast.expr) -> bool:
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id == "workflow"
    if isinstance(target, ast.Attribute):
        return target.attr == "workflow"
    return False


def _base_name(expr: ast.expr) -> str | None:
    """Innermost Name id of an attribute chain: `a.b.c` -> `'a'`."""
    while isinstance(expr, ast.Attribute):
        expr = expr.value
    return expr.id if isinstance(expr, ast.Name) else None


def _resolved_module(name: str, imports: _ImportTracker) -> str | None:
    if name in imports.module_alias:
        return imports.module_alias[name]
    if name in imports.from_alias:
        return imports.from_alias[name][0]
    return None


def _classify(module: str, attr: str) -> str | None:
    """Return a violation message for (module, attr) if it's banned, else None."""
    if (module, attr) in _BANNED_ATTR_CALLS:
        name = _BANNED_ATTR_CALLS[(module, attr)]
        return f"{name} is nondeterministic -- use ctx.now/ctx.random/ctx.uuid"
    if module in _BANNED_ANY_ATTR_MODULES:
        return f"{module}.{attr}() is nondeterministic -- use ctx.random"
    if (module, attr) == _BANNED_UUID_CALL:
        return "uuid.uuid4() is nondeterministic -- use ctx.uuid"
    if module == "os" and attr == "getenv":
        return "os.getenv() reads unrecorded external state -- read it via a ctx.step"
    return None


def _check_workflow_body(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    filename: str,
    imports: _ImportTracker,
) -> list[Violation]:
    found: list[Violation] = []

    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr == "environ":
            base = _base_name(node.value)
            if base and _resolved_module(base, imports) == "os":
                found.append(
                    Violation(
                        filename,
                        node.lineno,
                        fn.name,
                        "os.environ reads unrecorded external state -- "
                        "read it via a ctx.step",
                    )
                )
            continue

        if not isinstance(node, ast.Call):
            continue

        func = node.func
        module: str | None = None
        attr: str | None = None

        if isinstance(func, ast.Attribute):
            base = _base_name(func.value)
            module = _resolved_module(base, imports) if base else None
            attr = func.attr
        elif isinstance(func, ast.Name):
            resolved = imports.from_alias.get(func.id)
            if resolved:
                module, attr = resolved

        if module is not None and attr is not None:
            message = _classify(module, attr)
            if message is not None:
                found.append(Violation(filename, node.lineno, fn.name, message))

    return found


def check_source(source: str, filename: str = "<string>") -> list[Violation]:
    """Check one module's source for nondeterminism inside `@workflow` bodies."""
    tree = ast.parse(source, filename=filename)

    imports = _ImportTracker()
    imports.visit(tree)

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not any(_is_workflow_decorator(d) for d in node.decorator_list):
            continue
        violations.extend(_check_workflow_body(node, filename, imports))
    return violations


def check_file(path: Path) -> list[Violation]:
    return check_source(path.read_text(), filename=str(path))


def check_paths(paths: list[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for path in paths:
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for f in files:
            violations.extend(check_file(f))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    paths = [Path(a) for a in args] or [Path("agent"), Path("scribe"), Path("eval")]

    violations = check_paths(paths)
    for v in violations:
        print(v)

    if violations:
        print(f"\n{len(violations)} determinism violation(s) found.")
        return 1
    print("No determinism violations found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
