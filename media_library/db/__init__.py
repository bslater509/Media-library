"""Database module exports."""

from media_library.db.connection import db_conn, get_db
from media_library.db.indexer import (
    add_file_to_index,
    index_folder,
    indexer_lock,
    indexer_state,
    prune_stale_index_entries,
    remove_file_from_index,
    run_background_crawler,
    update_file_in_index,
)
from media_library.db.schema import init_backup_db

__all__ = [
    "add_file_to_index",
    "db_conn",
    "get_db",
    "index_folder",
    "indexer_lock",
    "indexer_state",
    "init_backup_db",
    "prune_stale_index_entries",
    "remove_file_from_index",
    "run_background_crawler",
    "update_file_in_index",
]
