"""The unified status API's ``transcode`` key must carry the shaped payload.

The Library page reads the *top-level* ``transcode`` key (the sorter reads
``transfers.transcode``), so both must expose the queued list, ``is_paused`` and
the target filenames. Regression guard for the shape divergence that left the
Library's transcode panel empty and rendered converted tiles as "undefined".
"""

from unittest.mock import MagicMock, patch

from media_library.services import status as status_mod


def test_system_status_transcode_matches_transfers_shape(api_client):
    transfer = MagicMock()
    transfer.get_status.return_value = {"active": None}
    transfer.transfer_items_map = {}
    transfer.transfers_history = []

    transcode = MagicMock()
    transcode.transcode_paused = True
    transcode.get_status.return_value = {"active": None}
    transcode.transcode_items_map = {
        "tq": {
            "id": "tq",
            "filename": "clip.mkv",
            "source_path": "/media/inc/clip.mkv",
            "status": "queued",
            "created_at": 5.0,
        }
    }
    transcode.transcode_history = [
        {
            "id": "th",
            "filename": "clip.mkv",
            "source_path": "/media/inc/clip.mkv",
            "target_path": "/media/out/clip.mp4",
            "target_size": 2 * 1024 * 1024,
            "status": "completed",
            "method": "remux",
            "completed_at": 3.0,
        }
    ]

    with (
        patch.object(status_mod, "transfer_worker_instance", transfer),
        patch.object(status_mod, "transcode_worker_instance", transcode),
    ):
        res = api_client.get("/api/system/status")

    assert res.status_code == 200
    transcode_payload = res.json()["transcode"]

    assert transcode_payload["is_paused"] is True
    assert transcode_payload["total_pending"] == 1

    queued = transcode_payload["queued"]
    assert len(queued) == 1
    assert queued[0]["id"] == "tq"
    assert queued[0]["source_path"] == "/media/inc/clip.mkv"

    history = transcode_payload["history"]
    assert history[0]["target_filename"] == "clip.mp4"
    assert history[0]["source_path"] == "/media/inc/clip.mkv"
    assert history[0]["target_path"] == "/media/out/clip.mp4"
    assert history[0]["target_size_formatted"] == "2.0 MB"


def test_system_status_is_active_with_queued_transcode(api_client):
    transfer = MagicMock()
    transfer.get_status.return_value = {"active": None}
    transfer.transfer_items_map = {}
    transfer.transfers_history = []

    transcode = MagicMock()
    transcode.transcode_paused = False
    transcode.get_status.return_value = {"active": None}
    transcode.transcode_items_map = {
        "tq": {
            "id": "tq",
            "filename": "clip.mkv",
            "status": "queued",
            "created_at": 1.0,
        }
    }
    transcode.transcode_history = []

    with (
        patch.object(status_mod, "transfer_worker_instance", transfer),
        patch.object(status_mod, "transcode_worker_instance", transcode),
    ):
        res = api_client.get("/api/system/status")

    assert res.status_code == 200
    assert res.json()["is_active"] is True
