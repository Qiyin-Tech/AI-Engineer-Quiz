# 测评交付说明

## 仓库与实现范围

- Fork：[Szk1222/AI-Engineer-Quiz](https://github.com/Szk1222/AI-Engineer-Quiz)
- 开发分支：[`feat/asset-search-export`](https://github.com/Szk1222/AI-Engineer-Quiz/tree/feat/asset-search-export)
- 边界问题修复提交：[`352c22feb7b5c61fa87d007733860aa36533f981`](https://github.com/Szk1222/AI-Engineer-Quiz/commit/352c22feb7b5c61fa87d007733860aa36533f981)，包含斜杠标签删除与集合 ID 上限校验及回归测试。
- 最终 SHA：包含后续文档更新，以提交分支的最终 HEAD 为准；交付邮件中填写 `git rev-parse HEAD` 的完整输出。
- 从题目版本 `3aa137ccde6f981981582f6f71b81448c72a9dab` 开始，保留上游与阶段提交历史。
- 完成组合筛选、稳定排序分页、全部结果 CSV 导出、编辑后的查询/导出同步，以及对应自动化验证。保留 FastAPI、SQLite/FTS5、原生 JavaScript 技术栈，无新增运行时依赖。

## 安装与运行

Python 3.10+，本地主要验证使用 Python 3.12.7。以下命令从仓库根目录执行。

```powershell
git clone --branch feat/asset-search-export https://github.com/Szk1222/AI-Engineer-Quiz.git
cd AI-Engineer-Quiz
python -m pip install uv
python -m uv venv --python 3.12
python -m uv pip install --python .venv/Scripts/python.exe -e '.[dev]'
$env:KEEPSTACK_AI_ENABLED='false'
$env:KEEPSTACK_OCR_ENABLED='false'
.venv/Scripts/python.exe -m keepstack seed-quiz
.venv/Scripts/python.exe -m keepstack
```

打开 http://localhost:8000，使用 `admin` / `admin` 登录。运行数据默认在忽略的 `data/` 目录；可用 `KEEPSTACK_DATA_DIR` 指定独立目录。

Linux/macOS 对应命令：

```bash
python -m uv venv --python 3.12
python -m uv pip install --python .venv/bin/python -e '.[dev]'
export KEEPSTACK_AI_ENABLED=false KEEPSTACK_OCR_ENABLED=false
.venv/bin/python -m keepstack seed-quiz
.venv/bin/python -m keepstack
```

## 可复现数据与页面验收

`seed-quiz` 生成 60 条素材、2 个集合，使用 Pillow 合成小图片及一个文本文件，不依赖模型或外部素材。样本包含同创建时间、多标签、中文、逗号、引号、换行、archived/deleted 状态和不匹配项。

样本使用固定 UUID，重复执行会恢复这 60 条演示素材的标题、描述、标签、集合、状态和时间，不清空其他素材。建议在独立数据目录运行；已有普通 `seed` 数据可以共存，以下组合条件的预期数量仍成立。

1. 登录，在搜索框输入 `harbor`，集合选择 `Quiz Harbor Campaign`。
2. 分别添加包含标签 `approved`、`campaign`，排除标签 `internal`，应匹配 **54 条**。
3. 类型选择 `image`，应匹配 **53 条**，每页 24 条，共 **3 页**（24 / 24 / 5）。
4. 切换 Oldest / Newest，关键词存在时也遵守创建时间顺序；条件或排序变化返回第一页。
5. 在第三页点击 **Export all CSV**，应下载 **53 行数据及一行表头**，与遍历列表的 UUID 顺序一致。
6. 添加完整标签 `标签,完整名称`，应只匹配一条；删除对应条件可恢复。清空条件恢复所有 active 素材，排序保留当前选择。
7. 经详情页修改标题、描述、添加或移除标签后，再查询/导出应反映修改。关键词还会匹配文件名等原有 FTS 字段，不能仅因为改掉标题就认定旧关键词一定消失。

页面截图：[组合筛选与分页](docs/screenshots/07-search-export.png)。侧栏数字标明为全部 active 素材统计，不是当前匹配数。

## 接口与 CSV 约定

列表 `GET /api/assets` 和导出 `GET /api/assets/export.csv` 共用筛选条件：

```text
q=harbor&collection_id=1&media_type=image
&include_tags=approved&include_tags=campaign&exclude_tags=internal&sort=oldest
```

- 标签参数可重复，去首尾空白、转小写、去重，并按完整名称匹配。包含标签全部满足，排除标签任一命中即排除；包含与排除冲突返回空。兼容旧 `tag` 参数，将其合入包含条件。
- 标签删除路由支持完整路径形式的标签名，页面编码后的 `/`、连续 `/` 和中文标签均可删除；删除后重新建立 FTS 索引，查询和导出立即反映结果。
- 仅提供 active 关键词列表/导出；`status` 只接受 `active`、`mode` 只接受 `keyword`，非法值返回 422。语义搜索的内部实现仍保留，但搜索页面已隐藏入口。
- 排序支持 `newest`、`oldest`、既有 `name`、`largest`。创建时间相同时使用同方向 ID 排序；列表默认 `limit=60`，页面使用 24；要求 `1 <= limit <= 200`、`0 <= offset <= 2^63-1`，非法值返回 422。
- 列表与导出共用集合参数校验，要求 `1 <= collection_id <= 2^63-1`，超出 SQLite 整数范围返回 422；范围内不存在的集合返回空结果。
- 沿用原 FTS5 搜索字段、`porter unicode61` 分词与安全前缀查询。空白关键词不限制；非空但仅含标点的输入返回空结果。
- 两个接口均要求 `Authorization: Bearer <token>`；CSV 忽略 `limit/offset`，导出所有匹配项。固定路由注册在素材 UUID 路由前。
- CSV 使用 UTF-8 BOM，列顺序为 `uuid,title,description,filename,media_type,tags,created_at`。`tags` 为按名称排序的 JSON 数组字符串，空数组为 `[]`。使用标准库 CSV 编码，保留字段原文和换行，支持中文、逗号及双引号；空结果只有表头。

## 自动化验证与实测结果

API/回归测试：

```powershell
$env:KEEPSTACK_AI_ENABLED='false'
$env:KEEPSTACK_OCR_ENABLED='false'
.venv/Scripts/python.exe -m pytest -q
node --check web/app.js
git diff --check
```

测试使用临时目录和数据库，不读取实际 `data/`。新增用例覆盖条件交集、大小写/重复/冲突/不存在标签、特殊关键词、非法分页、active 限制、53 条结果跨页及相同时间、鉴权、CSV 顺序/全量/转义/空表头，以及通过真实编辑和标签 API 验证索引更新。补充回归覆盖含 `/`、连续 `/` 和中文的标签添加/删除及查询/导出同步，以及列表和导出的集合 ID 上下界与超范围参数。

真实浏览器验收（额外的可选测试依赖，无需用于运行应用）：

```powershell
python -m uv pip install --python .venv/Scripts/python.exe -e '.[dev,browser]'
.venv/Scripts/python.exe -m playwright install chromium
.venv/Scripts/python.exe tests/browser_acceptance.py
```

Linux 使用 `.venv/bin/python`，安装浏览器可执行 `python -m playwright install --with-deps chromium`。也可设置 `BROWSER_EXECUTABLE` 使用已安装的 Chrome/Edge。脚本自动生成隔离数据、启动临时服务，并在结束时停止服务；加 `--screenshot` 会更新交付截图。

2026-10-30 本地实测：

- 修改前基线：Python 3.10.11，**22 passed / 2 skipped**。
- 首轮实现后：Python 3.12.7，**42 passed / 2 skipped**；Python 3.10 的结果同样通过。
- 边界问题修复后：Python 3.12.7 和 Python 3.10.11 均为 **55 passed / 2 skipped**。
- 浏览器：Windows Chrome 无头模式，组合筛选、三页遍历、第三页全量下载及顺序比对、含逗号的完整标签、延迟旧请求、查询/下载 500 错误与重试、详情编辑及含 `/` 和中文的标签添加/删除、空结果 CSV、清除条件、390px 小屏无横向溢出均通过；无页面脚本异常。
- 演示生成器连续运行两次后，独立数据库仍为 **60 条素材**。
- `node --check web/app.js`、`git diff --check` 通过。
- 两个跳过项是原有可选 CLIP 模型与 Tesseract 测试，本机未安装；保留原有 startup 的弃用提示及测试依赖弃用提示，不影响功能结果。
- GitHub Actions **CI 已通过**：提交 `fb5a8de9ce49257f935cdf280a747dc0f9c3c431` 对应的 Python 3.10、Python 3.12 API 测试和 Chromium 浏览器验收三个任务均成功，见 [CI 运行记录](https://github.com/Szk1222/AI-Engineer-Quiz/actions/runs/41056217376)。后续仅更新本文的 CI 状态说明；最新运行结果见 [Actions 页面](https://github.com/Szk1222/AI-Engineer-Quiz/actions)。

## 主要取舍与限制

- 使用 EXISTS/NOT EXISTS 构造参数化查询，避免标签关联导致重复计数；列表、总数和导出共用查询构造器，不新增数据库表或迁移。
- 前端在条件变化时立即递增请求序号并取消旧请求，覆盖输入防抖期间的旧响应；加载期间禁用翻页/导出，失败可重试。编辑造成末页变空时自动回到最后有效页。
- CSV 同步生成并驻留内存，适合本题中小规模清单；没有大规模压力测试，不提供异步任务或跨请求快照。列表与导出的一致性以数据不变为前提。
- 演示生成器逐条导入、可重复恢复，优先保证清晰与可复现；不是生产批量导入工具。
- 不实现模型接入、新权限体系、实时订阅或线上部署。本题必做功能无已知未完成项；范围外既有问题未做全面审计。
- 开发使用 AI 编程工具完成实现、验证与文档整理；未修改原题要求和 MIT 许可。
