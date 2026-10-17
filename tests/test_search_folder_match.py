"""Library search matches folder names, not just filenames.

Typing a folder's name ("iceland 2023") has to find the clips inside it even
when no filename carries those words — that is how people actually remember a
library. The match is scoped to the path *relative to the library root* so the
root's own name (and its ancestors) can never match everything.
"""

import os

from media_library.core.state import state
from media_library.db.connection import db_conn
from media_library.services.media_service import search_backup_index


def _insert(media_dir, folder, name):
    path = (
        os.path.join(str(media_dir), folder, name)
        if folder
        else os.path.join(str(media_dir), name)
    )
    with db_conn() as conn, conn:
        conn.execute(
            """INSERT OR REPLACE INTO files (path, name, folder_path, type, ext, size, size_formatted, mtime)
               VALUES (?, ?, ?, 'video', '.mp4', 100, '100 B', 10.0)""",
            (
                path,
                name,
                os.path.join(str(media_dir), folder) if folder else str(media_dir),
            ),
        )
    return path


def test_search_matches_the_containing_folder_name(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)

    _insert(media, "Iceland 2023", "DJI_0001.mp4")
    _insert(media, "Iceland 2023", "DJI_0002.mp4")
    _insert(media, "Comedy", "funny_clip.mp4")

    names = {r["name"] for r in search_backup_index("iceland")}
    assert names == {"DJI_0001.mp4", "DJI_0002.mp4"}

    # Multi-word queries span the folder name too.
    assert len(search_backup_index("iceland 2023")) == 2
    # A word in neither the name nor the folder path still matches nothing.
    assert search_backup_index("reykjavik") == []


def test_filename_matches_still_win_over_folder_matches(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)

    _insert(media, "Comedy", "standup_special.mp4")
    _insert(media, "Documentaries", "comedy_of_errors.mp4")

    results = search_backup_index("comedy")
    names = {r["name"] for r in results}
    assert names == {"standup_special.mp4", "comedy_of_errors.mp4"}

    # Narrowing by type still applies on top of the folder match.
    assert [r["name"] for r in search_backup_index("comedy", type_filter="image")] == []


def test_library_root_and_its_ancestors_are_not_searchable(sandbox_env):
    """`/…/sandbox/media/…` must not make "sandbox" or "media" match everything."""
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)

    _insert(media, "", "clip_one.mp4")
    _insert(media, "Holidays", "clip_two.mp4")

    # The root itself, and every ancestor above it, are outside the searched
    # relative path; only the folder path *inside* the library can match.
    assert search_backup_index("media") == []
    assert search_backup_index("sandbox") == []
    assert [r["name"] for r in search_backup_index("holidays")] == ["clip_two.mp4"]


def test_search_results_still_expose_the_folder(sandbox_env):
    media = sandbox_env["media_dir"]
    state["destination_dir"] = str(media)
    _insert(media, "Iceland 2023", "DJI_0001.mp4")

    result = search_backup_index("iceland")[0]
    assert result["folder_name"] == "Iceland 2023"
    assert result["rel_folder"] == "Iceland 2023"
    assert result["media_url"].startswith("/api/backup/")
