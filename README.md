# AI 协作工作站

一个总管 AI + 多个员工 AI，通过共享文件夹协作；本地网页 UI 是人机接口。

- **工作站只放协作记录**：本项目文件夹里只存事件、任务、收件箱、日志；**你的项目代码放在你自己建的项目文件夹里**（一般在 `F:\Desktop\Study\#Project\<项目名>`）。
- **总管也是 AI**（会话 `MGR`），只是角色是“总管”，负责接需求、组织讨论、收敛方案、拆任务、监督、验收，并作为你唯一的对接窗口。
- **员工也是 AI**（默认按单人推进：总管会停用 `S02`；`S02` 按需在 2 人内启用；`S03`/`S04` 需批准），按任务卡在你的项目工作区里实施，不直接找你。
- **默认编制**：总管 Claude（当前配置，可改 Codex），员工 Claude（DeepSeek）；**默认单人，员工上限 2 人**，超过 2 人需要你批准。
- **文件是唯一事实来源**：需求、讨论、投票、方案、批准、任务、交付、验收都落盘，事件只追加。
- **UI 负责**：按真实顺序展示事件/讨论/任务/进度；让你和总管对话；让你批准或驳回方案；在浏览器里直接新建项目。

`方案.md` 是原始设计稿，已保留；实际执行以 `protocol.md` 为准。旧的 `../_ai_collab` 只是参考，本工作站不复用它。

![工作站界面（深色）](docs/ui-dark.png)

![工作站界面（浅色）](docs/ui-light.png)

---

## 1. 日常用法：双击启动，在浏览器里建项目

**不用开多个终端，也不用敲项目名。**

1. **先建好项目文件夹**：在 `F:\Desktop\Study\#Project` 下建一个文件夹，比如 `我的项目`。你的代码/文档都放这里。
2. 双击根目录的 **`启动.bat`**（或 ASCII 的 **`start.bat`**；会打开一个命令行窗口和浏览器；这个窗口别关）。
3. 浏览器里点右上角 **＋ 新建项目**。
4. 填三样：**项目名**、**项目文件夹的完整路径**（如 `F:\Desktop\Study\#Project\我的项目`）、**要做什么**；点 **创建并交给总管**。
5. 启动器会自动为这个项目拉起总管和员工；总管收到需求后开始讨论。员工只在你的项目文件夹里干活。
6. 之后你只在右边和总管对话、批准方案。

说明：

- 项目 ID 自动取自文件夹名（中文可以）；工作记录存在工作站的 `projects/<项目ID>/`，不会写进你的项目文件夹。
- 项目文件夹必须**已经存在**，而且不能放在工作站目录里面。
- 同一时间只有一个“活动项目”在跑；在顶部下拉框切换项目，启动器会停掉旧项目的 worker、拉起新项目的 worker。
- UI 会把你选中的项目写到 `run/active_project.json`，worker 跟随它（`start_all` 不带 `--project` 时就是这种跟随模式）。
- 关掉启动器窗口 = 停止总管和员工；文件记录都还在，重开会继续。
- 想看脚本演练：顶部切到 `demo`（50+ 条事件，不调用模型）；切到 `demo-approval` 看“等待批准 → 已批准”的历史（没有 worker 时批准只会排队）。

### 高级：固定项目启动（可选）

要脚本化、或同时开多个项目窗口时，可以跳过 UI 新建：

```bash
python station.py init --project myproj --title "我的项目" --workspace "F:\Desktop\Study\#Project\我的项目" --text "需求原文……"
python run\start_all.py --project myproj --open
```

固定模式会忽略 UI 里选中的项目，只跑 `myproj`。`--open` 只是打开浏览器。

### 只启动 UI / 只启动 worker

```bash
python ui\server.py --open                    # 只看，不唤醒任何人；新建项目也只建文件
python agents\worker.py --project myproj --session MGR --once   # 人工触发一次
python run\start_all.py --project myproj --no-ui                # 只跑 worker
```

停止：在启动器窗口按 `Ctrl+C`，或另开窗口运行 `python run\stop_all.py`。

### 切换项目 / 暂停 / 回来继续

- **切换**：顶部下拉框选另一个项目。UI 会从文件重新加载那个项目的完整记录：讨论时间线、方案、任务、聊天、决策、验收，一条不少。
- **暂停**：没有单独的“暂停”按钮；**切到别的项目**就会停掉当前项目的 worker，这就是暂停。记录全部留在磁盘，关掉工作站再开也还在。
- **继续**：切回那个项目，worker 重新上线；之前排队没处理的消息会接着处理。总管每次唤醒都会重新读文件状态，不依赖会话记忆。
- **全部停止**：关启动器窗口，或 `python run\stop_all.py`。
- **完成后回来**：项目已“完成”时，直接在右边提新需求即可。阶段会回到讨论，总管会 `reopen` 重新打开；小优化可在原方案内直接做，范围变了会重新提交方案等你批准。
- 同一时间只有一个活动项目；切换会强制停旧 worker，正在处理的那条消息可能留在 `processing/`，需要人工移回收件箱重试。

---

## 2. 它是怎么“醒”的（重要）

**文件系统不会唤醒 CLI。** 新消息放进收件箱后，必须有一个 worker 进程去发现并调用模型：

```text
UI 用户消息 ─┐
员工发言 ────┼─> inbox/<session>/*.json ─> worker.py 轮询
总管消息 ────┘        │
                      ├─ 原子领取到 processing/
                      ├─ 调 claude -p 或 codex exec（一次模型调用）
                      ├─ 会话用站点绝对路径调 station.py 写事件/任务/消息
                      └─ 成功 -> processed/；失败 -> 重试 3 次 -> failed/
```

- **没有新消息就不调用模型**，空闲轮询不消耗 token。
- **员工的工作目录是项目工作区**（`meta.json` 的 `workspace`）；站点根目录通过 `--add-dir` 加入，用于执行 `station.py` 和读写协作记录。提示词里会给出 `station.py` 的绝对路径。
- 启动器不带 `--project` 时是“跟随浏览器”模式：读 `run/active_project.json`，切换项目会停掉旧项目的 worker、拉起新项目的 worker。
- 每个会话一个 worker；心跳写 `projects/<p>/logs/sessions/<sid>.json`，90 秒没心跳 UI 显示离线。离线期间消息照常排队。
- 同一个会话同一项目只允许一个 worker（`*.worker.lock`），防止重复领取。
- UI 每 2 秒轮询 `/api/state` 刷新；这是轮询，不是实时推送。关掉 UI 不影响文件工作流。
- 如果 worker 没跑，只能人工触发：`python agents/worker.py ... --once`。不要宣称“已经自动监听”。
- **身份绑定**：worker 启动的 CLI 带着 `AI_STATION_SESSION` / `AI_STATION_PROJECT`，`station.py` 会拒绝跨会话写入和用户专属操作（init/request/approve）。总管不能冒用员工身份写发言，员工也不能冒充用户批准。
- **总管一次只推进一步，但必须推进到有人接手**：开场后立刻分配第一位发言人再停；收到发言后分配下一位或提交方案；不允许停在“轮次结束、无人发言”的卡死状态（UI 会显示“讨论停滞”）。

### 本机实测（2026-10-23）

| 项目 | 结果 |
|---|---|
| 单元测试（并发事件、批准闸门、任务状态、消息领取、身份绑定、员工上限/增减、工作区校验、完成后重开、依赖闸门/改派） | 17/17 通过 |
| 脚本演练 `demo` | 53 事件（现有演示），9 项校验全过 |
| Claude 员工 `S01` 真实唤醒 | 通过，约 9–18 秒写入事件 |
| Codex 引擎真实唤醒（S02 临时指定） | 通过，约 32–43 秒写入事件 |
| Claude 总管 `MGR` 真实回合 | 通过，10.2 秒写入事件 |
| **Codex 总管 `MGR` 真实回合**（默认配置） | 通过，100.9 秒完成“开场 + 分配 S01”并停下 |
| UI 接口端到端 | 19 项全过 |
| 真实短链路（总管开场 → 分配 S01 → S01 发言） | 通过；总管 35.5 秒后停在“等 S01”，S01 75.4 秒写入提案；无代跑 |
| 员工上限策略 | 默认 2；第 3 人起需 `approve-staff`；超限启动/启用被拒绝 |
| 员工编制（2 人上限） | S03/S04 默认停用；`staff-enable S03` 在未批准时被拒绝；批准后可启用并写入项目时间线 |
| 总管自主判断用人（真实，旧默认 4 时） | 通过；三条并行工作流的需求下，自己启用 S03、写“编制变更”事件并 chat 说明，45.8 秒；**现默认 2，需先批准** |
| 浏览器建项目 + 启动器跟随 | 通过；无项目参数启动，设置活动项目后自动起 3 个 worker；切换项目会自动停旧起新 |
| 项目工作区隔离 | 通过；Codex 总管 48.8 秒、Claude 员工 49.6 秒都在外部工作区运行，工作区新建文件 0 个 |
| 完成后追加需求 | 通过；用户消息隐式回到讨论，`reopen` 留痕，v2 重新走用户批准 |

复现：

```bash
python -m unittest discover -s tests -v
python scripts\rehearse.py                 # 已存在 demo 时不会覆盖
python scripts\smoke_wake.py --session S01                  # Claude 员工，会消耗 token
python scripts\smoke_wake.py --session S02 --engine codex   # Codex 引擎
python scripts\smoke_wake.py --session MGR                  # 默认走配置（Codex 总管）
python scripts\ui_smoke.py
```

---

## 3. 三个区域

| 区域 | 内容 |
|---|---|
| 左栏 | 当前发言/阶段、会话在线状态与待办数、任务与验收进度、方案状态、项目元数据 |
| 中间 | 完整事件时间线：发言、投票、批准、任务状态、阻塞、拉会、修订、交付、审查、验收；每条可展开全文 |
| 右栏 | 待批准方案（含备注、批准/驳回）、与总管的对话 |

事件按 `seq` 排序，不依赖时间戳；讨论历史不会被“当前状态”覆盖。

---

## 4. 配置

改 `config/station.json`，重启 worker/UI 生效。

### 默认编制（2026-10-23 起）

| 会话 | 名字 | 角色 | 默认引擎 | 默认启用 |
|---|---|---|---|---|
| `MGR` | 总管 | manager | **Claude**（当前配置，可改 Codex） | 是 |
| `S01` | 员工A | worker | **Claude**（DeepSeek） | 是 |
| `S02` | 员工B | worker | **Claude**（DeepSeek） | 是（默认可用；总管默认按单人推进，按需停/启） |
| `S03` | 员工C | worker | Claude | 否（超过 2 人需批准后启用） |
| `S04` | 员工D | worker | Claude | 否（超过 2 人需批准后启用） |

- 总管当前是 Claude；想换 Codex，把 `MGR.engine` 改成 `"codex"` 重启即可。
- 员工默认 Claude（DeepSeek）；**尽量不改**。
- **默认按单人推进（总管会停用 S02）**；S02 只有在 >=2 条真正独立、未阻塞的工作流时才启用。**员工上限 2 人**；要上第 3/4 人，必须先由你运行 `approve-staff` 批准。

```json
{
  "ui": {"host": "127.0.0.1", "port": 8877},
  "default_project": "demo",
  "workspace_roots": [],
  "staffing": {"default_max_employees": 2},
  "engines": {
    "claude": {"bin": "claude", "permission_mode": "acceptEdits", "allowed_tools": ["Read","Write","Edit","Glob","Grep","Bash","PowerShell"]},
    "codex":  {"bin": "codex", "sandbox": "danger-full-access"}
  },
  "sessions": {
    "MGR": {"name":"总管","role":"manager","engine":"claude","enabled":true,"timeout_sec":900},
    "S01": {"name":"员工A","role":"worker","engine":"claude","enabled":true,"timeout_sec":1200},
    "S02": {"name":"员工B","role":"worker","engine":"claude","enabled":true,"timeout_sec":1200},
    "S03": {"name":"员工C","role":"worker","engine":"claude","enabled":false,"timeout_sec":1200},
    "S04": {"name":"员工D","role":"worker","engine":"claude","enabled":false,"timeout_sec":1200}
  },
  "worker": {"poll_seconds": 3, "heartbeat_seconds": 10, "max_attempts": 3, "offline_after_seconds": 90}
}
```

### 员工数量与上限

- **默认按单人推进**：总管接需求时会停用 S02；只有 >=2 条真正独立、未阻塞、接口已冻结的工作流，才会在 2 人内启用 S02；UI/单模块/小项目保持单人。
- **要启用第 3 人（S03）或第 4 人（S04），必须先由你批准**。
- 总管发现 2 人仍排队/审查积压、确实需要加人时，会先向你说明理由，请你运行：

```bash
python station.py staff                          # 看当前员工数/上限
python station.py approve-staff --max-employees 3 --note "当前项目需要第3人"
```

- 你批准后，总管才能：
  `python station.py staff-enable --project P --target S03 --note "用户已批准"`
  S04 同理；没有批准时 `staff-enable` 会被拒绝。
- 降回 2 人：`python station.py staff-disable --project P --target S03`，然后 `revoke-staff` 撤销额外批准。
- 前提是 `start_all` 正在运行、且启动时**没有用 `--sessions` 限定**；它每 2 秒跟随配置，几秒内自动拉起/停止对应 worker。
- 停用前要确认没有未完成任务；正在处理消息时停用，当前消息可能留在 `processing/`，需要人工移回收件箱重试。
- 只有 `start_all` 拉起的 worker 会被自动停掉；你自己手动启动的 worker 需要自己结束。
- 超过上限时，`start_all` / `worker` / `staff-enable` 都会拒绝，并提示你先批准。

`approve-staff` 是用户专属操作：worker 里调用会被身份绑定拒绝；`staff-enable`/`staff-disable` 只允许总管角色或用户调用，普通员工调用会被拒绝。

### 引擎

- `claude`：调用 `claude -p`，支持 `--append-system-prompt` 角色说明、`--allowedTools`。
- `codex`：调用 `codex exec`，提示词走 stdin；Windows 上需要 `danger-full-access`。
- 总管和员工可以混用；角色名称与引擎无关。总管 Codex 与 Claude 都已实测；当前配置是 **Claude**（额度原因从 Codex 切过来）。
- Codex 的长回复曾出现流断连；worker 已去掉 `--json` 流式输出（最终回复仍从 `-o` 文件读），失败会自动重试。

### 权限

默认给 worker 的 Claude 会话完整 `PowerShell`/`Bash` 与文件编辑权限，因为员工要真正实现任务。`allowed_tools` 可按会话追加更细的规则（会话级 `allowed_tools` 会追加到引擎默认列表）。

**这不是沙箱。** 只在可信工程目录运行；重要资料先备份。UI 只监听 `127.0.0.1`，不要对外暴露。

### 项目工作区与额外目录

- 每个项目在创建时记录 `meta.json` 的 `workspace`（你的项目文件夹）。员工进程以它为**当前工作目录**，代码/文档都写在这里。
- 工作站自己的目录会作为 `--add-dir` 加入，只用于执行 `station.py`、读写协作记录；协议禁止在这里建项目文件。
- 任务卡里的 `scope` / `forbidden` 路径默认相对于项目工作区，写清楚。
- 如果某个项目还要访问另一个共享目录（例如公共组件库），把它加到 `workspace_roots`：

```json
"workspace_roots": ["F:\\Desktop\\Study\\Code\\shared_lib"]
```

  worker 会对每个项目额外加上 `--add-dir`。任务卡的 `scope` / `forbidden` 仍然要写清楚。
- 工作区必须已经存在，且不能放在工作站目录内；同一个文件夹只能对应一个项目。工作区被移动/删除后，worker 会报错重试，不会退回工作站目录。

---

## 5. 目录

```text
station.py                 CLI：唯一状态写入口
stationlib/                存储（原子写/锁/事件序号）、状态推导、业务动作
agents/worker.py           文件驱动唤醒器（Claude/Codex）
agents/prompts.py          角色 + 状态 + 消息 → 提示词
ui/server.py               本地 UI 与 JSON API
ui/index.html|app.js|style.css
启动.bat / start.bat       双击启动 UI + 跟随模式的总管/员工（内容相同）
config/station.json        会话、引擎、权限、端口、员工编制
config/staff_approval.json 用户批准的超编上限（只在批准后存在）
config/roles/*.md          总管/员工/审查角色说明
logs/staff_changes.jsonl   员工增减审计（谁、何时、启停谁）
templates/                 需求、方案、决策、任务、交付模板
tests/test_station.py      单元测试
scripts/rehearse.py        脚本演练（不调模型）
scripts/smoke_wake.py      真实唤醒自检（调模型）
scripts/ui_smoke.py        UI 接口自检
projects/<id>/             该项目的协作记录（事件/消息/任务/交付/日志），不含项目代码
projects/<id>/meta.json    项目名、workspace（项目文件夹路径）
run/start_all.py|.bat      启动 UI + worker（不带 --project = 跟随浏览器）
run/active_project.json    UI 当前选中的项目（启动器跟随它）
run/stop_all.py|.bat       停止
```

**项目代码不在这里。** 它在每个项目 `meta.json` 的 `workspace` 指向的文件夹里，通常是 `F:\Desktop\Study\#Project\<项目名>`。站点只知道路径，记录和代码分开存。

---

## 6. 诚实的能力边界

1. **不会自动醒来**：必须有 worker 进程；UI 会显示离线。没有魔法监听。
2. **总管是 AI**：它按 `config/roles/manager.md` 执行；它也会犯错、会需要重试。文件记录让它可被接管。
3. **演示数据是脚本生成的**：`demo`、`demo-approval` 用于展示与试用，不代表模型真实讨论质量。
4. **Codex 在 Windows 上无法用 `workspace-write`**：会降级为只读并拦截命令，因此配置成 `danger-full-access`。这是有风险的，见安全节。
5. **讨论顺序与身份只在 `station.py` 入口强制**：员工仍能用 Write 直接改文件（权限层面挡不住），也能绕过 `station.py` 手写事件文件。任务边界、事件审计和备份是第二道防线；不要把这个站点当成安全沙箱。
6. **总管需要多次唤醒才能推进一场讨论**：这是刻意的，防止一个回合内自问自答。代价是流程按“消息数”推进，不是一次跑完。
7. **长历史会被截断进提示词**：每次唤醒只带最近 30 条事件 + 任务/方案状态；完整历史仍在文件里。
8. **UI 是轮询**：最多 2 秒延迟；没有推送、没有多用户、没有登录。
9. **一个会话同一项目一个 worker**：跨项目可并行，但同一会话没有共享记忆；一切以文件为准。
10. **中断恢复**：消息停在 `processing/` 时可手工移回收件箱重试；worker 崩溃后用 `--once` 或重启。
11. **员工上限 2 是策略检查，不是系统权限**：`start_all` / `worker` / `staff-enable` 会拒绝超编，`approve-staff` 只能由用户运行；这防的是 AI 自行扩编，不是防你本人改配置。
12. **动态增减依赖启动器**：`start_all` 没跑，或用了 `--sessions` 限定，`staff-enable` 只会改配置，不会真的拉起 worker；需要重启或去掉 `--sessions`。
13. **总管在 2 人以内自主调配；超过 2 人必须由你批准**，`staff-enable` 会明确拒绝。
14. **默认引擎是配置，不是硬编码**：总管当前 Claude（可改 Codex），员工默认 Claude（DeepSeek）；可以换，但员工侧建议保持 Claude。
15. **跟随模式同一时间只跑一个活动项目**：切换项目会强制停掉旧项目的 worker；正在处理的消息可能留在 `processing/`，需要移回收件箱重试。要并行跑多个项目，用固定 `--project` 模式（注意 UI 端口冲突，第二个可加 `--no-ui`）。
16. **工作区隔离靠路径与提示，不是系统沙箱**：员工以项目工作区为 cwd；工作站目录也通过 `--add-dir` 加入（为了跑 `station.py`），协议禁止在那里建项目文件，但权限层面仍可写。
17. **工作区必须存在**：`meta.json` 的 `workspace` 指向的文件夹被移动/删除后，worker 会失败重试，不会退回工作站目录；旧演示项目没有工作区，仍在站点目录里跑。

---

## 7. 排障

| 现象 | 检查 |
|---|---|
| UI 里会话一直“未启动/离线” | worker 没跑，或心跳超过 90 秒；看 `projects/<p>/logs/sessions/<sid>.console.log` |
| `FileNotFoundError` 启动引擎 | `where claude` / `where codex`；Windows 上由 `engine_prefix()` 解析到 exe/node，避免 `.CMD` 破坏参数 |
| Claude 报 `requires approval` | 检查 `engines.claude.allowed_tools`；复合命令用完整工具名 `PowerShell`/`Bash` 更稳 |
| Codex 报 `blocked by policy` | `sandbox` 改 `danger-full-access`（本机已验证），并确认在可信目录 |
| 批准按钮不出现 | 当前没有 `plan_proposed` 待批；看 `python station.py state --project P` |
| 任务无法 `done` | 缺少 `review_result=pass`；先让审查者 `review` |
| 任务无法 `in_progress` | 方案未获用户批准 |
| 端口被占 | 改 `config/station.json` 的 `ui.port`，或 `python ui\server.py --port 8899` |
| 中文乱码 | 用 `.bat`（已设 UTF-8）或 `set PYTHONUTF8=1`；日志文件本身是 UTF-8 |
| 想重来演示 | 删除 `projects/demo`、`projects/demo-approval`，再跑 `python scripts\rehearse.py --with-pending`（会重建两个演示项目） |
| 启动时报“员工超限” | `python station.py staff` 看人数；把多余会话改 `enabled:false`，或 `approve-staff --max-employees N` |
| 新建项目报“工作区不能放在工作站目录内” | 换一个工作站目录之外的文件夹，比如 `F:\Desktop\Study\#Project\我的项目` |
| 项目工作区不存在/被移动 | 把文件夹放回原路径；或编辑 `projects/<项目>/meta.json` 的 `workspace` 后重启 worker |
| 同一个文件夹建第二个项目被拒 | 一个工作区只对应一个项目；开新项目就新建文件夹 |
| 员工说找不到项目文件 | 看 UI 项目信息里的“工作区”，以及 `projects/<项目>/meta.json` 的 `workspace` |
| 总管启用了 S03 但一直离线 | `start_all` 是否在跑？是否用了 `--sessions` 限定？看 `logs/sessions/S03.console.log`；再次 `staff-enable` 或重启启动器 |
| 浏览器新建项目后总管不动 | 启动器是否在跑？是否用了 `--project` 固定模式（固定模式会忽略 UI 选中的项目）？看 `run/active_project.json` |
| 切换项目后旧项目消息卡住 | 切换会强制停旧 worker；看 `inbox/<会话>/processing/`，把文件移回收件箱重试 |
| UI 显示“讨论停滞” | 总管没有安排下一位；在右边发一句话提醒它继续，或看 `state` 的当前发言人 |
| Codex 总管很慢/断流 | 已关闭 `--json` 流式输出并自动重试；仍失败时看 `logs/sessions/MGR.invocations.jsonl`，可临时把 `MGR.engine` 改回 `claude` |

---

## 8. 安全提醒

- 站点不含 API key；引擎凭据由本机 CLI 管理。
- 站点内不写密码、密钥、无关隐私。
- worker 有 shell 与文件写入权限；协议禁止删除资料、覆盖文件、发布内容、产生费用、改生产数据，但**协议不是沙箱**。
- `station.py` 会校验 worker 身份（`AI_STATION_SESSION`）：总管不能以员工身份写发言，员工不能以用户身份批准，worker 不能自行批准扩编；但这只约束走 `station.py` 的写入，不阻止直接改文件。
- 员工上限 2 人：超过时启动/启用检查会拒绝；批准记录在 `config/staff_approval.json`，由用户专属命令写入。
- 高影响/不可逆操作：总管必须先向你说明影响并取得确认，再关联到任务。
- 定期备份整个 `projects/`（事件、任务、决策、交付、日志）。

---

## 9. 与旧方案的关系

- `方案.md`：原始设计稿（保留，不改）。
- `protocol.md`：本实现的可执行协议。
- `../_ai_collab`：早先两个 AI 的文件协作，仅作参考，本工作站没有复用它。
