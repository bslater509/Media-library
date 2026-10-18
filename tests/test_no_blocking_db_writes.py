"""Guards the invariant that keeps the web server reachable.

SQLite admits one writer at a time, and a connection waits for that write lock
for its whole ``timeout`` (30s) before raising ``database is locked``. Awaiting
such a write on the event loop stalls *every* request for the duration — the
process stays "active" and bound to its port while answering nothing, which is
exactly how a contended write once took the whole site down.

Two layers of guard:

* a source scan that fails when a mutating ``with db_conn()`` block appears
  directly in a coroutine (the shape that caused the outage), and
* a behavioural check that ``run_db_write`` really does leave the loop thread.
"""

import ast
import asyncio
import threading
from pathlib import Path

import pytest

from media_library.db.connection import run_db_write

_PACKAGE = Path(__file__).resolve().parent.parent / "media_library"

# Statements that take the write lock. DDL is included because it takes it too.
_MUTATING_SQL = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER")


def _is_db_conn_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "db_conn"
    )


def _is_conn_context(node: ast.AST) -> bool:
    """True for ``with conn:`` — the block that actually begins the transaction."""
    return isinstance(node, ast.With) and any(
        getattr(it.context_expr, "id", None) == "conn" for it in node.items
    )


def _mutating_sql(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return any(
            node.value.lstrip().upper().startswith(stmt) for stmt in _MUTATING_SQL
        )
    return False


def _walk_same_scope(node: ast.AST):
    """Yield every descendant that belongs to the *current* function scope.

    A nested function body is a separate scope — ``def _update_db(): with
    db_conn() ...`` inside a coroutine is fine as long as it is handed to a
    thread (that is what ``asyncio.to_thread`` is for, and the coroutines that
    do it are scanned on their own pass).
    """
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        yield child
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stack.extend(ast.iter_child_nodes(child))


def _block_mutates(with_node: ast.With) -> bool:
    for child in _walk_same_scope(with_node):
        if _is_conn_context(child) or _mutating_sql(child):
            return True
    return False


class _OnLoopWriteFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.offenders: list[tuple[str, int]] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        for child in _walk_same_scope(node):
            if (
                isinstance(child, ast.With)
                and any(_is_db_conn_call(it.context_expr) for it in child.items)
                and _block_mutates(child)
            ):
                self.offenders.append((node.name, child.lineno))
        # Nested coroutines are separate scopes; let the visitor reach them.
        self.generic_visit(node)


def _scan_module(path: Path) -> list[tuple[str, int]]:
    finder = _OnLoopWriteFinder()
    finder.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return finder.offenders


def test_no_mutating_db_conn_on_the_event_loop():
    offenders = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        for name, line in _scan_module(path):
            offenders.append(f"{path.relative_to(_PACKAGE.parent)}:{line} (in {name})")

    assert not offenders, (
        "These mutate SQLite directly in a coroutine, so a contended write "
        "blocks the entire event loop (and every request with it). Route the "
        "write through `await run_db_write(...)` or `asyncio.to_thread(...)`:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_run_db_write_leaves_the_loop_thread():
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def _probe() -> str:
        seen.append(threading.get_ident())
        return "done"

    assert asyncio.run(run_db_write(_probe)) == "done"
    assert seen and seen[0] != loop_thread


def test_run_db_write_propagates_errors():
    def _boom() -> None:
        raise RuntimeError("locked")

    with pytest.raises(RuntimeError, match="locked"):
        asyncio.run(run_db_write(_boom))
