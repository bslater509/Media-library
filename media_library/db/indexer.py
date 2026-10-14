"""Media library filesystem scanning, folder indexing, and pruning.

Split into ``indexer_scan``, ``indexer_files``, ``indexer_prune`` and
``indexer_crawl``; everything is re-exported here so existing imports of
``media_library.db.indexer`` keep working.
"""

from media_library.core.config import DEFAULT_DESTINATION_DIR, THUMBNAIL_CACHE_DIR  # noqa: F401
from media_library.db.indexer_state import indexer_lock, indexer_state  # noqa: F401
from media_library.db.indexer_scan import (  # noqa: F401
    _index_zip_archive,
    _scan_folder_entries,
    _scan_root_dirs,
    _update_folder_in_db,
    index_folder,
)
from media_library.db.indexer_files import (  # noqa: F401
    add_file_to_index,
    delete_path_from_index,
    move_path_in_index,
    remove_file_from_index,
    update_file_in_index,
)
from media_library.db.indexer_prune import (  # noqa: F401
    _clean_orphaned_thumbnails,
    _count_files_in_folders,
    _get_stale_folders,
    _path_confirmed_gone,
    _record_index_health,
    _remove_stale_folders,
    deep_clean_database,
    prune_stale_index_entries,
)
from media_library.db.indexer_crawl import run_background_crawler  # noqa: F401
