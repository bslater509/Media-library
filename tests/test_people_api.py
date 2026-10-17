"""The /api/people surface: scanning triggers, curation, search and crops."""

import os
from pathlib import Path

from media_library.core import config
from media_library.db.connection import db_conn


def _scan(scan_face_batch) -> int:
    from media_library.workers.face_worker import face_worker_instance as worker

    return scan_face_batch(worker)


def _people(client):
    res = client.get("/api/people")
    assert res.status_code == 200
    return res.json()


def test_status_payload_shape_and_disabled_default(api_client, monkeypatch):
    monkeypatch.setattr(config, "FACE_RECOGNITION_ENABLED", False)
    payload = api_client.get("/api/people/status").json()
    assert payload["enabled"] is False
    assert payload["engine"]["available"] is False  # no models in a bare checkout
    assert payload["faces_total"] == 0
    assert payload["threshold_presets"] == {
        "strict": 0.28,
        "balanced": 0.36,
        "loose": 0.46,
    }


def test_scan_is_refused_while_disabled(people_client, monkeypatch):
    monkeypatch.setattr(config, "FACE_RECOGNITION_ENABLED", False)
    res = people_client.post("/api/people/scan", json={"all": True})
    assert res.status_code == 409
    assert "FACE_RECOGNITION_ENABLED" in res.json()["detail"]


def test_scan_requires_a_scope(people_client):
    res = people_client.post("/api/people/scan", json={})
    assert res.status_code == 422


def test_scan_all_then_listing_and_person_detail(
    people_client, face_image_factory, scan_face_batch
):
    face_image_factory("anna1.jpg", (255, 0, 0))
    face_image_factory("anna2.jpg", (250, 4, 4))
    face_image_factory("bob.jpg", (0, 0, 255))

    queued = people_client.post("/api/people/scan", json={"all": True}).json()
    assert queued["success"] is True
    assert queued["scope"] == "all"

    assert _scan(scan_face_batch) == 3

    listing = _people(people_client)
    assert listing["total"] == 2
    assert listing["counts"]["faces"] == 3
    assert listing["counts"]["files"] == 3
    assert listing["status"]["enabled"] is True

    biggest = max(listing["people"], key=lambda p: p["face_count"])
    assert biggest["face_count"] == 2
    assert biggest["media_count"] == 2
    assert biggest["is_named"] is False
    assert biggest["cover_url"].startswith("/api/people/crop?face_id=")

    detail = people_client.get(f"/api/people/{biggest['id']}").json()
    assert detail["name"] is None
    assert detail["face_count"] == 2
    assert detail["media_total"] == 2
    assert {m["name"] for m in detail["media"]} == {"anna1.jpg", "anna2.jpg"}
    assert detail["faces"][0]["crop_url"]

    assert people_client.get("/api/people/9999").status_code == 404
    faces = people_client.get(f"/api/people/{biggest['id']}/faces").json()
    assert faces["total"] == 2 and len(faces["faces"]) == 2


def test_crop_endpoint_serves_jpeg_and_404s(
    people_client, face_image_factory, scan_face_batch
):
    face_image_factory("face.jpg", (255, 0, 0))
    people_client.post("/api/people/scan", json={"all": True})
    _scan(scan_face_batch)

    face_id = (
        _people(people_client)["people"][0]["cover_url"].split("=")[1].split("&")[0]
    )
    res = people_client.get(f"/api/people/crop?face_id={face_id}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content[:2] == b"\xff\xd8"
    assert people_client.get("/api/people/crop?face_id=424242").status_code == 404


def test_rename_merge_split_dismiss_flow(
    people_client, face_image_factory, scan_face_batch
):
    face_image_factory("a.jpg", (255, 0, 0))
    face_image_factory("b.jpg", (0, 0, 255))
    people_client.post("/api/people/scan", json={"all": True})
    _scan(scan_face_batch)

    listing = _people(people_client)["people"]
    first, second = listing[0]["id"], listing[1]["id"]

    renamed = people_client.post(
        "/api/people/rename", json={"person_id": first, "name": "  Anna  "}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Anna"
    assert (
        people_client.post(
            "/api/people/rename", json={"person_id": 9999, "name": "X"}
        ).status_code
        == 404
    )
    assert people_client.post("/api/people/rename", json={}).status_code == 422

    merged = people_client.post(
        "/api/people/merge", json={"target_id": first, "source_ids": [second]}
    )
    assert merged.status_code == 200
    assert merged.json()["moved"] == 1
    assert (
        people_client.post(
            "/api/people/merge", json={"target_id": first, "source_ids": [first]}
        ).status_code
        == 422
    )

    with db_conn() as conn:
        face_id = conn.execute("SELECT id FROM faces LIMIT 1").fetchone()["id"]
    split = people_client.post("/api/people/split", json={"face_ids": [face_id]})
    assert split.status_code == 200
    assert split.json()["moved"] == 1
    new_person = split.json()["person_id"]

    dismissed = people_client.post("/api/people/dismiss", json={"face_ids": [face_id]})
    assert dismissed.status_code == 200
    assert dismissed.json()["dismissed"] == 1

    dismissed_person = people_client.post(
        "/api/people/dismiss", json={"person_id": new_person}
    )
    assert dismissed_person.status_code == 200
    assert people_client.post("/api/people/dismiss", json={}).status_code == 422


def test_recluster_endpoint_keeps_names(
    people_client, face_image_factory, scan_face_batch
):
    face_image_factory("a1.jpg", (255, 0, 0))
    face_image_factory("a2.jpg", (252, 2, 2))
    people_client.post("/api/people/scan", json={"all": True})
    _scan(scan_face_batch)

    person_id = _people(people_client)["people"][0]["id"]
    people_client.post(
        "/api/people/rename", json={"person_id": person_id, "name": "Anna"}
    )
    res = people_client.post("/api/people/recluster", json={})
    assert res.status_code == 200
    assert res.json()["kept"] == 1
    listing = _people(people_client)
    assert listing["counts"]["named"] == 1


def test_threshold_preset_endpoint(people_client, monkeypatch):
    res = people_client.post("/api/people/threshold", json={"preset": "loose"})
    assert res.status_code == 200
    assert res.json()["threshold"] == 0.46
    assert res.json()["preset"] == "loose"

    status = people_client.get("/api/people/status").json()
    assert status["threshold"] == 0.46
    assert status["threshold_preset"] == "loose"

    explicit = people_client.post("/api/people/threshold", json={"threshold": 0.2})
    assert explicit.json()["threshold"] == 0.2
    assert explicit.json()["preset"] == "custom"

    assert (
        people_client.post(
            "/api/people/threshold", json={"preset": "nonsense"}
        ).status_code
        == 422
    )
    assert people_client.post("/api/people/threshold", json={}).status_code == 422


def test_folder_and_path_scoped_scans(
    people_client, face_image_factory, scan_face_batch
):
    first = face_image_factory("album/one.jpg", (255, 0, 0))
    face_image_factory("other/two.jpg", (0, 0, 255))
    people_client.post("/api/people/scan", json={"all": True})
    _scan(scan_face_batch)

    # Re-queue just one folder: only its scan rows are dropped.
    folder = str(first.parent)
    res = people_client.post("/api/people/scan", json={"folder": folder})
    assert res.status_code == 200
    assert res.json()["scope"] == "folder"
    assert res.json()["queued"] == 1
    with db_conn() as conn:
        remaining = conn.execute("SELECT file_path FROM face_scans").fetchall()
    assert [Path(r["file_path"]).name for r in remaining] == ["two.jpg"]

    # A path list works the same way (duplicates included).
    res = people_client.post(
        "/api/people/scan", json={"paths": [str(first), str(first)]}
    )
    assert res.json()["scope"] == "paths"
    with db_conn() as conn:
        remaining = {
            r["file_path"]
            for r in conn.execute("SELECT file_path FROM face_scans").fetchall()
        }
    assert remaining == {str(first.parent.parent / "other" / "two.jpg")}


def test_pause_and_resume_endpoint(people_client):
    from media_library.workers.face_worker import face_worker_instance as worker

    paused = people_client.post("/api/people/pause", json={"paused": True}).json()
    assert paused["is_paused"] is True
    assert worker.is_paused() is True
    resumed = people_client.post("/api/people/pause", json={"paused": False}).json()
    assert resumed["is_paused"] is False
    assert worker.is_paused() is False


def test_search_person_token(people_client, face_image_factory, scan_face_batch):
    face_image_factory("anna_iceland.mp4", (255, 0, 0))
    face_image_factory("bob_iceland.jpg", (0, 0, 255))
    people_client.post("/api/people/scan", json={"all": True})
    _scan(scan_face_batch)
    person_id = _people(people_client)["people"][0]["id"]
    people_client.post(
        "/api/people/rename", json={"person_id": person_id, "name": "Anna"}
    )

    # Only the files that person appears in.
    res = people_client.get("/api/backup/search", params={"q": "person:Anna"}).json()
    assert res["person"]["matched"] is True
    assert [r["name"] for r in res["results"]] == ["anna_iceland.mp4"]

    # The person filter composes with the usual filename/folder words.
    combined = people_client.get(
        "/api/backup/search", params={"q": "person:Anna iceland"}
    ).json()
    assert [r["name"] for r in combined["results"]] == ["anna_iceland.mp4"]
    none = people_client.get(
        "/api/backup/search", params={"q": "person:Anna missing_word"}
    ).json()
    assert none["results"] == []

    # An unknown person is reported as such instead of looking like a bad search.
    unknown = people_client.get(
        "/api/backup/search", params={"q": "person:Nobody"}
    ).json()
    assert unknown["person"]["matched"] is False
    assert unknown["total"] == 0
    assert unknown["results"] == []

    # Quoted names and plain searches still work.
    quoted = people_client.get(
        "/api/backup/search", params={"q": 'person:"anna" bob'}
    ).json()
    assert quoted["results"] == []
    plain = people_client.get("/api/backup/search", params={"q": "bob"}).json()
    assert [r["name"] for r in plain["results"]] == ["bob_iceland.jpg"]
    assert plain["person"] is None


def test_system_status_exposes_faces_block(
    people_client, face_image_factory, scan_face_batch
):
    face_image_factory("who.jpg", (255, 0, 0))
    people_client.post("/api/people/scan", json={"all": True})
    _scan(scan_face_batch)

    payload = people_client.get("/api/system/status").json()
    faces = payload["faces"]
    assert faces["enabled"] is True
    assert faces["faces_total"] == 1
    assert faces["people_total"] == 1
    assert faces["cache_max_size_formatted"] == "2.0 GB"
    assert "engine" in faces


def test_people_reads_never_walk_the_filesystem(
    people_client, face_image_factory, scan_face_batch, monkeypatch
):
    image = face_image_factory("walk.jpg", (255, 0, 0))
    people_client.post("/api/people/scan", json={"all": True})
    _scan(scan_face_batch)

    def _walk(*_a, **_k):
        raise AssertionError("os.walk must not run in the face read path")

    monkeypatch.setattr(os, "walk", _walk)
    listing = _people(people_client)
    person_id = listing["people"][0]["id"]
    assert people_client.get(f"/api/people/{person_id}").status_code == 200
    assert people_client.get(f"/api/people/{person_id}/faces").status_code == 200
    assert people_client.get("/api/people/status").status_code == 200
    assert Path(image).exists()
