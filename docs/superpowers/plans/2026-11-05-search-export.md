# 素材检索与导出 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Keepstack 增加组合筛选、按创建时间的稳定排序分页、以及全量 CSV 导出，并补齐自动化测试与交付说明。

**Architecture:** 在 `keepstack/search.py` 内用单一 `_build_filters()` 生成 WHERE/参数/排序，列表、总数与 CSV 导出共用；FastAPI 暴露 `page/page_size` 与 `export.csv`；原生 JS 客户端增加筛选、分页与下载控件。所有业务逻辑用 pytest 覆盖。

**Tech Stack:** Python 3.12 / FastAPI / SQLite + FTS5 / 标准库 `csv` / 原生 JavaScript / pytest + httpx。

---

## 文件清单

| 文件 | 动作 | 责任 |
| --- | --- | --- |
| `keepstack/search.py` | 修改 | 筛选构造器、稳定排序、分页元数据、导出行迭代 |
| `keepstack/app.py` | 修改 | `page/page_size` 参数、`export.csv` 路由（在 `{uuid}` 之前） |
| `web/index.html` | 修改 | 筛选栏、生效条件 chips、分页条、导出按钮；移除语义下拉 |
| `web/app.js` | 修改 | 状态、请求竞态、筛选/分页/导出逻辑 |
| `web/styles.css` | 修改 | 筛选栏、chips、分页条样式 |
| `tests/test_quiz.py` | 新建 | 验收测试（组合筛选、分页、导出、编辑同步） |
| `scripts/seed_quiz_data.py` | 新建 | 可复现的演示数据 |
| `SUBMISSION.md` | 新建 | 运行、验证、取舍、限制 |
| `docs/superpowers/specs/2026-11-05-search-export-design.md` | 已建 | 设计说明 |

---

### Task 1: 组合筛选与稳定排序（后端）

**Files:** `keepstack/search.py`, `tests/test_quiz.py`

- [ ] Step 1: 在 `tests/test_quiz.py` 写失败的验收测试：组合筛选交集、include AND、exclude ANY、重复/冲突标签、仅 active、空白关键词。
- [ ] Step 2: 运行 `uv run --no-sync python -m pytest tests/test_quiz.py -q`，确认因缺少 `include_tags`/`exclude_tags` 参数而失败。
- [ ] Step 3: 实现 `_normalize_tags()` + `_build_filters()`；`SORTS` 增加 `id` 次级键；删除关键词强制 bm25 分支；`search()` 接受新参数。
- [ ] Step 4: 重跑测试，确认通过且原有 `tests/test_core.py` 不回归。
- [ ] Step 5: 提交 `feat: add combined include/exclude tag filters and stable sort`。

**关键实现：**

```python
def _normalize_tags(values):
    out = []
    for v in values or []:
        for part in str(v).split(","):
            name = part.strip().lower()
            if name and name not in out:
                out.append(name)
    return out

def _build_filters(*, q, media_type, collection_id, include_tags, exclude_tags):
    where = ["a.status = ?"]
    params = ["active"]
    fts = _fts_query(q) if q.strip() else ""
    if fts:
        where.append("a.id IN (SELECT rowid FROM asset_fts WHERE asset_fts MATCH ?)")
        params.append(fts)
    if media_type:
        where.append("a.media_type = ?"); params.append(media_type)
    if collection_id:
        where.append("EXISTS (SELECT 1 FROM collection_assets ca "
                     "WHERE ca.asset_id = a.id AND ca.collection_id = ?)")
        params.append(collection_id)
    inc = _normalize_tags(include_tags)
    if inc:
        marks = ",".join("?" * len(inc))
        where.append("a.id IN (SELECT at.asset_id FROM asset_tags at "
                     "JOIN tags t ON t.id = at.tag_id "
                     f"WHERE t.name IN ({marks}) GROUP BY at.asset_id "
                     "HAVING COUNT(DISTINCT t.id) = ?)")
        params.extend([*inc, len(inc)])
    exc = _normalize_tags(exclude_tags)
    if exc:
        marks = ",".join("?" * len(exc))
        where.append("a.id NOT IN (SELECT at.asset_id FROM asset_tags at "
                     f"JOIN tags t ON t.id = at.tag_id WHERE t.name IN ({marks}))")
        params.extend(exc)
    return " AND ".join(where), params
```

---

### Task 2: 分页与总数（后端）

**Files:** `keepstack/search.py`, `keepstack/app.py`, `tests/test_quiz.py`

- [ ] Step 1: 写失败测试：25 条数据 page_size=10 三页、并集完整；相同 `created_at` 稳定；`page` 超界返回空 items；关键词下升序/降序正确。
- [ ] Step 2: 运行确认失败（无 `page` 参数 / 无 `pages` 字段）。
- [ ] Step 3: `search()` 改为 `page/page_size`，返回 `page/pages`；`list_assets` 增加 `page: int = Query(1, ge=1)`、`page_size: int = Query(12, ge=1, le=100)`，透传筛选参数。
- [ ] Step 4: 运行测试通过；用 curl 验证 422 边界。
- [ ] Step 5: 提交 `feat: add page-based pagination with total and stable ordering`。

**关键实现：**

```python
order = SORTS.get(sort, SORTS["newest"])
total = conn.execute(f"SELECT COUNT(*) AS c FROM assets a WHERE {where}", params).fetchone()["c"]
rows = conn.execute(
    f"SELECT a.* FROM assets a WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
    [*params, page_size, (page - 1) * page_size]).fetchall()
...
return {"total": total, "items": [...], "facets": _facets(status), "mode": mode,
        "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}
```

---

### Task 3: CSV 导出（后端）

**Files:** `keepstack/search.py`, `keepstack/app.py`, `tests/test_quiz.py`

- [ ] Step 1: 写失败测试：导出 uuid/顺序与逐页列表一致；表头；中文/逗号/引号/换行；零结果仅表头；未登录 401。
- [ ] Step 2: 运行确认 404 / 失败。
- [ ] Step 3: `search.iter_export()` 复用 `_build_filters()` 与排序；`app.py` 在 `/api/assets/{uuid}` 之前注册 `GET /api/assets/export.csv`，BOM + `csv.writer` 流式输出。
- [ ] Step 4: 运行测试通过；curl 检查响应头与 BOM。
- [ ] Step 5: 提交 `feat: export all matching assets as CSV`。

**关键实现：**

```python
EXPORT_COLUMNS = ["uuid", "title", "description", "filename", "media_type", "tags", "created_at"]

@app.get("/api/assets/export.csv")
def export_assets_csv(q: str = "", ..., user: dict = Depends(require_user)):
    def rows():
        buf = io.StringIO()
        writer = csv.writer(buf)
        buf.write("\ufeff")
        writer.writerow(EXPORT_COLUMNS)
        for row in search.iter_export(q=q, ...):
            writer.writerow([row[c] for c in EXPORT_COLUMNS])
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)
    return StreamingResponse(rows(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="assets-export.csv"'})
```

---

### Task 4: 前端筛选、分页与导出

**Files:** `web/index.html`, `web/app.js`, `web/styles.css`

- [ ] Step 1: 改 HTML：筛选栏（集合/包含/排除）、`#active-filters`、分页条、导出按钮；移除语义下拉。
- [ ] Step 2: 改 JS：状态、`buildParams()`、`loadLibrary()`（AbortController + 序号）、`renderFacets` 支持 include 多选、`renderActiveFilters`、分页事件、`exportCsv()`。
- [ ] Step 3: 加 CSS，复用现有变量与 8px 间距。
- [ ] Step 4: `node --check web/app.js`；起服务手工走查业务场景。
- [ ] Step 5: 提交 `feat: add filter, pagination and CSV export controls to the web client`。

---

### Task 5: 演示数据脚本

**Files:** `scripts/seed_quiz_data.py`

- [ ] Step 1: 用 Pillow 生成约 28 个素材并 `ingest_stream`，打上 `approved`/`campaign`/`internal` 等标签，创建 “Harbor Campaign” 集合，写出一对相同 `created_at`。
- [ ] Step 2: 在干净 `KEEPSTACK_DATA_DIR` 运行脚本，确认输出计数。
- [ ] Step 3: 提交 `test: add reproducible quiz dataset generator`。

---

### Task 6: 收尾验证与交付文档

**Files:** `SUBMISSION.md`, `tests/test_core.py`（可选 OCR skip 修复）

- [ ] Step 1: 全量 `pytest -q`（OCR 开/关各跑一次）。
- [ ] Step 2: 起服务，按业务场景手工走查并记录真实数字。
- [ ] Step 3: 写 `SUBMISSION.md`：运行/验证命令、实测结果、取舍、限制、未完成项、commit 列表。
- [ ] Step 4: 提交 `docs: add SUBMISSION.md with verification results`。
- [ ] Step 5: `git push -u origin feature/quiz-search-export`，输出分支与 commit SHA。

---

## 验证命令

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'
export KEEPSTACK_AI_ENABLED=false KEEPSTACK_OCR_ENABLED=true
uv run --no-sync python -m pytest -q
KEEPSTACK_DATA_DIR=/tmp/keepstack-quiz uv run --no-sync python -m keepstack seed
KEEPSTACK_DATA_DIR=/tmp/keepstack-quiz uv run --no-sync python scripts/seed_quiz_data.py
KEEPSTACK_DATA_DIR=/tmp/keepstack-quiz uv run --no-sync python -m keepstack
```

## 提交策略

按 Task 顺序小步提交；不 squash / amend / rebase；修正用新 commit。`.gitignore` 已覆盖 `data/`、`*.db`、`.venv/`。
