"""Background in-place H.265 (x265 / HEVC) conversion worker.

Powers the Library lightbox's "Convert to x265" action: it re-encodes a single
video to HEVC at a visually transparent CRF, then atomically replaces the
original file on disk (same MP4 output, new ``.mp4`` name when the source used
another container) so no duplicate is left behind.

Design notes
------------
* One job at a time — x265 is CPU heavy, so serialising jobs keeps the machine
  responsive for the rest of the app instead of saturating every core.
* ``preset medium`` / ``crf 18`` targets *no visible loss*: CRF is a
  quality target, so the output looks the same while typically still being
  smaller than the H.264 source.
* Audio is stream-copied when the MP4 container already accepts the codec
  (aac/mp3/ac3/eac3/alac) and re-encoded to AAC otherwise, so a video in a
  format the browser cannot play audio for still ends up web-playable.
* Writes go to ``TRANSCODE_TEMP_DIR`` first and are committed with
  ``os.replace`` (falling back to a verified copy + replace across devices),
  matching the transcode worker's FUSE-safe pattern.
"""

import asyncio
import logging
import os
import shutil
import threading
import time
import uuid
from typing import Any

from media_library.core.config import TRANSCODE_TEMP_DIR
from media_library.core.state import format_size, get_thumbnail_cache_path
from media_library.db.connection import db_conn
from media_library.db.indexer import update_file_in_index
from media_library.services.anim_cache import unlink_animated_previews
from media_library.services.ffmpeg_service import format_eta_str, probe_video_meta
from media_library.workers.base import BaseWorker

logger = logging.getLogger(__name__)

# Video codecs whose streams the MP4 container accepts unchanged, so the audio
# track can be stream-copied instead of re-encoded (which would throw away
# quality for nothing). Anything else is transcoded to AAC.
_MP4_AUDIO_CODECS = {"aac", "mp3", "ac3", "eac3", "alac"}

# Quality knobs. CRF is a quality target, and 18 at "medium" is visually
# transparent for x265 while still shrinking typical H.264 sources.
X265_PRESET = "medium"
X265_CRF = 18
X265_AUDIO_BITRATE = "192k"

_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


class H265ConvertWorker(BaseWorker):
    """Serial, in-place H.265 (HEVC) re-encoding queue with live progress."""

    def __init__(self):
        super().__init__("h265_worker")
        self.queue: asyncio.Queue = asyncio.Queue()
        self.active: dict[str, Any] | None = None
        self.jobs: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []

    # -- queue management --------------------------------------------------

    def _serialize(self, job: dict[str, Any] | None) -> dict[str, Any] | None:
        """Job dict without the non-JSON-serializable ``abort_event``."""
        if not job:
            return None
        return {k: v for k, v in job.items() if k != "abort_event"}

    def job_for_path(self, path: str) -> dict[str, Any] | None:
        """The queued/running job for ``path``, if any."""
        for job in self.jobs.values():
            if job.get("source_path") == path and job.get("status") in (
                "queued",
                "converting",
            ):
                return job
        return None

    def enqueue(self, source_path: str) -> dict[str, Any]:
        """Queue ``source_path`` for x265 conversion; raises ``ValueError`` when busy."""
        if self.job_for_path(source_path):
            raise ValueError("A conversion for this file is already queued or running")
        job_id = uuid.uuid4().hex[:10]
        job = {
            "id": job_id,
            "source_path": source_path,
            "filename": os.path.basename(source_path),
            "target_path": source_path,
            "status": "queued",
            "percent": 0.0,
            "fps": 0.0,
            "speed": "",
            "eta": "",
            "method": "x265",
            "message": "",
            "error": None,
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "size_before": 0,
            "size_after": 0,
            "abort_event": threading.Event(),
        }
        try:
            job["size_before"] = (
                os.path.getsize(source_path) if os.path.exists(source_path) else 0
            )
        except OSError:
            job["size_before"] = 0
        self.jobs[job_id] = job
        self._prune_jobs()
        self.enqueue_nowait(self.queue, job)
        return job

    def _prune_jobs(self):
        if len(self.jobs) <= 200:
            return
        keep = {h.get("id") for h in self.history[-50:]}
        if self.active:
            keep.add(self.active.get("id"))
        for key in list(self.jobs.keys()):
            if key not in keep and self.jobs[key].get("status") in _TERMINAL_STATUSES:
                del self.jobs[key]

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.get("status") in ("queued", "converting"):
            job["status"] = "cancelled"
            if job.get("abort_event"):
                job["abort_event"].set()
            return True
        return False

    def get_status(self, path: str | None = None) -> dict[str, Any]:
        """Status payload; when ``path`` is given, ``job`` is that file's job."""
        queued = [j for j in self.jobs.values() if j.get("status") == "queued"]
        job = None
        if path:
            job = self.job_for_path(path)
            if not job:
                # Fall back to the most recent job for this path (terminal),
                # so the client can observe completion/failure after the fact.
                recent = [j for j in self.jobs.values() if j.get("source_path") == path]
                if recent:
                    job = max(recent, key=lambda j: j.get("created_at", 0))
        return {
            "status": self._status,
            "active": self._serialize(self.active),
            "job": self._serialize(job),
            "queue_length": self.queue.qsize(),
            "active_count": 1 if self.active else 0,
            "queued_count": len(queued),
            "queued": [self._serialize(j) for j in queued],
            "jobs": [self._serialize(j) for j in self.jobs.values()],
            "history": [self._serialize(j) for j in self.history[-20:]],
        }

    def _finish(self, job: dict[str, Any]):
        job.setdefault("completed_at", time.time())
        self.history.append(job)
        if len(self.history) > 500:
            self.history = self.history[-500:]

    # -- conversion loop ---------------------------------------------------

    async def _run_loop(self):
        while True:
            try:
                item = await self.queue.get()
                try:
                    if item.get("status") in ("cancelled", "completed"):
                        continue
                    if not os.path.exists(item["source_path"]):
                        item["status"] = "failed"
                        item["error"] = "Source file missing"
                        self._finish(item)
                        continue

                    item["status"] = "converting"
                    item["started_at"] = time.time()
                    self.active = item
                    self._status = "converting"

                    await self._convert(item)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    item["status"] = "failed"
                    item["error"] = str(e)
                    self._finish(item)
                    logger.error(f"Error converting to x265: {e}")
                finally:
                    self.active = None
                    self._status = "idle"
                    try:
                        self.queue.task_done()
                    except (ValueError, Exception):
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Fatal error in x265 worker loop: {e}")
                await asyncio.sleep(1.0)

    def _build_command(
        self, source_path: str, temp_path: str, copy_audio: bool
    ) -> list[str]:
        cmd = [
            "nice",
            "-n",
            "10",
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            source_path,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx265",
            "-preset",
            X265_PRESET,
            "-crf",
            str(X265_CRF),
            "-pix_fmt",
            "yuv420p",
            "-tag:v",
            "hvc1",
        ]
        if copy_audio:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", X265_AUDIO_BITRATE, "-ac", "2"]
        cmd += [
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-f",
            "mp4",
            temp_path,
        ]
        return cmd

    async def _convert(self, item: dict[str, Any]):
        source_path = item["source_path"]
        source_dir = os.path.dirname(source_path)
        base_name = os.path.splitext(os.path.basename(source_path))[0]
        target_path = os.path.join(source_dir, f"{base_name}.mp4")
        if target_path != source_path and os.path.exists(target_path):
            # Never clobber an unrelated same-stem file that is already there.
            cnt = 1
            while os.path.exists(os.path.join(source_dir, f"{base_name}_{cnt}.mp4")):
                cnt += 1
            target_path = os.path.join(source_dir, f"{base_name}_{cnt}.mp4")
        item["target_path"] = target_path

        probe = await probe_video_meta(source_path)
        duration = float(probe.get("duration") or 0.0)
        audio_codec = (probe.get("audio_codec") or "").lower()
        video_codec = (probe.get("video_codec") or "").lower()
        item["video_codec_before"] = video_codec

        abort_event: threading.Event = item["abort_event"]

        # Re-encoding an already-HEVC file only adds generation loss, so treat
        # it as a no-op and tell the user.
        if video_codec == "hevc":
            item["percent"] = 100.0
            item["method"] = "skipped"
            item["message"] = "Already H.265/HEVC — nothing to do"
            item["status"] = "completed"
            self._finish(item)
            return

        if abort_event.is_set():
            item["status"] = "cancelled"
            self._finish(item)
            return

        copy_audio = audio_codec in _MP4_AUDIO_CODECS
        item["audio_mode"] = "copy" if copy_audio else "aac"

        temp_path = str(
            TRANSCODE_TEMP_DIR / f"h265_{item['id']}_{os.path.basename(target_path)}"
        )
        cmd = self._build_command(source_path, temp_path, copy_audio)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        async def read_progress():
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if "=" not in text:
                    continue
                key, val = text.split("=", 1)
                if key in ("out_time_us", "out_time_ms"):
                    try:
                        out_sec = int(val) / 1_000_000.0
                        if duration > 0:
                            item["percent"] = round(
                                min(99.0, max(0.0, (out_sec / duration) * 100.0)), 1
                            )
                    except ValueError:
                        pass
                elif key == "fps":
                    try:
                        item["fps"] = round(float(val), 1)
                    except ValueError:
                        pass
                elif key == "speed":
                    item["speed"] = val
                    try:
                        spd = float(val.replace("x", "").strip())
                        if spd > 0 and duration > 0:
                            rem = (
                                max(
                                    0.0,
                                    duration - (item["percent"] / 100.0 * duration),
                                )
                                / spd
                            )
                            item["eta"] = format_eta_str(rem)
                    except ValueError:
                        pass

        progress_task = asyncio.create_task(read_progress())
        cancelled = False
        while proc.returncode is None:
            if abort_event.is_set():
                cancelled = True
                try:
                    proc.terminate()
                except Exception:
                    pass
                break
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

        try:
            await progress_task
        except (asyncio.CancelledError, Exception):
            pass

        if cancelled:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            item["status"] = "cancelled"
            self._finish(item)
            return

        ok = (
            proc.returncode == 0
            and os.path.exists(temp_path)
            and os.path.getsize(temp_path) > 1024
        )
        if not ok:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            item["status"] = "failed"
            item["error"] = "Conversion failed (original preserved)"
            self._finish(item)
            return

        try:
            await asyncio.to_thread(self._commit, target_path, temp_path)
        except Exception as e:
            logger.error(f"Error committing x265 conversion to {target_path}: {e}")
            item["status"] = "failed"
            item["error"] = f"Failed to commit converted file: {e}"
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            self._finish(item)
            return

        item["size_after"] = (
            os.path.getsize(target_path) if os.path.exists(target_path) else 0
        )
        item["percent"] = 100.0
        item["status"] = "completed"
        item["message"] = "Converted"
        self._finish(item)

        if target_path != source_path and os.path.exists(source_path):
            try:
                await asyncio.to_thread(os.remove, source_path)
            except OSError as e:
                logger.warning(f"Could not remove original {source_path}: {e}")

        self._update_index(item, target_path, probe, copy_audio)

    @staticmethod
    def _commit(target_path: str, temp_path: str):
        """Atomically move the finished temp file onto ``target_path``."""
        target_dir = os.path.dirname(target_path)
        commit_tmp = os.path.join(target_dir, f".tmp_h265_{uuid.uuid4().hex[:8]}.mp4")
        try:
            try:
                os.replace(temp_path, target_path)
            except OSError:
                shutil.copy2(temp_path, commit_tmp)
                if not os.path.exists(commit_tmp) or os.path.getsize(
                    commit_tmp
                ) != os.path.getsize(temp_path):
                    raise OSError("Cross-device x265 copy verification failed")
                os.replace(commit_tmp, target_path)
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        finally:
            if os.path.exists(commit_tmp):
                try:
                    os.remove(commit_tmp)
                except OSError:
                    pass

    def _update_index(
        self,
        item: dict[str, Any],
        target_path: str,
        probe: dict[str, Any],
        copy_audio: bool,
    ):
        """Rewrite the SQLite index for the replaced file.

        Uses ``preserve_hashes=True`` so the (content-independent) probe columns
        and capture time survive, then clears the two content hashes (the bytes
        changed) and refreshes the codec/size film. ``has_thumb = 0`` lets the
        ThumbnailWorker regenerate the poster for the new file key, and
        ``has_animated`` resets to its default during the index rewrite.
        """
        source_path = item["source_path"]
        new_size = item["size_after"]

        # The cached animated preview is keyed on the *old* path/mtime/size, so
        # read those before the rewrite and drop the stale file (the new key has
        # none, exactly like the poster below).
        try:
            with db_conn() as conn:
                row = conn.execute(
                    "SELECT path, mtime, size FROM files WHERE path = ? LIMIT 1",
                    (source_path,),
                ).fetchone()
            if row:
                unlink_animated_previews([row])
        except Exception:
            pass

        try:
            update_file_in_index(
                source_path,
                target_path,
                os.path.basename(target_path),
                new_size,
                ".mp4",
                preserve_hashes=True,
            )
        except Exception as e:
            logger.error(f"Error updating index for x265 output {target_path}: {e}")

        audio_codec = (probe.get("audio_codec") or "").lower() if copy_audio else "aac"
        try:
            with db_conn() as conn, conn:
                conn.execute(
                    "UPDATE files SET quick_hash = NULL, thumb_hash = NULL, "
                    "video_codec = 'hevc', audio_codec = ?, needs_transcode = 0, "
                    "has_thumb = 0, size = ?, size_formatted = ? WHERE path = ?",
                    (
                        audio_codec or None,
                        new_size,
                        format_size(new_size),
                        target_path,
                    ),
                )
        except Exception as e:
            logger.error(f"Error refreshing probe columns for {target_path}: {e}")

        # Drop the stale poster (its key was derived from the old mtime/size);
        # the thumbnail worker's sweep regenerates it because has_thumb is 0.
        try:
            old_thumb = get_thumbnail_cache_path(source_path)
            if old_thumb.exists():
                old_thumb.unlink()
        except Exception:
            pass
        if target_path != source_path:
            try:
                stale = get_thumbnail_cache_path(target_path)
                if stale.exists():
                    stale.unlink()
            except Exception:
                pass


h265_worker_instance = H265ConvertWorker()
