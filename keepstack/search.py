"""Search and discovery.

Three complementary modes over one catalog:

  * keyword  - FTS5 full-text across title, description, tags, metadata
  * filter   - faceted structured filters (media type, tag, status, dates)
  * semantic - embedding cosine ranking ("natural language" / more-like-this)

Facet counts are returned alongside results so the UI can render a live,
drill-down sidebar.
"""
from __future__ import annotations

from typing import Optional

from . import ai
from .db import get_conn

SORTS = {
    "newest": "a.created_at DESC, a.id DESC",
    "oldest": "a.created_at ASC, a.id ASC",
    "name": "a.title ASC, a.id ASC",
    "largest": "a.size DESC, a.id DESC",
}


def _fts_query(q: str) -> str:
    """Turn user text into a safe FTS5 prefix query."""
    terms = [t for t in "".join(c if c.isalnum() or c.isspace() else " " for c in q).split() if t]
    return " ".join(f'"{t}"*' for t in terms)


def matching_query(
    *, q="", media_type=None, tag=None, collection_id=None,
    include_tags=(), exclude_tags=(), status="active", sort="newest",
) -> tuple[str, list, str]:
    """Shared unpaginated selection for list, count and CSV. Values are bound."""
    where, params = ["a.status = ?"], [status]
    join = ""
    if q.strip():
        fts = _fts_query(q)
        if fts:
            join = " JOIN asset_fts ON asset_fts.rowid = a.id"
            where.append("asset_fts MATCH ?")
            params.append(fts)
        else:
            # Nonempty punctuation contains no searchable terms.
            where.append("0")
    if media_type:
        where.append("a.media_type = ?")
        params.append(media_type)
    if collection_id is not None:
        where.append("EXISTS (SELECT 1 FROM collection_assets ca "
                     "WHERE ca.asset_id = a.id AND ca.collection_id = ?)")
        params.append(collection_id)
    included = {t.strip().lower() for t in (*include_tags, tag or "") if t.strip()}
    excluded = {t.strip().lower() for t in exclude_tags if t.strip()}
    for names, negate in ((included, False), (excluded, True)):
        for name in sorted(names):
            where.append(("NOT " if negate else "") +
                         "EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
                         "WHERE at.asset_id = a.id AND t.name = ?)")
            params.append(name)
    return (f"FROM assets a{join} WHERE {' AND '.join(where)}",
            params, SORTS.get(sort, SORTS["newest"]))


def search(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    tag: Optional[str] = None,
    include_tags: tuple | list = (),
    exclude_tags: tuple | list = (),
    collection_id: Optional[int] = None,
    status: str = "active",
    sort: str = "newest",
    mode: str = "keyword",
    limit: int = 60,
    offset: int = 0,
) -> dict:
    conn = get_conn()

    # Semantic mode ranks the candidate pool by embedding similarity.
    if mode == "semantic" and q.strip():
        return _semantic_search(q, media_type, status, limit, offset)

    selection, params, order = matching_query(
        q=q, media_type=media_type, tag=tag, collection_id=collection_id,
        include_tags=include_tags, exclude_tags=exclude_tags, status=status, sort=sort)

    total = conn.execute(
        f"SELECT COUNT(*) AS c {selection}", params
    ).fetchone()["c"]

    rows = conn.execute(
        f"""SELECT a.* {selection}
            ORDER BY {order} LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()

    return {
        "total": total,
        "items": [_card(dict(r)) for r in rows],
        "facets": _facets(status),
        "mode": mode,
    }


def _semantic_search(q, media_type, status, limit, offset) -> dict:
    conn = get_conn()
    qvec = ai.embed(q)
    where = ["status = ?", "embedding IS NOT NULL"]
    params: list = [status]
    if media_type:
        where.append("media_type = ?")
        params.append(media_type)
    rows = conn.execute(
        f"SELECT * FROM assets WHERE {' AND '.join(where)}", params
    ).fetchall()
    scored = []
    for r in rows:
        score = ai.cosine(qvec, ai.unpack_embedding(r["embedding"]))
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    page = scored[offset:offset + limit]
    items = []
    for score, r in page:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        items.append(card)
    return {"total": len(scored), "items": items, "facets": _facets(status), "mode": "semantic"}


def more_like(asset_id: int, limit: int = 12) -> list[dict]:
    conn = get_conn()
    base = conn.execute("SELECT embedding FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not base or not base["embedding"]:
        return []
    bvec = ai.unpack_embedding(base["embedding"])
    rows = conn.execute(
        "SELECT * FROM assets WHERE id != ? AND status = 'active' AND embedding IS NOT NULL",
        (asset_id,),
    ).fetchall()
    scored = [(ai.cosine(bvec, ai.unpack_embedding(r["embedding"])), r) for r in rows]
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, r in scored[:limit]:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        out.append(card)
    return out


def _facets(status: str) -> dict:
    conn = get_conn()
    media = conn.execute(
        "SELECT media_type, COUNT(*) c FROM assets WHERE status = ? GROUP BY media_type ORDER BY c DESC",
        (status,),
    ).fetchall()
    tags = conn.execute(
        """SELECT t.name, COUNT(*) c FROM tags t
           JOIN asset_tags at ON at.tag_id = t.id
           JOIN assets a ON a.id = at.asset_id AND a.status = ?
           GROUP BY t.name ORDER BY c DESC LIMIT 25""",
        (status,),
    ).fetchall()
    return {
        "media_types": [{"value": r["media_type"], "count": r["c"]} for r in media],
        "tags": [{"value": r["name"], "count": r["c"]} for r in tags],
    }


def _card(a: dict) -> dict:
    """Compact asset representation for grid/list views."""
    return {
        "id": a["id"],
        "uuid": a["uuid"],
        "title": a["title"],
        "filename": a["filename"],
        "media_type": a["media_type"],
        "mime_type": a["mime_type"],
        "ext": a["ext"],
        "size": a["size"],
        "width": a["width"],
        "height": a["height"],
        "alt_text": a["alt_text"],
        "status": a["status"],
        "created_at": a["created_at"],
    }
