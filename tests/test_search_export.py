"""API acceptance tests; every test uses a fresh catalog and real authentication."""
import csv
import io
import json
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    from keepstack import db, ingest
    from keepstack.app import app
    from keepstack.config import config

    for key, value in {"data_dir": tmp_path, "db_path": tmp_path / "test.db",
                       "blob_dir": tmp_path / "blobs", "thumb_dir": tmp_path / "thumbs",
                       "cache_dir": tmp_path / "cache", "ai_enabled": False,
                       "ocr_enabled": False}.items():
        monkeypatch.setattr(config, key, value)
    monkeypatch.setattr(db, "_local", threading.local())
    connections = []
    original_connect = sqlite3.connect

    def connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        client.headers["Authorization"] = "Bearer " + login.json()["token"]
        conn = db.get_conn()
        for cid in (1, 2):
            conn.execute("INSERT INTO collections (id,uuid,name,created_at) VALUES (?,?,?,?)",
                         (cid, f"collection-{cid}", f"Collection {cid}", "2026-01-01"))
        assets = []

        def asset(title="Harbor", tags=("approved", "campaign"), collection=1,
                  media="image", status="active", created="2026-01-01T00:00:00Z",
                  description="", filename=None):
            idx = len(assets) + 1
            uuid = f"asset-{idx}"
            conn.execute("INSERT INTO assets (uuid,sha256,filename,title,description,media_type,"
                         "status,storage_key,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (uuid, uuid, filename or f"sample-{idx}.txt", title, description,
                          media, status, "test.txt", created, created))
            aid = conn.execute("SELECT id FROM assets WHERE uuid=?", (uuid,)).fetchone()["id"]
            ingest._set_tags(conn, aid, [(t, 1.0) for t in tags], "manual")
            if collection:
                conn.execute("INSERT INTO collection_assets (collection_id,asset_id,added_at) "
                             "VALUES (?,?,?)", (collection, aid, created))
            conn.commit()
            ingest.reindex_fts(aid)
            assets.append(uuid)
            return uuid

        yield client, asset
    for conn in connections:
        conn.close()


def ids(response):
    assert response.status_code == 200, response.text
    return [item["uuid"] for item in response.json()["items"]]


def test_combined_filters_and_tag_edges(catalog):
    client, asset = catalog
    wanted = asset()
    asset(tags=("approved", "campaign", "internal"))
    asset(tags=("approved",))
    asset(tags=("approved-extra", "campaign"))
    asset(collection=2)
    asset(media="document")
    asset(status="deleted")
    asset(status="archived")
    asset(title="Mountain")
    params = [("q", "harbor"), ("collection_id", 1), ("media_type", "image"),
              ("include_tags", " Approved "), ("include_tags", "campaign"),
              ("include_tags", "approved"), ("exclude_tags", "internal"),
              ("exclude_tags", "unknown")]
    result = client.get("/api/assets", params=params)
    assert ids(result) == [wanted]
    assert result.json()["total"] == 1
    assert ids(client.get("/api/assets", params=params + [("exclude_tags", "APPROVED")])) == []
    assert ids(client.get("/api/assets", params=params + [("include_tags", "missing")])) == []
    assert ids(client.get("/api/assets", params=params + [("tag", "campaign")])) == [wanted]


@pytest.mark.parametrize("q", ["", "harbor"])
@pytest.mark.parametrize("sort", ["oldest", "newest"])
def test_stable_pagination_with_equal_timestamps(catalog, q, sort):
    client, asset = catalog
    expected = [asset(created=f"2026-01-0{1 + i // 25}T00:00:00Z") for i in range(53)]
    if sort == "newest":
        expected.reverse()
    actual = []
    for offset in (0, 24, 48, 72):
        result = client.get("/api/assets", params=dict(q=q, sort=sort, offset=offset, limit=24))
        assert result.json()["total"] == 53
        actual.extend(ids(result))
    assert actual == expected
    assert len(set(actual)) == 53


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": -1}, {"limit": 201},
                                    {"offset": -1}, {"limit": "abc"},
                                    {"offset": 2**64}, {"collection_id": 0},
                                    {"status": "deleted"}, {"sort": "bogus"}])
def test_invalid_parameters(catalog, params):
    client, _ = catalog
    assert client.get("/api/assets", params=params).status_code == 422


def test_keyword_special_characters_and_all_search_fields(catalog):
    client, asset = catalog
    wanted = asset(title="港口 Harbor", description="needlecaption", tags=("tagneedle",),
                   filename="fileneedle.txt")
    for q in ("   ", "港口", "harbor", 'harbor" OR *', "needlecaption", "tagneedle", "fileneedle"):
        response = client.get("/api/assets", params={"q": q})
        # Sanitized terms still combine with AND; a literal OR is a term.
        assert response.status_code == 200
        if q != 'harbor" OR *':
            assert ids(response) == [wanted]
    for q in ('"*(){}', "' ; --"):
        assert ids(client.get("/api/assets", params={"q": q})) == []


def test_list_requires_login(catalog):
    client, _ = catalog
    client.headers.pop("Authorization")
    assert client.get("/api/assets").status_code == 401
