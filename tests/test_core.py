"""Core pipeline tests: storage dedup, ingest, metadata, search, auth, fixity.

These run against a throwaway data directory so they never touch a real
repository. Run with:  python -m pytest -q
"""
import io
import json
import os
import tempfile
from pathlib import Path

import pytest

os.environ["KEEPSTACK_DATA_DIR"] = tempfile.mkdtemp(prefix="keepstack-test-")
os.environ["KEEPSTACK_AI_ENABLED"] = "false"

from PIL import Image  # noqa: E402

from keepstack import ai, ingest, ocr, search, standards, storage, thumbnails, vision  # noqa: E402
from keepstack.auth import (hash_password, make_token, role_at_least,  # noqa: E402
                         verify_password, verify_token)
from keepstack.config import config  # noqa: E402
from keepstack.db import get_conn, init_db  # noqa: E402


def _img_bytes(color=(90, 130, 240), size=(400, 300)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _xmp_img_bytes():
    xmp = """<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
           xmlns:dc="http://purl.org/dc/elements/1.1/"
           xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/">
    <rdf:Description>
      <dc:title><rdf:Alt><rdf:li xml:lang="x-default">XMP Harbor Archive</rdf:li></rdf:Alt></dc:title>
      <dc:description><rdf:Alt><rdf:li xml:lang="x-default">NeedleMuseum catalog caption</rdf:li></rdf:Alt></dc:description>
      <dc:creator><rdf:Seq><rdf:li>Ada Archivist</rdf:li></rdf:Seq></dc:creator>
      <dc:subject><rdf:Bag><rdf:li>estuaries</rdf:li><rdf:li>tidal maps</rdf:li></rdf:Bag></dc:subject>
      <dc:publisher>NeedleMuseum Press</dc:publisher>
      <dc:date>2026-07-02</dc:date>
      <dc:rights>Creative Commons Test License</dc:rights>
      <photoshop:Credit>Archive Credit Line</photoshop:Credit>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""
    buf = io.BytesIO()
    Image.new("RGB", (180, 120), (20, 90, 130)).save(buf, format="JPEG", xmp=xmp.encode("utf-8"))
    return buf.getvalue()


def _split_img_bytes():
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 60), (20, 40, 80))
    for x in range(100):
        for y in range(60):
            img.putpixel((x, y), (220, 30, 30) if x < 50 else (30, 180, 80))
    img.save(buf, format="JPEG")
    return buf.getvalue()


def setup_module(_):
    init_db()


def test_password_hashing():
    h = hash_password("s3cret")
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)


def test_token_roundtrip():
    tok = make_token(42, "admin")
    payload = verify_token(tok)
    assert payload["sub"] == 42 and payload["role"] == "admin"
    assert verify_token(tok + "x") is None


def test_role_hierarchy():
    assert role_at_least("admin", "viewer")
    assert role_at_least("editor", "contributor")
    assert not role_at_least("viewer", "editor")


def test_ingest_creates_asset_with_dimensions_and_thumb():
    a = ingest.ingest_stream(io.BytesIO(_img_bytes()), "skyline.jpg")
    assert a["media_type"] == "image"
    assert a["width"] == 400 and a["height"] == 300
    assert a["sha256"] and storage.blob_path(a["storage_key"]).exists()
    assert storage.thumb_path(a["thumb_key"]).exists()


def test_content_addressable_dedup():
    data = _img_bytes(color=(10, 200, 120), size=(256, 256))
    first = ingest.ingest_stream(io.BytesIO(data), "dup_a.jpg")
    second = ingest.ingest_stream(io.BytesIO(data), "dup_b.jpg")
    assert second.get("duplicate") is True
    assert first["sha256"] == second["sha256"]


def test_keyword_search_finds_by_title():
    ingest.ingest_stream(io.BytesIO(_img_bytes(color=(200, 50, 90), size=(420, 260))),
                         "unique_harbor_photo.jpg", title="Unique Harbor Photo")
    res = search.search(q="harbor")
    assert res["total"] >= 1
    assert any("harbor" in (i["title"] or "").lower() for i in res["items"])


def test_semantic_search_returns_ranked_results():
    ingest.ingest_stream(io.BytesIO(_img_bytes(color=(40, 150, 90), size=(330, 500))),
                         "mountain_trail.jpg", title="Mountain trail in the alpine forest")
    res = search.search(q="alpine forest trail", mode="semantic")
    assert res["mode"] == "semantic"
    assert res["total"] >= 1
    # scores should be present and sorted descending
    scores = [i.get("score", 0) for i in res["items"]]
    assert scores == sorted(scores, reverse=True)


def test_embedding_similarity_is_symmetric_and_bounded():
    v = ai.embed("a civic poster about public transit")
    assert 0.99 <= ai.cosine(v, v) <= 1.01
    other = ai.embed("a watershed environmental survey diagram")
    assert -1.01 <= ai.cosine(v, other) <= 1.01


def test_fixity_detects_intact_and_corruption():
    a = ingest.ingest_stream(io.BytesIO(_img_bytes(size=(120, 120))), "fixity.jpg")
    assert storage.verify_fixity(a["storage_key"], a["sha256"]) is True
    # corrupt the blob and confirm the check fails
    storage.blob_path(a["storage_key"]).write_bytes(b"tampered")
    assert storage.verify_fixity(a["storage_key"], a["sha256"]) is False


def test_gc_removes_blob_after_asset_delete():
    a = ingest.ingest_stream(io.BytesIO(_img_bytes(color=(5, 20, 250), size=(111, 111))), "gc-delete.jpg")
    path = storage.blob_path(a["storage_key"])
    assert path.exists()

    conn = get_conn()
    conn.execute("UPDATE assets SET status = 'deleted' WHERE id = ?", (a["id"],))
    conn.commit()

    result = storage.collect_orphan_blobs()

    assert result["removed"] >= 1
    assert not path.exists()


def test_gc_keeps_active_asset_versions():
    a = ingest.ingest_stream(io.BytesIO(_img_bytes(color=(250, 20, 5), size=(112, 112))), "gc-version.jpg")
    old_key = a["storage_key"]
    _new_sha, new_key, new_size = storage.store_bytes(b"replacement version", "txt")

    conn = get_conn()
    conn.execute(
        "INSERT INTO asset_versions (asset_id, version_no, sha256, storage_key, size, note, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (a["id"], 1, a["sha256"], old_key, a["size"], "previous current", "2026-07-01T00:00:00Z"),
    )
    conn.execute(
        "UPDATE assets SET storage_key = ?, size = ? WHERE id = ?",
        (new_key, new_size, a["id"]),
    )
    conn.commit()

    result = storage.collect_orphan_blobs()

    assert old_key not in {b["key"] for b in result["blobs"]}
    assert storage.blob_path(old_key).exists()
    assert storage.blob_path(new_key).exists()


def test_dublin_core_mapping():
    a = ingest.ingest_stream(io.BytesIO(_img_bytes(color=(120, 90, 210), size=(360, 360))),
                             "dc_test.jpg", title="DC Test")
    dc = standards.dublin_core(a)
    assert dc["title"] == "DC Test"
    assert dc["type"] == "image"
    assert dc["identifier"] == a["uuid"]


def test_xmp_fields_seed_catalog_and_search():
    a = ingest.ingest_stream(io.BytesIO(_xmp_img_bytes()), "xmp_harbor.jpg")

    assert a["title"] == "XMP Harbor Archive"
    assert a["description"] == "NeedleMuseum catalog caption"
    assert a["dc_creator"] == "Ada Archivist"
    assert a["dc_subject"] == "estuaries, tidal maps"
    assert a["dc_publisher"] == "NeedleMuseum Press"
    assert a["dc_date"] == "2026-07-02"
    assert a["rights_statement"] == "Creative Commons Test License"
    assert a["credit"] == "Archive Credit Line"

    dc = standards.dublin_core(a)
    assert dc["publisher"] == "NeedleMuseum Press"

    meta = get_conn().execute("SELECT xmp_json FROM asset_metadata WHERE asset_id = ?", (a["id"],)).fetchone()
    xmp = json.loads(meta["xmp_json"])
    assert xmp["fields"]["dc:publisher"] == "NeedleMuseum Press"

    res = search.search(q="NeedleMuseum")
    assert any(i["uuid"] == a["uuid"] for i in res["items"])


def test_iiif_info_advertises_level_2():
    info = standards.iiif_info({"uuid": "asset-1", "width": 100, "height": 60})
    assert info["profile"] == "level2"
    assert info["tiles"][0]["width"] == 512


def test_iiif_rendition_crops_and_rotates():
    sha, key, _ = storage.store_bytes(_split_img_bytes(), "jpg")

    data = thumbnails.iiif_rendition(sha, key, "image", "10,20,30,20", "full", "90")
    img = Image.open(io.BytesIO(data))

    assert img.size == (20, 30)


def test_iiif_rendition_supports_percent_region_and_best_fit_size():
    sha, key, _ = storage.store_bytes(_split_img_bytes(), "jpg")

    data = thumbnails.iiif_rendition(sha, key, "image", "pct:0,0,50,100", "!25,25", "0")
    img = Image.open(io.BytesIO(data))

    assert img.size == (21, 25)


def test_oai_identify_is_valid_xml():
    body, status = standards.oai_response({"verb": "Identify"})
    assert status == 200
    assert "<repositoryName>" in body and "OAI-PMH" in body


def test_login_throttle_locks_success_clears_and_expires(monkeypatch):
    from keepstack import auth
    from keepstack.config import config
    monkeypatch.setattr(config, "login_max_attempts", 3)
    monkeypatch.setattr(config, "login_max_attempts_per_ip", 100)  # isolate the pair bucket
    monkeypatch.setattr(config, "login_lockout_seconds", 60)
    auth.reset_login_throttle()
    user, ip = "admin", "203.0.113.9"

    # First two failures do not lock; the third trips it.
    assert auth.note_login_failure(user, ip, now=1000) is False
    assert auth.note_login_failure(user, ip, now=1000) is False
    assert auth.note_login_failure(user, ip, now=1000) is True
    assert auth.login_locked(user, ip, now=1000) > 0
    assert auth.login_locked(user, "198.51.100.4", now=1000) == 0  # a different IP is open

    # A successful login clears an ACTIVE lockout (not just an empty bucket).
    auth.note_login_success(user, ip)
    assert auth.login_locked(user, ip, now=1000) == 0

    # A fresh lockout later expires once the window passes.
    for _ in range(3):
        auth.note_login_failure(user, ip, now=2000)
    assert auth.login_locked(user, ip, now=2000) > 0
    assert auth.login_locked(user, ip, now=2000 + 61) == 0


def test_login_throttle_per_ip_catches_username_spray(monkeypatch):
    from keepstack import auth
    from keepstack.config import config
    monkeypatch.setattr(config, "login_max_attempts", 100)  # pair bucket effectively off
    monkeypatch.setattr(config, "login_max_attempts_per_ip", 4)
    monkeypatch.setattr(config, "login_lockout_seconds", 60)
    auth.reset_login_throttle()
    ip = "203.0.113.50"
    # Every username is distinct, so no pair bucket ever trips, but the per-IP
    # bucket does after four sprayed attempts, locking the whole source IP.
    for i in range(4):
        assert auth.login_locked(f"user{i}", ip, now=500) == 0
        auth.note_login_failure(f"user{i}", ip, now=500)
    assert auth.login_locked("someone-else", ip, now=500) > 0


def test_client_ip_ignores_spoofed_xff(monkeypatch):
    from keepstack.app import client_ip
    from keepstack.config import config

    class _Req:
        def __init__(self, peer, xff=None):
            self.client = type("C", (), {"host": peer})()
            self.headers = {"X-Forwarded-For": xff} if xff else {}

    # No trusted proxies: X-Forwarded-For is ignored, the real peer wins.
    monkeypatch.setattr(config, "trusted_proxies", [])
    assert client_ip(_Req("198.51.100.7", xff="1.2.3.4")) == "198.51.100.7"

    # Behind a trusted proxy: the right-most non-proxy hop is the client.
    monkeypatch.setattr(config, "trusted_proxies", ["10.0.0.0/8"])
    assert client_ip(_Req("10.0.0.9", xff="9.9.9.9, 10.0.0.5")) == "9.9.9.9"
    # An untrusted direct peer still cannot spoof its way past.
    assert client_ip(_Req("203.0.113.1", xff="9.9.9.9")) == "203.0.113.1"


def test_iiif_manifest_shape():
    a = ingest.ingest_stream(io.BytesIO(_img_bytes(size=(300, 200))),
                             "manifest_test.jpg", title="Manifest Test")
    m = standards.iiif_manifest(a)
    assert m["type"] == "Manifest"
    assert m["@context"].endswith("presentation/3/context.json")
    assert m["label"]["none"] == ["Manifest Test"]

    canvas = m["items"][0]
    assert canvas["type"] == "Canvas"
    assert canvas["width"] == 300 and canvas["height"] == 200

    anno = canvas["items"][0]["items"][0]
    assert anno["motivation"] == "painting"
    assert anno["body"]["id"].endswith("/full/max/0/default.jpg")
    service = anno["body"]["service"][0]
    assert service["type"] == "ImageService3"
    assert service["id"].endswith(f"/iiif/3/{a['uuid']}")


@pytest.mark.skipif(not vision.ready(), reason="local vision model not installed")
def test_local_clip_cross_modal_similarity():
    from PIL import ImageDraw, ImageFont
    data_dir = Path(os.environ["KEEPSTACK_DATA_DIR"])

    page = Image.new("RGB", (448, 448), (250, 250, 250))
    draw = ImageDraw.Draw(page)
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default(size=28)
    for i in range(12):
        draw.text((20, 20 + i * 36), "Quarterly report on estuary water quality.", font=font, fill=(20, 20, 20))
    page_path = data_dir / "clip_textpage.png"
    page.save(page_path)
    color_path = data_dir / "clip_colorfield.png"
    Image.new("RGB", (448, 448), (215, 30, 30)).save(color_path)

    tvec = vision.embed_text("a page of printed text")
    pvec = vision.embed_image(page_path)
    cvec = vision.embed_image(color_path)

    assert tvec and pvec and cvec
    assert ai.cosine(tvec, pvec) > ai.cosine(tvec, cvec)


@pytest.mark.skipif(not (ocr.available() and config.ocr_enabled),
                    reason="OCR is disabled or tesseract is not installed")
def test_ocr_makes_image_text_searchable():
    from PIL import ImageDraw, ImageFont
    img = Image.new("RGB", (900, 240), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 96)
    except Exception:
        font = ImageFont.load_default(size=96)
    draw.text((40, 60), "QUILLFEATHER ARCHIVE", font=font, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    a = ingest.ingest_stream(io.BytesIO(buf.getvalue()), "ocr_sample.png")

    res = search.search(q="quillfeather")
    assert any(i["uuid"] == a["uuid"] for i in res["items"])


def test_version_endpoint_reports_name_and_version():
    from keepstack.app import version
    payload = version()
    assert payload["name"] == "keepstack"
    assert payload["version"]
