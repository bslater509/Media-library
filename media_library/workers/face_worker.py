"""Background face recognition worker ("People").

Sweeps the index and detects + embeds faces, one file at a time:

* **Images** are read from the already-cached thumbnail in
  ``THUMBNAIL_CACHE_DIR`` — the source file is never pulled over the (possibly
  FUSE) mount, and an image whose thumbnail has not been generated yet simply
  stays pending until the thumbnail worker catches up.
* **Videos** are read from the cached 480px candidate frames the animated
  previews and the thumbnail chooser already use (one frame every
  ``FACE_VIDEO_SAMPLE_INTERVAL`` seconds, capped at ``FACE_VIDEO_MAX_FRAMES``),
  and every hit keeps its timestamp so a person's page can jump to the moment.

Only loose files are scanned: images inside ZIP archives are skipped entirely,
since detecting on them would force an archive extraction over FUSE.

Each file is scanned at most once per ``mtime``/``size`` (recorded in
``face_scans``), so a re-run resumes instead of restarting, and a file that
changed is re-scanned with its previous detections replaced. Memory is guarded
the same way the WebP optimizer guards itself: the sweep waits when available
memory (host *or* cgroup headroom) drops below ``MEMORY_GUARD_MB`` and skips the
current file rather than risking the kernel throttling the server.
"""

import asyncio
import logging
import os
import time
from typing import Any

from PIL import Image

from media_library.core import config
from media_library.core.state import get_thumbnail_cache_path
from media_library.db.connection import db_conn
from media_library.services import face_engine, face_service
from media_library.services.ffmpeg_frames import extract_candidate_frame
from media_library.workers.base import BaseWorker
from media_library.workers.webp_worker import (
    MEMORY_GUARD_MB,
    _available_memory_mb,
    _release_memory,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE = 8
#: A failed model download is retried at most this often.
_MODEL_RETRY_S = 600.0
_STATS_INTERVAL_S = 2.0


def sample_timestamps(duration: float, interval: float, max_frames: int) -> list[float]:
    """Frame timestamps to scan in a video of ``duration`` seconds."""
    try:
        duration = float(duration or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    interval = max(1.0, float(interval))
    max_frames = max(1, int(max_frames))
    if duration <= 0.0:
        return [0.0]

    safe_end = max(0.0, duration - min(0.5, duration * 0.02))
    step = interval if (duration / interval) <= max_frames else duration / max_frames
    stamps: list[float] = []
    current = 0.0
    while current < duration and len(stamps) < max_frames:
        stamps.append(round(min(current, safe_end), 3))
        current += step
    if not stamps:
        stamps = [0.0]
    if len(stamps) < max_frames and stamps[-1] < safe_end * 0.98:
        stamps.append(round(safe_end, 3))
    return stamps


def _open_scan_image(path: str) -> Any:
    """Load a local scan image (thumbnail / candidate frame) as a PIL image."""
    with Image.open(path) as img:
        return img.convert("RGB").copy()


def _detect_in_image(
    engine: Any,
    image: Any,
    file_path: str,
    frame_time: float,
) -> list[dict[str, Any]]:
    """Detect faces in one local image, writing a crop for each hit."""
    import numpy as np

    try:
        rgb = np.asarray(image, dtype="uint8")
    except Exception:
        return []
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        return []
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])

    faces = engine.detect(bgr)
    out: list[dict[str, Any]] = []
    for face in faces:
        name = face_service.face_crop_name(
            file_path, frame_time, int(face["x"]), int(face["y"])
        )
        stored = face_service.write_face_crop(image, face, name)
        out.append(
            {
                "x": int(face["x"]),
                "y": int(face["y"]),
                "w": int(face["w"]),
                "h": int(face["h"]),
                "score": float(face.get("score") or 0.0),
                "embedding": face["embedding"],
                "frame_time": frame_time,
                "crop": name if stored else None,
            }
        )
    return out


class FaceWorker(BaseWorker):
    """Detects faces and assigns them to person clusters."""

    def __init__(self):
        super().__init__("face_worker")
        self.state: dict[str, Any] = {
            "status": "idle",
            "total_files": 0,
            "pending_count": 0,
            "processed_count": 0,
            "percent": 100.0,
            "current_file": "",
            "faces_total": 0,
            "people_total": 0,
        }
        self._stats_at = 0.0
        self._last_model_try = 0.0

    def pause(self):
        super().pause()
        if self.state["status"] != "disabled":
            self.state["status"] = "paused"

    def resume(self):
        super().resume()
        if self.state["status"] == "paused":
            self.state["status"] = "idle"

    def get_status(self) -> dict[str, Any]:
        status = dict(self.state)
        status["is_paused"] = self.is_paused()
        status["enabled"] = face_service.is_enabled()
        status["engine"] = face_engine.engine_status()
        status.update(face_service.face_crop_payload())
        counts = face_service.people_counts()
        status["faces_total"] = counts.get("faces", 0)
        status["people_total"] = counts.get("people", 0)
        status["unnamed_people"] = counts.get("unnamed", 0)
        status["dismissed_faces"] = counts.get("dismissed", 0)
        return status

    def _refresh_stats(self):
        now = time.time()
        if now - self._stats_at < _STATS_INTERVAL_S:
            return
        self._stats_at = now
        stats = face_service.face_stats()
        self.state.update(
            {
                "total_files": stats["total_files"],
                "pending_count": stats["pending_count"],
                "processed_count": stats["processed_count"],
                "percent": stats["percent"],
            }
        )

    async def _next_batch(self) -> list[Any]:
        """Eligible, not-yet-scanned loose files (images first — they are cheap)."""
        try:
            with db_conn() as conn:
                return conn.execute(
                    """
                    SELECT f.path, f.type, f.mtime, f.size, f.duration
                    FROM files f
                    LEFT JOIN folders fo ON f.folder_path = fo.path
                    LEFT JOIN face_scans fs ON fs.file_path = f.path
                    WHERE COALESCE(fo.is_zip, 0) = 0
                      AND (
                            (f.type = 'image' AND f.has_thumb = 1)
                         OR (f.type = 'video' AND f.duration IS NOT NULL)
                      )
                      AND (
                            fs.file_path IS NULL
                         OR (f.mtime IS NOT NULL AND fs.mtime IS NOT NULL AND fs.mtime <> f.mtime)
                         OR (f.size IS NOT NULL AND fs.size IS NOT NULL AND fs.size <> f.size)
                      )
                    ORDER BY CASE f.type WHEN 'image' THEN 0 ELSE 1 END, f.path ASC
                    LIMIT ?
                    """,
                    (_BATCH_SIZE,),
                ).fetchall()
        except Exception as e:
            logger.error(f"[Faces] Could not read the next scan batch: {e}")
            return []

    async def _scan_image(self, engine: Any, row: Any) -> list[dict[str, Any]] | None:
        thumb = get_thumbnail_cache_path(row["path"], row["mtime"], row["size"])
        try:
            if not (thumb.exists() and thumb.stat().st_size > 0):
                return None  # thumbnail not generated yet: stay pending
        except OSError:
            return None
        try:
            image = await asyncio.to_thread(_open_scan_image, str(thumb))
        except Exception:
            return []
        return await asyncio.to_thread(
            _detect_in_image, engine, image, row["path"], face_service.IMAGE_FRAME_TIME
        )

    async def _scan_video(self, engine: Any, row: Any) -> list[dict[str, Any]]:
        stamps = sample_timestamps(
            row["duration"],
            getattr(config, "FACE_VIDEO_SAMPLE_INTERVAL", 30.0),
            getattr(config, "FACE_VIDEO_MAX_FRAMES", 24),
        )
        faces: list[dict[str, Any]] = []
        for stamp in stamps:
            try:
                frame = await extract_candidate_frame(row["path"], timestamp=stamp)
            except Exception as e:
                logger.warning(
                    f"[Faces] Frame extraction failed for {row['path']}: {e}"
                )
                continue
            if not frame:
                continue
            try:
                image = await asyncio.to_thread(_open_scan_image, str(frame))
            except Exception as e:
                logger.warning(f"[Faces] Could not read a frame of {row['path']}: {e}")
                continue
            hits = await asyncio.to_thread(
                _detect_in_image, engine, image, row["path"], float(stamp)
            )
            faces.extend(hits)
        return faces

    async def _wait_for_memory(self) -> bool:
        """Wait for headroom; False when the guard is still tripped after a minute."""
        waited = 0
        while _available_memory_mb() < MEMORY_GUARD_MB and waited < 60:
            if not self._pause_event.is_set():
                return False
            await asyncio.sleep(1.0)
            waited += 1
        return _available_memory_mb() >= MEMORY_GUARD_MB

    async def _run_loop(self):
        while True:
            try:
                if not face_service.is_enabled():
                    self.state["status"] = "disabled"
                    self.state["current_file"] = ""
                    await asyncio.sleep(10.0)
                    continue

                if not self._pause_event.is_set():
                    self.state["status"] = "paused"
                    await self._pause_event.wait()

                engine = face_engine.get_engine()
                if engine is None:
                    # Models missing (or OpenCV absent): try to fetch them, then
                    # report why faces cannot be scanned instead of spinning.
                    status = face_engine.engine_status()
                    if status.get("model_dir") and face_engine.opencv_available():
                        now = time.time()
                        if now - self._last_model_try > _MODEL_RETRY_S:
                            self._last_model_try = now
                            result = await asyncio.to_thread(
                                face_engine.download_models
                            )
                            if result.get("success") and result.get("downloaded"):
                                face_engine.reset_engine()
                                continue
                            if not result.get("success"):
                                logger.warning(
                                    f"[Faces] Model download failed: {result.get('error')}"
                                )
                    self.state["status"] = "unavailable"
                    self.state["current_file"] = ""
                    await asyncio.sleep(30.0)
                    continue

                self._refresh_stats()

                if not await self._wait_for_memory():
                    self.state["status"] = "paused"
                    continue

                batch = await self._next_batch()
                if not batch:
                    self.state["status"] = "idle"
                    self.state["current_file"] = ""
                    await asyncio.sleep(5.0)
                    continue

                self.state["status"] = "scanning"
                centroids = face_service.get_centroids()
                for row in batch:
                    if not self._pause_event.is_set():
                        break
                    path = row["path"]
                    self.state["current_file"] = os.path.basename(path)
                    faces = (
                        await self._scan_image(engine, row)
                        if row["type"] == "image"
                        else await self._scan_video(engine, row)
                    )
                    if faces is None:
                        # Nothing we can do with this file right now; leave it
                        # pending and move on rather than blocking the sweep.
                        continue
                    await asyncio.to_thread(
                        face_service.record_scan,
                        path,
                        row["mtime"],
                        row["size"],
                        faces,
                        centroids,
                    )
                    self.state["processed_count"] += 1
                    _release_memory()

                # Crop eviction is throttled internally (once a minute) and
                # re-queues the affected files, so call it after every batch.
                await asyncio.to_thread(face_service.manage_face_crop_lru)
                await asyncio.sleep(0.05)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Faces] Worker loop error: {e}")
                await asyncio.sleep(3.0)


face_worker_instance = FaceWorker()
