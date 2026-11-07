"""Empty-folder cleanup.

Candidates come from the index (leaf folders with no files and no child
folders). Deletion verifies the directory is genuinely empty on disk, removing
only benign OS junk first, and updates the index in the same operation.
"""

from __future__ import annotations

import os
from typing import Any

from media_library import writes
from media_library.db import reader, writer
from media_library.paths import resolve_library_path

_JUNK = {".DS_Store", "Thumbs.db", "thumbs.db", "desktop.ini", ".directory"}


def candidates() -> dict[str, Any]:
    root = resolve_library_path(None)
    with reader() as conn:
        rows = conn.execute(
            """
            SELECT fo.path, fo.name, fo.parent_path
            FROM folders fo
            WHERE COALESCE(fo.is_zip, 0) = 0
              AND fo.path != ?
              AND NOT EXISTS (
                  SELECT 1 FROM files x WHERE x.folder_path = fo.path)
              AND NOT EXISTS (
                  SELECT 1 FROM folders c WHERE c.parent_path = fo.path)
            ORDER BY LOWER(fo.path)
            """,
            (root,),
        ).fetchall()
    items = [
        {"path": row["path"], "name": row["name"], "parent_path": row["parent_path"]}
        for row in rows
        if "_media_library_backup" not in row["path"]
    ]
    return {"count": len(items), "folders": items}


def delete_empty(paths: list[str]) -> dict[str, Any]:
    if not paths:
        raise writes.WriteError("No folders selected", 400)
    if len(paths) > 1000:
        raise writes.WriteError("Too many folders in one request", 400)
    root = resolve_library_path(None)
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    parents: set[str] = set()

    for raw in paths:
        try:
            target = resolve_library_path(raw)
            if target == root:
                raise writes.WriteError("Refusing to delete the library root", 400)
            if not os.path.isdir(target):
                raise writes.WriteError("Folder not found", 404)
            entries = os.listdir(target)
            junk = [name for name in entries if name in _JUNK]
            remaining = [name for name in entries if name not in _JUNK]
            if remaining:
                raise writes.WriteError("Folder is not empty", 409)
            for name in junk:
                try:
                    os.remove(os.path.join(target, name))
                except OSError:
                    pass
            os.rmdir(target)
            deleted.append(target)
            parent = os.path.dirname(target)
            if parent:
                parents.add(parent)
        except writes.WriteError as exc:
            failed.append({"path": raw, "error": exc.message})
        except OSError as exc:
            failed.append({"path": raw, "error": str(exc)})

    if deleted:
        with writer() as conn, conn:
            for target in deleted:
                conn.execute("DELETE FROM folders WHERE path = ?", (target,))
            for parent in parents:
                conn.execute(
                    "UPDATE folders SET subfolder_count = ("
                    "SELECT COUNT(*) FROM folders WHERE parent_path = ?) WHERE path = ?",
                    (parent, parent),
                )
    return {"deleted": deleted, "failed": failed, "remaining": candidates()["count"]}
