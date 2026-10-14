"""Deduplication service supporting exact chunk hash matching and perceptual visual clustering.

Split into ``dedup_common``, ``dedup_visual``, ``dedup_backup`` and ``dedup_resolve``;
everything is re-exported here so existing imports keep working.
"""

from media_library.services.dedup_common import (  # noqa: F401
    _files_have_identical_content,
    compute_quick_hash,
    compute_thumbnail_dhash,
    compute_thumbnail_phash,
    get_ignored_pairs_set,
    invalidate_visual_cache,
    normalize_pair,
    score_duplicate_file,
    visual_duplicates_cache,
)
from media_library.services.dedup_visual import (  # noqa: F401
    _prune_missing_files_bg,
    filter_and_prune_groups,
    find_visual_duplicates,
)
from media_library.services.dedup_backup import get_backup_duplicates  # noqa: F401
from media_library.services.dedup_resolve import (  # noqa: F401
    autodelete_verified_duplicates,
    resolve_duplicate_group,
)
