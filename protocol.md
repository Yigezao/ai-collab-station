# AI 协作工作站协议 v1.9

> 本文件是可执行细则；`方案.md` 是原始设计稿，冲突时以本文件为准。
> 建立：2026-10-23

## 0. 一句话

**一个总管 AI + 多个员工 AI，通过共享文件协作；用户只通过 UI 和总管说话；文件是唯一事实来源。**

总管也是 AI，只是角色是 `manager`；员工也是 AI，角色是 `worker`。一个正在处理消息的 CLI 会话就是一个智能体。

## 1. 角色与权限

| 角色 | 会话 | 能做 | 不能做 |
|---|---|---|---|
| 用户 | 人 | 提需求、回答问题、批准/驳回方案 | 不需要直接指挥员工 |
| 总管 | `MGR` | 接需求、组织讨论、收敛方案、拆分任务、监督、验收、向用户汇报 | 不承接实施任务、不写员工负责的产物 |
| 员工 | `S01`、`S02`… | 参与讨论、按任务卡实施、交付、接受审查 | 不绕过总管找用户、不越界改别人模块 |
| 审查 | 由某个员工兼任 | 按验收标准检查、写 `review_result` | 不静默改写 owner 的交付 |

用户的消息只写入总管收件箱（`inbox/MGR/`）；员工的消息只发给总管或按任务需要发给其他员工。员工之间、员工与总管之间的交流全部落在工作站文件里。

**身份绑定**：worker 调用模型时会注入 `AI_STATION_SESSION` / `AI_STATION_PROJECT`。`station.py` 拒绝跨会话写入（例如总管不能用 `--session S01` 说话）、拒绝跨项目写入，并拒绝 worker 执行 `init` / `request` / `approve` 等用户专属操作。总管不能代员工发言，员工不能冒充用户批准。

## 2. 目录与事实来源

```text
AI_Communication_Station/
  station.py               唯一的状态写入口
  stationlib/              存储、状态推导、动作
  agents/worker.py         文件驱动唤醒器
  ui/                      本地 UI（只读展示 + 用户消息/批准）
  config/station.json      会话、引擎、权限、端口
  config/roles/            总管/员工/审查角色说明
  projects/<project-id>/   协作记录，不含项目代码
    meta.json              项目元数据；workspace = 项目工作区路径
    PROJECT.md
    request/               需求记录（REQ-*.md / .json）
    discussion/
      proposals/           提案（备用）
      rounds/              逐轮发言正文（R*.md）
    decisions/             方案、修订、决策正文
    tasks/                 任务卡（T*.json）
    inbox/<session>/       收件箱；processing/ processed/ failed/
    events/                E000001.json …（追加式事件）
    handoffs/              交付件
    artifacts/             验收后的产物或索引
    logs/                  会话心跳、调用日志、演练报告
    archive/
```

项目代码**不在**本站点目录里：它在 `meta.json` 的 `workspace` 指向的文件夹（通常是 `F:\Desktop\Study\#Project\<项目名>`）。站点只存协作记录、日志和交付索引。

事实来源优先级：

1. 需求 → `request/` + `request_registered` 事件
2. 讨论结论 → 决策事件 + `decisions/` 正文
3. 方案是否可实施 → `plan_proposed` / `user_approval` / `revision_published` 事件
4. 任务状态 → `tasks/*.json` + `task_status` 事件
5. 交付 → `handoffs/` + `handoff` 事件
6. UI 只展示推导结果，不另存一份状态

## 3. 事件（events）

- 一个事件一个文件：`events/E000001.json`，**只追加，不修改，不删除**。
- 顺序只认 `seq`，不按机器时间戳猜。序号在 `events/.lock` 保护下分配；先写临时文件，再同目录原子改名发布。
- 每条事件至少有：`seq`、`event_id`、`project_id`、`type`、`ts`、`actor`、`summary`。
- 长正文放 `discussion/rounds/`、`decisions/`、`handoffs/`，事件里用 `body_file` 引用；短消息可内联 `body`。
- 事件类型见 `stationlib/store.py` 的 `EVENT_TYPES`。写未知类型会被拒绝，防止拼写错误静默丢数据。

常用类型：

`project_created` `request_registered` `manager_opening` `turn_granted` `proposal` `critique` `response` `vote` `round_closed` `user_question` `user_answer` `user_message` `manager_chat` `plan_proposed` `user_approval` `user_rejection` `plan_approved` `assignment` `task_status` `progress` `blocker` `staffing_change` `meeting_request` `meeting_open` `meeting_closed` `revision_published` `decision` `handoff` `review_result` `acceptance` `project_done` `project_reopened` `worker_error` `system`

## 4. 消息（inbox）

- 一条消息一个 JSON 文件，字段：`message_id`、`project_id`、`from_session`、`from_role`、`to_session`、`created_at`、`type`、`subject`、`body`、`task_id`、`refs`、`status`。
- 文件名唯一：`<时间戳>_<message_id>.json`；先写临时文件再原子发布。
- 消费流程：`ready` → 用 `os.rename` 原子移入 `processing/`（防重复领取）→ 成功移入 `processed/`；失败重试，超过 3 次移入 `failed/`，同时写 `worker_error` 事件并通知总管。
- 回执新建消息并引用原 `message_id`，不改写原消息。
- 消息类型见 `MESSAGE_TYPES`。

## 5. 讨论与发言顺序

- 总管 `opening` 开场，确定本轮顺序。
- 总管用 `turn` **一次只放一个会话**发言；员工收到 `discussion_turn` 后用 `speak` 写正文与事件。
- `speak` 会检查当前轮次：没轮到你会被拒绝（人工补救可加 `--force`，需在事件里说明原因）。
- 员工每次发言自动通知总管；总管收齐后 `round-close`，再放下一轮或提交方案。
- 投票用 `speak --type vote --choice <选项>`，理由写在正文。

分歧收敛：先共识 → 表决 → 折中再表决，最多三轮 → 按“满足用户需求、风险可控、可验证、成本/时间”选择，并记录落选方案与重评条件。

## 5.5 项目创建与活动项目

- 用户可以在 UI 里点“新建项目”，填**项目名、项目文件夹（已存在）、需求**。系统用文件夹名生成项目 ID（中文可以），写入：
  `project_created` → `request_registered` → `user_message`（聊天里能看到），并给总管发 `user_request` 消息。用户不需要手敲 `station.py init`。
- `meta.json` 的 `workspace` 记录项目工作区。工作区必须已存在、不能放在工作站目录内，同一个文件夹只能对应一个项目。项目代码写在工作区，协作记录写在 `projects/<id>/`。
- UI 会把当前选中的项目写到 `run/active_project.json`。`run/start_all.py` **不带 `--project`** 时跟随这个文件：
  - 选中/新建项目 → 为该项目的已启用会话起 worker；
  - 切换项目 → 停掉旧项目的全部 worker，再起新项目的；
  - 同一时间只有一个活动项目。正在处理的消息可能留在 `processing/`，切换后需人工重试。
- 带 `--project P` 是固定模式，忽略活动项目文件；适合脚本或单独跑一个项目。

### 完成后追加需求

- 项目 `project_done` 后，用户仍可提新需求。用户消息本身会让阶段回到讨论；总管应显式记录 `reopen`（`project_reopened` 事件）。
- 小范围、在原批准方案内：可直接建任务；范围/目标变化：重新 `plan` 并等用户批准。
- 旧讨论、任务、验收记录全部保留，新工作追加在后面。

## 6. 方案与用户批准（硬闸门）

1. 总管 `plan --version v1` 提交方案；系统生成 `plan_proposed` 事件，阶段变成“等待用户批准”。
2. **批准前**：`task-create` 和任务进入 `in_progress` 都会被拒绝。
3. 用户只能在 UI（或 `station.py approve`）批准/驳回；生成 `user_approval` / `user_rejection` 事件，并投递消息给总管。
4. 驳回后回到讨论；修改后用 `v2` 重新提交。
5. 实施中的普通修订用 `revision`（`requires_user_approval=false`），继承原批准；若改变用户目标/约束、扩大范围、引入重大成本/风险或需要额外授权，必须 `--requires-user-approval`，重新等用户批准。
6. 内部投票不能替代用户授权。

## 7. 任务

状态机：

```text
open -> in_progress -> review -> done
         \-> blocked -> in_progress
review -> in_progress（需要修改）
```

- 任务卡必填：标题、目标/背景、输入、owner、reviewer、允许范围、禁止范围、依赖、交付物、验收标准。
- `scope` / `forbidden` 路径默认相对于项目工作区（`meta.workspace`）；需要跨目录时写绝对路径。
- **依赖闸门（硬规则）**：任务进入 `in_progress` 前，`dependencies` 里能解析出的 `Txxx` 必须全部 `done`。依赖未完成时 `station.py` 直接拒绝；需要提前做不依赖的部分，就拆成独立任务，不允许在 blocked 任务上偷跑。
- **任务改派**：员工停用/超载时，总管用 `task-reassign` 改 owner/reviewer，留 `assignment` 事件和消息；不要把未完成任务留在停用会话上。
- **一个任务只有一个 owner**；总管不能当 owner。
- 进入 `done` 前必须有 `review_result` 且结果为 `pass`/`approved`；审查者或总管确认。
- 状态每次变化写 `task_status` 事件（from/to/reason/evidence）。
- 进度只报“已验收任务数 / 总任务数”，不编造加权百分比。

## 7.5 员工编制（默认上限 2 人）

- 默认编制：总管 `MGR`（当前 Claude，可改 Codex），**默认按单人推进（总管会停用 S02）**；`S01` 固定启用，`S02` 仅在 >=2 条独立未阻塞工作流时在 2 人内启用；`S03`、`S04` 为预留、需用户批准。员工默认 Claude，建议保持。
- “员工”指所有 `role != manager` 的会话；编制数只统计 `enabled=true` 的会话。
- **默认单人（S01），员工上限 2 人**。超过 2 人时 `run/start_all.py`、`agents/worker.py` 和 `staff-enable` 拒绝，并提示先批准。
- **只有 >=2 条真正独立、未阻塞、接口已冻结的工作流，才启用 S02**；UI/单模块/小项目保持单人。总管内可在 2 人内调配/改派；要启用第 3 人（S03）或第 4 人（S04），必须先请用户运行 `approve-staff --max-employees 3`（或 4）；**未批准前不得 staff-enable S03/S04**。批准后才能：
  `python station.py staff-enable --project P --target S03 --note "用户已批准"`
  每次变更写 `staffing_change` 事件并用 `chat` 告知用户。
- 判断标准：默认 1 人；>=2 条真正独立未阻塞 → 可启用 S02；3 条以上或 2 人明显排队/审查积压 → 向用户说明需要第 3/4 人及理由，批准后再启用；严格串行/强耦合 → 不加；长期空闲且无未完成任务 → 可减。团队尽量小。
- 超过上限时 `staff-enable` 拒绝，必须由用户批准：`python station.py approve-staff --max-employees N --note "..."`（写入 `config/staff_approval.json`），批准后 `staff-enable --target S03/S04`；`revoke-staff` 撤销额外批准，回到默认 2 人。
- worker 环境中的 `station.py` 会拒绝 `approve-staff` / `revoke-staff` / `approve` / `init` / `request`，员工不能冒充用户批准；`staff-enable` / `staff-disable` 只允许总管角色或用户调用。
- `start_all` 只有在**未指定 `--sessions`** 时才会自动跟随配置增减；用 `--sessions` 限定启动时不会自动拉起新员工。
- 未启用的会话不能被分配发言或任务；总管在状态里会看到“未启用，不要分配”。
- 每次启停都追加到 `logs/staff_changes.jsonl`（时间、操作者、目标、当时人数/上限、备注）。

## 8. 临时拉会与修订

员工遇到影响批准方案、接口、多个模块或整体质量的问题：

1. `meeting-request`（附证据、影响、建议）。
2. 总管评估后 `meeting-open`；受影响任务暂停新增修改，保存工作点。
3. 员工轮流发言、表决；总管 `decision` 记录结论。
4. 普通修订 `revision` 后通知恢复；涉及用户目标/约束的，重新走 `plan` + 用户批准。
5. `meeting-close` 后恢复工作。

## 9. 唤醒机制（必须如实理解）

**文件系统不会唤醒 CLI。** 真正负责“叫醒”的是 `agents/worker.py`：

- 每个会话一个 worker 进程，轮询自己的收件箱；**没有新消息就不调用模型，不烧 token**。
- **总管一次只推进一步，但必须推进到有人接手**：开场后立刻分配第一位发言人；收到发言后分配下一位或结束轮次后立刻开下一轮；提交方案后停下等用户。**不允许在“轮次结束、无人发言、总管收件箱也空”时结束**，否则流程会卡死。
- 有 ready 消息时：原子领取 → 组装“角色说明 + 协议 + 当前状态 + 消息” → 调 `claude -p` 或 `codex exec` → 处理完归档。
- 员工进程的**当前工作目录是项目工作区**（`meta.workspace`），站点根目录通过 `--add-dir` 加入。提示词给出 `station.py` 的绝对路径；角色文档里的 `python station.py` 要替换成该绝对路径。工作区不存在时 worker 失败重试，不退回站点目录。
- 心跳写入 `logs/sessions/<session>.json`；超过 90 秒没心跳，UI 显示离线。离线时消息仍会排队，不会丢。
- 如果 worker 不在跑，只能人工触发：手动运行一次
  `python agents/worker.py --project <p> --session <S01> --once`
  或启动 `run/start_all.py`。
- UI 每 2 秒轮询 `/api/state`，属于轮询刷新，不是推送；关闭 UI 不影响文件工作流。
- **跟随浏览器**：`start_all` 不带 `--project` 时读 `run/active_project.json`；切换项目会停旧起新。带 `--project` 是固定模式，忽略该文件。

实测能力（2026-10-23，本机）：Claude 与 Codex 均可被 worker 唤醒并执行 `station.py`；Codex 在 Windows 上 `workspace-write` 会降级为只读并拦截命令，因此配置为 `danger-full-access`（见安全节）。

## 10. 安全与授权

- UI 只绑定 `127.0.0.1`，没有登录；不要把端口暴露到局域网/公网。
- 站点里**不保存** API key、密码、无关隐私。引擎凭据由本机 CLI 自己管理。
- worker 的 Claude 会话当前允许完整 PowerShell/Bash、文件读写；Codex 为 `danger-full-access`。这意味着**只能在可信工程目录运行**，并自行备份重要资料。协议禁止删除用户资料、覆盖已有文件、发布内容、产生费用或改生产数据；这些约束靠角色提示、任务边界和事件审计，不是操作系统沙箱。
- 高影响/不可逆操作必须由总管汇总影响、向用户确认，并把授权记录关联到任务。
- 身份绑定只约束经 `station.py` 的写入；直接改文件仍然可能绕过，必须靠任务边界、事件审计和备份兜底。
- `workspace_roots` 可把其它工程目录加入可写范围；只加需要的目录。

## 11. 恢复与排障

- 会话重启不依赖记忆：worker 每次重新读 `state`、相关任务卡和消息。
- 消息卡在 `processing/`（例如进程被杀）：手工移回 `inbox/<session>/` 即可重试；超过重试次数会进 `failed/`。
- worker 启动锁：`logs/sessions/<sid>.worker.lock`；确认进程已死可删除。
- 停站：`run/stop_all.py` 写 `run/stop.flag` 并结束进程树；`start_all` 会自动清理标记。
- 事件序号断档/重复属于严重问题：停止写入，先备份 `events/`，再排查。

## 12. 命令速查

```text
# 项目
python station.py init --project demo --title "标题" --workspace "F:\Desktop\Study\#Project\我的项目" --text "需求"
python station.py state --project demo [--json]
python station.py projects

# 讨论
python station.py opening --project P --session MGR --text-file opening.md --order S01,S02
python station.py turn --project P --session MGR --target S01 --round 1 --topic "..."
python station.py speak --project P --session S01 --round 1 --type proposal --text-file p.md
python station.py round-close --project P --session MGR --round 1 --summary "..."
python station.py decision --project P --session MGR --title "..." --text-file d.md

# 方案与批准
python station.py plan --project P --session MGR --version v1 --title "..." --text-file plan.md
python station.py approve --project P --decision approve --note "同意"
python station.py revision --project P --session MGR --version v1.1 --title "..." --text-file r.md

# 任务
python station.py task-create --project P --session MGR --title "..." --owner S01 --reviewer S02 \
  --acceptance "标准1;标准2" --scope "允许路径" --forbidden "禁止路径"
python station.py task-status --project P --task T001 --status in_progress --session S01 --reason "..."
python station.py review --project P --session S02 --task T001 --result pass --note "..." --evidence "..."
python station.py handoff --project P --session S01 --task T001 --title "..." --text-file h.md --files "a;b"
python station.py finish --project P --session MGR --summary "..."
python station.py reopen --project P --session MGR --reason "用户追加..."

# 沟通
python station.py msg --project P --from MGR --to S01 --type assignment --subject "..." --body "..."
python station.py inbox --project P
python station.py ping --project P --session S01 --status idle

# 启动（不是 station.py 子命令）
python run\start_all.py                 # 跟随浏览器选中的项目
python run\start_all.py --project P     # 固定项目
python run\stop_all.py

# 员工编制
python station.py staff
python station.py staff-enable --project P --target S03 --note "需要并行"
python station.py staff-disable --project P --target S03
python station.py approve-staff --max-employees 5 --note "临时加一名"   # 用户专属
python station.py revoke-staff                                      # 用户专属
```

## 13. 变更记录

- 2026-10-23 v1.0：按 `方案.md` 落地；明确文件驱动唤醒、批准闸门、事件序号、消息领取与重试。
- 2026-10-23 v1.1：增加 worker 身份绑定与“总管一次只推进一步”约束；禁止跨会话写入和用户专属操作。
- 2026-10-23 v1.2：总管默认 Codex、员工默认 Claude；员工默认上限 4 人，超编需用户批准；未启用会话不可分配。
- 2026-10-23 v1.3：总管可在上限内用 `staff-enable` / `staff-disable` 直接增减员工；运行中的 `start_all`（未指定 `--sessions`）自动拉起/停止 worker。
- 2026-10-23 v1.4：明确总管在 4 人以内**自主判断用人**，不需要征求用户同意；每次变更写 `staffing_change` 事件并告知用户；>4 仍由用户批准。
- 2026-10-23 v1.5：支持在 UI 里新建项目并直接交给总管；`start_all` 不带 `--project` 时跟随 `run/active_project.json`，切换项目自动停旧起新。
- 2026-10-23 v1.6：项目工作区与工作站分离；`meta.workspace` 记录项目文件夹，员工以它为 cwd；工作站只存协作记录；工作区必须存在且不能在站点目录内。
- 2026-10-23 v1.7：支持切换项目并恢复完整记录；完成后用户追加需求可用 `reopen` 重新打开，用户消息也会隐式触发讨论阶段。
- 2026-10-24 v1.8：员工默认上限从 4 改为 2；总管只能在 2 人内自主调配，启用 S03/S04 前必须由用户 `approve-staff`。
- 2026-10-24 v1.9：默认按单人推进（总管停用 S02）；增加任务依赖闸门、`task-reassign`、start_all 单实例锁、按项目成本记录；小项目控制任务数量并冻结验收。
