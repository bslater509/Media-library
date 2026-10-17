"""Face clustering, crop-cache and engine unit tests.

The OpenCV engine never runs here: the `fake_face_engine` conftest fixture stands
in (same interface, no models, no inference), so the suite stays offline and fast
while still covering the clustering, cache and index-propagation behaviour.
"""

import asyncio
import hashlib
import os
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from media_library.core import config
from media_library.db.connection import db_conn
from media_library.db.indexer_files import (
    add_file_to_index,
    delete_path_from_index,
    move_path_in_index,
)
from media_library.services import face_engine, face_service


@pytest.fixture(autouse=True)
def _faces_on(fake_face_engine, faces_enabled):
    """Every test here runs with the feature on and the fake engine installed."""
    return


# ------------------------------------------------------------------ engine


def test_engine_status_reports_missing_models(monkeypatch, sandbox_env):
    face_engine.reset_engine()
    monkeypatch.setattr(
        config, "FACE_MODEL_DIR", str(sandbox_env["data_dir"] / "models")
    )
    status = face_engine.engine_status()
    assert status["available"] is False
    assert "models not downloaded" in status["reason"]


def test_engine_status_never_creates_directories(monkeypatch, tmp_path):
    """A status read must not mkdir: only an actual download writes."""
    face_engine.reset_engine()
    target = tmp_path / "not-created" / "models"
    monkeypatch.setattr(config, "FACE_MODEL_DIR", str(target))
    assert face_engine.engine_status()["available"] is False
    assert not target.exists()
    assert not target.parent.exists()


def test_suite_keeps_face_paths_inside_the_sandbox(sandbox_env):
    """Models and crops must resolve into the sandbox for every test.

    A test that triggered a model download or wrote a crop used to be able to
    land in the real ``data/`` directory (and leave an empty ``data/models``
    behind); ``patch_runtime_directories`` now pins both.
    """
    assert face_engine.get_model_dir(create=False) == sandbox_env["data_dir"] / "models"
    assert face_service.get_face_crop_dir() == sandbox_env["thumb_dir"] / "faces"


def test_engine_status_accepts_an_injected_engine(fake_face_engine):
    assert face_engine.engine_status()["available"] is True
    assert face_engine.get_engine() is fake_face_engine


def test_download_models_verifies_size_and_hash(monkeypatch, sandbox_env):
    payload = b"onnx-weights" * 8
    spec = {
        "url": "https://example.invalid/model.onnx",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }
    monkeypatch.setattr(face_engine, "_MODEL_SPECS", {"model.onnx": spec})
    monkeypatch.setattr(
        config, "FACE_MODEL_DIR", str(sandbox_env["data_dir"] / "models")
    )

    class _Response:
        def __init__(self, body):
            self._body = body
            self._read = False

        def read(self, _n):
            if self._read:
                return b""
            self._read = True
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        face_engine.urllib.request, "urlopen", lambda *a, **k: _Response(payload)
    )
    result = face_engine.download_models()
    assert result["success"] is True
    assert result["downloaded"] == ["model.onnx"]
    stored = face_engine.get_model_dir() / "model.onnx"
    assert stored.read_bytes() == payload

    # A body that does not match the pinned hash must be discarded, not stored.
    monkeypatch.setattr(
        face_engine.urllib.request, "urlopen", lambda *a, **k: _Response(b"tampered")
    )
    second = face_engine.download_models(force=True)
    assert second["success"] is False
    assert "SHA-256" in second["error"]
    assert stored.read_bytes() == payload


def test_engine_download_failure_is_reported(monkeypatch, sandbox_env):
    monkeypatch.setattr(
        face_engine,
        "_MODEL_SPECS",
        {
            "model.onnx": {
                "url": "https://example.invalid/x",
                "sha256": "0" * 64,
                "bytes": 4,
            }
        },
    )
    monkeypatch.setattr(
        config, "FACE_MODEL_DIR", str(sandbox_env["data_dir"] / "models")
    )

    def boom(*_a, **_k):
        raise OSError("offline")

    monkeypatch.setattr(face_engine.urllib.request, "urlopen", boom)
    result = face_engine.download_models()
    assert result["success"] is False
    assert "Could not download" in result["error"]


# --------------------------------------------------------------- clustering


def test_record_scan_clusters_same_person_and_separates_others(
    sandbox_env, face_image_factory, scan_face_batch
):
    a1 = face_image_factory("a1.jpg", (255, 0, 0))
    a2 = face_image_factory("a2.jpg", (250, 4, 4))
    b1 = face_image_factory("b1.jpg", (0, 0, 255))
    plain = face_image_factory("plain.jpg", (0, 0, 0))

    from media_library.workers.face_worker import face_worker_instance as worker

    assert scan_face_batch(worker) == 4

    with db_conn() as conn:
        groups = {}
        for row in conn.execute("SELECT person_id, file_path FROM faces"):
            groups.setdefault(int(row["person_id"]), set()).add(row["file_path"])
        assert sorted(len(v) for v in groups.values()) == [1, 2]
        assert {str(a1), str(a2)} in list(groups.values())
        assert {str(b1)} in list(groups.values())
        # The no-face image is still recorded as scanned exactly once.
        assert conn.execute("SELECT COUNT(*) FROM face_scans").fetchone()[0] == 4
        assert (
            conn.execute(
                "SELECT faces_found FROM face_scans WHERE file_path = ?", (str(plain),)
            ).fetchone()[0]
            == 0
        )
        # Every face carries an embedding of the expected size.
        for row in conn.execute("SELECT embedding FROM faces"):
            assert len(row["embedding"]) == 128 * 4

    stats = face_service.face_stats()
    assert stats["total_files"] == 4
    assert stats["pending_count"] == 0
    assert face_service.people_counts()["faces"] == 3


def test_record_scan_is_idempotent_per_file(
    sandbox_env, face_image_factory, scan_face_batch
):
    face_image_factory("one.jpg", (255, 0, 0))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)
    # A second pass has nothing pending (mtime/size unchanged).
    assert scan_face_batch(worker) == 0
    with db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 1


def test_rescan_replaces_detections_and_keeps_dismissals(
    sandbox_env, face_image_factory, scan_face_batch
):
    image = face_image_factory("person.jpg", (255, 0, 0))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)
    with db_conn() as conn:
        face_id = conn.execute("SELECT id FROM faces").fetchone()["id"]
    assert face_service.dismiss_faces([face_id]) == 1

    # Force a re-scan of the same file: the dismissal must survive.
    with db_conn() as conn, conn:
        conn.execute("DELETE FROM face_scans")
    scan_face_batch(worker)
    with db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM dismissed_faces").fetchone()[0] == 1
    assert face_service.people_counts()["dismissed"] == 1
    assert Path(image).exists()


def test_video_scan_keeps_frame_timestamps(
    sandbox_env, face_image_factory, scan_face_batch, monkeypatch
):
    media = sandbox_env["media_dir"]
    video = media / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00 ftypmp42" + b"A" * 512)
    add_file_to_index(str(video))
    with db_conn() as conn, conn:
        conn.execute(
            "UPDATE files SET type='video', duration=8.0 WHERE path = ?", (str(video),)
        )

    frame = face_image_factory("frame.jpg", (0, 0, 255))
    stamps: list[float] = []

    async def fake_extract(file_path, timestamp=0.0):
        stamps.append(round(float(timestamp), 2))
        return frame

    from media_library.workers import face_worker

    monkeypatch.setattr(face_worker, "extract_candidate_frame", fake_extract)
    monkeypatch.setattr(config, "FACE_VIDEO_SAMPLE_INTERVAL", 4.0)
    monkeypatch.setattr(config, "FACE_VIDEO_MAX_FRAMES", 3)

    scan_face_batch(face_worker.face_worker_instance)
    assert stamps == [0.0, 4.0, 7.84]
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT frame_time FROM faces WHERE file_path = ? ORDER BY frame_time",
            (str(video),),
        ).fetchall()
    assert [r["frame_time"] for r in rows] == [0.0, 4.0, 7.84]


def test_sample_timestamps_bounds():
    from media_library.workers.face_worker import sample_timestamps

    assert sample_timestamps(0, 30, 24) == [0.0]
    assert sample_timestamps(20, 30, 24) == [0.0, 19.6]
    long_clip = sample_timestamps(3600, 30, 24)
    assert len(long_clip) == 24
    assert long_clip[0] == 0.0
    assert long_clip[-1] == pytest.approx(3450.0, abs=1.0)


# -------------------------------------------------------------- crops/cache


def test_crop_cache_lru_evicts_and_requeues(
    sandbox_env, face_image_factory, scan_face_batch
):
    face_image_factory("one.jpg", (255, 0, 0))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)
    crop_dir = face_service.get_face_crop_dir()
    crops = list(crop_dir.glob("face_*.jpg"))
    assert len(crops) == 1

    # Push the crop's mtime back so the oldest-write eviction picks it up, and
    # shrink the ceiling below one crop's size.
    old = time.time() - 3600
    os.utime(crops[0], (old, old))
    report = face_service.manage_face_crop_lru(max_bytes=1)
    assert report["evicted"] == 1
    assert list(crop_dir.glob("face_*.jpg")) == []
    # The file is re-queued so the crop can be rebuilt on the next sweep.
    with db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM face_scans").fetchone()[0] == 0


def test_orphan_sweep_removes_unreferenced_crops(
    sandbox_env, face_image_factory, scan_face_batch
):
    image = face_image_factory("suspect.jpg", (255, 0, 0))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)
    crop_dir = face_service.get_face_crop_dir()
    assert len(list(crop_dir.glob("face_*.jpg"))) == 1

    with db_conn() as conn, conn:
        conn.execute("DELETE FROM faces")
    with db_conn() as conn:
        assert face_service.clean_orphaned_face_crops(conn) == 1
    assert list(crop_dir.glob("face_*.jpg")) == []
    # A temp file another generator is still writing is never touched.
    temp = crop_dir / "face_0123456789abcdef01234567.abcd.tmp.jpg"
    temp.write_bytes(b"x")
    with db_conn() as conn:
        face_service.clean_orphaned_face_crops(conn)
    assert temp.exists()
    assert Path(image).exists()


def test_face_crop_payload_reports_usage(
    sandbox_env, face_image_factory, scan_face_batch
):
    face_image_factory("shot.jpg", (255, 0, 0))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)
    payload = face_service.face_crop_payload()
    assert payload["cache_files"] == 1
    assert payload["cache_bytes"] > 0
    assert payload["cache_max_bytes"] == int(2.0 * 1024**3)
    assert payload["cache_size_formatted"].endswith("KB")


def test_crop_path_rejects_foreign_names(sandbox_env):
    assert face_service.crop_path("../../etc/passwd") is None
    assert face_service.crop_path(None) is None


# ------------------------------------------------------------ curation ops


def _two_clusters(face_image_factory, scan_face_batch):
    face_image_factory("a.jpg", (255, 0, 0))
    face_image_factory("b.jpg", (0, 0, 255))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT person_id, COUNT(*) AS n FROM faces GROUP BY person_id"
        ).fetchall()
    return sorted(int(r["person_id"]) for r in rows)


def test_rename_merge_split_and_dismiss(face_image_factory, scan_face_batch):
    first, second = _two_clusters(face_image_factory, scan_face_batch)
    assert face_service.rename_person(first, "  Anna  ") is True
    assert face_service.rename_person(9999, "Nobody") is False

    merged = face_service.merge_people(first, [second])
    assert merged["success"] is True
    assert merged["moved"] == 1
    assert face_service.people_counts()["named"] == 1

    with db_conn() as conn:
        face_id = conn.execute("SELECT id FROM faces LIMIT 1").fetchone()["id"]
    split = face_service.split_faces([face_id], "Anna 2")
    assert split["success"] is True
    assert face_service.people_counts()["people"] == 2

    # Dismissing the whole cluster removes its faces and remembers the rejection.
    result = face_service.dismiss_person(split["person_id"])
    assert result["dismissed"] == 1
    assert result["removed"] == 0  # it was named, so the row is kept
    assert face_service.people_counts()["dismissed"] == 1

    # Merging into an unknown target fails cleanly.
    assert face_service.merge_people(4242, [first])["success"] is False
    assert face_service.split_faces([])["success"] is False


def test_recluster_reattaches_names(face_image_factory, scan_face_batch):
    face_image_factory("a1.jpg", (255, 0, 0))
    face_image_factory("a2.jpg", (250, 3, 3))
    face_image_factory("b1.jpg", (0, 0, 255))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)
    with db_conn() as conn:
        top = conn.execute(
            "SELECT person_id, COUNT(*) AS n FROM faces GROUP BY person_id "
            "ORDER BY n DESC LIMIT 1"
        ).fetchone()
    person_id = int(top["person_id"])
    assert face_service.rename_person(person_id, "Anna") is True

    # Scatter the faces first: the re-cluster has to rebuild the clusters.
    with db_conn() as conn, conn:
        conn.execute("UPDATE faces SET person_id = NULL")
        conn.execute("DELETE FROM people WHERE name IS NULL")
    report = face_service.recluster_people()
    assert report["success"] is True
    assert report["faces"] == 3
    assert report["kept"] == 1
    with db_conn() as conn:
        names = [
            r["name"]
            for r in conn.execute("SELECT name FROM people WHERE name IS NOT NULL")
        ]
        # The same person's two faces are back in one cluster.
        assert (
            conn.execute("SELECT COUNT(DISTINCT person_id) FROM faces").fetchone()[0]
            == 2
        )
    assert names == ["Anna"]


def test_recluster_with_no_faces_is_a_noop(sandbox_env):
    report = face_service.recluster_people()
    assert report["success"] is True
    assert report["faces"] == 0


def test_recluster_refuses_absurd_face_count(face_image_factory, scan_face_batch):
    face_image_factory("only.jpg", (255, 0, 0))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)
    report = face_service.recluster_people(max_faces=0)
    assert report["success"] is False
    assert "Too many faces" in report["error"]


def test_cluster_labels_chunks_large_inputs(monkeypatch):
    # Two well-separated groups split across chunk boundaries must still come
    # back as two clusters (the chunk-centroid pass stitches them together).
    from media_library.services import face_service

    monkeypatch.setattr(face_service, "_CLUSTER_CHUNK", 2)
    matrix = np.zeros((6, 128), dtype="float32")
    matrix[:3, 0] = 1.0
    matrix[3:, 1] = 1.0
    labels = face_service._cluster_labels(matrix, 0.36)
    assert int(labels[0]) == int(labels[1]) == int(labels[2])
    assert int(labels[3]) == int(labels[4]) == int(labels[5])
    assert int(labels[0]) != int(labels[3])


# ----------------------------------------------------------------- queries


def test_person_media_groups_a_video_once_with_its_moments(
    sandbox_env, face_image_factory, scan_face_batch, monkeypatch
):
    face_image_factory("portrait.jpg", (255, 0, 0))
    media = sandbox_env["media_dir"]
    video = media / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00 ftypmp42" + b"B" * 512)
    add_file_to_index(str(video))
    with db_conn() as conn, conn:
        conn.execute(
            "UPDATE files SET type='video', duration=12.0 WHERE path = ?", (str(video),)
        )
    frame = sandbox_env["media_dir"] / "frame.jpg"
    Image.new("RGB", (64, 64), (255, 0, 0)).save(frame, "JPEG")

    async def fake_extract(file_path, timestamp=0.0):
        return frame

    from media_library.workers import face_worker

    monkeypatch.setattr(face_worker, "extract_candidate_frame", fake_extract)
    monkeypatch.setattr(config, "FACE_VIDEO_SAMPLE_INTERVAL", 4.0)

    scan_face_batch(face_worker.face_worker_instance)
    with db_conn() as conn:
        person_id = conn.execute(
            "SELECT person_id FROM faces GROUP BY person_id "
            "ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()["person_id"]
        media_out = face_service.read_person_media(conn, person_id)
        people = face_service.read_people(conn)

    assert media_out["total"] == 2
    video_rows = [m for m in media_out["files"] if m["type"] == "video"]
    assert len(video_rows) == 1
    assert video_rows[0]["frames"] >= 2
    assert video_rows[0]["moments"]
    assert all(m["frame_time"] >= 0 for m in video_rows[0]["moments"])
    assert video_rows[0]["moments"][0]["crop_url"].startswith("/api/people/crop")
    # Shaped like a Library browse entry, so the page can reuse the lightbox.
    assert video_rows[0]["media_url"].startswith("/api/backup/")
    assert video_rows[0]["thumb_url"].startswith("/api/backup/thumbnail")
    # The still image keeps its place and carries no moments.
    image_row = next(m for m in media_out["files"] if m["type"] == "image")
    assert image_row["moments"] == []

    assert people[0]["face_count"] >= 1
    assert people[0]["cover_url"].startswith("/api/people/crop")
    assert people[0]["media_count"] == 2
    assert people[0]["is_named"] is False


def test_person_media_sorting_and_paging(face_image_factory, scan_face_batch):
    face_image_factory("b.jpg", (255, 0, 0))
    face_image_factory("a.jpg", (255, 0, 0))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)
    with db_conn() as conn:
        person_id = conn.execute("SELECT person_id FROM faces LIMIT 1").fetchone()[
            "person_id"
        ]
        by_name = face_service.read_person_media(conn, person_id, sort="name")
        first_page = face_service.read_person_media(conn, person_id, limit=1)
        second_page = face_service.read_person_media(conn, person_id, limit=1, offset=1)
    assert [m["name"] for m in by_name["files"]] == ["a.jpg", "b.jpg"]
    assert first_page["total"] == 2
    assert first_page["truncated"] is True
    assert second_page["truncated"] is False
    assert first_page["files"][0]["name"] != second_page["files"][0]["name"]


def test_read_person_faces_and_counts(face_image_factory, scan_face_batch):
    face_image_factory("one.jpg", (255, 0, 0))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)
    with db_conn() as conn:
        person_id = conn.execute("SELECT person_id FROM faces LIMIT 1").fetchone()[
            "person_id"
        ]
        faces = face_service.read_person_faces(conn, person_id)
        assert face_service.count_person_faces(conn, person_id) == 1
        assert face_service.read_person_faces(conn, 9999) == []
    assert faces[0]["crop_url"].startswith("/api/people/crop")
    assert faces[0]["frame_time"] is None  # an image, not a video moment


def test_resolve_person_ids_matches_partial_names(face_image_factory, scan_face_batch):
    first, _second = _two_clusters(face_image_factory, scan_face_batch)
    face_service.rename_person(first, "Anna Smith")
    with db_conn() as conn:
        assert face_service.resolve_person_ids(conn, "anna") == [first]
        assert face_service.resolve_person_ids(conn, "smith") == [first]
        assert face_service.resolve_person_ids(conn, "") == []
        assert face_service.resolve_person_ids(conn, "nobody") == []


# --------------------------------------------------------- index lifecycle


def test_move_and_delete_propagate_faces(face_image_factory, scan_face_batch):
    image = face_image_factory("keep.jpg", (255, 0, 0))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)

    target_dir = image.parent / "sorted"
    target_dir.mkdir()
    moved = target_dir / "keep.jpg"
    image.rename(moved)
    move_path_in_index(str(image), str(moved), "file")
    with db_conn() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM faces WHERE file_path = ?", (str(moved),)
            ).fetchone()[0]
            == 1
        )
        assert conn.execute("SELECT COUNT(*) FROM face_scans").fetchone()[0] == 1

    delete_path_from_index(str(moved), "file")
    with db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM face_scans").fetchone()[0] == 0


def test_folder_move_propagates_faces(sandbox_env, face_image_factory, scan_face_batch):
    album = sandbox_env["media_dir"] / "album"
    image = face_image_factory("album/one.jpg", (255, 0, 0))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)

    renamed = sandbox_env["media_dir"] / "album_2024"
    album.rename(renamed)
    move_path_in_index(str(album), str(renamed), "folder")
    with db_conn() as conn:
        stored = conn.execute("SELECT file_path FROM faces").fetchone()["file_path"]
    assert stored == str(renamed / "one.jpg")
    assert Path(image).exists() is False


def test_folder_delete_removes_faces(sandbox_env, face_image_factory, scan_face_batch):
    album = sandbox_env["media_dir"] / "album2"
    face_image_factory("album2/one.jpg", (255, 0, 0))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)
    delete_path_from_index(str(album), "folder")
    with db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM dismissed_faces").fetchone()[0] == 0


# ---------------------------------------------------------------- threshold


def test_threshold_prefers_stored_value_over_env(faces_enabled):
    assert face_service.get_threshold() == pytest.approx(0.36)
    assert face_service.set_threshold(0.46) == pytest.approx(0.46)
    assert face_service.get_threshold() == pytest.approx(0.46)
    assert face_service.threshold_preset_name(0.46) == "loose"
    assert face_service.threshold_preset_name(0.31) == "custom"
    # An out-of-range request is clamped rather than stored as-is.
    assert face_service.set_threshold(9.0) == pytest.approx(1.2)
    assert config.FACE_MATCH_THRESHOLD == pytest.approx(0.36)


def test_assign_person_respects_threshold(sandbox_env):
    embedding = np.zeros(128, dtype="float32")
    embedding[0] = 1.0
    other = np.zeros(128, dtype="float32")
    other[0] = 1.0
    other[1] = 1.0
    other = other / np.linalg.norm(other)  # ~45 degrees away: distance ~0.29
    centroids = {7: embedding}
    assert face_service.assign_person(other, centroids, threshold=0.36) == (
        7,
        pytest.approx(0.293, abs=0.01),
    )
    person_id, distance = face_service.assign_person(other, centroids, threshold=0.1)
    assert person_id is None and distance is None
    assert face_service.assign_person(embedding, {}) == (None, None)
    assert face_service.assign_person(embedding, {1: embedding})[0] == 1


def test_embedding_pack_roundtrip(sandbox_env):
    vec = np.linspace(0, 1, 128, dtype="float32")
    blob = face_service.pack_embedding(vec)
    assert len(blob) == 512
    back = face_service.unpack_embeddings([blob, b"", b"short"])
    assert back.shape == (1, 128)
    assert np.allclose(back[0], vec)
    assert face_service.unpack_embeddings([]).shape == (0, 128)


def test_normalize_handles_zero_vectors(sandbox_env):
    assert face_service._normalize(np.zeros(128, dtype="float32")).sum() == 0
    assert face_service._centroid_of(np.zeros((0, 128), dtype="float32")) is None


def test_worker_get_status_shape(face_image_factory, scan_face_batch, faces_enabled):
    from media_library.workers.face_worker import face_worker_instance as worker

    status = worker.get_status()
    for key in (
        "status",
        "enabled",
        "is_paused",
        "engine",
        "total_files",
        "pending_count",
        "faces_total",
        "people_total",
        "cache_files",
        "cache_max_bytes",
    ):
        assert key in status
    assert status["enabled"] is True
    assert status["engine"]["available"] is True


def test_worker_pause_resume_flags(sandbox_env, faces_enabled):
    from media_library.workers.face_worker import face_worker_instance as worker

    assert worker.is_paused() is False
    worker.pause()
    assert worker.is_paused() is True
    assert worker.get_status()["is_paused"] is True
    worker.resume()
    assert worker.is_paused() is False


def test_worker_reports_disabled_when_feature_off(monkeypatch, sandbox_env):
    monkeypatch.setattr(config, "FACE_RECOGNITION_ENABLED", False)
    from media_library.workers.face_worker import face_worker_instance as worker

    assert worker.get_status()["enabled"] is False


def test_worker_loop_marks_unavailable_without_engine(
    monkeypatch, sandbox_env, faces_enabled
):
    """With no engine and no downloadable models the loop parks, not spins."""

    async def _once():
        task = asyncio.create_task(worker._run_loop())
        for _ in range(50):
            await asyncio.sleep(0.05)
            if worker.state["status"] == "unavailable":
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return worker.state["status"]

    monkeypatch.setattr(face_engine, "_ENGINE_OVERRIDE", None)
    monkeypatch.setattr(face_engine, "get_engine", lambda: None)
    monkeypatch.setattr(
        face_engine,
        "download_models",
        lambda *a, **k: {"success": False, "error": "offline"},
    )
    monkeypatch.setattr(face_engine, "opencv_available", lambda: True)
    from media_library.workers.face_worker import face_worker_instance as worker

    assert asyncio.run(_once()) == "unavailable"


def test_worker_loop_skips_when_disabled(monkeypatch, sandbox_env):
    monkeypatch.setattr(config, "FACE_RECOGNITION_ENABLED", False)
    from media_library.workers.face_worker import face_worker_instance as worker

    async def _once():
        task = asyncio.create_task(worker._run_loop())
        await asyncio.sleep(0.05)
        status = worker.state["status"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return status

    assert asyncio.run(_once()) == "disabled"


def test_image_without_thumbnail_stays_pending(
    sandbox_env, face_image_factory, scan_face_batch
):
    """An image whose thumbnail has not been generated yet is retried later."""
    image = face_image_factory("later.jpg", (255, 0, 0))
    from media_library.core.state import get_thumbnail_cache_path
    from media_library.workers.face_worker import face_worker_instance as worker

    stat = image.stat()
    thumb = get_thumbnail_cache_path(str(image), stat.st_mtime, stat.st_size)
    Path(thumb).unlink()

    # Nothing is scanned and no scan row is written, so it is retried once the
    # thumbnail worker catches up.
    assert scan_face_batch(worker) == 0
    with db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM face_scans").fetchone()[0] == 0
    stats = face_service.face_stats()
    assert stats["total_files"] == 1
    assert stats["pending_count"] == 1

    # Once the index knows there is no thumbnail, it drops out of the sweep.
    with db_conn() as conn, conn:
        conn.execute("UPDATE files SET has_thumb = 0 WHERE path = ?", (str(image),))
    assert face_service.face_stats()["total_files"] == 0


def test_zip_members_are_never_eligible(sandbox_env, face_image_factory):
    """Images inside a ZIP archive are skipped: no archive extraction over FUSE."""
    image = face_image_factory("zipmember.jpg", (255, 0, 0))
    with db_conn() as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO folders (path, name, parent_path, is_zip) "
            "VALUES (?, ?, ?, 1)",
            (str(image.parent), image.parent.name, str(image.parent.parent)),
        )
    assert face_service.face_stats()["total_files"] == 0
    with db_conn() as conn:
        rows = asyncio.run(_next_batch_for(conn))
    assert rows == []


async def _next_batch_for(_conn):
    from media_library.workers.face_worker import face_worker_instance as worker

    return await worker._next_batch()


def test_face_service_never_walks_the_filesystem(
    monkeypatch, face_image_factory, scan_face_batch
):
    """The queries must stay index-only: no os.walk anywhere in the read path."""
    face_image_factory("w.jpg", (255, 0, 0))
    from media_library.workers.face_worker import face_worker_instance as worker

    scan_face_batch(worker)
    calls = {"walk": 0}

    def _walk(*_a, **_k):
        calls["walk"] += 1
        raise AssertionError("os.walk must not run in the face read path")

    monkeypatch.setattr(os, "walk", _walk)
    with db_conn() as conn:
        face_service.read_people(conn)
        person_id = conn.execute("SELECT person_id FROM faces LIMIT 1").fetchone()[
            "person_id"
        ]
        face_service.read_person_media(conn, person_id)
        face_service.read_person_faces(conn, person_id)
        face_service.people_counts(conn)
        face_service.face_stats()
    assert calls["walk"] == 0


def test_face_crop_name_is_stable(sandbox_env):
    first = face_service.face_crop_name("/m/a.jpg", -1.0, 10, 20)
    second = face_service.face_crop_name("/m/a.jpg", -1.0, 10, 20)
    third = face_service.face_crop_name("/m/a.jpg", 12.5, 10, 20)
    assert first == second
    assert first != third
    assert first.startswith("face_") and first.endswith(".jpg")


def test_write_face_crop_crops_and_resizes(sandbox_env):
    image = Image.new("RGB", (200, 200), (255, 0, 0))
    name = face_service.face_crop_name("/m/a.jpg", -1.0, 50, 50)
    assert face_service.write_face_crop(
        image, {"x": 50, "y": 50, "w": 60, "h": 60}, name
    )
    path = face_service.get_face_crop_dir() / name
    with Image.open(path) as stored:
        assert stored.size == (face_service.CROP_SIZE, face_service.CROP_SIZE)
    # Already cached: a second write is a no-op that still reports success.
    assert face_service.write_face_crop(image, {"x": 0, "y": 0, "w": 60, "h": 60}, name)
    # A box that is entirely outside the image cannot be cropped.
    assert (
        face_service.write_face_crop(
            image, {"x": 190, "y": 190, "w": 1, "h": 1}, "face_" + "0" * 24 + ".jpg"
        )
        is False
    )
