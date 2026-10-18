"""The /api/people surface: scanning triggers, curation, search and crops."""

import os
import time
from pathlib import Path

import numpy as np
import pytest

from media_library.core import config
from media_library.db.connection import db_conn


def _scan(scan_face_batch) -> int:
    from media_library.workers.face_worker import face_worker_instance as worker

    return scan_face_batch(worker)


def _people(client):
    res = client.get("/api/people")
    assert res.status_code == 200
    return res.json()


def _unit(**weights: float) -> bytes:
    """A 128-d unit embedding: one-hot, or a mix of axes for a near-miss.

    Two vectors built from the same axis are ``1 - dot`` apart, so
    ``_unit(a0=0.8, a1=0.6)`` sits 0.20 from ``_unit(a0=1.0)``.
    """
    vec = np.zeros(128, dtype="float32")
    for axis, weight in weights.items():
        vec[int(axis[1:])] = float(weight)
    vec /= np.linalg.norm(vec)
    return vec.astype("<f4").tobytes()


def _seed_person(person_id: int, name: str | None, embedding: bytes, faces: int):
    """A cluster with ``faces`` members at the given embedding (no crops)."""
    now = time.time()
    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
            (person_id, name, now),
        )
        for index in range(faces):
            conn.execute(
                "INSERT INTO faces (file_path, frame_time, x, y, w, h, score,"
                " embedding, person_id, crop, created_at)"
                " VALUES (?, -1.0, 10, 10, 60, 60, 0.9, ?, ?, NULL, ?)",
                (f"/media/cluster{person_id}_{index}.jpg", embedding, person_id, now),
            )


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


def test_listing_filters_by_name_for_the_merge_picker(
    people_client, face_image_factory, scan_face_batch
):
    face_image_factory("anna.jpg", (255, 0, 0))
    face_image_factory("bob.jpg", (0, 0, 255))
    people_client.post("/api/people/scan", json={"all": True})
    _scan(scan_face_batch)

    listing = _people(people_client)["people"]
    assert len(listing) == 2
    named_id = listing[0]["id"]
    other_id = listing[1]["id"]
    assert (
        people_client.post(
            "/api/people/rename", json={"person_id": named_id, "name": "Anna"}
        ).status_code
        == 200
    )

    # No filter: both clusters, and the default total still counts them.
    both = _people(people_client)
    assert both["total"] == 2
    assert {p["id"] for p in both["people"]} == {named_id, other_id}

    # named=false is the merge picker's view: only clusters that can be folded
    # away without losing a name.
    unnamed = people_client.get("/api/people?named=false").json()
    assert unnamed["total"] == 1
    assert [p["id"] for p in unnamed["people"]] == [other_id]
    assert unnamed["people"][0]["is_named"] is False
    assert unnamed["counts"]["named"] == 1
    assert unnamed["counts"]["unnamed"] == 1

    only_named = people_client.get("/api/people?named=true").json()
    assert only_named["total"] == 1
    assert only_named["people"][0]["name"] == "Anna"

    # The filter pages like the unfiltered list.
    first = people_client.get("/api/people?named=false&limit=1&offset=0").json()
    second = people_client.get("/api/people?named=false&limit=1&offset=1").json()
    assert first["total"] == second["total"] == 1
    assert first["people"][0]["id"] == other_id
    assert second["people"] == []

    # Clearing the name returns the cluster to the unnamed set.
    assert (
        people_client.post(
            "/api/people/rename", json={"person_id": named_id, "name": ""}
        ).json()["name"]
        == ""
    )
    assert people_client.get("/api/people?named=true").json()["total"] == 0
    assert people_client.get("/api/people?named=false").json()["total"] == 2

    assert people_client.get("/api/people?named=maybe").status_code == 422


def test_listing_ranks_same_folder_clusters_first(people_client, sandbox_env):
    """`near=<person>` puts the clusters filed next to theirs at the top."""
    import time

    from media_library.db.connection import db_conn
    from media_library.db.indexer_files import add_file_to_index

    media = Path(sandbox_env["media_dir"])

    def add_file(folder: str, name: str) -> str:
        path = media / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        add_file_to_index(str(path))
        return str(path)

    # The target person lives in album/; one candidate is filed there too (but is
    # the *smaller* cluster), a bigger unrelated one sits in other/.
    target_files = [add_file("album", f"target{i}.jpg") for i in range(2)]
    same_folder = [add_file("album", f"duplicate{i}.jpg") for i in range(3)]
    elsewhere = [add_file("other", f"unrelated{i}.jpg") for i in range(6)]

    now = time.time()
    with db_conn() as conn, conn:
        for person_id, name in ((1, "Anna"), (2, None), (3, None)):
            conn.execute(
                "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
                (person_id, name, now),
            )
        for person_id, paths in ((1, target_files), (2, same_folder), (3, elsewhere)):
            for path in paths:
                conn.execute(
                    "INSERT INTO faces (file_path, frame_time, x, y, w, h, score,"
                    " embedding, person_id, crop, created_at)"
                    " VALUES (?, -1.0, 10, 10, 60, 60, 0.9, NULL, ?, NULL, ?)",
                    (path, person_id, now),
                )

    # Without `near`: biggest first, and no folder annotation.
    plain = people_client.get("/api/people?named=false&limit=5").json()
    assert [p["id"] for p in plain["people"]] == [3, 2]
    assert plain["people"][0].get("shared_folders") is None

    ranked = people_client.get("/api/people?named=false&near=1&limit=5").json()
    assert [p["id"] for p in ranked["people"]] == [2, 3]
    assert ranked["total"] == 2  # the target itself is never a candidate
    top, other = ranked["people"]
    assert top["shared_folders"] == 1
    assert top["shared_faces"] == 3
    assert top["shared_folder_names"] == ["album"]
    assert other["shared_folders"] == 0
    assert other["shared_faces"] == 0
    assert other["shared_folder_names"] == []

    # A cluster with no folders of its own simply has no affinity.
    assert (
        people_client.get("/api/people?named=false&near=3&limit=5").json()["people"][0][
            "shared_folders"
        ]
        == 0
    )
    assert people_client.get("/api/people?near=0").status_code == 422


def test_listing_weights_the_folders_the_target_is_all_over(people_client, sandbox_env):
    """A folder the person appears in heavily outranks one they pass through."""
    import time

    from media_library.db.connection import db_conn
    from media_library.db.indexer_files import add_file_to_index

    media = Path(sandbox_env["media_dir"])

    def add_file(folder: str, name: str) -> str:
        path = media / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        add_file_to_index(str(path))
        return str(path)

    # The target is all over "series" (3 faces) and only passes through "extras".
    target_files = [add_file("series", f"t{i}.jpg") for i in range(3)]
    target_files += [add_file("extras", "t-extra.jpg")]
    extras_heavy = [add_file("extras", f"e{i}.jpg") for i in range(2)]
    series_light = [add_file("series", "s0.jpg")]

    now = time.time()
    with db_conn() as conn, conn:
        for person_id, name in ((1, "Anna"), (2, None), (3, None)):
            conn.execute(
                "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
                (person_id, name, now),
            )
        for person_id, paths in (
            (1, target_files),
            (2, extras_heavy),
            (3, series_light),
        ):
            for path in paths:
                conn.execute(
                    "INSERT INTO faces (file_path, frame_time, x, y, w, h, score,"
                    " embedding, person_id, crop, created_at)"
                    " VALUES (?, -1.0, 10, 10, 60, 60, 0.9, NULL, ?, NULL, ?)",
                    (path, person_id, now),
                )

    # Bigger first when there is no folder context: 2 faces before 1.
    plain = people_client.get("/api/people?named=false&limit=5").json()
    assert [p["id"] for p in plain["people"]] == [2, 3]

    ranked = people_client.get("/api/people?named=false&near=1&limit=5").json()
    # 3 has fewer shared faces (1 vs 2) but sits in the folder the target is all
    # over, so it is the better merge candidate.
    assert [p["id"] for p in ranked["people"]] == [3, 2]
    assert ranked["people"][0]["shared_folder_names"] == ["series"]
    assert ranked["people"][0]["affinity"] > ranked["people"][1]["affinity"]


def test_listing_filters_by_folder_path(people_client, sandbox_env):
    """The picker's folder box: a substring match on the folder path."""
    import time

    from media_library.db.connection import db_conn
    from media_library.db.indexer_files import add_file_to_index

    media = Path(sandbox_env["media_dir"])

    def add_file(folder: str, name: str) -> str:
        path = media / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        add_file_to_index(str(path))
        return str(path)

    folders = {
        1: ["Iceland 2023/Day 1"],  # the target, and named
        2: ["Iceland 2023/Day 1"],
        3: ["Norway 2023/Day 1"],
        4: ["Iceland 2022/Day 9"],
    }
    now = time.time()
    # Index the files first: add_file_to_index() opens its own connection, so it
    # must not run inside the write transaction below.
    paths_by_person = {
        person_id: [add_file(folder, f"p{person_id}_{i}.jpg") for i in range(2)]
        for person_id, (folder,) in folders.items()
    }
    with db_conn() as conn, conn:
        for person_id in folders:
            conn.execute(
                "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
                (person_id, "Anna" if person_id == 1 else None, now),
            )
            for path in paths_by_person[person_id]:
                conn.execute(
                    "INSERT INTO faces (file_path, frame_time, x, y, w, h, score,"
                    " embedding, person_id, crop, created_at)"
                    " VALUES (?, -1.0, 10, 10, 60, 60, 0.9, NULL, ?, NULL, ?)",
                    (path, person_id, now),
                )

    def ids(query: str) -> list[int]:
        return [
            p["id"] for p in people_client.get(f"/api/people?{query}").json()["people"]
        ]

    assert ids("named=false") == [2, 3, 4]
    assert ids("named=false&folder=Iceland") == [2, 4]
    # Case-insensitive, and a substring at any depth matches.
    assert ids("named=false&folder=iceland") == [2, 4]
    assert ids("named=false&folder=2023") == [2, 3]
    assert ids("named=false&folder=Iceland%202023/Day%201") == [2]
    assert ids("named=false&folder=Iceland%202022") == [4]
    assert ids("named=false&folder=nothing here") == []

    # `total` follows the filter, so "Show more" stays honest, and each row says
    # which folder matched (last two segments, so a bare date is not shown alone).
    body = people_client.get("/api/people?named=false&folder=Iceland").json()
    assert body["total"] == 2
    assert body["people"][0]["match_folders"] == ["Iceland 2023/Day 1"]

    # It composes with `near`, which still ranks the shared folder first.
    ranked = people_client.get("/api/people?named=false&folder=iceland&near=1").json()
    assert [p["id"] for p in ranked["people"]] == [2, 4]
    assert ranked["people"][0]["shared_folder_names"] == ["Iceland 2023/Day 1"]
    assert ranked["total"] == 2

    # A folder filter never leaks a named cluster.
    assert people_client.get("/api/people?folder=Iceland").json()["total"] == 3
    assert ids("folder=Iceland") == [1, 2, 4]

    # Leading/trailing space and a bare wildcard are treated literally.
    assert ids("named=false&folder=%20Iceland%20") == [2, 4]
    assert ids("named=false&folder=%25") == []


def test_listing_reports_the_folders_a_cluster_is_filed_in(people_client, sandbox_env):
    """Every row says where its faces live, and how many crops it carries."""
    import time

    from media_library.db.connection import db_conn
    from media_library.db.indexer_files import add_file_to_index

    media = Path(sandbox_env["media_dir"])

    def add_file(folder: str, name: str) -> str:
        path = media / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        add_file_to_index(str(path))
        return str(path)

    iceland = [add_file("Iceland 2023/Day 4", f"i{i}.jpg") for i in range(4)]
    goa = [add_file("Goa/Day 1", f"g{i}.jpg") for i in range(2)]
    # A face whose file left the index (a stale row): no folder to report.
    orphan = str(media / "gone" / "vanished.jpg")

    now = time.time()
    # Index the files first: add_file_to_index() opens its own connection, so it
    # must not run inside the write transaction below.
    with db_conn() as conn, conn:
        conn.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
            (1, "Anna", now),
        )
        conn.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, NULL, ?)", (2, now)
        )
        for index, path in enumerate([*iceland, *goa]):
            conn.execute(
                "INSERT INTO faces (file_path, frame_time, x, y, w, h, score,"
                " embedding, person_id, crop, created_at)"
                " VALUES (?, -1.0, 10, 10, ?, 60, 0.9, NULL, 1, ?, ?)",
                (path, 60 + index, f"face_{index:024d}.jpg", now),
            )
        conn.execute(
            "INSERT INTO faces (file_path, frame_time, x, y, score, embedding,"
            " person_id, crop, created_at)"
            " VALUES (?, -1.0, 10, 10, 0.9, NULL, 2, NULL, ?)",
            (orphan, now),
        )

    rows = {
        p["id"]: p for p in people_client.get("/api/people?limit=10").json()["people"]
    }
    # Busiest folder first, relative to the library root, last two segments.
    assert rows[1]["folder_names"] == ["Iceland 2023/Day 4", "Goa/Day 1"]
    assert rows[2]["folder_names"] == []
    assert rows[1]["sample_urls"][0].startswith("/api/people/crop?face_id=")
    assert rows[1]["cover_url"] == rows[1]["sample_urls"][0]

    # `samples` sets how many crops a row carries: the merge picker wants a few,
    # the review card a handful more, and the server clamps the band.
    def crop_urls(query: str) -> list[str]:
        body = people_client.get(f"/api/people?{query}").json()
        return body["people"][0]["sample_urls"]

    # 6 faces have a crop; the default is 4 and the review queue asks for more.
    assert len(crop_urls("limit=1&named=true")) == 4
    assert len(crop_urls("limit=1&named=true&samples=8")) == 6
    assert len(crop_urls("limit=1&named=true&samples=12")) == 6
    # A cluster with no crop falls back to none rather than a broken URL.
    assert crop_urls("limit=1&named=false") == []
    assert people_client.get("/api/people?samples=0").status_code == 422
    assert people_client.get("/api/people?samples=13").status_code == 422


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


def test_auto_match_folds_lookalikes_into_named_people(people_client):
    # Anna (named) with a near-miss cluster 0.20 away and an unrelated cluster
    # orthogonal to her — the shape of a backlog built before the name existed.
    _seed_person(1, "Anna", _unit(a0=1.0), 3)
    _seed_person(2, None, _unit(a0=0.8, a1=0.6), 2)
    _seed_person(3, None, _unit(a5=1.0), 2)

    preview = people_client.post("/api/people/auto-match", json={"dry_run": True})
    assert preview.status_code == 200
    body = preview.json()
    assert (body["matched"], body["faces"], body["people"]) == (1, 2, 1)
    assert body["remaining_unnamed"] == 1
    assert body["by_person"][0]["name"] == "Anna"
    assert body["by_person"][0]["clusters"] == 1
    assert body["samples"][0]["target_name"] == "Anna"
    assert body["samples"][0]["distance"] == pytest.approx(0.2, abs=0.01)
    with db_conn() as conn:  # a dry run moves nothing
        assert (
            conn.execute("SELECT COUNT(*) FROM faces WHERE person_id = 2").fetchone()[0]
            == 2
        )

    applied = people_client.post("/api/people/auto-match", json={})
    assert applied.status_code == 200
    assert (applied.json()["removed"], applied.json()["faces"]) == (1, 2)
    with db_conn() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM faces WHERE person_id = 1").fetchone()[0]
            == 5
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM people WHERE id = 2").fetchone()[0] == 0
        )
        # An unnamed cluster is never folded into another unnamed one, so the
        # unrelated cluster keeps its faces and its (still unnamed) cluster.
        assert (
            conn.execute("SELECT COUNT(*) FROM faces WHERE person_id = 3").fetchone()[0]
            == 2
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM people WHERE id = 3 AND name IS NULL"
            ).fetchone()[0]
            == 1
        )


def test_auto_match_threshold_and_preset_controls(people_client):
    _seed_person(1, "Anna", _unit(a0=1.0), 2)
    _seed_person(2, None, _unit(a0=0.6, a1=0.8), 2)  # 0.40 away

    strict = people_client.post(
        "/api/people/auto-match", json={"preset": "strict", "dry_run": True}
    ).json()
    assert strict["threshold"] == 0.28
    assert strict["matched"] == 0

    balanced = people_client.post(
        "/api/people/auto-match", json={"preset": "balanced", "dry_run": True}
    ).json()
    assert balanced["matched"] == 0

    loose = people_client.post(
        "/api/people/auto-match", json={"preset": "loose", "dry_run": True}
    ).json()
    assert loose["threshold"] == 0.46
    assert loose["matched"] == 1

    assert (
        people_client.post(
            "/api/people/auto-match", json={"preset": "nope"}
        ).status_code
        == 422
    )
    # An explicit threshold is clamped to the same range the setting allows.
    clamped = people_client.post(
        "/api/people/auto-match", json={"threshold": 0.01, "dry_run": True}
    ).json()
    assert clamped["threshold"] == 0.05
    assert clamped["matched"] == 0


def test_auto_match_is_available_while_scanning_is_off(api_client, monkeypatch):
    """Curation of an existing index never needs the feature switched on."""
    monkeypatch.setattr(config, "FACE_RECOGNITION_ENABLED", False)
    _seed_person(1, "Anna", _unit(a0=1.0), 1)
    _seed_person(2, None, _unit(a0=0.9, a1=0.43589), 1)  # 0.10 away

    res = api_client.post("/api/people/auto-match", json={})

    assert res.status_code == 200
    assert res.json()["removed"] == 1
    assert api_client.post("/api/people/scan", json={"all": True}).status_code == 409


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
