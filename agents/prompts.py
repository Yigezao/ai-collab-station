from __future__ import annotations

import json

from stationlib.config import session_config
from stationlib.paths import ROOT, ROLES_DIR
from stationlib.state import project_state


def load_role(role: str) -> str:
    p = ROLES_DIR / f"{role}.md"
    if not p.exists():
        return f"# 角色：{role}\n\n按协议与消息要求工作。\n"
    return p.read_text(encoding="utf-8")


def compact_state(project_id: str, session_id: str, event_limit: int = 30) -> str:
    st = project_state(project_id, event_limit=event_limit)
    plan = st["plan"]
    lines = []
    lines.append(f"项目: {project_id} — {st['project'].get('title', '')}")
    lines.append(f"项目工作区: {st['project'].get('workspace') or '（未设置）'}")
    lines.append(f"阶段: {st['phase']['label']}（{st['phase']['key']}）")
    if plan.get("pending"):
        lines.append(f"方案: {plan['pending']['version']} 等待用户批准（未批准前不得实施）")
    elif plan.get("approved_version"):
        lines.append(f"方案: 已批准 {plan['approved_version']}")
    else:
        lines.append("方案: 未提交")
    t = st["turn"]
    lines.append(f"当前发言: {t['current'] or '无'}" + (f"（第 {t['round']} 轮）" if t.get("round") else ""))
    if st["meeting"]["active"]:
        lines.append("临时讨论: 进行中；受影响任务先暂停新增修改")
    lines.append("任务:")
    if not st["tasks"]:
        lines.append("  （暂无）")
    for task in st["tasks"]:
        lines.append(
            f"  {task['task_id']} [{task.get('status')}] {task.get('title')}"
            f" owner={task.get('owner_session')} reviewer={task.get('reviewer_session') or '-'}"
        )
        if task.get("status") == "blocked" and task.get("blocked_reason"):
            lines.append(f"    阻塞: {task['blocked_reason']}")
    lines.append("会话:")
    for s in st["sessions"]:
        if not s.get("enabled", True):
            lines.append(
                f"  {s['session_id']} {s['name']}（{s['role']}/{s['engine']}）未启用，不要分配"
            )
            continue
        lines.append(
            f"  {s['session_id']} {s['name']}（{s['role']}/{s['engine']}）"
            f" {'在线' if s['online'] else '离线'} 收件箱={s['inbox']['ready']}"
        )
    stf = st.get("staffing") or {}
    lines.append(
        f"员工编制: {stf.get('employees_enabled', '?')}/{stf.get('limit', '?')} 启用"
        f"（默认上限 {stf.get('default_limit', 2)}；超过需用户批准）"
    )
    lines.append("最近事件:")
    for e in st["events"][-event_limit:]:
        actor = (e.get("actor") or {}).get("session_id", "?")
        lines.append(f"  #{e['seq']} {e['type']} [{actor}] {e.get('summary', '')}")
    return "\n".join(lines)


def build_prompt(
    project_id: str, session_id: str, msg: dict, engine: str, workspace
) -> tuple[str, str]:
    cfg = session_config(session_id)
    role = cfg.get("role", "worker")
    role_doc = load_role(role)
    state_text = compact_state(project_id, session_id)
    station_cli = str(ROOT / "station.py")

    body = (
        f"你是 AI 协作工作站的会话 {session_id}（{cfg.get('name', session_id)}），"
        f"角色是 {role}。\n\n"
        f"【路径约定】\n"
        f"- 项目工作区（当前工作目录）: {workspace}\n"
        f"- 站点根目录: {ROOT}\n"
        f"- 角色文档里的 `python station.py ...` 是简写；当前目录不是站点根目录，"
        f"实际执行请用绝对路径：python \"{station_cli}\" ...\n"
        f"- 项目产物只写到项目工作区；不要在工作站目录里创建项目文件。\n"
        f"- 工作站的 events/tasks/inbox 只能通过 station.py 写。\n\n"
        f"项目: {project_id}\n\n"
        f"【本次收到的消息】\n"
        f"- message_id: {msg.get('message_id')}\n"
        f"- 来自: {msg.get('from_session')}（{msg.get('from_role')}）\n"
        f"- 类型: {msg.get('type')}\n"
        f"- 主题: {msg.get('subject')}\n"
        f"- 关联任务: {msg.get('task_id') or '无'}\n"
        f"- 正文:\n{msg.get('body', '')}\n\n"
        f"【项目当前状态（实时从文件汇总）】\n{state_text}\n\n"
        f"【执行要求】\n"
        f"1. 先读站点根目录下的 `protocol.md` 和 `config/roles/{role}.md`，再处理这条消息。\n"
        f"2. 所有协作状态变化只通过 `python station.py ...` 写入；"
        f"不要直接编辑 `projects/` 下的 events/、tasks/、inbox/ 文件。\n"
        f"   在 Windows 上用 PowerShell 工具执行命令；一次只执行一条命令，不要用 ; 或 && 串联。\n"
        f"3. 不直接向用户汇报；需要用户知道的事写入事件并通知总管（MGR）。\n"
        f"4. 不覆盖已有文件，不删除用户资料，不编造测试或验证结果。\n"
        f"5. 只做这条消息要求的事；处理完就结束，不要抢别的会话的工作。\n"
        f"6. 只能以 {session_id} 身份写事件；不要启动其他 worker，不要代替其他会话发言。\n"
        f"7. 如果无法完成，写 `blocker` 或 `worker_error` 说明原因，并给总管发消息。\n"
        f"8. 结论、证据、检查结果写进事件/任务/交接件，不要只在回复里说。\n"
    )
    if engine == "claude":
        return body, role_doc
    # Codex has no append-system-prompt here, so the role doc goes into the prompt.
    return f"{role_doc}\n\n---\n\n{body}", ""


def message_json(msg: dict) -> str:
    return json.dumps(msg, ensure_ascii=False, indent=2)
