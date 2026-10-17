"""Tests for browser chunked upload and finalization."""

import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import media_library.services.upload_service as upload_svc
from media_library.app_factory import create_app


@pytest.fixture
def app_instance(sandbox_env):
    """Creates a test app instance using the sandbox environment."""
    return create_app(
        source_dir=str(sandbox_env["incoming_dir"]),
        destination_dir=str(sandbox_env["media_dir"]),
        run_startup_workers=False,
    )


@pytest.mark.anyio
async def test_upload_chunk_success(app_instance, sandbox_env):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload_id = "upl_1728640000_0_abc12"
        chunk_data = b"Hello, world upload chunk!"

        files = {"chunk": ("test.jpg", io.BytesIO(chunk_data), "image/jpeg")}
        data = {
            "upload_id": upload_id,
            "chunk_index": "0",
            "total_chunks": "1",
        }

        response = await client.post("/api/backup/upload/chunk", data=data, files=files)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["chunk_index"] == 0
        assert body["received_bytes"] == len(chunk_data)
        assert body["duplicate"] is False

        staging_file = upload_svc.UPLOAD_STAGING_DIR / f"{upload_id}.part"
        assert staging_file.exists()
        assert staging_file.read_bytes() == chunk_data

        # Finalize upload
        finalize_payload = {
            "upload_id": upload_id,
            "original_filename": "uploaded_photo.jpg",
            "relative_path": "uploaded_photo.jpg",
            "destination_dir": str(sandbox_env["media_dir"]),
            "collision_policy": "auto-rename",
        }
        res_fin = await client.post(
            "/api/backup/upload/finalize", json=finalize_payload
        )
        assert res_fin.status_code == 200
        result = res_fin.json()
        assert result["status"] == "ok"
        final_file = Path(result["path"])
        assert final_file.exists()
        assert final_file.read_bytes() == chunk_data
        assert not staging_file.exists()


@pytest.mark.anyio
async def test_upload_chunk_invalid_id(app_instance):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        invalid_ids = ["../traversal", "upl/bad", "upl.part", "bad@id!", ""]
        for bad_id in invalid_ids:
            files = {"chunk": ("test.jpg", io.BytesIO(b"data"), "image/jpeg")}
            data = {
                "upload_id": bad_id,
                "chunk_index": "0",
                "total_chunks": "1",
            }
            res = await client.post("/api/backup/upload/chunk", data=data, files=files)
            assert res.status_code in (400, 422)


@pytest.mark.anyio
async def test_upload_heic_allowed(app_instance, sandbox_env):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload_id = "upl_heic_test_1"
        files = {
            "chunk": ("IMG_1234.HEIC", io.BytesIO(b"mock heic data"), "image/heic")
        }
        data = {
            "upload_id": upload_id,
            "chunk_index": "0",
            "total_chunks": "1",
        }
        res = await client.post("/api/backup/upload/chunk", data=data, files=files)
        assert res.status_code == 200

        finalize_payload = {
            "upload_id": upload_id,
            "original_filename": "IMG_1234.HEIC",
            "relative_path": "IMG_1234.HEIC",
            "destination_dir": str(sandbox_env["media_dir"]),
            "collision_policy": "auto-rename",
        }
        res_fin = await client.post(
            "/api/backup/upload/finalize", json=finalize_payload
        )
        assert res_fin.status_code == 200
        assert Path(res_fin.json()["path"]).exists()


@pytest.mark.anyio
async def test_upload_disallowed_extension(app_instance, sandbox_env):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload_id = "upl_bad_ext_1"
        files = {
            "chunk": ("malware.exe", io.BytesIO(b"bad"), "application/octet-stream")
        }
        data = {
            "upload_id": upload_id,
            "chunk_index": "0",
            "total_chunks": "1",
        }
        res = await client.post("/api/backup/upload/chunk", data=data, files=files)
        assert res.status_code == 200

        finalize_payload = {
            "upload_id": upload_id,
            "original_filename": "malware.exe",
            "relative_path": "malware.exe",
            "destination_dir": str(sandbox_env["media_dir"]),
            "collision_policy": "auto-rename",
        }
        res_fin = await client.post(
            "/api/backup/upload/finalize", json=finalize_payload
        )
        assert res_fin.status_code == 400
        assert "not allowed" in res_fin.json()["detail"]


@pytest.mark.anyio
async def test_cancel_upload(app_instance):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload_id = "upl_cancel_test_1"
        files = {"chunk": ("test.jpg", io.BytesIO(b"cancel me"), "image/jpeg")}
        data = {
            "upload_id": upload_id,
            "chunk_index": "0",
            "total_chunks": "1",
        }
        res = await client.post("/api/backup/upload/chunk", data=data, files=files)
        assert res.status_code == 200
        staging_file = upload_svc.UPLOAD_STAGING_DIR / f"{upload_id}.part"
        assert staging_file.exists()

        res_cancel = await client.post(
            "/api/backup/upload/cancel", json={"upload_id": upload_id}
        )
        assert res_cancel.status_code == 200
        assert not staging_file.exists()


# ---------------------------------------------------------------------------
# Resumable uploads: offset-verified chunks, resume status and staging GC
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_upload_status_reports_staged_bytes(app_instance, sandbox_env):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload_id = "upl_resume_status_1"

        empty = await client.get(
            "/api/backup/upload/status", params={"upload_id": upload_id}
        )
        assert empty.status_code == 200
        assert empty.json()["received_bytes"] == 0
        assert empty.json()["exists"] is False

        files = {"chunk": ("test.jpg", io.BytesIO(b"resume-me"), "image/jpeg")}
        data = {
            "upload_id": upload_id,
            "chunk_index": "0",
            "total_chunks": "2",
            "offset": "0",
        }
        assert (
            await client.post("/api/backup/upload/chunk", data=data, files=files)
        ).status_code == 200

        res = await client.get(
            "/api/backup/upload/status", params={"upload_id": upload_id}
        )
        assert res.status_code == 200
        assert res.json()["received_bytes"] == len(b"resume-me")
        assert res.json()["exists"] is True

        bad = await client.get(
            "/api/backup/upload/status", params={"upload_id": "../etc"}
        )
        assert bad.status_code == 400


@pytest.mark.anyio
async def test_retried_chunk_is_idempotent(app_instance, sandbox_env):
    """A chunk retried after a timeout must not be appended twice."""
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload_id = "upl_retry_same_chunk"
        payload = b"1234567890"

        for _ in range(2):
            files = {"chunk": ("test.jpg", io.BytesIO(payload), "image/jpeg")}
            data = {
                "upload_id": upload_id,
                "chunk_index": "0",
                "total_chunks": "1",
                "offset": "0",
            }
            res = await client.post("/api/backup/upload/chunk", data=data, files=files)
            assert res.status_code == 200

        assert res.json()["duplicate"] is True
        staging_file = upload_svc.UPLOAD_STAGING_DIR / f"{upload_id}.part"
        assert staging_file.read_bytes() == payload


@pytest.mark.anyio
async def test_chunk_sent_past_the_staged_end_is_rejected(app_instance, sandbox_env):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload_id = "upl_gap_rejected"
        files = {"chunk": ("test.jpg", io.BytesIO(b"aaaa"), "image/jpeg")}
        data = {
            "upload_id": upload_id,
            "chunk_index": "0",
            "total_chunks": "3",
            "offset": "0",
        }
        assert (
            await client.post("/api/backup/upload/chunk", data=data, files=files)
        ).status_code == 200

        # Chunk 2 arrives while chunk 1 is missing: refuse rather than write a hole.
        gap_files = {"chunk": ("test.jpg", io.BytesIO(b"cccc"), "image/jpeg")}
        gap_data = {
            "upload_id": upload_id,
            "chunk_index": "2",
            "total_chunks": "3",
            "offset": "8",
        }
        res = await client.post(
            "/api/backup/upload/chunk", data=gap_data, files=gap_files
        )
        assert res.status_code == 409

        staging_file = upload_svc.UPLOAD_STAGING_DIR / f"{upload_id}.part"
        assert staging_file.read_bytes() == b"aaaa"


@pytest.mark.anyio
async def test_overlapping_chunk_rewrites_the_partial_tail(app_instance, sandbox_env):
    """A crash mid-write leaves a partial chunk; re-sending it must heal the file."""
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload_id = "upl_partial_tail"
        staging_file = upload_svc.UPLOAD_STAGING_DIR / f"{upload_id}.part"
        staging_file.write_bytes(b"hello world")

        # Chunk 0 is re-sent with its full 11 bytes after a writable crash left
        # 6 of them behind; the file ends up exactly the chunk, not 17 bytes.
        files = {"chunk": ("test.jpg", io.BytesIO(b"hello world"), "image/jpeg")}
        data = {
            "upload_id": upload_id,
            "chunk_index": "0",
            "total_chunks": "1",
            "offset": "0",
        }
        res = await client.post("/api/backup/upload/chunk", data=data, files=files)
        assert res.status_code == 200
        assert staging_file.read_bytes() == b"hello world"


@pytest.mark.anyio
async def test_finalize_refuses_a_short_upload(app_instance, sandbox_env):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload_id = "upl_short_finalize"
        files = {"chunk": ("test.jpg", io.BytesIO(b"half"), "image/jpeg")}
        data = {
            "upload_id": upload_id,
            "chunk_index": "0",
            "total_chunks": "2",
            "offset": "0",
        }
        assert (
            await client.post("/api/backup/upload/chunk", data=data, files=files)
        ).status_code == 200

        res = await client.post(
            "/api/backup/upload/finalize",
            json={
                "upload_id": upload_id,
                "original_filename": "short.jpg",
                "relative_path": "short.jpg",
                "destination_dir": str(sandbox_env["media_dir"]),
                "collision_policy": "auto-rename",
                "total_size": 100,
            },
        )
        assert res.status_code == 400
        assert "incomplete" in res.json()["detail"].lower()
        # The staged bytes survive so the client can finish the upload and retry.
        assert (upload_svc.UPLOAD_STAGING_DIR / f"{upload_id}.part").exists()

        # Completing the upload (the client has the real total) finalizes cleanly.
        files = {"chunk": ("test.jpg", io.BytesIO(b"-done"), "image/jpeg")}
        data = {
            "upload_id": upload_id,
            "chunk_index": "1",
            "total_chunks": "2",
            "offset": "4",
        }
        assert (
            await client.post("/api/backup/upload/chunk", data=data, files=files)
        ).status_code == 200

        res_ok = await client.post(
            "/api/backup/upload/finalize",
            json={
                "upload_id": upload_id,
                "original_filename": "short.jpg",
                "relative_path": "short.jpg",
                "destination_dir": str(sandbox_env["media_dir"]),
                "collision_policy": "auto-rename",
                "total_size": 9,
            },
        )
        assert res_ok.status_code == 200
        assert Path(res_ok.json()["path"]).read_bytes() == b"half-done"


def test_gc_stale_uploads_removes_only_old_parts(sandbox_env):
    import os
    import time as _time

    staging = upload_svc.UPLOAD_STAGING_DIR
    old_part = staging / "upl_abandoned.part"
    old_part.write_bytes(b"x" * 32)
    fresh_part = staging / "upl_in_flight.part"
    fresh_part.write_bytes(b"y" * 16)
    dl_dir = staging / "dl_job123"  # owned by the URL download worker
    dl_dir.mkdir(exist_ok=True)

    stale_time = _time.time() - 48 * 3600
    os.utime(old_part, (stale_time, stale_time))

    report = upload_svc.gc_stale_uploads(max_age_hours=24)
    assert report["removed"] == 1
    assert report["freed_bytes"] == 32
    assert not old_part.exists()
    assert fresh_part.exists()
    assert dl_dir.exists()
