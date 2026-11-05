"""Search and discovery.

Three complementary modes over one catalog:

  * keyword  - FTS5 full-text across title, description, tags, metadata
  * filter   - faceted structured filters (media type, collection, tags)
  * semantic - embedding cosine ranking ("natural language" / more-like-this)

Facet counts are returned alongside results so the UI can render a live,
drill-down sidebar.

The list, the total count, and the CSV export all share ``_build_filters`` so
that a filtered query and its export can never disagree about which assets
match.
"""
from __future__ import annotations

from typing import Iterable, Optional

from . import ai
from .db import get_conn

PAGE_SIZE_DEFAULT = 12
PAGE_SIZE_MAX = 100

EXPORT_COLUMNS = ["uuid", "title", "description", "filename", "media_type", "tags", "created_at"]

# Stable ordering: every sort has a deterministic tiebreaker so equal
# created_at values cannot move between pages.
SORTS = {
    "newest": "a.created_at DESC, a.id DESC",
    "oldest": "a.created_at ASC, a.id ASC",
    "name": "a.title ASC, a.id ASC",
    "largest": "a.size DESC, a.id ASC",
}


def _fts_query(q: str) -> str:
    """Turn user text into a safe FTS5 prefix query."""
    terms = [t for t in "".join(c if c.isalnum() or c.isspace() else " " for c in q).split() if t]
    return " ".join(f'"{t}"*' for t in terms)


def _normalize_tags(values: Optional[Iterable[str]]) -> list[str]:
    """Lower-case, trim, split comma-separated values, and de-duplicate.

    Tags are stored lower-case by the ingest pipeline and the tag endpoints;
    normalising here keeps the API consistent with that behaviour.
    """
    out: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            name = part.strip().lower()
            if name and name not in out:
                out.append(name)
    return out


def _build_filters(
    *,
    q: str,
    media_type: Optional[str],
    collection_id: Optional[int],
    include_tags: Optional[Iterable[str]],
    exclude_tags: Optional[Iterable[str]],
    status: str,
) -> tuple[str, list]:
    """Build the shared WHERE clause. Returns ``(sql, params)``.

    Every condition is expressed as a subquery on ``assets`` (never a JOIN),
    which avoids duplicate rows from multi-tag joins and keeps the parameter
    order predictable for the list/count/export queries.
    """
    where = ["a.status = ?"]
    params: list = [status]

    if q.strip():
        fts = _fts_query(q)
        if fts:
            where.append("a.id IN (SELECT rowid FROM asset_fts WHERE asset_fts MATCH ?)")
            params.append(fts)

    if media_type:
        where.append("a.media_type = ?")
        params.append(media_type)

    if collection_id:
        where.append(
            "EXISTS (SELECT 1 FROM collection_assets ca "
            "WHERE ca.asset_id = a.id AND ca.collection_id = ?)"
        )
        params.append(collection_id)

    include = _normalize_tags(include_tags)
    if include:
        marks = ",".join("?" * len(include))
        where.append(
            "a.id IN (SELECT at.asset_id FROM asset_tags at "
            "JOIN tags t ON t.id = at.tag_id "
            f"WHERE t.name IN ({marks}) "
            "GROUP BY at.asset_id HAVING COUNT(DISTINCT t.id) = ?)"
        )
        params.extend([*include, len(include)])

    exclude = _normalize_tags(exclude_tags)
    if exclude:
        marks = ",".join("?" * len(exclude))
        where.append(
            "a.id NOT IN (SELECT at.asset_id FROM asset_tags at "
            "JOIN tags t ON t.id = at.tag_id "
            f"WHERE t.name IN ({marks}))"
        )
        params.extend(exclude)

    return " AND ".join(where), params


def search(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    tag: Optional[str] = None,
    include_tags: Optional[Iterable[str]] = None,
    exclude_tags: Optional[Iterable[str]] = None,
    collection_id: Optional[int] = None,
    status: str = "active",
    sort: str = "newest",
    mode: str = "keyword",
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
) -> dict:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), PAGE_SIZE_MAX))

    # ``tag`` is the legacy single-tag parameter; keep it as an extra include.
    include = _normalize_tags([*_normalize_tags(include_tags), *([tag] if tag else [])])

    if mode == "semantic" and q.strip():
        # Semantic ranking keeps the existing behaviour and only understands
        # media type / status. The web client hides this mode for the quiz.
        return _semantic_search(q, media_type, status, page, page_size)

    where, params = _build_filters(
        q=q, media_type=media_type, collection_id=collection_id,
        include_tags=include, exclude_tags=exclude_tags, status=status,
    )
    order = SORTS.get(sort, SORTS["newest"])
    offset = (page - 1) * page_size

    conn = get_conn()
    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM assets a WHERE {where}", params
    ).fetchone()["c"]

    rows = conn.execute(
        f"SELECT a.* FROM assets a WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, page_size, offset],
    ).fetchall()

    return {
        "total": total,
        "items": [_card(dict(r)) for r in rows],
        "facets": _facets(status),
        "mode": mode,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


def iter_export(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    tag: Optional[str] = None,
    include_tags: Optional[Iterable[str]] = None,
    exclude_tags: Optional[Iterable[str]] = None,
    collection_id: Optional[int] = None,
    status: str = "active",
    sort: str = "newest",
):
    """Yield every asset matching the list filters, in the same order.

    Pages are intentionally not applied: the export must cover the whole
    result set, not just the page currently visible in the browser.
    """
    include = _normalize_tags([*_normalize_tags(include_tags), *([tag] if tag else [])])
    where, params = _build_filters(
        q=q, media_type=media_type, collection_id=collection_id,
        include_tags=include, exclude_tags=exclude_tags, status=status,
    )
    order = SORTS.get(sort, SORTS["newest"])
    conn = get_conn()
    rows = conn.execute(
        f"""SELECT a.uuid, a.title, a.description, a.filename, a.media_type, a.created_at,
                   (SELECT GROUP_CONCAT(name, '|') FROM (
                        SELECT t.name AS name FROM asset_tags at
                        JOIN tags t ON t.id = at.tag_id
                        WHERE at.asset_id = a.id ORDER BY t.name
                    )) AS tag_names
            FROM assets a WHERE {where} ORDER BY {order}""",
        params,
    )
    for row in rows:
        yield {
            "uuid": row["uuid"] or "",
            "title": row["title"] or "",
            "description": row["description"] or "",
            "filename": row["filename"] or "",
            "media_type": row["media_type"] or "",
            "tags": row["tag_names"] or "",
            "created_at": row["created_at"] or "",
        }


def _semantic_search(q, media_type, status, page: int, page_size: int) -> dict:
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
    offset = (page - 1) * page_size
    page_rows = scored[offset:offset + page_size]
    items = []
    for score, r in page_rows:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        items.append(card)
    total = len(scored)
    return {
        "total": total,
        "items": items,
        "facets": _facets(status),
        "mode": "semantic",
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


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
