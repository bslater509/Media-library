"""Event-loop guard: no mutating database work may run inside a coroutine.

SQLite admits one writer at a time, and a connection waits for that lock for its
whole timeout. A coroutine that opens a write transaction therefore stalls every
request on the server for the duration. v2 routers perform their mutations
through ``await asyncio.to_thread(...)``; this test fails the build when a
coroutine reaches for the writer directly, or executes mutating SQL itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "media_library_v2"

_OFFLOAD_CALLS = {"to_thread", "run_in_executor"}
_MUTATING_SQL_PREFIXES = (
    "INSERT ",
    "UPDATE ",
    "DELETE ",
    "REPLACE ",
    "CREATE ",
    "DROP ",
    "ALTER ",
    "VACUUM",
    "PRAGMA journal_mode",
)


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        parts: list[str] = [func.attr]
        while isinstance(func.value, ast.Attribute):
            parts.append(func.value.attr)
            func = func.value
        if isinstance(func.value, ast.Name):
            parts.append(func.value.id)
        return ".".join(reversed(parts))
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _offloaded_ids(function: ast.AST) -> set[int]:
    """Ids of every node that sits inside an offload call's arguments."""
    ids: set[int] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and _call_name(node).split(".")[-1] in (
            _OFFLOAD_CALLS
        ):
            for argument in node.args:
                ids.update(id(child) for child in ast.walk(argument))
    return ids


def _write_function_names() -> set[str]:
    """Top-level function names defined in ``writes.py`` (the mutators)."""
    module = PACKAGE / "writes.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


# Errors and helpers in ``writes`` (``WriteError``) are not mutations; only the
# functions that actually touch the database are.
_WRITE_FUNCS = _write_function_names()


def _violations(source: str) -> list[str]:
    tree = ast.parse(source)
    offloaded = _offloaded_ids(tree)
    problems: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.AsyncFunctionDef, ast.AsyncFor)):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "writes"
                and node.attr in _WRITE_FUNCS
                and id(node) not in offloaded
            ):
                problems.append(
                    f"{function.name}: uses writes.{node.attr} on the event loop"
                )
            if (
                isinstance(node, ast.Name)
                and node.id == "writer"
                and id(node) not in offloaded
            ):
                problems.append(
                    f"{function.name}: opens the write connection on the event loop"
                )
            if isinstance(node, ast.Call) and _call_name(node).endswith(
                ("execute", "executemany")
            ):
                if not node.args:
                    continue
                first = node.args[0]
                if (
                    isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                    and first.value.strip().upper().startswith(_MUTATING_SQL_PREFIXES)
                    and id(node) not in offloaded
                ):
                    problems.append(
                        f"{function.name}: runs mutating SQL on the event loop"
                    )
    return problems


def test_the_guard_catches_both_shapes():
    bad = """
async def handler():
    with writer() as conn:
        conn.execute("DELETE FROM files")
    writes.delete_files(["a"])
    return await asyncio.to_thread(writes.delete_files, ["b"])
"""
    problems = _violations(bad)
    assert len(problems) == 3
    assert any("write connection" in p for p in problems)
    assert any("mutating SQL" in p for p in problems)
    assert any("writes.delete_files" in p for p in problems)


def test_offload_calls_are_allowed():
    good = """
async def handler():
    return await asyncio.to_thread(writes.delete_files, ["a"])
"""
    assert _violations(good) == []


def test_no_router_mutates_on_the_event_loop():
    offenders: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        problems = _violations(path.read_text(encoding="utf-8"))
        if problems:
            offenders[str(path.relative_to(PACKAGE.parent))] = problems
    assert not offenders, (
        "Mutating database work runs inside a coroutine:\n"
        + "\n".join(f"{name}: {issues}" for name, issues in offenders.items())
    )
