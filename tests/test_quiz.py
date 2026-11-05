"""Acceptance tests for the quiz task.

Covers combined filters, stable created_at pagination, CSV export, and
query/export consistency after edits made through the existing API.

Each test builds its own collection, so tests are independent of each other
and of the demo catalog created by ``python -m keepstack seed``.
"""
import csv
import io
import os
import tempfile
import uuid as uuidlib

import pytest

os.environ["KEEPSTACK_DATA_DIR"] = tempfile.mkdtemp(prefix="keepstack-quiz-test-")
os.environ["KEEPSTACK_AI_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from keepstack import ingest, search  # noqa: E402
from keepstack.app import app  # noqa: E402
from keepstack.auth import hash_password, make_token  # noqa: E402
from keepstack.db import get_conn, init_db, transaction  # noqa: E402

CSV_HEADER = ["uuid", "title", "description", "filename", "media_type", "tags", "created_at"]


def setup_module(_):
    init_db()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
def _collection(name: str = "") -> int:
    conn = get_conn()
    with transaction() as c:
        cur = c.execute(
            "INSERT INTO collections (uuid, name, description, kind, created_at) "
            "VALUES (?,?,?,?,?)",
            (str(uuidlib.uuid4()), name or f"quiz-{uuidlib.uuid4().hex[:10]}", "", "folder",
             "2026-01-01T00:00:00Z"),
        )
    return cur.lastrowid


def _asset(
    *,
    title: str = "Asset",
    tags: tuple[str, ...] = (),
    collection_id: int | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
    media_type: str = "image",
    status: str = "active",
    description: str = "",
    filename: str | None = None,
) -> int:
    conn = get_conn()
    filename = filename or f"{uuidlib.uuid4().hex}.jpg"
    with transaction() as c:
        cur = c.execute(
            """INSERT INTO assets
               (uuid, sha256, filename, mime_type, media_type, ext, size,
                title, description, status, storage_key, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuidlib.uuid4()), uuidlib.uuid4().hex, filename, "image/jpeg", media_type,
             "jpg", 1024, title, description, status, "quiz/test", created_at, created_at),
        )
        asset_id = cur.lastrowid
        if collection_id is not None:
            c.execute(
                "INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, added_at) "
                "VALUES (?,?,?)",
                (collection_id, asset_id, created_at),
            )
        for name in tags:
            name = name.strip().lower()
            c.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag_id = c.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
            c.execute(
                "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id, source) VALUES (?,?, 'manual')",
                (asset_id, tag_id),
            )
    ingest.reindex_fts(asset_id)
    return asset_id


def _ids(result: dict) -> list[int]:
    return [item["id"] for item in result["items"]]


def _auth_headers() -> dict:
    conn = get_conn()
    with transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, is_active, created_at) "
            "VALUES ('quiz-admin', ?, 'admin', 1, '2026-01-01T00:00:00Z')",
            (hash_password("quiz-password"),),
        )
    user_id = conn.execute("SELECT id FROM users WHERE username='quiz-admin'").fetchone()["id"]
    return {"Authorization": "Bearer " + make_token(user_id, "admin")}


def _client() -> TestClient:
    return TestClient(app)


def _csv_rows(response) -> list[list[str]]:
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def _negative_fixture_set(cid: int) -> tuple[int, int]:
    """One true match plus one asset for every filter that must reject it."""
    match = _asset(title="Harbor match", tags=("approved", "campaign"), collection_id=cid)
    _asset(title="Harbor approved only", tags=("approved",), collection_id=cid)
    _asset(title="Harbor campaign only", tags=("campaign",), collection_id=cid)
    _asset(title="Harbor internal", tags=("approved", "campaign", "internal"),
           collection_id=cid)
    _asset(title="Harbor document", tags=("approved", "campaign"), collection_id=cid,
           media_type="document")
    deleted = _asset(title="Harbor deleted", tags=("approved", "campaign"),
                     collection_id=cid, status="deleted")
    return match, deleted


# ---------------------------------------------------------------------------
# 1. Combined filtering
# ---------------------------------------------------------------------------
def test_combined_filters_intersect():
    cid = _collection()
    match = _asset(title="Harbor approved campaign", tags=("approved", "campaign"),
                   collection_id=cid)
    _asset(title="Harbor approved only", tags=("approved",), collection_id=cid)
    _asset(title="Harbor campaign only", tags=("campaign",), collection_id=cid)
    _asset(title="Harbor approved campaign internal",
           tags=("approved", "campaign", "internal"), collection_id=cid)
    _asset(title="Approved campaign without keyword", tags=("approved", "campaign"),
           collection_id=cid)
    _asset(title="Harbor approved campaign deleted", tags=("approved", "campaign"),
           collection_id=cid, status="deleted")

    res = search.search(
        q="harbor", collection_id=cid,
        include_tags=["approved", "campaign"], exclude_tags=["internal"],
    )

    assert _ids(res) == [match]
    assert res["total"] == 1


def test_include_requires_all_and_exclude_rejects_any():
    cid = _collection()
    both = _asset(title="both", tags=("alpha", "beta"), collection_id=cid)
    with_gamma = _asset(title="both plus gamma", tags=("alpha", "beta", "gamma"),
                        collection_id=cid)
    _asset(title="alpha only", tags=("alpha",), collection_id=cid)
    _asset(title="beta only", tags=("beta",), collection_id=cid)

    res = search.search(collection_id=cid, include_tags=["alpha", "beta"])
    assert set(_ids(res)) == {both, with_gamma}

    res = search.search(collection_id=cid, include_tags=["alpha", "beta"],
                        exclude_tags=["gamma"])
    assert _ids(res) == [both]

    res = search.search(collection_id=cid, include_tags=["alpha", "beta"],
                        exclude_tags=["alpha"])
    assert res["total"] == 0


def test_duplicate_and_conflicting_tags():
    cid = _collection()
    match = _asset(title="alpha beta", tags=("alpha", "beta"), collection_id=cid)

    duplicated = search.search(collection_id=cid,
                               include_tags=["alpha", "alpha", "beta", " BETA "])
    assert _ids(duplicated) == [match]

    # The same tag on both sides must yield an empty result, not an error.
    conflict = search.search(collection_id=cid, include_tags=["alpha", "beta"],
                             exclude_tags=["beta"])
    assert conflict["total"] == 0


def test_comma_separated_and_legacy_tag_parameter():
    cid = _collection()
    match = _asset(title="alpha beta", tags=("alpha", "beta"), collection_id=cid)
    alpha_only = _asset(title="alpha only", tags=("alpha",), collection_id=cid)

    comma = search.search(collection_id=cid, include_tags=["alpha,beta"])
    assert _ids(comma) == [match]

    legacy = search.search(collection_id=cid, tag="alpha")
    assert set(_ids(legacy)) == {match, alpha_only}


def test_only_active_assets_are_returned():
    cid = _collection()
    active = _asset(title="active", tags=("approved",), collection_id=cid)
    _asset(title="pending", tags=("approved",), collection_id=cid, status="pending")
    _asset(title="deleted", tags=("approved",), collection_id=cid, status="deleted")

    res = search.search(collection_id=cid, include_tags=["approved"])
    assert _ids(res) == [active]


def test_blank_keyword_is_unrestricted():
    cid = _collection()
    for i in range(3):
        _asset(title=f"Asset {i}", collection_id=cid, created_at=f"2026-01-0{i + 1}T00:00:00Z")

    assert search.search(q="", collection_id=cid)["total"] == 3
    assert search.search(q="   ", collection_id=cid)["total"] == 3


def test_tag_matching_is_case_insensitive_like_existing_behavior():
    cid = _collection()
    match = _asset(title="cased", tags=("Approved",), collection_id=cid)
    res = search.search(collection_id=cid, include_tags=["APPROVED"])
    assert _ids(res) == [match]


# ---------------------------------------------------------------------------
# 2. Sorting and pagination
# ---------------------------------------------------------------------------
def test_created_at_sort_applies_with_keyword():
    cid = _collection()
    first = _asset(title="Harbor first", collection_id=cid, created_at="2026-01-01T00:00:00Z")
    second = _asset(title="Harbor second", collection_id=cid, created_at="2026-02-01T00:00:00Z")
    third = _asset(title="Harbor third", collection_id=cid, created_at="2026-03-01T00:00:00Z")

    oldest = search.search(q="harbor", collection_id=cid, sort="oldest")
    newest = search.search(q="harbor", collection_id=cid, sort="newest")

    assert _ids(oldest) == [first, second, third]
    assert _ids(newest) == [third, second, first]


def test_pagination_multi_page_has_no_duplicates_or_gaps():
    cid = _collection()
    expected = [
        _asset(title=f"Harbor page {i:02d}", collection_id=cid,
               created_at=f"2026-01-{i + 1:02d}T00:00:00Z")
        for i in range(25)
    ]

    seen: list[int] = []
    for page in (1, 2, 3):
        res = search.search(q="harbor", collection_id=cid, sort="oldest",
                            page=page, page_size=10)
        assert res["total"] == 25
        assert res["pages"] == 3
        assert res["page"] == page
        assert res["page_size"] == 10
        seen.extend(_ids(res))

    assert seen == expected
    assert len(seen) == len(set(seen)) == 25


def test_page_beyond_last_is_empty_but_reports_total():
    cid = _collection()
    for i in range(3):
        _asset(title=f"Harbor {i}", collection_id=cid)

    res = search.search(q="harbor", collection_id=cid, page=4, page_size=10)
    assert res["items"] == []
    assert res["total"] == 3
    assert res["pages"] == 1


def test_identical_created_at_is_stable_across_pages():
    cid = _collection()
    expected = [
        _asset(title=f"Tie {i}", collection_id=cid, created_at="2026-05-01T12:00:00Z")
        for i in range(10)
    ]

    seen: list[int] = []
    for page in range(1, 5):
        res = search.search(collection_id=cid, sort="oldest", page=page, page_size=3)
        seen.extend(_ids(res))

    assert seen == expected
    assert len(seen) == len(set(seen)) == 10


# ---------------------------------------------------------------------------
# 3. CSV export
# ---------------------------------------------------------------------------
def test_export_matches_paged_list_order_and_filters():
    cid = _collection()
    for i in range(13):
        tags = ("approved", "campaign") if i % 2 == 0 else ("approved",)
        _asset(title=f"Harbor export {i:02d}", tags=tags, collection_id=cid,
               created_at=f"2026-01-{i + 1:02d}T00:00:00Z", media_type="image")

    paged: list[str] = []
    for page in (1, 2, 3):
        res = search.search(q="harbor", collection_id=cid, sort="newest",
                            media_type="image", page=page, page_size=5)
        paged.extend(item["uuid"] for item in res["items"])

    resp = _client().get(
        "/api/assets/export.csv",
        params=[("collection_id", str(cid)), ("q", "harbor"), ("sort", "newest"),
                ("include_tags", "approved"), ("media_type", "image")],
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))
    exported = [row["uuid"] for row in rows]
    assert len(exported) == 13
    assert exported == [u for u in paged]  # same filter, same stable order

    # Compare against the exactly filtered set as well.
    filtered = search.search(q="harbor", collection_id=cid, sort="newest",
                             media_type="image", include_tags=["approved"],
                             page_size=100)
    assert exported == [item["uuid"] for item in filtered["items"]]


def test_export_handles_chinese_commas_quotes_and_newlines():
    cid = _collection()
    title = 'Harbor, "quoted"\nsecond line'
    description = "港口，中文素材，含逗号"
    _asset(title=title, description=description, filename="harbor,file.jpg",
           tags=("campaign", "approved"), collection_id=cid)

    resp = _client().get(
        "/api/assets/export.csv",
        params={"collection_id": cid, "include_tags": "approved,campaign"},
        headers=_auth_headers(),
    )
    rows = _csv_rows(resp)

    assert resp.content.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in resp.content
    assert rows[0] == CSV_HEADER
    assert rows[1][1] == title
    assert rows[1][2] == description
    assert rows[1][3] == "harbor,file.jpg"
    assert rows[1][4] == "image"
    # Tags are stored/inserted in reverse order to prove the export sorts them.
    assert rows[1][5] == "approved|campaign"
    assert rows[1][6].startswith("2026-01-01")


def test_export_with_zero_results_still_has_header():
    cid = _collection()
    _asset(title="Harbor present", tags=("approved",), collection_id=cid)
    resp = _client().get(
        "/api/assets/export.csv",
        params={"collection_id": cid, "include_tags": "does-not-exist"},
        headers=_auth_headers(),
    )
    rows = _csv_rows(resp)
    assert rows == [CSV_HEADER]


def test_export_applies_every_filter():
    cid = _collection()
    match, deleted = _negative_fixture_set(cid)
    match_uuid = get_conn().execute(
        "SELECT uuid FROM assets WHERE id = ?", (match,)).fetchone()["uuid"]
    deleted_uuid = get_conn().execute(
        "SELECT uuid FROM assets WHERE id = ?", (deleted,)).fetchone()["uuid"]

    resp = _client().get(
        "/api/assets/export.csv",
        params=[("collection_id", str(cid)), ("q", "harbor"),
                ("include_tags", "approved"), ("include_tags", "campaign"),
                ("exclude_tags", "internal"), ("media_type", "image")],
        headers=_auth_headers(),
    )
    exported = [row["uuid"] for row in
                csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig")))]
    assert exported == [match_uuid]

    # A client-supplied status must not widen the export beyond active assets.
    resp = _client().get(
        "/api/assets/export.csv",
        params={"collection_id": cid, "status": "deleted"},
        headers=_auth_headers(),
    )
    exported = [row["uuid"] for row in
                csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig")))]
    assert match_uuid in exported
    assert deleted_uuid not in exported


def test_list_route_applies_filters_and_reports_pagination():
    cid = _collection()
    match, _ = _negative_fixture_set(cid)

    resp = _client().get(
        "/api/assets",
        params=[("collection_id", str(cid)), ("q", "harbor"),
                ("include_tags", "approved"), ("include_tags", "campaign"),
                ("exclude_tags", "internal"), ("media_type", "image"),
                ("page", "1"), ("page_size", "5")],
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert [item["id"] for item in data["items"]] == [match]
    assert data["total"] == 1
    assert data["page"] == 1
    assert data["page_size"] == 5
    assert data["pages"] == 1


def test_status_parameter_cannot_expose_inactive_assets():
    cid = _collection()
    active = _asset(title="Harbor active", tags=("approved",), collection_id=cid)
    _asset(title="Harbor pending", tags=("approved",), collection_id=cid, status="pending")
    _asset(title="Harbor deleted", tags=("approved",), collection_id=cid, status="deleted")

    data = _client().get(
        "/api/assets",
        params={"collection_id": cid, "status": "deleted"},
        headers=_auth_headers(),
    ).json()
    assert [item["id"] for item in data["items"]] == [active]

    rows = list(csv.DictReader(io.StringIO(_client().get(
        "/api/assets/export.csv",
        params={"collection_id": cid, "status": "deleted"},
        headers=_auth_headers(),
    ).content.decode("utf-8-sig"))))
    assert len(rows) == 1


def test_export_requires_authentication():
    resp = _client().get("/api/assets/export.csv")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 4. Edits are reflected by the next query and export
# ---------------------------------------------------------------------------
def test_edit_title_description_and_tags_are_visible_to_query_and_export():
    cid = _collection()
    asset_id = _asset(title="Harbor draft", description="old description",
                      tags=("approved",), collection_id=cid)
    asset_uuid = get_conn().execute(
        "SELECT uuid FROM assets WHERE id = ?", (asset_id,)).fetchone()["uuid"]
    client = _client()
    headers = _auth_headers()

    resp = client.patch(
        f"/api/assets/{asset_uuid}",
        json={"title": "Harbor Needle", "description": "更新后的描述 lagoonneedle"},
        headers=headers,
    )
    assert resp.status_code == 200

    found = search.search(q="needle", collection_id=cid)
    assert _ids(found) == [asset_id]

    # The description change must also reach the FTS index.
    by_description = search.search(q="lagoonneedle", collection_id=cid)
    assert _ids(by_description) == [asset_id]

    added = client.post(f"/api/assets/{asset_uuid}/tags", json={"name": "Campaign"},
                        headers=headers)
    assert added.status_code == 200
    both = search.search(collection_id=cid, q="needle",
                         include_tags=["approved", "campaign"])
    assert _ids(both) == [asset_id]

    removed = client.delete(f"/api/assets/{asset_uuid}/tags/campaign", headers=headers)
    assert removed.status_code == 200
    after_remove = search.search(collection_id=cid, q="needle",
                                 include_tags=["approved", "campaign"])
    assert after_remove["total"] == 0

    exported = client.get(
        "/api/assets/export.csv",
        params={"collection_id": cid, "q": "needle"},
        headers=headers,
    )
    rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert len(rows) == 1
    assert rows[0]["title"] == "Harbor Needle"
    assert rows[0]["description"] == "更新后的描述 lagoonneedle"
    assert rows[0]["tags"] == "approved"


def test_status_change_through_patch_hides_asset_from_list_and_export():
    cid = _collection()
    asset_id = _asset(title="Harbor archive", tags=("approved",), collection_id=cid)
    asset_uuid = get_conn().execute(
        "SELECT uuid FROM assets WHERE id = ?", (asset_id,)).fetchone()["uuid"]
    client = _client()
    headers = _auth_headers()

    resp = client.patch(f"/api/assets/{asset_uuid}", json={"status": "deleted"},
                        headers=headers)
    assert resp.status_code == 200

    data = client.get("/api/assets", params={"collection_id": cid, "q": "harbor"},
                      headers=headers).json()
    assert data["total"] == 0

    exported = client.get("/api/assets/export.csv",
                          params={"collection_id": cid, "q": "harbor"},
                          headers=headers)
    rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert rows == []


def test_removing_tag_removes_it_from_keyword_search_and_export():
    cid = _collection()
    asset_id = _asset(title="Untitled", tags=("zebra",), collection_id=cid)
    asset_uuid = get_conn().execute(
        "SELECT uuid FROM assets WHERE id = ?", (asset_id,)).fetchone()["uuid"]
    client = _client()
    headers = _auth_headers()

    assert search.search(q="zebra", collection_id=cid)["total"] == 1
    assert client.delete(f"/api/assets/{asset_uuid}/tags/zebra",
                         headers=headers).status_code == 200
    assert search.search(q="zebra", collection_id=cid)["total"] == 0

    exported = client.get("/api/assets/export.csv",
                          params={"collection_id": cid, "q": "zebra"},
                          headers=headers)
    assert list(csv.DictReader(io.StringIO(
        exported.content.decode("utf-8-sig")))) == []


def test_page_size_default_and_upper_bound():
    cid = _collection()
    for i in range(2):
        _asset(title=f"Harbor {i}", collection_id=cid)
    client = _client()
    headers = _auth_headers()

    default = client.get("/api/assets", params={"collection_id": cid},
                         headers=headers).json()
    assert default["page_size"] == 12

    upper = client.get("/api/assets", params={"collection_id": cid, "page_size": 100},
                       headers=headers).json()
    assert upper["page_size"] == 100


def test_empty_tag_parameters_are_ignored():
    cid = _collection()
    for i in range(2):
        _asset(title=f"Harbor {i}", collection_id=cid)
    data = _client().get(
        "/api/assets",
        params=[("collection_id", str(cid)), ("include_tags", ""),
                ("exclude_tags", "   ")],
        headers=_auth_headers(),
    ).json()
    assert data["total"] == 2


def test_unknown_sort_falls_back_to_newest():
    cid = _collection()
    old = _asset(title="Harbor old", collection_id=cid,
                 created_at="2026-01-01T00:00:00Z")
    new = _asset(title="Harbor new", collection_id=cid,
                 created_at="2026-02-01T00:00:00Z")

    data = _client().get("/api/assets",
                         params={"collection_id": cid, "sort": "bogus"},
                         headers=_auth_headers()).json()
    assert [item["id"] for item in data["items"]] == [new, old]


# ---------------------------------------------------------------------------
# 5. Bad input / special characters
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("params", [
    {"page": 0},
    {"page_size": 0},
    {"page_size": 101},
    {"page": "abc"},
])
def test_illegal_pagination_parameters_are_rejected(params):
    resp = _client().get("/api/assets", params=params, headers=_auth_headers())
    assert resp.status_code == 422


def test_special_character_keywords_do_not_break_search():
    cid = _collection()
    for i in range(3):
        _asset(title=f"Harbor {i}", collection_id=cid)

    client = _client()
    headers = _auth_headers()
    for q in ['***', '"', 'harbor" OR "1', "(((", "港口"]:
        resp = client.get("/api/assets", params={"q": q, "collection_id": cid},
                          headers=headers)
        assert resp.status_code == 200, q

    # A query that sanitises to nothing behaves like an empty keyword.
    blank = client.get("/api/assets", params={"q": "***", "collection_id": cid},
                       headers=headers)
    assert blank.json()["total"] == 3
