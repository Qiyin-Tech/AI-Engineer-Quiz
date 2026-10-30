"""Repeatable acceptance catalog: python -m keepstack seed-quiz.

Restores this generator's 60 samples on rerun, without clearing other assets.
"""
import io
import uuid

from PIL import Image, ImageDraw

from . import ingest
from .app import _startup
from .db import get_conn


def run_seed():
    _startup()
    conn = get_conn()
    collections = []
    for name in ("Quiz Harbor Campaign", "Quiz Other Collection"):
        uid = str(uuid.uuid5(uuid.NAMESPACE_URL, "keepstack-quiz/" + name))
        conn.execute("INSERT OR IGNORE INTO collections (uuid,name,created_at) VALUES (?,?,?)",
                     (uid, name, "2026-01-01T00:00:00Z"))
        collections.append(conn.execute("SELECT id FROM collections WHERE uuid=?", (uid,)).fetchone()["id"])
    conn.commit()
    for i in range(60):
        title = f"Harbor campaign {i + 1:02d}"
        description = "Repeatable quiz acceptance sample"
        tags = ["approved", "campaign"]
        status, collection = "active", collections[0]
        if i == 52:
            title = 'Harbor 港口, "活动"\n第二行'
            description = '说明,包含"双引号"\r\n以及换行'
            tags += ['标签,完整名称', 'quoted"tag']
        if i == 53:
            tags += ["internal"]
        if i == 54:
            tags = ["approved"]
        if i == 55:
            collection = collections[1]
        if i in (57, 58):
            status = "archived" if i == 57 else "deleted"
        if i == 59:
            title = "Mountain landscape"
        if i == 56:
            data, filename = b"Quiz document sample 57", "quiz-document-57.txt"
        else:
            image = Image.new("RGB", (240, 160), (45 + i * 3 % 180, 95, 150))
            ImageDraw.Draw(image).text((16, 65), f"Quiz sample {i + 1:02d}", fill="white")
            buf = io.BytesIO(); image.save(buf, format="PNG")
            data, filename = buf.getvalue(), f"quiz-harbor-{i + 1:02d}.png"
            if i == 59:
                filename = "quiz-mountain-60.png"
        sample_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"keepstack-quiz/sample/{i}"))
        existing = conn.execute("SELECT id FROM assets WHERE uuid=?", (sample_uuid,)).fetchone()
        if existing:
            aid = existing["id"]
        else:
            asset = ingest.ingest_stream(io.BytesIO(data), filename, title=title, run_ai=False)
            aid = asset["id"]
            conn.execute("UPDATE assets SET uuid=? WHERE id=?", (sample_uuid, aid))
        created = f"2026-01-0{1 + i // 25}T00:00:00Z"
        conn.execute("UPDATE assets SET title=?,description=?,status=?,created_at=?,updated_at=? WHERE id=?",
                     (title, description, status, created, created, aid))
        conn.execute("DELETE FROM asset_tags WHERE asset_id=?", (aid,))
        ingest._set_tags(conn, aid, [(t, 1.0) for t in tags], "manual")
        conn.execute("DELETE FROM collection_assets WHERE asset_id=?", (aid,))
        conn.execute("INSERT INTO collection_assets (collection_id,asset_id,added_at) VALUES (?,?,?)",
                     (collection, aid, created))
        conn.commit()
        ingest.reindex_fts(aid)
    print("Seeded/restored 60 quiz samples and 2 collections.")
    print("Harbor + Quiz Harbor Campaign + approved + campaign - internal: 54 matches; image: 53.")


if __name__ == "__main__":
    run_seed()
