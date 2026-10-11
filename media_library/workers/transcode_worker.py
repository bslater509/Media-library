"""Background video transcoding worker with automatic MP4 conversion and progress tracking."""

import asyncio
import logging
import os
import shutil
import threading
import time
import uuid
from typing import Any

from media_library.core.config import TRANSCODE_TEMP_DIR
from media_library.core.constants import NATIVE_EXTENSIONS, VIDEO_EXTENSIONS
from media_library.core.state import (
    get_thumbnail_cache_path,
    make_video_meta,
    state,
)
from media_library.db.indexer import update_file_in_index
from media_library.services.ffmpeg_service import format_eta_str, probe_video_meta
from media_library.workers.base import BaseWorker

logger = logging.getLogger(__name__)


class TranscodeWorker(BaseWorker):
    """Worker managing video conversion queue, smart remuxing, and H.264 fallback."""

    def __init__(self):
        super().__init__("transcode_worker")
        self.transcode_queue: asyncio.Queue = asyncio.Queue()
        self.active_transcode: dict[str, Any] | None = None
        self.transcode_history: list[dict[str, Any]] = []
        self.transcode_items_map: dict[str, dict[str, Any]] = {}
        self.transcode_paused: bool = False
        self.transcode_pause_event: asyncio.Event = asyncio.Event()
        self.transcode_pause_event.set()

    def enqueue(self, item: dict[str, Any]):
        if len(self.transcode_items_map) > 1000:
            active_ids = {h.get("id") for h in self.transcode_history[-100:]}
            if self.active_transcode:
                active_ids.add(self.active_transcode.get("id"))
            for k in list(self.transcode_items_map.keys()):
                if k not in active_ids and self.transcode_items_map[k].get("status") in (
                    "completed",
                    "failed",
                    "cancelled",
                ):
                    del self.transcode_items_map[k]
        self.transcode_items_map[item["id"]] = item
        self.enqueue_nowait(self.transcode_queue, item)

    def pause(self):
        self.transcode_paused = True
        self.transcode_pause_event.clear()
        self._status = "paused"

    def resume(self):
        self.transcode_paused = False
        self.transcode_pause_event.set()
        self._status = "running"

    def cancel(self, task_id: str) -> bool:
        if task_id in self.transcode_items_map:
            item = self.transcode_items_map[task_id]
            item["status"] = "cancelled"
            if item.get("abort_event"):
                item["abort_event"].set()
            return True
        return False

    def cancel_all(self):
        for item in self.transcode_items_map.values():
            if item.get("status") in ("queued", "transcoding"):
                item["status"] = "cancelled"
                if item.get("abort_event"):
                    item["abort_event"].set()

    def enqueue_for_backup(
        self, folder_path: str | None = None, file_path: str | None = None
    ) -> list[dict[str, Any]]:
        from media_library.services.media_service import validate_backup_path

        enqueued = []
        files_to_check = []
        if file_path:
            real_file = validate_backup_path(file_path)
            if os.path.exists(real_file) and os.path.isfile(real_file):
                files_to_check.append(real_file)
        elif folder_path:
            real_folder = validate_backup_path(folder_path)
            if os.path.exists(real_folder) and os.path.isdir(real_folder):
                try:
                    with os.scandir(real_folder) as entries:
                        for entry in entries:
                            if entry.is_file(
                                follow_symlinks=False
                            ) and not entry.name.startswith("."):
                                files_to_check.append(entry.path)
                except Exception as e:
                    logger.error(f"Error scanning backup folder for transcode: {e}")

        for fpath in files_to_check:
            ext = os.path.splitext(fpath)[1].lower()
            if ext in VIDEO_EXTENSIONS and ext != ".mp4":
                fname = os.path.basename(fpath)
                existing = any(
                    t.get("source_path") == fpath
                    and t.get("status") in ("queued", "transcoding", "completed")
                    for t in self.transcode_items_map.values()
                )
                if not existing:
                    task_id = uuid.uuid4().hex[:10]
                    item = {
                        "id": task_id,
                        "file_id": fname,
                        "filename": fname,
                        "source_path": fpath,
                        "target_path": None,
                        "status": "queued",
                        "percent": 0.0,
                        "fps": 0.0,
                        "speed": "",
                        "eta": "",
                        "method": "pending",
                        "created_at": time.time(),
                        "started_at": None,
                        "completed_at": None,
                        "error": None,
                        "abort_event": threading.Event(),
                    }
                    self.enqueue(item)
                    enqueued.append(
                        {
                            "id": task_id,
                            "filename": fname,
                            "source_path": fpath,
                        }
                    )
        return enqueued

    def enqueue_indexed_folder(self, folder_path: str) -> list[dict[str, Any]]:
        """Enqueues non-native videos in an indexed folder using SQLite only.

        Unlike :meth:`enqueue_for_backup`, this performs no ``os.scandir`` or
        ``os.path`` calls, so it is safe to call from request handlers on remote
        FUSE mounts. Missing files are handled by the worker loop, which marks a
        queued item failed if its source no longer exists.
        """
        from media_library.db.connection import db_conn

        rows = []
        try:
            with db_conn() as conn:
                rows = conn.execute(
                    "SELECT path, name FROM files "
                    "WHERE folder_path = ? AND type = 'video' AND LOWER(ext) != '.mp4'",
                    (folder_path,),
                ).fetchall()
        except Exception as e:
            logger.error(f"Error querying indexed videos for transcode: {e}")
            rows = []

        enqueued = []
        for r in rows:
            fpath = r["path"]
            ext = os.path.splitext(fpath)[1].lower()
            if ext not in VIDEO_EXTENSIONS or ext == ".mp4":
                continue
            existing = any(
                t.get("source_path") == fpath
                and t.get("status") in ("queued", "transcoding", "completed")
                for t in self.transcode_items_map.values()
            )
            if existing:
                continue
            fname = r["name"]
            task_id = uuid.uuid4().hex[:10]
            item = {
                "id": task_id,
                "file_id": fname,
                "filename": fname,
                "source_path": fpath,
                "target_path": None,
                "status": "queued",
                "percent": 0.0,
                "fps": 0.0,
                "speed": "",
                "eta": "",
                "method": "pending",
                "created_at": time.time(),
                "started_at": None,
                "completed_at": None,
                "error": None,
                "abort_event": threading.Event(),
            }
            self.enqueue(item)
            enqueued.append(
                {
                    "id": task_id,
                    "filename": fname,
                    "source_path": fpath,
                }
            )
        return enqueued

    def enqueue_all_for_backup(self) -> list[dict[str, Any]]:
        from media_library.db.connection import db_conn

        try:
            with db_conn() as conn:
                cur = conn.execute(
                    "SELECT path, name, size FROM files WHERE type = 'video' AND LOWER(path) NOT LIKE '%.mp4' ORDER BY size ASC"
                )
                rows = cur.fetchall()
        except Exception as e:
            logger.error(f"Error querying non-mp4 files for bulk transcode: {e}")
            rows = []

        enqueued = []
        for r in rows:
            fpath = r["path"]
            if not os.path.exists(fpath):
                continue

            existing = any(
                t.get("source_path") == fpath
                and t.get("status") in ("queued", "transcoding", "completed")
                for t in self.transcode_items_map.values()
            )
            if not existing:
                fname = r["name"]
                task_id = uuid.uuid4().hex[:10]
                item = {
                    "id": task_id,
                    "file_id": fname,
                    "filename": fname,
                    "source_path": fpath,
                    "target_path": None,
                    "status": "queued",
                    "percent": 0.0,
                    "fps": 0.0,
                    "speed": "",
                    "eta": "",
                    "method": "pending",
                    "created_at": time.time(),
                    "started_at": None,
                    "completed_at": None,
                    "error": None,
                    "abort_event": threading.Event(),
                }
                self.enqueue(item)
                enqueued.append(
                    {
                        "id": task_id,
                        "filename": fname,
                        "source_path": fpath,
                    }
                )
        return enqueued

    def get_status(self) -> dict[str, Any]:
        return {
            "active": self.active_transcode,
            "paused": self.transcode_paused,
            "queue_length": self.transcode_queue.qsize(),
            "history": self.transcode_history[-20:],
        }

    async def _run_loop(self):
        while True:
            try:
                await self.transcode_pause_event.wait()
                item = await self.transcode_queue.get()
                try:
                    if item.get("status") in ("cancelled", "completed"):
                        continue

                    if not os.path.exists(item["source_path"]):
                        item["status"] = "failed"
                        item["error"] = "Source file missing"
                        continue

                    item["status"] = "transcoding"
                    item["started_at"] = time.time()
                    self.active_transcode = item
                    self._status = "transcoding"

                    await self._run_transcode_task(item)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    item["status"] = "failed"
                    item["error"] = str(e)
                    logger.error(f"Error in transcode task: {e}")
                finally:
                    self.active_transcode = None
                    self._status = "idle"
                    try:
                        self.transcode_queue.task_done()
                    except (ValueError, Exception):
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Fatal error in transcode worker outer loop: {e}")
                await asyncio.sleep(1.0)

    async def _run_transcode_task(self, item: dict[str, Any]):
        source_path = item["source_path"]
        if not os.path.exists(source_path):
            item["status"] = "failed"
            item["error"] = "Source file missing"
            return

        source_dir = os.path.dirname(source_path)
        base_name = os.path.splitext(os.path.basename(source_path))[0]

        target_name = f"{base_name}.mp4"
        target_path = os.path.join(source_dir, target_name)
        if os.path.exists(target_path) and target_path != source_path:
            cnt = 1
            while os.path.exists(
                os.path.join(source_dir, f"{base_name}_converted_{cnt}.mp4")
            ):
                cnt += 1
            target_path = os.path.join(source_dir, f"{base_name}_converted_{cnt}.mp4")

        temp_path = str(TRANSCODE_TEMP_DIR / f"temp_{item['id']}_{target_name}")
        item["target_path"] = target_path

        probe = await probe_video_meta(source_path)
        duration = probe.get("duration", 0.0)
        video_codec = probe.get("video_codec", "")
        audio_codec = probe.get("audio_codec", "")

        can_remux = video_codec == "h264" and audio_codec in ("aac", "mp3", "")
        success = False
        abort_event: threading.Event = item["abort_event"]

        if can_remux:
            item["method"] = "remux"
            remux_cmd = [
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
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                temp_path,
            ]
            proc = await asyncio.create_subprocess_exec(*remux_cmd)
            while proc.returncode is None:
                if abort_event.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    item["status"] = "cancelled"
                    return
                try:
                    await asyncio.wait_for(proc.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass

            if (
                proc.returncode == 0
                and os.path.exists(temp_path)
                and os.path.getsize(temp_path) > 1024
            ):
                success = True

        if not success and not abort_event.is_set():
            item["method"] = "reencode"
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

            encode_cmd = [
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
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-progress",
                "pipe:1",
                "-f",
                "mp4",
                temp_path,
            ]
            proc = await asyncio.create_subprocess_exec(
                *encode_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

            async def read_progress():
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").strip()
                    if "=" in text:
                        key, val = text.split("=", 1)
                        if key in ("out_time_us", "out_time_ms"):
                            try:
                                out_us = int(val)
                                out_sec = out_us / 1_000_000.0
                                if duration > 0:
                                    pct = min(
                                        99.0, max(0.0, (out_sec / duration) * 100.0)
                                    )
                                    item["percent"] = round(pct, 1)
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
                                    rem_sec = (
                                        max(
                                            0.0,
                                            duration
                                            - (item["percent"] / 100.0 * duration),
                                        )
                                        / spd
                                    )
                                    item["eta"] = format_eta_str(rem_sec)
                            except ValueError:
                                pass

            progress_task = asyncio.create_task(read_progress())
            while proc.returncode is None:
                if abort_event.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    progress_task.cancel()
                    try:
                        await progress_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    item["status"] = "cancelled"
                    return
                try:
                    await asyncio.wait_for(proc.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass

            await progress_task
            if (
                proc.returncode == 0
                and os.path.exists(temp_path)
                and os.path.getsize(temp_path) > 1024
            ):
                success = True

        if not success and not abort_event.is_set():
            item["method"] = "fallback_reencode"
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

            fallback_cmd = [
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
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                temp_path,
            ]
            proc = await asyncio.create_subprocess_exec(*fallback_cmd)
            while proc.returncode is None:
                if abort_event.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    item["status"] = "cancelled"
                    return
                try:
                    await asyncio.wait_for(proc.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass

            if (
                proc.returncode == 0
                and os.path.exists(temp_path)
                and os.path.getsize(temp_path) > 1024
            ):
                success = True

        if success:
            def _commit_transcode():
                target_dir = os.path.dirname(target_path)
                commit_tmp = os.path.join(
                    target_dir, f".tmp_tc_{uuid.uuid4().hex[:8]}.mp4"
                )
                try:
                    # Try os.replace first (instantaneous if same filesystem)
                    os.replace(temp_path, target_path)
                except OSError:
                    # Cross-device filesystem: copy to atomic tmp in target dir then replace
                    shutil.copy2(temp_path, commit_tmp)
                    if (
                        not os.path.exists(commit_tmp)
                        or os.path.getsize(commit_tmp) != os.path.getsize(temp_path)
                    ):
                        raise OSError("Cross-device transcode copy verification failed")
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

            try:
                await asyncio.to_thread(_commit_transcode)
            except Exception as e2:
                logger.error(f"Error moving transcoded file to {target_path}: {e2}")
                item["status"] = "failed"
                item["error"] = f"Failed to commit transcoded file: {e2}"
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                self.transcode_history.append(item)
                if len(self.transcode_history) > 500:
                    self.transcode_history = self.transcode_history[-500:]
                return

            if os.path.exists(source_path) and os.path.abspath(
                source_path
            ) != os.path.abspath(target_path):
                try:
                    await asyncio.to_thread(os.remove, source_path)
                except Exception as e:
                    logger.warning(
                        f"Warning: Failed to remove original file {source_path}: {e}"
                    )

            try:
                old_thumb = get_thumbnail_cache_path(source_path)
                if old_thumb.exists():
                    old_thumb.unlink()
            except Exception:
                pass

            target_size = (
                os.path.getsize(target_path) if os.path.exists(target_path) else 0
            )
            item["target_size"] = target_size
            item["percent"] = 100.0
            item["status"] = "completed"
            item["completed_at"] = time.time()
            self.transcode_history.append(item)
            if len(self.transcode_history) > 500:
                self.transcode_history = self.transcode_history[-500:]

            update_file_in_index(
                source_path,
                target_path,
                os.path.basename(target_path),
                target_size,
                ".mp4",
                preserve_hashes=False,
            )

            new_meta = make_video_meta(target_path)
            if new_meta:
                old_name = os.path.basename(source_path)
                state.update_queue(
                    lambda q: [
                        new_meta
                        if (v["id"] == old_name or v.get("filename") == old_name)
                        else v
                        for v in q
                    ]
                )
        else:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            item["status"] = "failed"
            item["error"] = "Conversion failed (safe fallback preserved original file)"
            self.transcode_history.append(item)
            if len(self.transcode_history) > 500:
                self.transcode_history = self.transcode_history[-500:]


transcode_worker_instance = TranscodeWorker()


def enqueue_pretranscode_for_source(source_dir: str):
    """Scans source_dir for non-native videos and auto-enqueues them for background transcoding."""
    if not os.path.isdir(source_dir):
        return
    try:
        with os.scandir(source_dir) as entries:
            for entry in entries:
                if entry.is_file() and not entry.name.startswith("."):
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in VIDEO_EXTENSIONS and ext not in NATIVE_EXTENSIONS:
                        task_id = f"tc_{int(time.time() * 1000)}_{entry.name}"
                        if any(
                            t.get("filename") == entry.name
                            and t.get("status") in ("queued", "transcoding")
                            for t in transcode_worker_instance.transcode_items_map.values()
                        ):
                            continue
                        t_item = {
                            "id": task_id,
                            "filename": entry.name,
                            "source_path": entry.path,
                            "target_path": None,
                            "percent": 0.0,
                            "fps": 0.0,
                            "speed": "",
                            "eta": "",
                            "status": "queued",
                            "method": "",
                            "abort_event": threading.Event(),
                            "created_at": time.time(),
                        }
                        transcode_worker_instance.enqueue(t_item)
    except Exception as e:
        logger.error(f"Error scanning source directory for pre-transcoding: {e}")
