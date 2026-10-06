"""Database module exports."""

from media_library.db.connection import get_db
from media_library.db.schema import init_backup_db
from media_library.db.indexer import (
    indexer_state,
    indexer_lock,
    index_folder,
    add_file_to_index,
    remove_file_from_index,
    update_file_in_index,
    prune_stale_index_entries,
    run_background_crawler,
)

__all__ = [
    "get_db",
    "init_backup_db",
    "indexer_state",
    "indexer_lock",
    "index_folder",
    "add_file_to_index",
    "remove_file_from_index",
    "update_file_in_index",
    "prune_stale_index_entries",
    "run_background_crawler",
]
