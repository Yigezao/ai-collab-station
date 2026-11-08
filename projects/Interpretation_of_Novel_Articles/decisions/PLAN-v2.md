# 方案 v2：UI 易用性改造 v2：浏览选择、操作即选择、产出与检索透明

# 方案 v2：UI 易用性改造——浏览选择、操作即选择、产出与检索透明

> 项目：Interpretation_of_Novel_Articles（小说解说生成）
> 提交：MGR；依据：用户追加需求 E000389、reopen E000390、第 3 轮讨论 E000393/E000398、决策 E000400
> 本方案需用户批准后才创建任务与实施。

## 0. 目标与用户可见结果

解决用户原话四点：

1. **直接浏览选择文件**：每个路径字段都有“浏览选择”按钮（目录树 + 盘符/常用位置快捷入口），选中后回填绝对路径，也可手动输入。
2. **想要什么操作直接选择（给出说明）**：每步先选操作卡片，卡片固定展示“做什么 / 必填与可选 / 默认值从哪来 / 会写到哪里 / 常见失败码”。
3. **尽量不写代码**：第 1 步参数串拆成结构化字段；第 4 步用绑定表格直接生成 `--binding`；所有路径可浏览；高级参数折叠但保留。
4. **接口留好、清晰明了；每个产出地址、具体检索什么都写好**：`/api/spec` 等接口冻结；产物卡片给绝对路径与来源（模块 JSON / 库布局推导）；Gutenberg 检索口径、退出码表、preflight 面板常驻页面并从配置读取。

## 1. 范围

**实施范围（仅 UI 层与文档）**

- 新增 `ui/specs.py`、`ui/fsbrowse.py`、`ui/static/index.html`、`ui/static/app.js`、`ui/static/app.css`。
- 修改 `ui/server.py`、`ui/runner.py`、`ui/cli.py`、`README.md`。
- 新增/扩展 `tests/ui/test_specs.py`、`test_fsbrowse.py`、`test_static_page.py`、`test_runner.py`、`test_server.py`、`tests/integration/test_ui_e2e.py`。

**不改（硬边界）**

- `source/`、`segmentation/`、`script/`、`audio/`、`video/`、`common/` 的代码、CLI 参数、JSON 字段、manifest 与退出码。
- 不引入第三方依赖（仍仅标准库 + 既有 `requests`）；不新增联网，除沿用现有 Gutenberg 检索。
- 不删除/覆盖用户 `data/library` 与任何用户资料；测试只用临时目录。
- 第 3 步仍 mock；第 4 步不自动配音；不宣称真实 ffmpeg 编码已验证。

## 2. 关键决策（详见决策 E000400）

| # | 决策 |
|---|---|
| D1 | 选型 A；B（上传复制）、C（系统对话框）不引入 |
| D2 | 全盘只读浏览 + 6 条硬约束；不做白名单；README 写明“无认证 loopback，本机进程均可调用” |
| D3 | 页面拆 `ui/static/` 三文件 + 固定路由 + token 替换；静态文件缺失 500 明确报错 |
| D4 | 第 4 步绑定表格：读选中的 `index.json` 列 seg_id，直接构造重复 `--binding`；失败退回文件方式 |
| D5 | 产物路径逐项标注“模块 JSON / 库布局推导”；hash 目录只显示父目录+规则，运行后回填实际路径 |
| D6 | 库位置统一为单一 UI 字段，映射第 1 步 `--library` 与第 2 步 `--library-dir` |
| D7 | `specs.py` 单一数据源，`runner.build_command` spec 驱动；`--source-args` 保留但 deprecated，与结构化字段冲突即 `INVALID_INPUT` |
| D8 | 单子进程锁，并发 `BUSY`；超时 300s/600s 映射 `failed`；UI 永不自动加 `--force` |

## 3. 冻结接口契约（并行开发依据）

### 3.1 `ui/specs.py` 数据结构

```text
STEPS = [
  {step: int, module: str, title: str, operations: [
     {id, name, summary, fields: [FIELD...], artifacts: [...], notes: [...]} ]}
]
FIELD = {key, label, kind, required, default, default_source, choices, accept,
         help, cli: {mode: "flag|positional|repeat", name}, target_next}
kind = text | int | float | bool | choice | file | files | dir | seg_bindings
default_source = constant | step_artifact:N | library | last_run | user
```

- 5 步 × 全部操作（第 1 步 6 个：import/search/download/variants/parse/select；第 2–5 步按现有能力）。
- 每个 `StepOptions` 字段必须能在 specs 找到映射；测试防漂移。
- 范围校验写入 specs：`limit 1..50`、`fps > 0`、`target_chars`/`chapters` 二选一、`confirm_binding` 条件必填。

### 3.2 `ui/runner.py`

- `StepOptions` 增加结构化字段：`import_paths`、`title`、`author`、`work_id`、`language`、`limit`、`gutenberg_id`、`format`、`confirm_binding`、`seg_bindings`、`index_file`、`mode`、`images_dir`、`materials_dir`、`duration_strategy`、`in_point`、`out_point`、`resolution`、`fps`、`library_dir`、`confirm_overwrite` 等（以 specs 为准）。
- `build_command(step, options)` 按 `cli.mode` 生成 argv：`flag`、`positional`（search 的 queries）、`repeat`（`--path` / `--binding`）；仅保留少量特例（第 4 步无绑定返回 `None`、第 2 步模式互斥）。
- 结构化字段与 `source_args` 同时出现 → `INVALID_INPUT`；`source_args` 在 CLI 帮助标 deprecated。
- 新增 `extract_artifacts(step, payload)`：返回 `(label, path, kind, source)`，`source` 取 `module_json` 或 `layout_inferred`。
- 覆盖保护：`confirm_overwrite` 非 true 时绝不放 `--force`。

### 3.3 `ui/fsbrowse.py`

- `resolve(path) -> abs Path`：展开 `~` → `resolve()` → 校验绝对/存在/类型；失败抛结构化错误（`INVALID_INPUT` / `NOT_FOUND` / `NOT_A_DIRECTORY` / `PERMISSION_DENIED`）。
- `list_roots()`：盘符（`os.listdrives()`，3.12+；否则 A–Z `exists()` 探测并缓存 30s）+ 快捷入口（主目录、桌面、下载、项目根、`data/library`）。
- `list_dir(path, kind, accept, show_hidden)`：单层、上限 2000、不读内容；返回 `cwd/parent/exists/is_dir/writable/truncated/warnings/entries`；`entries[]` 含 `name/path/kind/size/mtime/hidden/ext`，目录在前按名排序；`accept` 只供前端灰显；junction 不展开，单条 stat 失败跳过进 `warnings`。
- `make_dir(parent, name)`：只建一层、不覆盖、不删除、不改名。
- `reveal_target(path)`：只接受已存在目录（文件取父目录）；不可用时返回 `unsupported`。

### 3.4 `ui/server.py` 路由（全部 loopback）

```text
GET  /                        → render_page()（读模板 + token 替换）
GET  /static/app.js|app.css   → 固定映射，无用户路径
GET  /api/status              → 现有
GET  /api/spec                → STEPS
GET  /api/fs/roots            → 盘符 + 快捷入口 + 默认库绝对路径
GET  /api/fs/list             → 参数 path/kind/accept/show_hidden
POST /api/fs/mkdir            → {parent,name}
GET  /api/works               → work/variant 下拉（扫 library）
GET  /api/artifacts?step=N    → 候选 index.json / audio_manifest / 目录（按 mtime）
GET  /api/preflight           → Python、库根可写、ffmpeg/ffprobe、项目根、库绝对路径
POST /api/preview             → {step,options}，不启动子进程
POST /api/step                → {step,options,confirm_overwrite}，返回 artifacts/next_inputs
POST /api/reveal              → {path}
```

- 模板 token：`%%DISCLAIMER%%`、`%%EXIT_CODES%%`、`%%SEARCH_INFO%%`、`%%PROJECT_ROOT%%`、`%%LIBRARY_PATH%%`、`%%STEP_NAV%%`；禁止 `str.format`。
- 安全：Host 校验（带端口）、`X-UI-Request: 1`、JSON Content-Type、Origin 同源校验、无 CORS 头；违规 400/403。
- 运行：模块子进程单锁，冲突 `BUSY`；超时映射 `failed` 并说明“模块执行超时”。

### 3.5 前端（`ui/static/`）

- 无构建链、零依赖；JS 运行时 `fetch("/api/spec")` 渲染表单，不复制 specs。
- 每步三块：操作卡片 → 输入区（浏览 / 手动双入口，第 1 步 import 多选）→ 产出区（预览 / 运行结果 / 产物卡片 / 下一步预填）。
- 专用 UI：目录浏览弹窗（roots/快捷入口/隐藏项开关）、第 4 步绑定表格、产物卡片（路径/类型/exists/来源/打开文件夹/复制）、preflight 面板、检索口径与退出码常驻。
- 第 5 步 `duration_strategy` 必须带中文说明；`in_point`/`out_point` 仅在 `in_out` 显示；第 3 步 mock 徽标与版权提示。
- 运行中禁用按钮；`BUSY`/失败/未验证状态不得显示为 `ok`；请求体只有结构化 JSON，不出现参数串。

### 3.6 产物路径映射（页面与测试共用）

| 步骤/操作 | 来源 | 字段 |
|---|---|---|
| import | 模块 JSON | `variants[].variant_dir`、`variants[].manifest` |
| search | 无产物 | 结果列表 `groups[].results[]` |
| download | 模块 JSON | `variant_id`、`variant_dir`、`manifest` |
| variants | **布局推导** | `<library>/<work_id>/comparison.json` |
| parse | 模块 JSON | `chapters_file` |
| select | 模块 JSON | `selection` |
| 2 segment | 模块 JSON | `output_dir`、`index` |
| 3 generate | 模块 JSON（标 mock） | `out_dir`、`files.script`、`files.lines`、`files.manifest` |
| 4 import | 模块 JSON | `manifest`、`artifact_manifest`、`items[]` |
| 5 render | 模块 JSON | `manifest`、`output`、`timeline` |

## 4. 任务拆分、owner 与依赖

| 任务 | 内容 | owner | reviewer | 依赖 |
|---|---|---|---|---|
| T015 | `ui/specs.py` + spec 驱动 `runner`/`cli` 结构化字段 | S01 | S02 | 无 |
| T016 | `ui/fsbrowse.py` + server 路由/安全/预览/产物提取 + 静态服务与 token 渲染 | S02 | S01 | T015（specs 结构）；fsbrowse 部分可先行 |
| T017 | `ui/static/` 三文件前端（操作卡片/浏览弹窗/绑定表格/产物卡片/说明常驻） | S03 | S02 | 契约冻结于本方案；联调依赖 T016 |
| T018 | 五步免手填 E2E + README UI v2 文档 + 独立复核 | S04 | S01 | T015、T016、T017 |

### 各任务验收标准（摘要，任务卡为准）

**T015**

1. specs 覆盖 5 步全部操作，字段齐全（kind/required/default_source/cli.mode/repeat/positional/范围），与 `StepOptions` 无漂移（测试断言）。
2. 六种 source 操作 argv 正确；中文/空格路径不经 shell；search 多书名用位置参数；import 多文件与第 4 步绑定用重复参数。
3. 结构化字段与 `--source-args` 同时出现返回 `INVALID_INPUT`；`--source-args` 标注 deprecated。
4. `confirm_overwrite` 未 true 时命令行绝不出现 `--force`。
5. 互斥/范围校验（chapters vs target_chars、limit、fps）有测试。
6. `tests/ui/test_specs.py`、`test_runner.py` 通过；既有断言不得删除。
7. 模块 CLI 零改动（契约测试通过）。

**T016**

1. `fsbrowse.resolve/list_dir` 满足 6 条硬约束（绝对/存在/单层/2000 上限/隐藏/不读内容/结构化错误/junction 不展开/warnings）。
2. roots 含盘符与 7 类快捷入口；盘符探测有 3.12+ 与回退分支。
3. mkdir 只建一层且不覆盖；reveal 仅目录、`unsupported` 分支。
4. 全部新路由可用；`/api/preview` 可精确 vs 仅规则两类口径正确；`/api/works`、`/api/artifacts`、`/api/preflight` 有测试。
5. `extract_artifacts` 输出绝对路径 + 来源标注（含 variants 的布局推导）。
6. 安全测试：缺 `X-UI-Request`、错 Host、跨源 Origin、非 JSON Content-Type 被拒；无 CORS 头。
7. 并发第二个请求 `BUSY`；超时映射 `failed`。
8. `render_page()` token 替换生效；静态文件缺失返回 500 + 明确信息；固定路由不可穿越。
9. `test_fsbrowse.py`、`test_server.py` 通过；页面文案断言更新而非删除。

**T017**

1. 三文件静态页面可被服务；无构建链、零依赖。
2. 每步操作卡片五要素齐全（做什么/必填可选/默认来源/写到哪里/失败码）。
3. 所有路径字段有浏览 + 手动输入；import 多选；浏览弹窗含 roots/快捷入口/隐藏项开关，返回绝对路径。
4. 第 4 步绑定表格从选中 index.json 生成，提交构造重复 `--binding`，用户不写 json。
5. 产物卡片含标签/绝对路径/类型/exists/来源（模块 JSON 或布局推导）/打开文件夹/复制；下一步只填空白并标注来源步骤。
6. 检索口径、退出码表、preflight 常驻且值来自接口/配置（不在页面写死配置副本）。
7. 第 5 步策略中文说明与条件字段；第 3 步 mock 徽标；版权提示。
8. 运行中禁用、`BUSY`/失败/未验证不显示为 ok；请求仅结构化 JSON。
9. `tests/ui/test_static_page.py` 断言关键文案/元素与静态路由；不实现第二份业务逻辑。

**T018**

1. 五步免手填 E2E：运行时生成 UTF-8 中文 3 章 txt（中文+空格文件名）；第 2 步 `--chapters 1`；第 3 步 mock；第 4 步用标准库 `wave` 生成 8kHz/单声道/16bit/0.2s 静音 wav 并逐个绑定；第 5 步 images + base64 1×1 PNG；全程临时库，不碰用户数据。
2. 断言：全程仅结构化字段构造 argv，无参数串；产物路径来自模块 JSON 或明确标注布局推导；失败/未验证不显示 ok。
3. ffmpeg 分支：无 ffmpeg 断言退出码 3 / `DEPENDENCY_MISSING` 且 UI 不判 done；有 ffmpeg 才跑真实渲染并断言 `final.mp4`。报告须如实说明本机实际走哪条分支。
4. README 增加 UI v2 小节：接口清单、每步/每操作产物绝对路径规则与来源、Gutenberg 检索口径与空结果说明、退出码表、安全边界（无认证 loopback）、已知限制（hash 目录运行后确定、真实编码未验证）。
5. 全量测试通过；报告精确命令与结果；对 T015–T017 交付逐条独立复核，发现问题记录并退回，不代改。

## 5. 人员安排

- 方案批准后启用 **S03**（前端）与 **S04**（E2E/文档/独立复核），团队 4/4，在默认上限内，无需用户额外批准。
- 理由：4 条相对独立工作流（specs/runner、fsbrowse/server、静态前端、E2E+文档+复核），且需要独立复核。
- 全部任务验收后，S03/S04 无未完成任务时停用回收。

## 6. 实施顺序

1. 批准 → 启用 S03/S04 → 建 T015–T018（按依赖）。
2. T015 先行（S01）；T016 的 fsbrowse 部分可与 T015 并行，server 集成在 specs 合入后进行。
3. T017 依据冻结契约开发，T016 静态路由就绪后联调。
4. T018 在 T015–T017 交付后跑 E2E 与文档，独立复核；发现问题退回 owner 修正。
5. 全部 `review_result=pass` 后由总管验收、`finish` 并汇报。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| 并行开发导致接口漂移 | 本方案冻结 specs/路由/token/产物映射；reviewer 按契约复核；发现漂移退回修正 |
| 全盘浏览扩大本机信息面 | 6 条硬约束 + Host/Origin/自定义头/无 CORS；README 明示残余风险；出现跨站迹象先加启动 token |
| hash 目录无法精确预览 | 只显示父目录+规则+运行后确定；每步可显式指定输出目录；严禁复制 hash 逻辑 |
| 本机无 ffmpeg | 测试按 `shutil.which` 分支；无 ffmpeg 断言依赖缺失；不宣称真实编码已验证 |
| 静态文件缺失/路径穿越 | 固定映射表 + 缺失 500 明确报错；测试覆盖 |
| 页面复杂度上升 | 拆三文件；JS 从 `/api/spec` 渲染，不重复业务逻辑；既有文案断言保留 |
| 覆盖用户产物 | UI 永不自动 `--force`；`confirm_overwrite` 服务端校验；默认输出目录必须为空或不存在 |
| 并发写同一输出目录 | 单子进程锁 + `BUSY`；前端运行中禁用 |

## 8. 交付物与验收证据

- 代码与测试：上述新增/修改文件；全量测试命令与结果。
- 文档：`README.md` UI v2 小节（接口、产物地址表、检索口径、退出码、安全边界、已知限制）。
- 协作记录：T015–T018 任务卡、handoff、review_result；总管 acceptance 与收工报告。
- 验证证据须可复现：测试命令、E2E fixture 生成方式、ffmpeg 分支实际结果、未验证项单列。

## 9. 不变量

- 模块 CLI/JSON/manifest/退出码零改动；零新依赖；只绑 `127.0.0.1`。
- 第 3 步 mock；第 4 步人工绑定；不自动配音；不宣称端到端真实编码通过。
- 不覆盖/删除用户资料；不把猜测写成已核实；测试只用临时目录。

## 10. 回滚

改动集中在 UI 层（`ui/` 与 README、tests），模块与用户产物不受影响。若 UI v2 需回退，恢复 `ui/server.py`、`ui/runner.py`、`ui/cli.py` 至 v1 交付版本并移除新增 UI 文件即可；协作记录与任务历史保留，不删除。
