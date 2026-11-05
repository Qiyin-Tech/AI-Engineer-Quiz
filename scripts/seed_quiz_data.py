"""Generate a reproducible demo catalog for the quiz business scenario.

Usage (from the repository root)::

    export KEEPSTACK_DATA_DIR=/tmp/keepstack-quiz
    uv run --no-sync python -m keepstack seed        # optional base catalog
    uv run --no-sync python scripts/seed_quiz_data.py

Creates a "Harbor Campaign" collection with assets that exercise the
combined-filter scenario from the README:

    keyword=harbor  include=approved,campaign  exclude=internal

The script is idempotent: it exits without changes if the collection already
exists. All images are generated on the fly, so nothing binary is committed.
"""
from __future__ import annotations

import io
import uuid as uuidlib
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw

from keepstack import ingest
from keepstack.audit import now_iso
from keepstack.auth import hash_password
from keepstack.db import get_conn, init_db, transaction

COLLECTION_NAME = "Harbor Campaign"
BASE_TIME = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)

# (title, tags) for assets inside the collection.
HARBOR_SPECS = (
    [(f"Harbor Campaign {i:02d}", ["approved", "campaign"] + (["internal"] if i < 3 else []))
     for i in range(12)]
    + [(f"Harbor Approved {i:02d}", ["approved"]) for i in range(4)]
    + [(f"Harbor Campaign Only {i:02d}", ["campaign"]) for i in range(2)]
    + [(f"Harbor Archive {i:02d}", ["approved", "campaign"]) for i in range(6)]
)

# Approved + campaign assets that do NOT match the keyword, outside the collection.
OTHER_SPECS = [(f"Civic Poster {i:02d}", ["approved", "campaign", "civic"]) for i in range(6)]


def _jpeg(index: int) -> bytes:
    img = Image.new("RGB", (360, 240), (30 + index * 6 % 200, 70, 130))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20 + index % 40, 40, 320, 200], outline=(240, 240, 240), width=3)
    draw.text((30, 110), f"harbor {index:02d}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=86)
    return buf.getvalue()


def _admin(conn) -> dict:
    row = conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone()
    if row:
        return dict(row)
    conn.execute(
        "INSERT INTO users (username, email, full_name, password_hash, role, is_active, created_at) "
        "VALUES ('admin','admin@example.org','Administrator',?, 'admin', 1, ?)",
        (hash_password("admin"), now_iso()),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM users WHERE username = 'admin'").fetchone())


def _tag(conn, asset_id: int, names: list[str]) -> None:
    for name in names:
        name = name.strip().lower()
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id, source) VALUES (?,?, 'manual')",
            (asset_id, tag_id),
        )


def run() -> None:
    init_db()
    conn = get_conn()
    if conn.execute("SELECT 1 FROM collections WHERE name = ?", (COLLECTION_NAME,)).fetchone():
        print(f"'{COLLECTION_NAME}' already exists; nothing to do.")
        return

    admin = _admin(conn)
    with transaction() as c:
        cur = c.execute(
            "INSERT INTO collections (uuid, name, description, kind, created_by, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (str(uuidlib.uuid4()), COLLECTION_NAME,
             "Demo data for combined filters and CSV export", "folder", admin["id"], now_iso()),
        )
        collection_id = cur.lastrowid

    created: list[int] = []
    index = 0
    for title, tags in HARBOR_SPECS:
        asset = ingest.ingest_stream(io.BytesIO(_jpeg(index)), f"harbor_{index:02d}.jpg",
                                     user=admin, run_ai=False, title=title)
        created.append(asset["id"])
        with transaction() as c:
            _tag(c, asset["id"], tags)
            c.execute(
                "INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, added_at) "
                "VALUES (?,?,?)",
                (collection_id, asset["id"], now_iso()),
            )
            # The first five share a timestamp so pagination stability is visible.
            created_at = (BASE_TIME if index < 5 else BASE_TIME + timedelta(hours=index)).isoformat()
            c.execute("UPDATE assets SET created_at = ?, updated_at = ? WHERE id = ?",
                      (created_at, created_at, asset["id"]))
        ingest.reindex_fts(asset["id"])
        index += 1

    for title, tags in OTHER_SPECS:
        asset = ingest.ingest_stream(io.BytesIO(_jpeg(index)), f"civic_{index:02d}.jpg",
                                     user=admin, run_ai=False, title=title)
        with transaction() as c:
            _tag(c, asset["id"], tags)
            created_at = (BASE_TIME + timedelta(days=1, hours=index)).isoformat()
            c.execute("UPDATE assets SET created_at = ?, updated_at = ? WHERE id = ?",
                      (created_at, created_at, asset["id"]))
        ingest.reindex_fts(asset["id"])
        index += 1

    print(f"Created {len(HARBOR_SPECS)} assets in '{COLLECTION_NAME}' "
          f"and {len(OTHER_SPECS)} assets outside it.")
    print("Scenario: q=harbor + include=approved,campaign + exclude=internal "
          f"+ collection='{COLLECTION_NAME}' -> 15 matches (2 pages at 12/page).")


if __name__ == "__main__":
    run()
