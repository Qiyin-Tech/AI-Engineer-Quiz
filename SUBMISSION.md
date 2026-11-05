# SUBMISSION

## 1. 提交信息

- Fork：https://github.com/zhentaozhang/AI-Engineer-Quiz
- 分支：`feature/quiz-search-export`
- 基于：`3aa137c`（收到题目时的 `main`）
- 最终 commit：见回复邮件与本文件所在 commit（`git log --oneline 3aa137c..HEAD`）

## 2. 做了什么

| 需求 | 实现 |
| --- | --- |
| 组合筛选 | `keepstack/search.py::_build_filters()` 统一生成 WHERE：关键词（FTS5）、集合、素材类型、include 标签（全部满足）、exclude 标签（任一命中即排除），固定 `status='active'`；列表、计数、导出共用 |
| 排序 | `newest` / `oldest` 按 `created_at`，并以 `id` 作为稳定次级排序键；关键词存在时也遵守所选排序 |
| 分页 | `GET /api/assets` 新增 `page`、`page_size`（默认 12，最大 100），响应增加 `page`、`page_size`、`pages`、`total` |
| CSV 导出 | `GET /api/assets/export.csv`，与列表同筛选/同排序，导出全部匹配项；UTF-8 BOM；`csv.writer` 处理逗号、引号、换行、中文；零结果仍输出表头；沿用 Bearer 鉴权 |
| 前端 | 集合下拉、include/exclude 标签输入、生效条件 chips、上一页/下一页与总数、带鉴权下载；筛选/排序变更回到第 1 页；请求竞态用 AbortController + 序号处理 |
| 修改后可见 | 复用现有 PATCH 资产、增删标签入口；`reindex_fts()` 已在这些入口调用，新增测试固化该行为 |
| 测试 | `tests/test_quiz.py` 21 条验收测试；`scripts/seed_quiz_data.py` 可复现演示数据 |

### 顺带修复的既有缺陷

1. **集合/单标签筛选永远返回 0 条**：`search()` 把 `JOIN` 中的占位符排在 `WHERE` 之前，但参数列表先放了 `status`，参数错位。新实现改用 `EXISTS` / `IN` 子查询，消除该类问题。
2. **网页登录按钮不可用**：`web/app.js::api()` 在传 `json` 时未设置 `method`，`fetch` 以 GET 携带 body 直接抛 `TypeError`，请求根本到不了后端。已改为 JSON 请求默认 POST。
3. **按 README 关闭 OCR 后测试失败**：OCR 测试只判断 `tesseract` 是否存在，未判断 `config.ocr_enabled`。已让 skip 条件同时检查开关（测试修复，不改业务逻辑）。

## 3. 运行与验证

需要 Python 3.10+ 与 `uv`。

```bash
git clone https://github.com/zhentaozhang/AI-Engineer-Quiz.git
cd AI-Engineer-Quiz
git checkout feature/quiz-search-export

uv venv --python 3.12
uv pip install -e '.[dev]'

export KEEPSTACK_AI_ENABLED=false
export KEEPSTACK_OCR_ENABLED=false
export KEEPSTACK_DATA_DIR=/tmp/keepstack-quiz   # 避免写入仓库

# 基础演示目录：24 个素材 + 3 个集合
uv run --no-sync python -m keepstack seed

# 本题目场景的数据：24 个 harbor 素材（集合内）+ 6 个集合外素材
uv run --no-sync python scripts/seed_quiz_data.py

uv run --no-sync python -m keepstack
# 打开 http://127.0.0.1:8000 ，账号 admin / admin
```

### 自动化测试

```bash
# README 的推荐环境（OCR 关闭）
export KEEPSTACK_AI_ENABLED=false KEEPSTACK_OCR_ENABLED=false
uv run --no-sync python -m pytest -q
```

实测结果：

| 环境 | 结果 |
| --- | --- |
| OCR 关闭（README 推荐） | **43 passed, 2 skipped** |
| OCR 开启 | **44 passed, 1 skipped** |

跳过项为需要本地视觉模型 / tesseract 的可选测试。

测试覆盖（`tests/test_quiz.py`）：

- `test_combined_filters_intersect`：集合 + 关键词 + include×2 + exclude 的交集。
- `test_include_requires_all_and_exclude_rejects_any`：include 必须全部、exclude 任一即排除。
- `test_duplicate_and_conflicting_tags`：重复标签不改变结果；同标签同时包含/排除返回空。
- `test_comma_separated_and_legacy_tag_parameter`：逗号分隔与旧 `tag` 参数兼容。
- `test_only_active_assets_are_returned`、`test_blank_keyword_is_unrestricted`、`test_tag_matching_is_case_insensitive_like_existing_behavior`。
- `test_created_at_sort_applies_with_keyword`：有关键词时升/降序仍正确。
- `test_pagination_multi_page_has_no_duplicates_or_gaps`、`test_page_beyond_last_is_empty_but_reports_total`。
- `test_identical_created_at_is_stable_across_pages`：相同时间戳跨页无重复/遗漏。
- `test_export_matches_paged_list_order_and_filters`：导出与逐页列表的 uuid 顺序完全一致。
- `test_export_handles_chinese_commas_quotes_and_newlines`、`test_export_with_zero_results_still_has_header`、`test_export_requires_authentication`。
- `test_edit_title_description_and_tags_are_visible_to_query_and_export`：PATCH 与增删标签后，查询和导出同步。
- `test_illegal_pagination_parameters_are_rejected`、`test_special_character_keywords_do_not_break_search`。

### 手工验收（实测）

在 `http://127.0.0.1:8000`，登录 `admin/admin` 后：

1. 集合选择 **Harbor Campaign**；
2. Include tags 填 `approved, campaign`；
3. Exclude tags 填 `internal`；
4. 搜索框输入 `harbor`；
5. 页面显示 **15 matches / 2 页**（第 1 页 12 条，第 2 页 3 条），生效条件 chips 为 `Harbor Campaign`、`+ approved`、`+ campaign`、`− internal`；
6. 点击 **Export CSV**，下载文件为 **1 行表头 + 15 行数据**，`tags` 形如 `approved|campaign`。

已用浏览器实际走查上述步骤并通过；导出接口在浏览器上下文带 Bearer token 返回 `200`、`text/csv; charset=utf-8`。

## 4. 接口摘要

### `GET /api/assets`

| 参数 | 说明 |
| --- | --- |
| `q` | 关键词，空白表示不限制；沿用现有 FTS5 字段与分词 |
| `media_type` | 素材类型 |
| `collection_id` | 集合，省略表示不限 |
| `include_tags` | 可重复传参或逗号分隔；必须全部具备 |
| `exclude_tags` | 可重复传参或逗号分隔；命中任一即排除 |
| `tag` | 兼容旧前端的单标签参数，等价于追加一个 include |
| `sort` | `newest`（默认）/ `oldest` / `name` / `largest` |
| `page` / `page_size` | 默认 1 / 12；`page>=1`、`1<=page_size<=100`，非法值返回 422 |

响应：`total`、`items`、`facets`、`mode`、`page`、`page_size`、`pages`。

### `GET /api/assets/export.csv`

参数同上（无分页），返回 `text/csv; charset=utf-8`，列固定为：

```text
uuid,title,description,filename,media_type,tags,created_at
```

`tags` 使用 `|` 连接（按名称排序）。文件带 UTF-8 BOM，便于 Excel 正确显示中文。

## 5. 主要取舍

1. **OFFSET 分页**：验收条件是“数据不变”，需要总页数与上一页/下一页；offset 实现最简单。深页性能与跨请求快照一致性不在本题范围。
2. **单一 WHERE 构造器**：列表、COUNT、导出共用 `_build_filters()`，用参数传递换取“列表与导出一致”的结构性保证，而不是两段 SQL 手工对齐。
3. **子查询而非 JOIN 做标签筛选**：`GROUP BY ... HAVING COUNT(DISTINCT)=N` 实现 include-ALL，`NOT IN` 实现 exclude-ANY；避免多标签 JOIN 造成重复行和计数虚高。
4. **CSV 流式输出**：使用 `StreamingResponse` + 生成器，内存占用与结果规模无关；代价是响应开始后若数据库出错无法再改状态码。
5. **导出格式选择 `|` + UTF-8 BOM**：`|` 比逗号更适合作为多标签分隔符，BOM 解决 Excel 中文乱码；均在本文档说明。
6. **保留语义搜索代码但不接入 UI**：题目允许隐藏语义入口；语义模式不支持新增的结构化筛选，作为限制记录。
7. **未新增依赖、未改表结构**：全部使用现有依赖与标准库 `csv`。

## 6. 限制与已知问题

- 语义搜索模式（API 仍可传 `mode=semantic`）只支持素材类型/状态，不支持集合与标签筛选；UI 已移除入口。
- 导出为同步流式响应，不提供后台任务、进度或超大结果的分片下载；题目不要求异步导出。
- 分页与导出的一致性在数据不变时成立；并发写入期间不保证跨请求快照一致。
- 侧栏 facet 统计是全局 active 素材统计，不随当前筛选联动；标签明确为 “Top tags”，未标注为当前匹配数。
- 深页 `OFFSET` 在超大数据量下会变慢；当前数据规模不构成问题。
- 未做 IE/旧版浏览器兼容；使用 `AbortController`、`fetch`、`Blob` 等现代 API。

## 7. 未完成项

本题要求范围内没有未完成项。未额外实现：每页条数选择器、导出进度提示、语义搜索的结构化筛选支持（均为非要求项）。

## 8. Commit 历史

```text
docs: add design and implementation plan for search and export
test: add acceptance tests for combined filters, stable pagination and CSV export
feat: implement combined filters, stable pagination and CSV export
feat: add filtering, pagination and CSV export to the web client
test: add reproducible quiz demo dataset generator
fix: skip the OCR test when OCR is disabled, matching the documented run setup
docs: add SUBMISSION.md with verification results
```

按要求未 squash / amend / rebase；后续修正使用新 commit。
