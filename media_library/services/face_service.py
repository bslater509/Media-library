"""Faces, People clusters, and the face-crop cache.

Sits between the :mod:`media_library.services.face_engine` (detection +
embeddings) and the ``/people`` surface: it stores detections, groups them into
person clusters, keeps a small cropped thumbnail per face, and shapes the rows
the API returns.

Grouping is **incremental**: a new face joins the nearest existing person when
the cosine distance to that person's centroid is within
``FACE_MATCH_THRESHOLD``, and otherwise starts a new (unnamed) person. The
explicit ``recluster_people`` maintenance action re-groups everything at once
and re-attaches the user's names by nearest centroid, so a wrong early grouping
can be fixed without losing naming work.

Cost control: the centroid cache is one pass over the ``faces`` table, reused
across many worker batches and only rebuilt when something outside the running
batch mutated the data (a merge, split, dismissal or re-cluster). Embeddings are
128 float32 values per face and compared by brute-force dot product, which is
exact and cheaper than any index at these sizes.

Configuration (thresholds, cache directory and its ceiling) is read from
``media_library.core.config`` at call time, never bound at import.
"""

import hashlib
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from media_library.core import config
from media_library.core.constants import SQL_IN_CHUNK
from media_library.core.state import format_size
from media_library.db.connection import db_conn, get_current_db_path
from media_library.services.face_engine import EMBEDDING_DIM, engine_status
from media_library.services.library_query import _build_file_entry

logger = logging.getLogger(__name__)

_GIB = 1024 * 1024 * 1024

#: Face crops are square JPEGs cut from the 480px scan image.
CROP_SIZE = 160
CROP_QUALITY = 85
#: Extra margin around the detected box, as a fraction of its size.
CROP_MARGIN = 0.28

#: Frame time stored for a face found in a still image (never NULL, so the
#: UNIQUE(file_path, frame_time, x, y) key is exact — SQLite treats NULLs as
#: distinct and would happily store the same face twice).
IMAGE_FRAME_TIME = -1.0

# Only finished crops count: `face_<24 hex>.jpg`. Writers use a
# `face_<hash>.<uuid>.tmp.jpg` sibling, which carries an extra segment.
_FINAL_CROP_RE = re.compile(r"^face_[0-9a-f]{24}\.jpg$")

#: Faces per agglomerative chunk; keeps the re-cluster pass' memory bounded
#: (scipy's linkage materialises an O(chunk^2) distance matrix).
_CLUSTER_CHUNK = 3000

#: How many folder labels a cluster row carries (`folder_names`): enough to say
#: where its faces were filed without turning the row into a path dump.
_FOLDER_LABELS = 3

_USAGE_CACHE: dict[str, Any] = {"key": "", "at": 0.0, "files": 0, "bytes": 0}
_USAGE_TTL_S = 60.0
_LRU_STATE: dict[str, Any] = {"key": "", "at": 0.0}
_LRU_MIN_INTERVAL_S = 60.0
_LRU_TARGET_RATIO = 0.9

# Centroid cache: rebuilt when ``revision`` changes (external mutations), not on
# the worker's own inserts (which update it incrementally).
_CENTROIDS: dict[str, Any] = {"revision": 0, "key": "", "vecs": {}, "counts": {}}
_CENTROIDS_LOCK = threading.RLock()
_REVISION = 0


# --------------------------------------------------------------------- config


def face_match_threshold() -> float:
    try:
        return float(getattr(config, "FACE_MATCH_THRESHOLD", 0.36))
    except (TypeError, ValueError):
        return 0.36


#: Strictness presets exposed on /people. The middle one matches the env default.
THRESHOLD_PRESETS: dict[str, float] = {
    "strict": 0.28,
    "balanced": 0.36,
    "loose": 0.46,
}
_THRESHOLD_CACHE: dict[str, Any] = {"value": None, "at": 0.0}
_THRESHOLD_TTL_S = 10.0


def get_threshold() -> float:
    """The match threshold in force.

    ``FACE_MATCH_THRESHOLD`` is the default, but the Strict/Balanced/Loose
    control on ``/people`` stores its choice in ``db_meta`` so it applies to the
    very next scan or re-group without a restart (and survives one).
    """
    now = time.time()
    cached = _THRESHOLD_CACHE.get("value")
    if (
        cached is not None
        and (now - float(_THRESHOLD_CACHE.get("at") or 0)) < _THRESHOLD_TTL_S
    ):
        return float(cached)
    value = face_match_threshold()
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT value FROM db_meta WHERE key = 'face_match_threshold'"
            ).fetchone()
        if row and row["value"]:
            value = min(1.2, max(0.05, float(row["value"])))
    except Exception:
        pass
    _THRESHOLD_CACHE.update(value=value, at=now)
    return float(value)


def set_threshold(value: float) -> float:
    """Persist a new match threshold (and drop the stale centroid cache)."""
    clean = min(1.2, max(0.05, float(value)))
    try:
        with db_conn() as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO db_meta (key, value) VALUES ('face_match_threshold', ?)",
                (str(clean),),
            )
    except Exception as e:
        logger.error(f"[Faces] Could not store the match threshold: {e}")
    _THRESHOLD_CACHE.update(value=clean, at=time.time())
    invalidate_centroids()
    return clean


def threshold_preset_name(value: float | None = None) -> str:
    """Closest preset name for a threshold (for the /people control state)."""
    if value is None:
        return "custom"
    for name, preset in THRESHOLD_PRESETS.items():
        if abs(preset - float(value)) < 0.005:
            return name
    return "custom"


def is_enabled() -> bool:
    return bool(getattr(config, "FACE_RECOGNITION_ENABLED", False))


def get_face_crop_dir(create: bool = False) -> Path:
    """Return the face crop cache directory (created only when writing)."""
    configured = getattr(config, "FACE_CROP_DIR", "") or ""
    base = (
        Path(configured).resolve()
        if configured
        else Path(config.THUMBNAIL_CACHE_DIR) / "faces"
    )
    if create:
        base.mkdir(parents=True, exist_ok=True)
    return base


def face_crop_max_bytes() -> int:
    try:
        gb = float(getattr(config, "FACE_CROP_MAX_SIZE_GB", 2.0))
    except (TypeError, ValueError):
        gb = 2.0
    return max(0, int(gb * _GIB))


# ----------------------------------------------------------------- embeddings


def pack_embedding(vector: Any) -> bytes:
    """Pack an L2-normalised float32 embedding for the ``faces`` table."""
    return np.asarray(vector, dtype="<f4").tobytes()


def unpack_embeddings(blobs: list[bytes]) -> np.ndarray:
    """Return an ``(n, EMBEDDING_DIM)`` float32 matrix for stored embeddings.

    A truncated or corrupt blob (any length that is not exactly
    ``EMBEDDING_DIM * 4`` bytes) is skipped rather than raising: a single bad row
    must not break a centroid rebuild or a re-cluster pass.
    """
    expected = EMBEDDING_DIM * 4
    rows = []
    for blob in blobs:
        if not blob or len(blob) != expected:
            continue
        rows.append(np.frombuffer(blob, dtype="<f4"))
    if not rows:
        return np.zeros((0, EMBEDDING_DIM), dtype="float32")
    return np.vstack(rows).astype("float32", copy=False)


def _normalize(vec: Any) -> np.ndarray:
    arr = np.asarray(vec, dtype="float32").reshape(-1)
    norm = float(np.linalg.norm(arr))
    return arr / norm if norm > 0 else arr


def _centroid_of(matrix: np.ndarray) -> np.ndarray | None:
    """L2-normalised mean of a set of normalised embeddings."""
    if matrix.shape[0] == 0:
        return None
    return _normalize(matrix.mean(axis=0))


# ------------------------------------------------------------------- centroids


def invalidate_centroids() -> None:
    """Mark the centroid cache stale (call after any external face mutation)."""
    global _REVISION
    with _CENTROIDS_LOCK:
        _REVISION += 1


def get_centroids(conn: Any | None = None) -> dict[int, np.ndarray]:
    """Return ``{person_id: centroid}``, rebuilt only when the revision moved.

    Keyed on the database path as well as the revision: a restore (or a test
    sandbox) can swap the whole index out from under the process, and a centroid
    from the previous file would assign faces to people that no longer exist.
    """
    db_key = str(get_current_db_path())
    with _CENTROIDS_LOCK:
        if (
            _CENTROIDS["revision"] == _REVISION
            and _CENTROIDS["key"] == db_key
            and _CENTROIDS["vecs"]
        ):
            return _CENTROIDS["vecs"]
        revision = _REVISION

    vecs: dict[int, np.ndarray] = {}
    counts: dict[int, int] = {}
    rows = []
    try:
        owned = conn is None
        if owned:
            with db_conn() as local:
                rows = local.execute(
                    "SELECT person_id, embedding FROM faces "
                    "WHERE person_id IS NOT NULL AND embedding IS NOT NULL"
                ).fetchall()
        else:
            rows = conn.execute(
                "SELECT person_id, embedding FROM faces "
                "WHERE person_id IS NOT NULL AND embedding IS NOT NULL"
            ).fetchall()
    except Exception as e:
        logger.error(f"[Faces] Could not read embeddings for centroids: {e}")
        return {}

    buckets: dict[int, list[Any]] = {}
    for r in rows:
        buckets.setdefault(int(r["person_id"]), []).append(r["embedding"])
    for person_id, blobs in buckets.items():
        matrix = unpack_embeddings(blobs)
        if matrix.shape[0] == 0:
            continue
        centroid = _centroid_of(matrix)
        if centroid is not None:
            vecs[person_id] = centroid
            counts[person_id] = matrix.shape[0]

    with _CENTROIDS_LOCK:
        # A rebuild that landed while we were reading is newer than ours and wins
        # (only if it is for the same database); otherwise ours is installed even
        # when it is empty, so a switched/replaced index cannot keep serving the
        # previous file's centroids.
        newer = _CENTROIDS["revision"] > revision and _CENTROIDS["key"] == db_key
        if not newer:
            _CENTROIDS["revision"] = revision
            _CENTROIDS["key"] = db_key
            _CENTROIDS["vecs"] = vecs
            _CENTROIDS["counts"] = counts
        return _CENTROIDS["vecs"]


def _track_centroid(person_id: int, embedding: np.ndarray) -> None:
    """Fold a newly assigned face into the cached centroid for its person."""
    with _CENTROIDS_LOCK:
        vecs = _CENTROIDS["vecs"]
        counts = _CENTROIDS["counts"]
        prev = vecs.get(person_id)
        prev_count = int(counts.get(person_id, 0))
        if prev is None or prev_count <= 0:
            vecs[person_id] = _normalize(embedding)
            counts[person_id] = 1
            return
        merged = (prev * prev_count + _normalize(embedding)) / (prev_count + 1)
        vecs[person_id] = _normalize(merged)
        counts[person_id] = prev_count + 1


def assign_person(
    embedding: np.ndarray,
    centroids: dict[int, np.ndarray],
    threshold: float | None = None,
) -> tuple[int | None, float | None]:
    """Nearest person within ``threshold`` cosine distance, else ``(None, None)``."""
    limit = get_threshold() if threshold is None else float(threshold)
    if not centroids:
        return None, None
    ids = list(centroids.keys())
    matrix = np.vstack([centroids[i] for i in ids])
    sims = matrix @ _normalize(embedding)
    distances = 1.0 - sims
    best = int(np.argmin(distances))
    best_distance = float(distances[best])
    if best_distance <= limit:
        return ids[best], best_distance
    return None, None


# ---------------------------------------------------------------------- crops


def face_crop_name(file_path: str, frame_time: float, x: int, y: int) -> str:
    """Deterministic crop filename for one detection (stable across re-scans)."""
    key = f"{file_path}|{round(float(frame_time), 3)}|{int(x)}|{int(y)}"
    return f"face_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}.jpg"


def write_face_crop(image: Any, box: dict[str, Any], name: str) -> bool:
    """Cut ``box`` out of ``image`` (PIL) and store it atomically as ``name``."""
    crop_dir = get_face_crop_dir(create=True)
    target = crop_dir / name
    if target.exists() and target.stat().st_size > 0:
        return True

    width, height = image.size
    bw, bh = max(1, int(box["w"])), max(1, int(box["h"]))
    mx, my = int(bw * CROP_MARGIN), int(bh * CROP_MARGIN)
    left = max(0, int(box["x"]) - mx)
    top = max(0, int(box["y"]) - my)
    right = min(width, int(box["x"]) + bw + mx)
    bottom = min(height, int(box["y"]) + bh + my)
    if right - left < 4 or bottom - top < 4:
        return False

    temp = target.with_name(f"{target.stem}.{os.getpid()}.tmp.jpg")
    try:
        crop = image.crop((left, top, right, bottom)).convert("RGB")
        crop = crop.resize((CROP_SIZE, CROP_SIZE))
        crop.save(temp, "JPEG", quality=CROP_QUALITY)
        if not temp.exists() or temp.stat().st_size == 0:
            return False
        os.replace(temp, target)
        _USAGE_CACHE["at"] = 0.0
        return True
    except Exception as e:
        logger.error(f"[Faces] Could not write face crop {name}: {e}")
        return False
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def crop_path(name: str | None) -> Path | None:
    """Absolute path of a stored crop, or None when it is missing/invalid."""
    if not name or not _FINAL_CROP_RE.match(name):
        return None
    path = get_face_crop_dir() / name
    try:
        if path.exists() and path.stat().st_size > 0:
            return path
    except OSError:
        return None
    return None


def crop_cache_usage(max_age_s: float = _USAGE_TTL_S) -> tuple[int, int]:
    """``(file_count, total_bytes)`` for the crop cache, memoized."""
    crop_dir = get_face_crop_dir()
    key = str(crop_dir)
    now = time.time()
    if _USAGE_CACHE["key"] == key and (now - float(_USAGE_CACHE["at"])) < max_age_s:
        return int(_USAGE_CACHE["files"]), int(_USAGE_CACHE["bytes"])

    files = 0
    total = 0
    try:
        with os.scandir(crop_dir) as entries:
            for entry in entries:
                if not _FINAL_CROP_RE.match(entry.name):
                    continue
                try:
                    if entry.is_file():
                        files += 1
                        total += entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass

    _USAGE_CACHE.update(key=key, at=now, files=files, bytes=total)
    return files, total


def face_crop_payload() -> dict[str, Any]:
    """Status-ready snapshot of the crop cache for the tasks modal."""
    files, total = crop_cache_usage()
    cap = face_crop_max_bytes()
    return {
        "cache_files": files,
        "cache_bytes": total,
        "cache_size_formatted": format_size(total),
        "cache_max_bytes": cap,
        "cache_max_size_formatted": format_size(cap),
    }


def clean_orphaned_face_crops(conn: Any) -> int:
    """Delete crops no ``faces`` row references. Returns the number removed."""
    crop_dir = get_face_crop_dir()
    try:
        valid = {
            r["crop"]
            for r in conn.execute(
                "SELECT DISTINCT crop FROM faces WHERE crop IS NOT NULL"
            ).fetchall()
        }
    except Exception as e:  # a broken index read must never delete anything
        logger.error(f"[Faces] Could not read faces for the crop sweep: {e}")
        return 0

    pruned = 0
    try:
        with os.scandir(crop_dir) as entries:
            for entry in entries:
                if not _FINAL_CROP_RE.match(entry.name) or entry.name in valid:
                    continue
                try:
                    if entry.is_file():
                        Path(entry.path).unlink(missing_ok=True)
                        pruned += 1
                except OSError:
                    continue
    except OSError:
        pass
    if pruned:
        _USAGE_CACHE["at"] = 0.0
    return pruned


def manage_face_crop_lru(
    max_bytes: int | None = None, min_interval_s: float = _LRU_MIN_INTERVAL_S
) -> dict[str, Any]:
    """Trim the crop cache back under its ceiling, oldest written first."""
    cap = face_crop_max_bytes() if max_bytes is None else max(0, int(max_bytes))
    crop_dir = get_face_crop_dir()
    key = f"{crop_dir}|{cap}"
    now = time.time()
    throttled = (
        max_bytes is None
        and _LRU_STATE["key"] == key
        and (now - float(_LRU_STATE["at"])) < min_interval_s
    )
    if not throttled:
        _LRU_STATE.update(key=key, at=now)

    report: dict[str, Any] = {
        "evicted": 0,
        "bytes": 0,
        "cap_bytes": cap,
        "skipped": throttled,
    }
    entries = []
    total = 0
    try:
        with os.scandir(crop_dir) as it:
            for e in it:
                if not _FINAL_CROP_RE.match(e.name):
                    continue
                try:
                    if not e.is_file():
                        continue
                    st = e.stat()
                except OSError:
                    continue
                entries.append((e.path, st.st_mtime, st.st_size))
                total += st.st_size
    except OSError:
        return report

    report["bytes"] = total
    if throttled or total <= cap:
        return report

    entries.sort(key=lambda t: t[1])
    target = int(cap * _LRU_TARGET_RATIO)
    evicted = 0
    for path, _mtime, size in entries:
        if total <= target:
            break
        try:
            Path(path).unlink(missing_ok=True)
            total -= size
            evicted += 1
        except OSError:
            continue

    report["evicted"] = evicted
    report["bytes"] = total
    _USAGE_CACHE["at"] = 0.0
    if evicted:
        # A face whose crop was evicted must be re-scanned to get it back.
        try:
            with db_conn() as conn, conn:
                conn.execute(
                    "DELETE FROM face_scans WHERE file_path IN "
                    "(SELECT DISTINCT file_path FROM faces WHERE crop IS NOT NULL)"
                )
        except Exception as e:
            logger.error(f"[Faces] Could not requeue drained face crops: {e}")
        logger.info(
            f"[Faces] Evicted {evicted} face crops "
            f"({format_size(report['bytes'])} of {format_size(cap)} left)."
        )
    return report


# ------------------------------------------------------------------- scanning


def _dismissed_keys(conn: Any, file_path: str) -> set[tuple]:
    rows = conn.execute(
        "SELECT frame_time, x, y FROM dismissed_faces WHERE file_path = ?",
        (file_path,),
    ).fetchall()
    return {
        (round(float(r["frame_time"]), 3), int(r["x"] or 0), int(r["y"] or 0))
        for r in rows
    }


def record_scan(
    file_path: str,
    mtime: float | None,
    size: int | None,
    faces: list[dict[str, Any]],
    centroids: dict[int, np.ndarray],
) -> int:
    """Replace a file's detections with ``faces`` and mark it scanned.

    Dismissals survive: a face the user rejected is not re-inserted, and the
    ``face_scans`` row records the mtime/size this result is valid for so an
    unchanged file is never re-scanned.
    """
    inserted = 0
    now = time.time()
    try:
        with db_conn() as conn, conn:
            dismissed = _dismissed_keys(conn, file_path)
            conn.execute("DELETE FROM faces WHERE file_path = ?", (file_path,))
            for face in faces:
                frame_time = float(face.get("frame_time", IMAGE_FRAME_TIME))
                key = (round(frame_time, 3), int(face["x"]), int(face["y"]))
                if key in dismissed:
                    continue
                embedding = face.get("embedding")
                if embedding is None:
                    continue
                embedding = _normalize(embedding)
                person_id = face.get("person_id")
                distance = face.get("distance")
                if person_id is None:
                    person_id, distance = assign_person(embedding, centroids)
                    if person_id is None:
                        cur = conn.execute(
                            "INSERT INTO people (name, created_at) VALUES (NULL, ?)",
                            (now,),
                        )
                        person_id = int(cur.lastrowid)
                conn.execute(
                    "INSERT OR REPLACE INTO faces (file_path, frame_time, x, y, w, h, "
                    "score, embedding, person_id, distance, crop, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        file_path,
                        frame_time,
                        int(face["x"]),
                        int(face["y"]),
                        int(face["w"]),
                        int(face["h"]),
                        float(face.get("score") or 0.0),
                        pack_embedding(embedding),
                        person_id,
                        distance,
                        face.get("crop"),
                        now,
                    ),
                )
                inserted += 1
                _track_centroid(int(person_id), embedding)
                tracked = _CENTROIDS["vecs"].get(int(person_id))
                if tracked is not None:
                    centroids[int(person_id)] = tracked
            conn.execute(
                "INSERT OR REPLACE INTO face_scans "
                "(file_path, mtime, size, faces_found, scanned_at) VALUES (?, ?, ?, ?, ?)",
                (file_path, mtime, size, inserted, now),
            )
    except Exception as e:
        logger.error(f"[Faces] Could not record the scan of {file_path}: {e}")
        invalidate_centroids()
        return 0
    return inserted


# ------------------------------------------------------------ cluster editing


def rename_person(person_id: int, name: str | None) -> bool:
    """Name (or clear the name of) a person cluster."""
    clean = (name or "").strip()[:120]
    try:
        with db_conn() as conn, conn:
            cur = conn.execute(
                "UPDATE people SET name = ?, updated_at = ? WHERE id = ?",
                (clean or None, time.time(), int(person_id)),
            )
            return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[Faces] Rename failed for person {person_id}: {e}")
        return False


def merge_people(target_id: int, source_ids: list[int]) -> dict[str, Any]:
    """Move every face of ``source_ids`` into ``target_id`` and drop empties."""
    target = int(target_id)
    sources = sorted({int(s) for s in source_ids if int(s) != target})
    moved = 0
    removed = 0
    try:
        with db_conn() as conn, conn:
            exists = conn.execute(
                "SELECT 1 FROM people WHERE id = ?", (target,)
            ).fetchone()
            if not exists:
                return {
                    "success": False,
                    "moved": 0,
                    "removed": 0,
                    "error": "Unknown person",
                }
            for source in sources:
                cur = conn.execute(
                    "UPDATE faces SET person_id = ?, distance = NULL WHERE person_id = ?",
                    (target, source),
                )
                moved += max(0, cur.rowcount or 0)
            if sources:
                placeholders = ",".join("?" for _ in sources)
                cur = conn.execute(
                    f"DELETE FROM people WHERE id IN ({placeholders}) AND name IS NULL",
                    sources,
                )
                removed = max(0, cur.rowcount or 0)
            conn.execute(
                "UPDATE people SET updated_at = ? WHERE id = ?", (time.time(), target)
            )
    except Exception as e:
        logger.error(f"[Faces] Merge into {target} failed: {e}")
        return {"success": False, "moved": moved, "removed": removed, "error": str(e)}
    invalidate_centroids()
    return {"success": True, "moved": moved, "removed": removed, "error": ""}


def split_faces(face_ids: list[int], name: str | None = None) -> dict[str, Any]:
    """Pull faces out of their clusters into a brand-new person cluster."""
    ids = sorted({int(f) for f in face_ids})
    if not ids:
        return {
            "success": False,
            "person_id": None,
            "moved": 0,
            "error": "No faces given",
        }
    clean = (name or "").strip()[:120]
    try:
        with db_conn() as conn, conn:
            placeholders = ",".join("?" for _ in ids)
            found = conn.execute(
                f"SELECT COUNT(*) FROM faces WHERE id IN ({placeholders})", ids
            ).fetchone()[0]
            if not found:
                return {
                    "success": False,
                    "person_id": None,
                    "moved": 0,
                    "error": "No matching faces",
                }
            cur = conn.execute(
                "INSERT INTO people (name, created_at) VALUES (?, ?)",
                (clean or None, time.time()),
            )
            new_id = int(cur.lastrowid)
            conn.execute(
                f"UPDATE faces SET person_id = ?, distance = NULL WHERE id IN ({placeholders})",
                [new_id, *ids],
            )
    except Exception as e:
        logger.error(f"[Faces] Split failed: {e}")
        return {"success": False, "person_id": None, "moved": 0, "error": str(e)}
    invalidate_centroids()
    return {"success": True, "person_id": new_id, "moved": len(ids), "error": ""}


def dismiss_faces(face_ids: list[int]) -> int:
    """Permanently reject faces ("not a person") so re-scans skip them."""
    ids = sorted({int(f) for f in face_ids})
    if not ids:
        return 0
    dismissed = 0
    rows: list[Any] = []
    try:
        with db_conn() as conn, conn:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT file_path, frame_time, x, y, crop FROM faces WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            now = time.time()
            for r in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO dismissed_faces "
                    "(file_path, frame_time, x, y, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        r["file_path"],
                        float(r["frame_time"]),
                        int(r["x"] or 0),
                        int(r["y"] or 0),
                        now,
                    ),
                )
                dismissed += 1
            conn.execute(f"DELETE FROM faces WHERE id IN ({placeholders})", ids)
    except Exception as e:
        logger.error(f"[Faces] Dismiss failed: {e}")
        return 0
    # The crops are unreferenced now; drop them rather than wait for the sweep.
    crop_dir = get_face_crop_dir()
    for r in rows:
        name = r["crop"] if "crop" in r.keys() else None
        if name and _FINAL_CROP_RE.match(name):
            try:
                (crop_dir / name).unlink(missing_ok=True)
            except OSError:
                pass
    invalidate_centroids()
    return dismissed


def dismiss_person(person_id: int) -> dict[str, Any]:
    """Reject every face of a cluster, then remove the (now empty) cluster."""
    try:
        with db_conn() as conn:
            ids = [
                int(r["id"])
                for r in conn.execute(
                    "SELECT id FROM faces WHERE person_id = ?", (int(person_id),)
                ).fetchall()
            ]
    except Exception as e:
        logger.error(f"[Faces] Could not read person {person_id} for dismissal: {e}")
        return {"success": False, "dismissed": 0, "removed": 0, "error": str(e)}

    count = dismiss_faces(ids)
    removed = 0
    try:
        with db_conn() as conn, conn:
            cur = conn.execute(
                "DELETE FROM people WHERE id = ? AND name IS NULL", (int(person_id),)
            )
            removed = max(0, cur.rowcount or 0)
    except Exception as e:
        logger.error(f"[Faces] Could not remove empty person {person_id}: {e}")
    return {"success": True, "dismissed": count, "removed": removed, "error": ""}


def _cluster_labels(matrix: np.ndarray, threshold: float) -> np.ndarray:
    """Agglomerative cosine clustering with bounded memory.

    Faces are clustered in chunks; when there is more than one chunk the chunk
    centroids are clustered too and the two levels are combined, so a library
    with tens of thousands of faces never materialises one huge distance matrix.
    """
    from scipy.cluster.hierarchy import fcluster, linkage

    n = matrix.shape[0]
    if n == 0:
        return np.zeros(0, dtype="int64")
    if n == 1:
        return np.zeros(1, dtype="int64")

    def _cluster_block(block: np.ndarray) -> np.ndarray:
        count = block.shape[0]
        if count < 2:
            return np.zeros(count, dtype="int64")
        if count == 2:
            same = float(1.0 - float(block[0] @ block[1])) <= threshold
            return np.array([0, 0 if same else 1], dtype="int64")
        z = linkage(block, method="average", metric="cosine")
        return fcluster(z, t=threshold, criterion="distance").astype("int64")

    if n <= _CLUSTER_CHUNK:
        return _cluster_block(matrix)

    chunk_labels: list[np.ndarray] = []
    # (chunk_index, local label) -> row in the chunk-centroid matrix.
    group_index: dict[tuple[int, int], int] = {}
    centroids: list[np.ndarray] = []
    for chunk_index, lo in enumerate(range(0, n, _CLUSTER_CHUNK)):
        block = matrix[lo : lo + _CLUSTER_CHUNK]
        labels = _cluster_block(block)
        chunk_labels.append(labels)
        for label in np.unique(labels):
            centroid = _centroid_of(block[labels == label])
            if centroid is None:
                continue
            group_index[(chunk_index, int(label))] = len(centroids)
            centroids.append(centroid)

    group_labels = _cluster_block(np.vstack(centroids))

    out = np.zeros(n, dtype="int64")
    for chunk_index, labels in enumerate(chunk_labels):
        lo = chunk_index * _CLUSTER_CHUNK
        for label in np.unique(labels):
            row = group_index.get((chunk_index, int(label)))
            out[lo : lo + labels.shape[0]][labels == label] = (
                int(group_labels[row]) if row is not None else -1
            )
    return out


def auto_match_people(
    threshold: float | None = None,
    dry_run: bool = False,
    max_unnamed: int = 200000,
    max_samples: int = 6,
) -> dict[str, Any]:
    """Fold unnamed clusters that sit on a named person into that person.

    Every face is assigned to the nearest person within the match threshold as it
    is scanned — but the people it *could* join are only the ones that existed at
    that moment. A person named after the fact, or one whose cluster has grown
    since, leaves unnamed clusters that a fresh assignment would have merged;
    that is the bulk of a long-lived backlog, and reviewing them one at a time is
    the slow way to undo it.

    This pass re-asks that question for every unnamed cluster at once, against
    the **named** people only (folding a cluster into another unnamed one would
    just move the problem), and reports what it would do before doing it when
    ``dry_run`` is set. A merge is exactly what ``merge_people`` does — faces
    move, the drained cluster is dropped — so a wrong match is undone with Split.
    """
    limit = (
        get_threshold() if threshold is None else min(1.2, max(0.05, float(threshold)))
    )
    report: dict[str, Any] = {
        "success": False,
        "threshold": limit,
        "dry_run": bool(dry_run),
        "named": 0,
        "unnamed": 0,
        "matched": 0,
        "faces": 0,
        "people": 0,
        "removed": 0,
        "remaining_unnamed": 0,
        "by_person": [],
        "samples": [],
        "counts": {},
        "error": "",
    }

    try:
        with db_conn() as conn:
            rows = conn.execute("SELECT id, name FROM people").fetchall()
            face_counts = {
                int(r["person_id"]): int(r["c"])
                for r in conn.execute(
                    "SELECT person_id, COUNT(*) AS c FROM faces "
                    "WHERE person_id IS NOT NULL GROUP BY person_id"
                ).fetchall()
            }
    except Exception as e:
        logger.error(f"[Faces] Auto-match read failed: {e}")
        report["error"] = str(e)
        return report

    named = {
        int(r["id"]): (r["name"] or "").strip()
        for r in rows
        if (r["name"] or "").strip()
    }
    unnamed = [int(r["id"]) for r in rows if not (r["name"] or "").strip()]
    report["named"] = len(named)
    report["unnamed"] = len(unnamed)
    report["remaining_unnamed"] = len(unnamed)
    if not named or not unnamed:
        report["success"] = True
        report["counts"] = people_counts()
        return report
    if len(unnamed) > max_unnamed:
        report["error"] = (
            f"Too many unnamed clusters to auto-match in one pass "
            f"({len(unnamed):,} > {max_unnamed:,})"
        )
        return report

    centroids = get_centroids()
    named_ids = [pid for pid in named if pid in centroids]
    unnamed_ids = [pid for pid in unnamed if pid in centroids]
    if not named_ids or not unnamed_ids:
        report["success"] = True
        report["counts"] = people_counts()
        return report

    named_matrix = np.vstack([centroids[pid] for pid in named_ids])
    unnamed_matrix = np.vstack([centroids[pid] for pid in unnamed_ids])
    distances = 1.0 - unnamed_matrix @ named_matrix.T
    best = np.argmin(distances, axis=1)
    best_distance = distances[np.arange(len(unnamed_ids)), best]

    targets: dict[int, int] = {}
    gains: dict[int, list[int]] = {}
    for index, pid in enumerate(unnamed_ids):
        distance = float(best_distance[index])
        if distance > limit:
            continue
        target = named_ids[int(best[index])]
        targets[pid] = target
        gains.setdefault(target, []).append(pid)

    report["matched"] = len(targets)
    report["faces"] = sum(int(face_counts.get(pid, 0)) for pid in targets)
    report["people"] = len(gains)
    report["remaining_unnamed"] = len(unnamed) - len(targets)

    # What the pass would do, biggest gain first, so the confirmation sheet can
    # show the shape of it before anything moves.
    by_person = [
        {
            "person_id": target,
            "name": named.get(target, ""),
            "clusters": len(sources),
            "faces": sum(int(face_counts.get(pid, 0)) for pid in sources),
        }
        for target, sources in gains.items()
    ]
    by_person.sort(key=lambda entry: (-entry["faces"], -entry["clusters"]))
    report["by_person"] = by_person[:8]

    samples: list[dict[str, Any]] = []
    for pid, target in sorted(
        targets.items(), key=lambda kv: -int(face_counts.get(kv[0], 0))
    )[:max_samples]:
        samples.append(
            {
                "person_id": pid,
                "faces": int(face_counts.get(pid, 0)),
                "target_id": target,
                "target_name": named.get(target, ""),
                "distance": round(
                    float(1.0 - float(centroids[target] @ centroids[pid])), 3
                ),
            }
        )
    report["samples"] = samples

    if dry_run:
        report["success"] = True
        report["counts"] = people_counts()
        return report

    moved = 0
    removed = 0
    try:
        with db_conn() as conn, conn:
            for source, target in targets.items():
                cur = conn.execute(
                    "UPDATE faces SET person_id = ?, distance = NULL WHERE person_id = ?",
                    (target, source),
                )
                moved += max(0, cur.rowcount or 0)
            sources = sorted(targets)
            for start in range(0, len(sources), SQL_IN_CHUNK):
                chunk = sources[start : start + SQL_IN_CHUNK]
                placeholders = ",".join("?" for _ in chunk)
                cur = conn.execute(
                    f"DELETE FROM people WHERE id IN ({placeholders}) AND name IS NULL",
                    chunk,
                )
                removed += max(0, cur.rowcount or 0)
            now = time.time()
            for target in gains:
                conn.execute(
                    "UPDATE people SET updated_at = ? WHERE id = ?", (now, target)
                )
    except Exception as e:
        logger.error(f"[Faces] Auto-match write failed: {e}")
        report["error"] = str(e)
        report["removed"] = removed
        return report

    report["removed"] = removed
    report["faces"] = moved
    invalidate_centroids()
    report["success"] = True
    report["counts"] = people_counts()
    logger.info(
        f"[Faces] Auto-matched {removed} unnamed clusters ({moved} faces) into "
        f"{len(gains)} named people at {limit:.2f}."
    )
    return report


def recluster_people(
    threshold: float | None = None, max_faces: int = 200000
) -> dict[str, Any]:
    """Re-group every face and re-attach the user's names by nearest centroid."""
    limit = get_threshold() if threshold is None else float(threshold)
    report: dict[str, Any] = {
        "success": False,
        "faces": 0,
        "people": 0,
        "created": 0,
        "kept": 0,
        "removed": 0,
        "error": "",
    }
    try:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT id, embedding FROM faces WHERE embedding IS NOT NULL"
            ).fetchall()
    except Exception as e:
        logger.error(f"[Faces] Re-cluster read failed: {e}")
        report["error"] = str(e)
        return report

    face_ids: list[int] = []
    blobs: list[bytes] = []
    for r in rows:
        if r["embedding"]:
            face_ids.append(int(r["id"]))
            blobs.append(r["embedding"])
    report["faces"] = len(face_ids)
    if not face_ids:
        report["success"] = True
        return report
    if len(face_ids) > max_faces:
        report["error"] = (
            f"Too many faces to re-cluster in one pass ({len(face_ids):,} > {max_faces:,})"
        )
        return report

    matrix = unpack_embeddings(blobs)
    if matrix.shape[0] != len(face_ids):
        report["error"] = "Stored embeddings are unreadable"
        return report

    try:
        labels = _cluster_labels(matrix, limit)
    except Exception as e:
        logger.error(f"[Faces] Clustering failed: {e}")
        report["error"] = f"Clustering failed: {e}"
        return report

    # Existing people claim the new cluster nearest to their centroid, best pair
    # first, so names and cluster ids survive a re-run.
    group_ids = [int(g) for g in np.unique(labels)]
    group_centroids: dict[int, np.ndarray] = {}
    for group in group_ids:
        centroid = _centroid_of(matrix[labels == group])
        if centroid is not None:
            group_centroids[group] = centroid

    claimed: dict[int, int] = {}  # group -> person_id
    if group_centroids:
        try:
            with db_conn() as conn:
                old_centroids = get_centroids(conn)
                known_people = {
                    int(r["id"])
                    for r in conn.execute("SELECT id FROM people").fetchall()
                }
        except Exception as e:
            logger.error(f"[Faces] Could not read people for re-clustering: {e}")
            old_centroids = {}
            known_people = set()
        pairs: list[tuple[float, int, int]] = []
        for person_id, centroid in old_centroids.items():
            if person_id not in known_people:
                continue
            for group, group_centroid in group_centroids.items():
                distance = float(1.0 - float(centroid @ group_centroid))
                if distance <= limit:
                    pairs.append((distance, person_id, group))
        pairs.sort()
        used_people: set[int] = set()
        for _distance, person_id, group in pairs:
            if person_id in used_people or group in claimed:
                continue
            claimed[group] = person_id
            used_people.add(person_id)
            report["kept"] += 1

    try:
        with db_conn() as conn, conn:
            for group in group_ids:
                person_id = claimed.get(group)
                if person_id is None:
                    cur = conn.execute(
                        "INSERT INTO people (name, created_at) VALUES (NULL, ?)",
                        (time.time(),),
                    )
                    person_id = int(cur.lastrowid)
                    report["created"] += 1
                members = [face_ids[i] for i in np.nonzero(labels == group)[0]]
                for start in range(0, len(members), SQL_IN_CHUNK):
                    chunk = members[start : start + SQL_IN_CHUNK]
                    placeholders = ",".join("?" for _ in chunk)
                    conn.execute(
                        f"UPDATE faces SET person_id = ?, distance = NULL "
                        f"WHERE id IN ({placeholders})",
                        [person_id, *chunk],
                    )
            # Empty unnamed clusters left behind by the regrouping.
            cur = conn.execute(
                "DELETE FROM people WHERE name IS NULL AND id NOT IN "
                "(SELECT DISTINCT person_id FROM faces WHERE person_id IS NOT NULL)"
            )
            report["removed"] = max(0, cur.rowcount or 0)
    except Exception as e:
        logger.error(f"[Faces] Re-cluster write failed: {e}")
        report["error"] = str(e)
        return report

    invalidate_centroids()
    try:
        with db_conn() as conn:
            report["people"] = int(
                conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
            )
    except Exception:
        report["people"] = len(group_ids)
    report["success"] = True
    return report


# -------------------------------------------------------------------- queries


def _crop_url(face_id: Any, crop: Any) -> str | None:
    if not crop:
        return None
    return f"/api/people/crop?face_id={int(face_id)}&v={crop}"


def _face_entry(row: Any) -> dict[str, Any]:
    frame_time = (
        float(row["frame_time"]) if row["frame_time"] is not None else IMAGE_FRAME_TIME
    )
    is_video = frame_time >= 0
    return {
        "id": int(row["id"]),
        "file_path": row["file_path"],
        "name": row["name"] if "name" in row.keys() else None,
        "type": row["type"] if "type" in row.keys() else None,
        "frame_time": frame_time if is_video else None,
        "x": int(row["x"] or 0),
        "y": int(row["y"] or 0),
        "w": int(row["w"] or 0),
        "h": int(row["h"] or 0),
        "score": float(row["score"] or 0.0),
        "person_id": int(row["person_id"]) if row["person_id"] is not None else None,
        "crop_url": _crop_url(row["id"], row["crop"]),
    }


def _people_name_condition(named: bool | None) -> str:
    """The bare ``p.name`` condition, for composing into a larger WHERE.

    An empty name is never stored (``rename_person`` writes NULL instead), so
    ``name IS NULL`` is exactly "unnamed". ``None`` matches every cluster.
    """
    if named is None:
        return ""
    return "p.name IS NOT NULL" if named else "p.name IS NULL"


def _folder_filter_condition(folder: str | None) -> tuple[str, list[Any]]:
    """Condition (plus its LIKE pattern) for "this cluster has a face in <folder>".

    A substring match on the folder path, case-insensitive (SQLite's ``LIKE``
    folds ASCII case), with the user's ``%``/``_`` escaped so a literal folder
    name cannot turn into a wildcard.
    """
    text = (folder or "").strip()
    if not text:
        return "", []
    escaped = text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    condition = (
        "EXISTS (SELECT 1 FROM faces fa3 JOIN files f3 ON f3.path = fa3.file_path"
        " WHERE fa3.person_id = p.id AND f3.folder_path LIKE ? ESCAPE '\\')"
    )
    return condition, [f"%{escaped}%"]


def _folder_label(path: str) -> str:
    """A short, still-disambiguating label for a folder path.

    Relative to the library root when the folder is inside it, keeping the last
    two segments ("Iceland 2023/Day 4") — so a folder whose own name is just a
    date or a number does not read as noise, and the library's own parent
    directories never leak into the label.
    """
    from media_library.core.config import DEFAULT_DESTINATION_DIR
    from media_library.core.state import state

    text = str(path or "").replace("\\", "/").rstrip("/")
    if not text:
        return ""
    root = (
        str(state.get("destination_dir") or DEFAULT_DESTINATION_DIR)
        .replace("\\", "/")
        .rstrip("/")
    )
    if root and text.startswith(root + "/"):
        text = text[len(root) + 1 :]
    parts = [p for p in text.split("/") if p]
    return "/".join(parts[-2:]) if parts else ""


def _folders_of_files(conn: Any, person_ids: list[int]) -> dict[int, set[str]]:
    """``{person_id: {folder_path}}`` for the given clusters, from the index."""
    if not person_ids:
        return {}
    placeholders = ",".join("?" for _ in person_ids)
    out: dict[int, set[str]] = {}
    for row in conn.execute(
        f"""
        SELECT fa.person_id AS person_id, f.folder_path AS folder_path
        FROM faces fa
        JOIN files f ON f.path = fa.file_path
        WHERE fa.person_id IN ({placeholders})
              AND f.folder_path IS NOT NULL AND f.folder_path <> ''
        GROUP BY fa.person_id, f.folder_path
        """,
        person_ids,
    ).fetchall():
        out.setdefault(int(row["person_id"]), set()).add(str(row["folder_path"]))
    return out


def _top_folders_of_files(
    conn: Any, person_ids: list[int], limit: int = _FOLDER_LABELS
) -> dict[int, list[str]]:
    """``{person_id: [folder label]}`` for the given clusters, busiest folder first.

    One grouped query for every cluster in a page (rather than one per row), so
    the review queue can say where a cluster's faces are filed — the fastest way
    to recognise whose it is — without a query per cluster.
    """
    if not person_ids:
        return {}
    placeholders = ",".join("?" for _ in person_ids)
    counts: dict[int, list[str]] = {}
    for row in conn.execute(
        f"""
        SELECT fa.person_id AS person_id, f.folder_path AS folder_path,
               COUNT(*) AS n
        FROM faces fa
        JOIN files f ON f.path = fa.file_path
        WHERE fa.person_id IN ({placeholders})
              AND f.folder_path IS NOT NULL AND f.folder_path <> ''
        GROUP BY fa.person_id, f.folder_path
        ORDER BY fa.person_id ASC, n DESC, f.folder_path ASC
        """,
        person_ids,
    ).fetchall():
        counts.setdefault(int(row["person_id"]), []).append(str(row["folder_path"]))
    return {
        person_id: [_folder_label(p) for p in paths[: max(1, int(limit))]]
        for person_id, paths in counts.items()
    }


def read_people(
    conn: Any,
    limit: int = 200,
    offset: int = 0,
    named: bool | None = None,
    near: int | None = None,
    folder: str | None = None,
    samples: int = 4,
) -> list[dict[str, Any]]:
    """Clusters with derived counts and a cover face, biggest first.

    ``named`` narrows the list to clusters the user has (True) or has not
    (False) named — the merge picker asks for the unnamed ones only, because
    folding a named person into another cluster would throw that name away.

    ``near`` ranks the clusters that share folders with that person's faces
    first (then the usual biggest-first), and annotates each row with
    ``shared_folders``/``shared_faces``/``shared_folder_names``. That is how the
    merge picker suggests the right duplicates: with per-folder media, the true
    duplicate of a cluster is nearly always filed next to it. The ranking weight
    (``affinity``) is the dot product of the two clusters' per-folder face counts,
    so a folder this person is all over counts for more than one they merely pass
    through — otherwise a busy folder shared with other people would outrank the
    duplicate.

    ``folder`` narrows the list to clusters with a face in a folder whose path
    contains that text (case-insensitive), annotating the matches with
    ``match_folders``. That is the picker's folder box: with the collection filed
    per folder, "the ones from this folder" is the fastest way to find the
    duplicate.

    Every row carries ``folder_names`` (the folders its faces are filed in,
    busiest first) so a caller can show where a cluster comes from, and
    ``sample_urls`` holds up to ``samples`` crops — the picker wants a few, the
    review queue a handful more so a cluster can be recognised at a glance.
    """
    folder_cond, folder_params = _folder_filter_condition(folder)
    sample_count = max(1, min(12, int(samples)))
    if near is None:
        conditions = [c for c in (_people_name_condition(named), folder_cond) if c]
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"""
            SELECT p.id AS person_id,
                   p.name AS name,
                   p.created_at AS created_at,
                   COUNT(fa.id) AS face_count,
                   COUNT(DISTINCT fa.file_path) AS media_count,
                   MAX(fa.frame_time) AS last_seen
            FROM people p
            LEFT JOIN faces fa ON fa.person_id = p.id
            {where}
            GROUP BY p.id
            HAVING COUNT(fa.id) > 0 OR p.name IS NOT NULL
            ORDER BY face_count DESC, p.id ASC
            LIMIT ? OFFSET ?
            """,
            (*folder_params, max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
    else:
        target = int(near)
        conditions = [c for c in (_people_name_condition(named), folder_cond) if c]
        extra = "".join(f" AND {c}" for c in conditions)
        rows = conn.execute(
            f"""
            WITH target_folders AS (
                SELECT f2.folder_path AS folder_path, COUNT(*) AS n
                FROM faces fa2
                JOIN files f2 ON f2.path = fa2.file_path
                WHERE fa2.person_id = ?
                      AND f2.folder_path IS NOT NULL AND f2.folder_path <> ''
                GROUP BY f2.folder_path
            )
            SELECT p.id AS person_id,
                   p.name AS name,
                   p.created_at AS created_at,
                   COUNT(fa.id) AS face_count,
                   COUNT(DISTINCT fa.file_path) AS media_count,
                   COUNT(DISTINCT CASE WHEN tf.folder_path IS NOT NULL
                         THEN f.folder_path END) AS shared_folders,
                   SUM(CASE WHEN tf.folder_path IS NULL THEN 0 ELSE 1 END)
                       AS shared_faces,
                   COALESCE(SUM(COALESCE(tf.n, 0)), 0) AS affinity
            FROM people p
            JOIN faces fa ON fa.person_id = p.id
            LEFT JOIN files f ON f.path = fa.file_path
            LEFT JOIN target_folders tf ON tf.folder_path = f.folder_path
            WHERE p.id <> ?{extra}
            GROUP BY p.id
            ORDER BY affinity DESC, face_count DESC, p.id ASC
            LIMIT ? OFFSET ?
            """,
            (target, target, *folder_params, max(1, int(limit)), max(0, int(offset))),
        ).fetchall()

    shared_names: dict[int, list[str]] = {}
    match_names: dict[int, list[str]] = {}
    if rows and (near is not None or folder):
        candidates = [int(r["person_id"]) for r in rows]
        per_person = _folders_of_files(conn, candidates)
        if near is not None:
            target_folders = sorted(
                _folders_of_files(conn, [int(near)]).get(int(near), set())
            )
            for person_id in candidates:
                names = [
                    _folder_label(p)
                    for p in target_folders
                    if p in per_person.get(person_id, ())
                ]
                if names:
                    shared_names[person_id] = names
        needle = (folder or "").strip().lower()
        if needle:
            for person_id in candidates:
                names = [
                    _folder_label(p)
                    for p in sorted(per_person.get(person_id, ()))
                    if needle in p.lower()
                ]
                if names:
                    match_names[person_id] = names[:3]

    people: list[dict[str, Any]] = []
    folder_labels = _top_folders_of_files(conn, [int(r["person_id"]) for r in rows])
    for r in rows:
        person_id = int(r["person_id"])
        cover = conn.execute(
            "SELECT id, crop, frame_time, file_path FROM faces WHERE person_id = ? "
            "ORDER BY (COALESCE(w,0) * COALESCE(h,0)) DESC, id ASC LIMIT ?",
            (person_id, sample_count),
        ).fetchall()
        samples = [_crop_url(c["id"], c["crop"]) for c in cover if c["crop"]]
        entry = {
            "id": person_id,
            "name": r["name"],
            "created_at": r["created_at"],
            "face_count": int(r["face_count"] or 0),
            "media_count": int(r["media_count"] or 0),
            "cover_url": samples[0] if samples else None,
            "sample_urls": samples,
            "is_named": bool(r["name"]),
            "folder_names": folder_labels.get(person_id, []),
        }
        if near is not None:
            entry["shared_folders"] = int(r["shared_folders"] or 0)
            entry["shared_faces"] = int(r["shared_faces"] or 0)
            entry["affinity"] = int(r["affinity"] or 0)
            entry["shared_folder_names"] = (shared_names.get(person_id) or [])[:3]
        if match_names:
            entry["match_folders"] = match_names.get(person_id, [])
        people.append(entry)
    return people


def count_people(
    conn: Any, named: bool | None = None, folder: str | None = None
) -> int:
    """How many clusters a matching ``read_people`` call could return in total."""
    folder_cond, folder_params = _folder_filter_condition(folder)
    conditions = [c for c in (_people_name_condition(named), folder_cond) if c]
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return int(
        conn.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT p.id FROM people p
                LEFT JOIN faces fa ON fa.person_id = p.id
                {where}
                GROUP BY p.id
                HAVING COUNT(fa.id) > 0 OR p.name IS NOT NULL
            )
            """,
            folder_params,
        ).fetchone()[0]
    )


def read_person_faces(
    conn: Any, person_id: int, limit: int = 120, offset: int = 0
) -> list[dict[str, Any]]:
    """A person's faces, best-scoring first, for the review grid."""
    rows = conn.execute(
        """
        SELECT fa.*, f.name AS name, f.type AS type
        FROM faces fa
        LEFT JOIN files f ON f.path = fa.file_path
        WHERE fa.person_id = ?
        ORDER BY fa.score DESC, fa.id ASC
        LIMIT ? OFFSET ?
        """,
        (int(person_id), max(1, int(limit)), max(0, int(offset))),
    ).fetchall()
    return [_face_entry(r) for r in rows]


def count_person_faces(conn: Any, person_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM faces WHERE person_id = ?", (int(person_id),)
        ).fetchone()[0]
    )


def read_person_media(
    conn: Any,
    person_id: int,
    limit: int = 500,
    offset: int = 0,
    sort: str = "count",
    max_moments: int = 5000,
) -> dict[str, Any]:
    """Every file a person appears in, each with the moments they appear at.

    A video is listed once (not once per sampled frame) with its hit timestamps,
    so a 10-minute clip does not flood the page with the same thumbnail. Rows are
    shaped like Library browse entries, so the page can reuse the same card,
    lightbox and media URLs.
    """
    pid = int(person_id)
    order_sql = {
        "count": "faces DESC, LOWER(name) ASC",
        "name": "LOWER(name) ASC",
        "date": "mtime DESC",
        "size": "size DESC",
    }.get(sort or "count", "faces DESC, LOWER(name) ASC")

    rows = conn.execute(
        f"""
        SELECT fa.file_path AS file_path, COUNT(*) AS faces,
               MAX(f.mtime) AS mtime, MAX(f.size) AS size, MAX(f.name) AS name
        FROM faces fa
        JOIN files f ON f.path = fa.file_path
        WHERE fa.person_id = ?
        GROUP BY fa.file_path
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        (pid, max(1, int(limit)), max(0, int(offset))),
    ).fetchall()
    total = int(
        conn.execute(
            "SELECT COUNT(DISTINCT file_path) FROM faces WHERE person_id = ?", (pid,)
        ).fetchone()[0]
    )

    paths = [r["file_path"] for r in rows]
    hit_counts = {r["file_path"]: int(r["faces"] or 0) for r in rows}

    files: dict[str, Any] = {}
    for start in range(0, len(paths), SQL_IN_CHUNK):
        chunk = paths[start : start + SQL_IN_CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        try:
            cur = conn.execute(
                f"SELECT f.*, CASE WHEN fav.path IS NOT NULL THEN 1 ELSE 0 END AS is_favorite "
                f"FROM files f LEFT JOIN favorites fav ON f.path = fav.path "
                f"WHERE f.path IN ({placeholders})",
                chunk,
            )
            for r in cur.fetchall():
                files[r["path"]] = r
        except Exception as e:
            logger.error(f"[Faces] Could not read person media rows: {e}")

    moments: dict[str, list[dict[str, Any]]] = {}
    moment_rows_budget = max(1, int(max_moments))
    for start in range(0, len(paths), SQL_IN_CHUNK):
        if moment_rows_budget <= 0:
            break
        chunk = paths[start : start + SQL_IN_CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        try:
            cur = conn.execute(
                f"SELECT id, file_path, frame_time, crop FROM faces "
                f"WHERE person_id = ? AND file_path IN ({placeholders}) "
                f"AND frame_time >= 0 ORDER BY frame_time ASC LIMIT ?",
                [pid, *chunk, moment_rows_budget],
            )
            for r in cur.fetchall():
                moments.setdefault(r["file_path"], []).append(
                    {
                        "frame_time": float(r["frame_time"]),
                        "crop_url": _crop_url(r["id"], r["crop"]),
                    }
                )
                moment_rows_budget -= 1
        except Exception as e:
            logger.error(f"[Faces] Could not read person moments: {e}")

    media: list[dict[str, Any]] = []
    for path in paths:
        row = files.get(path)
        if row is None:
            continue
        entry = _build_file_entry(row)
        video_moments = moments.get(path, [])
        entry["frames"] = hit_counts.get(path, 0)
        entry["moments"] = video_moments[:24]
        if video_moments:
            entry["first_frame_time"] = video_moments[0]["frame_time"]
        media.append(entry)

    return {
        "files": media,
        "total": total,
        "truncated": total > max(0, int(offset)) + len(media),
    }


def resolve_person_ids(conn: Any, term: str) -> list[int]:
    """Person ids whose name matches ``term`` (case-insensitive substring).

    Powers the Library's ``person:<name>`` search token. An unknown name returns
    an empty list, which the caller reports as "no such person" rather than
    silently searching for nothing.
    """
    clean = (term or "").strip().lower()
    if not clean:
        return []
    try:
        rows = conn.execute(
            "SELECT id FROM people WHERE name IS NOT NULL AND name <> '' "
            "AND LOWER(name) LIKE ? ORDER BY LOWER(name) ASC",
            (f"%{clean}%",),
        ).fetchall()
        return [int(r["id"]) for r in rows]
    except Exception as e:
        logger.error(f"[Faces] Could not resolve person '{term}': {e}")
        return []


def people_counts(conn: Any = None) -> dict[str, int]:
    """Totals for the /people header and the tasks-modal card."""
    owned = conn is None
    try:
        if owned:
            with db_conn() as local:
                return people_counts(local)
        counts = {
            "people": int(conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]),
            "named": int(
                conn.execute(
                    "SELECT COUNT(*) FROM people WHERE name IS NOT NULL AND TRIM(name) <> ''"
                ).fetchone()[0]
            ),
            "faces": int(conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0]),
            "dismissed": int(
                conn.execute("SELECT COUNT(*) FROM dismissed_faces").fetchone()[0]
            ),
            "files": int(
                conn.execute("SELECT COUNT(DISTINCT file_path) FROM faces").fetchone()[
                    0
                ]
            ),
        }
        counts["unnamed"] = max(0, counts["people"] - counts["named"])
        return counts
    except Exception as e:
        logger.error(f"[Faces] Could not read people counts: {e}")
        return {
            "people": 0,
            "named": 0,
            "unnamed": 0,
            "faces": 0,
            "dismissed": 0,
            "files": 0,
        }


def face_stats() -> dict[str, Any]:
    """Scan coverage figures: how much of the eligible library has been seen."""
    stats = {
        "total_files": 0,
        "pending_count": 0,
        "processed_count": 0,
        "percent": 100.0,
    }
    try:
        with db_conn() as conn:
            base = """
                FROM files f
                LEFT JOIN folders fo ON f.folder_path = fo.path
                LEFT JOIN face_scans fs ON fs.file_path = f.path
                WHERE COALESCE(fo.is_zip, 0) = 0
                  AND (
                        (f.type = 'image' AND f.has_thumb = 1)
                     OR (f.type = 'video' AND f.duration IS NOT NULL)
                  )
            """
            total = int(conn.execute(f"SELECT COUNT(*) {base}").fetchone()[0])
            done = int(
                conn.execute(
                    f"SELECT COUNT(*) {base} AND fs.file_path IS NOT NULL "
                    "AND (fs.mtime IS NULL OR f.mtime IS NULL OR fs.mtime = f.mtime) "
                    "AND (fs.size IS NULL OR f.size IS NULL OR fs.size = f.size)"
                ).fetchone()[0]
            )
        stats["total_files"] = total
        stats["processed_count"] = done
        stats["pending_count"] = max(0, total - done)
        stats["percent"] = round((done / total * 100.0), 1) if total else 100.0
    except Exception as e:
        logger.error(f"[Faces] Could not read face scan stats: {e}")
    return stats


def face_status_payload() -> dict[str, Any]:
    """Everything the /people page and tasks modal need about the feature."""
    from media_library.workers.face_worker import face_worker_instance

    status = dict(face_worker_instance.get_status())
    engine = engine_status()
    status.update(
        {
            "enabled": is_enabled(),
            "engine_available": bool(engine.get("available")),
            "engine_reason": engine.get("reason", ""),
            "model_dir": engine.get("model_dir", ""),
            "threshold": get_threshold(),
            "threshold_default": face_match_threshold(),
            "threshold_preset": threshold_preset_name(get_threshold()),
            "threshold_presets": dict(THRESHOLD_PRESETS),
        }
    )
    return status
