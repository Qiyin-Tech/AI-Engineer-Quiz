# 素材组合筛选、稳定排序分页与 CSV 导出 — 设计

- 日期：2026-11-05
- 状态：已实现前的设计基线
- 对应需求：仓库根目录 `README.md`（AI 应用开发工程师测试题）

## 1. 目标

在现有 Keepstack（FastAPI + SQLite/FTS5 + 原生 JS）上增量实现：

1. 关键词 / 集合 / 包含标签（AND）/ 排除标签（ANY）/ 素材类型 的交集筛选，仅 `active`。
2. 按 `created_at` 升/降序的稳定分页，返回匹配总数。
3. 与列表同筛选、同排序的**全量** CSV 导出。
4. 通过现有编辑入口修改标题/描述/标签后，查询与导出立即可见。

不引入新依赖，不做数据库迁移（现有表结构足够），不实现语义搜索的新筛选能力。

## 2. 现状与差距

| 位置 | 现状 | 差距 |
| --- | --- | --- |
| `keepstack/search.py::search` | 单 `tag` 参数；关键词存在时强制 `bm25` 排序；只有 `limit/offset`，无总数分页 | 缺多标签 AND/ANY、稳定排序、页码/总页数 |
| `keepstack/app.py::list_assets` | `limit<=200`、`offset`；无导出路由 | 缺 `page/page_size`、CSV |
| `web/app.js` | 固定 `limit=120`；单标签 facet；无上一页/下一页 | 缺筛选状态、分页控件、导出 |
| `keepstack/ingest.py::reindex_fts` | PATCH 资产、增删标签、改自定义字段后都会重建 FTS | 现有链路已满足需求 4，需测试固化 |

## 3. 方案

### 3.1 单一查询构造器

在 `search.py` 增加 `_build_filters()`，返回 `(where_sql, params, order_sql)`；列表、计数、导出都使用它，避免三处 SQL 漂移。

筛选条件（全部 AND）：

```sql
a.status = ?                                  -- 固定 active
a.id IN (SELECT rowid FROM asset_fts WHERE asset_fts MATCH ?)      -- 关键词
a.media_type = ?                                                    -- 素材类型
EXISTS (SELECT 1 FROM collection_assets ca
        WHERE ca.asset_id = a.id AND ca.collection_id = ?)          -- 集合
a.id IN (SELECT at.asset_id FROM asset_tags at JOIN tags t ON t.id = at.tag_id
         WHERE t.name IN (?, ...)
         GROUP BY at.asset_id
         HAVING COUNT(DISTINCT t.id) = ?)                          -- 包含全部
a.id NOT IN (SELECT at.asset_id FROM asset_tags at JOIN tags t ON t.id = at.tag_id
             WHERE t.name IN (?, ...))                             -- 排除任意
```

要点：

- 标签统一 `strip().lower()` 后去重；与现有 `add_tag` / `_set_tags` 的小写化一致。
- 同一标签同时出现在 include 与 exclude，AND 与 NOT IN 组合自然得到空集。
- 用 `IN (子查询)` 而非 JOIN，避免多标签造成行重复、计数虚高。
- 关键词经现有 `_fts_query()` 清洗；清洗结果为空（如 `***`）时视为不限制。
- 稳定排序：`created_at DESC, id DESC` / `ASC, id ASC`（`name`、`largest` 同样加 `id` 次级键）。
- 删除“有关键词时用 bm25”的分支，排序完全由 `sort` 决定；bm25 不再参与本题。

### 3.2 API 契约

`GET /api/assets`

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `q` | str | `""` | 空白 = 不限制 |
| `media_type` | str? | null | 精确匹配 |
| `collection_id` | int? | null | 不限集合 |
| `include_tags` | list[str] | `[]` | 可重复传参或逗号分隔；必须全部具备 |
| `exclude_tags` | list[str] | `[]` | 可重复传参或逗号分隔；命中任一即排除 |
| `tag` | str? | null | 兼容旧前端，等价于追加一个 include |
| `sort` | str | `newest` | `newest`/`oldest`/`name`/`largest`，非法值回落 `newest` |
| `page` | int | `1` | `>=1`，否则 422 |
| `page_size` | int | `12` | `1..100`，否则 422 |

两个路由都**强制** `status='active'`，不接受客户端覆盖（`?status=deleted` 会被忽略）。

响应在原有字段上增加 `page`、`page_size`、`pages`。

`GET /api/assets/export.csv`：同样的筛选与排序参数，忽略分页；`require_user` 鉴权；`text/csv; charset=utf-8`；`Content-Disposition: attachment; filename="assets-export.csv"`；UTF-8 BOM；CRLF。

列固定为 `uuid,title,description,filename,media_type,tags,created_at`；`tags` 用 `|` 连接并按名称排序；零结果只输出表头。

路由必须注册在 `/api/assets/{uuid}` 之前。

### 3.3 前端

- 状态：`{q, mode, media, collection, includeTags, excludeTags, sort, page, pageSize}`。
- 筛选区：集合下拉、包含标签输入、排除标签输入、素材类型 facet、清除全部。
- 生效条件 chips：媒体类型、集合名、每个 include/exclude 标签；单个可移除。
- 分页条：上一页 / 下一页 / “第 X / Y 页 · 共 N 条”；筛选或排序变更时 `page=1`。
- 竞态：`AbortController` + 自增请求序号，只渲染最后一次响应。
- 错误：加载失败显示可重试提示；空结果显示明确文案。
- 导出：`fetch` 带 Bearer 头 → `blob` → 临时 `<a download>`；失败 toast。
- 语义搜索入口从 UI 移除（题目允许），API 保留原有语义模式。

### 3.4 错误处理

| 场景 | 行为 |
| --- | --- |
| 非法 `page`/`page_size` | FastAPI 422（显式、可预期） |
| `page` 超出末页 | 200，`items=[]`，`total`/`pages` 正确 |
| 关键词只有特殊字符 | 清洗后为空 → 不限制关键词，200 |
| 导出无结果 | 200，仅表头 |
| 导出未登录 | 401 |
| 列表加载失败 | 前端可重试提示 + toast |

## 4. 测试策略

新增 `tests/test_quiz.py`，用直接插入构造互相隔离的集合与素材，覆盖：

1. 组合筛选交集（集合 + 关键词 + include×2 + exclude）。
2. include 全部 / exclude 任意 / 重复标签 / 同标签冲突。
3. 仅 active；空白关键词不限制。
4. 关键词下 `created_at` 升/降序。
5. 多页并集无重复无遗漏；超出末页。
6. 相同 `created_at` 的分页稳定性。
7. 导出与逐页列表的 uuid 顺序一致；筛选/排序一致。
8. CSV 表头、中文、逗号、引号、换行；零结果仅表头；未登录 401。
9. PATCH 标题/描述、增删标签后查询与导出同步。
10. 非法分页参数 422；特殊字符关键词不 500。

另提供 `scripts/seed_quiz_data.py` 生成可用于手工走查的演示数据。

## 5. 取舍

- **OFFSET 分页**：验收限定“数据不变”，能提供总页数与跳页；深页性能不在本题范围。
- **列表/导出共享 WHERE**：以少量参数传递换取一致性保证。
- **CSV 流式输出**：内存占用与结果规模无关；代价是响应中途出错无法改状态码，记录为限制。
- **保留语义搜索代码但不接入 UI**：避免无关改动；语义模式不支持新筛选，明确记为限制。

## 6. 不做

模型接入、相关性调优、新权限体系、异步导出、实时订阅、部署、跨请求快照一致性、上游 PR。
