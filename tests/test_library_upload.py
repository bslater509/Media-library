"""Tests for browser chunked upload and finalization."""

import io
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from media_library.app_factory import create_app
import media_library.services.upload_service as upload_svc


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
        assert response.json() == {"status": "ok", "chunk_index": 0}

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
        res_fin = await client.post("/api/backup/upload/finalize", json=finalize_payload)
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
        files = {"chunk": ("IMG_1234.HEIC", io.BytesIO(b"mock heic data"), "image/heic")}
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
        res_fin = await client.post("/api/backup/upload/finalize", json=finalize_payload)
        assert res_fin.status_code == 200
        assert Path(res_fin.json()["path"]).exists()


@pytest.mark.anyio
async def test_upload_disallowed_extension(app_instance, sandbox_env):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload_id = "upl_bad_ext_1"
        files = {"chunk": ("malware.exe", io.BytesIO(b"bad"), "application/octet-stream")}
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
        res_fin = await client.post("/api/backup/upload/finalize", json=finalize_payload)
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

        res_cancel = await client.post("/api/backup/upload/cancel", json={"upload_id": upload_id})
        assert res_cancel.status_code == 200
        assert not staging_file.exists()
