"""Background face recognition worker ("People").

Sweeps the index and detects + embeds faces, one file at a time:

* **Videos** are decoded **once** each, and FFmpeg samples the timeline itself
  (``fps=1/FACE_VIDEO_SAMPLE_INTERVAL``, 2 s by default) at ``FACE_SCAN_WIDTH``
  pixels. One sequential pass beats one seek per frame over a (possibly FUSE)
  mount, and nothing is written to disk: the sweep keeps only the face crops it
  finds, so it neither fills nor evicts the candidate-frame cache the thumbnail
  chooser and animated previews share. A face is recorded **once per appearance**
  (see ``TrackBuilder``) rather than once per frame, which is what makes a 2 s
  grid affordable in database and clustering terms.
* **Images** are read from the source once and downsized to ``FACE_SCAN_WIDTH``
  — a 360px thumbnail hands the recogniser a 112px crop that is mostly upscale —
  falling back to the cached thumbnail when the source cannot be read, and
  staying pending when neither is available.

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
from media_library.services import candidate_cache, face_engine, face_service
from media_library.services.ffmpeg_frames import iter_video_scan_frames
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


def effective_video_interval(
    duration: float, interval: float, max_frames: int
) -> float:
    """Seconds between sampled frames for a clip of ``duration``.

    ``FACE_VIDEO_SAMPLE_INTERVAL`` is the density; ``FACE_VIDEO_MAX_FRAMES`` is a
    frame *budget*, so a clip long enough to blow it is sampled evenly instead
    (the spacing widens rather than the sweep stopping early).
    """
    try:
        duration = float(duration or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    interval = max(0.05, float(interval))
    cap = max(1, int(max_frames))
    if duration > 0 and (duration / interval) > cap:
        return duration / cap
    return interval


def _open_scan_image(path: str) -> Any:
    """Load a local scan image (thumbnail) as a PIL image."""
    with Image.open(path) as img:
        return img.convert("RGB").copy()


def _open_scan_bytes(data: bytes) -> Any:
    """Decode one JPEG frame from the sweep's stream as a PIL image."""
    import io

    with Image.open(io.BytesIO(data)) as img:
        return img.convert("RGB").copy()


def _load_scan_image(file_path: str, mtime: Any, size: Any) -> Any:
    """The sharpest local image available for one photo, or None.

    The source is read once and downsized to ``FACE_SCAN_WIDTH``: the recogniser
    aligns every crop to 112x112, so a 360px thumbnail gives it mostly upscale,
    and the blur that produces is why one person's faces scatter past the match
    threshold. The cached thumbnail is the fallback, so a photo whose source
    cannot be read is still scanned rather than staying pending forever.
    """
    width = max(160, int(getattr(config, "FACE_SCAN_WIDTH", 960)))
    try:
        if os.path.exists(file_path):
            with Image.open(file_path) as img:
                photo = img.convert("RGB")
                if photo.width > width:
                    height = max(1, int(photo.height * width / photo.width))
                    photo = photo.resize((width, height), Image.Resampling.BILINEAR)
                return photo.copy()
    except Exception as e:
        logger.debug(f"[Faces] Could not read {file_path} directly: {e}")

    thumb = get_thumbnail_cache_path(file_path, mtime, size)
    try:
        if thumb.exists() and thumb.stat().st_size > 0:
            return _open_scan_image(str(thumb))
    except OSError:
        pass
    return None


def _detect_faces(engine: Any, image: Any, frame_time: float) -> list[dict[str, Any]]:
    """Detect faces in one image. Crops are written later, for the keeper only."""
    import numpy as np

    try:
        rgb = np.asarray(image, dtype="uint8")
    except Exception:
        return []
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        return []
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])

    out: list[dict[str, Any]] = []
    for face in engine.detect(bgr):
        out.append(
            {
                "x": int(face["x"]),
                "y": int(face["y"]),
                "w": int(face["w"]),
                "h": int(face["h"]),
                "score": float(face.get("score") or 0.0),
                "embedding": face["embedding"],
                "frame_time": frame_time,
                "crop": None,
            }
        )
    return out


def _store_crop(file_path: str, face: dict[str, Any], image: Any) -> None:
    """Store the crop of a face that is being kept (mutates ``face['crop']``)."""
    try:
        name = face_service.face_crop_name(
            file_path,
            float(face.get("frame_time") or 0.0),
            int(face["x"]),
            int(face["y"]),
        )
        face["crop"] = name if face_service.write_face_crop(image, face, name) else None
    except Exception as e:
        logger.debug(f"[Faces] Could not store a crop for {file_path}: {e}")
        face["crop"] = None


def _face_quality(face: dict[str, Any]) -> float:
    """How good a representative a detection makes: big and confident."""
    return max(1.0, float(face.get("w") or 0) * float(face.get("h") or 0)) * max(
        0.05, float(face.get("score") or 0.0)
    )


class TrackBuilder:
    """Collapse per-frame detections into one record per person per appearance.

    A 2 s grid detects the same face dozens of times inside one shot. Keeping
    every one of them triples the database, skews a cluster's centroid towards
    whichever shot happened to be longest, and fills the review queue with
    copies of a person who is already clustered. A detection therefore joins an
    existing track while it stays within ``FACE_TRACK_DISTANCE`` of the track's
    running mean and within ``FACE_TRACK_GAP_S`` of its last hit; the track keeps
    its sharpest crop and its **mean** embedding, which is also a cleaner cluster
    vote than any single frame. A gap longer than the window starts a new track,
    so a person who leaves and returns is recorded again (and their page still
    has moments to jump to).
    """

    def __init__(
        self,
        gap_s: float,
        distance: float,
        on_best: Any = None,
    ):
        self.gap_s = max(0.0, float(gap_s))
        self.distance = min(1.2, max(0.02, float(distance)))
        self.on_best = on_best
        self._tracks: list[dict[str, Any]] = []

    def add(self, face: dict[str, Any], image: Any = None) -> None:
        import numpy as np

        embedding = face.get("embedding")
        if embedding is None:
            return
        vec = np.asarray(embedding, dtype="float32").reshape(-1)
        norm = float(np.linalg.norm(vec))
        if norm <= 0:
            return
        vec = vec / norm
        when = float(face.get("frame_time") or 0.0)

        best: dict[str, Any] | None = None
        best_distance = 0.0
        for track in self._tracks:
            if self.gap_s and (when - float(track["last"])) > self.gap_s:
                continue
            distance = 1.0 - float(track["mean"] @ vec)
            if distance <= self.distance and (best is None or distance < best_distance):
                best, best_distance = track, distance

        if best is None:
            track = {
                "mean": vec,
                "count": 1,
                "last": when,
                "best": dict(face),
                "quality": _face_quality(face),
            }
            self._tracks.append(track)
            self._promote(track, image)
            return

        count = int(best["count"])
        merged = best["mean"] * count + vec
        norm = float(np.linalg.norm(merged))
        best["mean"] = merged / norm if norm else vec
        best["count"] = count + 1
        best["last"] = when
        quality = _face_quality(face)
        if quality > float(best["quality"]):
            best["best"] = dict(face)
            best["quality"] = quality
            self._promote(best, image)

    def _promote(self, track: dict[str, Any], image: Any) -> None:
        """A track's representative changed: keep its crop (if we have the frame).

        The crop the previous representative wrote is left behind; the crop
        sweep and its LRU ceiling collect it, and it is at most one stale file
        per change.
        """
        if self.on_best is not None and image is not None:
            self.on_best(track["best"], image)

    def finish(self) -> list[dict[str, Any]]:
        """The records to store: one per track, with the track's mean embedding."""
        out: list[dict[str, Any]] = []
        for track in self._tracks:
            face = dict(track["best"])
            face["embedding"] = track["mean"]
            out.append(face)
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
        # The shared candidate-frame cache (the 480px stills the thumbnail chooser
        # and animated previews use) is reported next to the crop cache: it is the
        # other on-disk cache the People feature's numbers are read against.
        status.update(candidate_cache.frame_cache_payload())
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
        image = await asyncio.to_thread(
            _load_scan_image, row["path"], row["mtime"], row["size"]
        )
        if image is None:
            return None  # nothing readable yet: stay pending
        hits = await asyncio.to_thread(
            _detect_faces, engine, image, face_service.IMAGE_FRAME_TIME
        )
        for hit in hits:
            _store_crop(row["path"], hit, image)
        return hits

    async def _scan_video(self, engine: Any, row: Any) -> list[dict[str, Any]]:
        duration = float(row["duration"] or 0.0)
        interval = effective_video_interval(
            duration,
            getattr(config, "FACE_VIDEO_SAMPLE_INTERVAL", 2.0),
            getattr(config, "FACE_VIDEO_MAX_FRAMES", 20000),
        )
        width = int(getattr(config, "FACE_SCAN_WIDTH", 960))
        cap = max(1, int(getattr(config, "FACE_VIDEO_MAX_FRAMES", 20000)))
        builder = TrackBuilder(
            getattr(config, "FACE_TRACK_GAP_S", 15.0),
            getattr(config, "FACE_TRACK_DISTANCE", 0.25),
            on_best=lambda face, image: _store_crop(row["path"], face, image),
        )
        # One pass may decode an hour of video, so the ceiling scales with the
        # clip instead of being the 12 s a single frame extraction needed.
        timeout = max(180.0, duration * 1.5)
        async for _index, stamp, jpeg in iter_video_scan_frames(
            row["path"], interval, width, cap, timeout
        ):
            try:
                image = await asyncio.to_thread(_open_scan_bytes, jpeg)
            except Exception as e:
                logger.warning(f"[Faces] Could not read a frame of {row['path']}: {e}")
                continue
            hits = await asyncio.to_thread(_detect_faces, engine, image, float(stamp))
            for hit in hits:
                builder.add(hit, image)
        return builder.finish()

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
