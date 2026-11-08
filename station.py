#!/usr/bin/env python
"""AI Communication Station command-line interface.

Every state change goes through this tool (or the UI server, which uses the same
library), so events stay ordered and append-only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stationlib import actions  # noqa: E402
from stationlib.config import (  # noqa: E402
    USER_ACTOR,
    all_sessions,
    load_config,
    make_actor,
    session_config,
)
from stationlib.paths import ROOT, list_projects, project_dir  # noqa: E402
from stationlib.staffing import (  # noqa: E402
    approve_staff,
    revoke_staff,
    set_employee_enabled,
    staffing_info,
)
from stationlib.state import project_state  # noqa: E402
from stationlib.store import (  # noqa: E402
    EVENT_TYPES,
    MESSAGE_TYPES,
    append_event,
    heartbeat,
    list_inbox,
    list_tasks,
    now_iso,
    read_json,
    send_message,
    task_summary,
    validate_task,
    write_task,
)


def out(msg: str) -> None:
    print(msg)


def dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def default_project() -> str:
    return load_config().get("default_project", "demo")


def read_text_arg(args) -> str:
    if getattr(args, "text_file", None):
        return Path(args.text_file).read_text(encoding="utf-8")
    text = getattr(args, "text", None)
    if not text:
        raise ValueError("需要 --text 或 --text-file")
    return text


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.replace(",", ";").split(";") if x.strip()]


def actor_for(session_id: str) -> dict:
    if session_id == "USER":
        return USER_ACTOR
    return make_actor(session_id)


def actor_arg(args) -> dict:
    return actor_for(args.session)


# ---------------------------------------------------------------- handlers


def cmd_init(args):
    text = read_text_arg(args)
    meta = actions.init_project(
        args.project,
        args.title,
        text,
        args.request_title,
        workspace=args.workspace,
    )
    out(f"OK 项目已建立: {args.project}（{meta['title']}）")
    out(f"   工作记录: {project_dir(args.project)}")
    out(f"   项目工作区: {meta.get('workspace') or '（未设置）'}")
    if not meta.get("workspace"):
        out("警告: 没指定 --workspace，员工会在工作站目录里工作；正式项目请指定项目文件夹。")


def cmd_request(args):
    req = actions.register_request(args.project, read_text_arg(args), args.title)
    out(f"OK 需求已登记: {req['request_id']}")


def cmd_event(args):
    pdir = project_dir(args.project)
    if args.body_file:
        bf = Path(args.body_file)
        if not bf.is_absolute():
            bf = pdir / bf
        if not bf.exists():
            raise FileNotFoundError(f"body 文件不存在: {bf}")
        body_file = str(bf.relative_to(pdir)).replace("\\", "/")
    else:
        body_file = None
    if args.data_file:
        data = json.loads(Path(args.data_file).read_text(encoding="utf-8"))
    else:
        data = json.loads(args.data) if args.data else None
    ev = append_event(
        args.project,
        args.type,
        actor_arg(args),
        args.summary,
        body=args.body,
        body_file=body_file,
        data=data,
        refs=split_list(args.refs) or None,
        round_no=args.round,
    )
    out(f"OK {ev['event_id']} {ev['type']} {ev['summary']}")


def cmd_speak(args):
    ev = actions.speak(
        args.project,
        args.session,
        read_text_arg(args),
        args.round,
        speak_type=args.type,
        title=args.title,
        choice=args.choice,
        force=args.force,
    )
    out(f"OK {ev['event_id']} 发言已记录: {ev['summary']}")


def cmd_msg(args):
    msg = send_message(
        args.project,
        args.from_session,
        args.to,
        args.type,
        args.subject,
        args.body,
        task_id=args.task_id,
        refs=split_list(args.refs) or None,
    )
    out(f"OK 消息已投递: {msg['message_id']} -> {args.to}")


def cmd_turn(args):
    ev = actions.grant_turn(args.project, args.session, args.target, args.round, args.topic)
    out(f"OK {ev['event_id']} 轮到 {args.target}")


def cmd_round_close(args):
    ev = actions.close_round(args.project, args.session, args.round, args.summary)
    out(f"OK {ev['event_id']} 轮次已结束")


def cmd_opening(args):
    ev = actions.manager_opening(
        args.project, args.session, read_text_arg(args), split_list(args.order) or None
    )
    out(f"OK {ev['event_id']} 开场已记录")


def cmd_plan(args):
    ev = actions.propose_plan(
        args.project, args.session, args.version, args.title, read_text_arg(args)
    )
    out(f"OK {ev['event_id']} 方案 {args.version} 已提交，等待用户在 UI/CLI 批准")


def cmd_revision(args):
    ev = actions.publish_revision(
        args.project,
        args.session,
        args.version,
        args.title,
        read_text_arg(args),
        requires_user_approval=args.requires_user_approval,
        changes=args.changes,
    )
    out(f"OK {ev['event_id']} 修订 {args.version} 已发布")


def cmd_approve(args):
    ev = actions.approve_plan(
        args.project, args.decision == "approve", args.note, by=args.by
    )
    out(f"OK {ev['event_id']} {ev['summary']}")


def cmd_decision(args):
    ev = actions.record_decision(args.project, args.session, args.title, read_text_arg(args))
    out(f"OK {ev['event_id']} 决策已记录")


def cmd_meeting_request(args):
    ev = actions.request_meeting(args.project, args.session, args.reason, args.evidence)
    out(f"OK {ev['event_id']} 已申请临时讨论")


def cmd_meeting_open(args):
    ev = actions.open_meeting(args.project, args.session, args.reason)
    out(f"OK {ev['event_id']} 临时讨论已开始")


def cmd_meeting_close(args):
    ev = actions.close_meeting(args.project, args.session, args.summary)
    out(f"OK {ev['event_id']} 临时讨论已结束")


def cmd_task_create(args):
    data = {}
    if args.file:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    task = actions.create_task(
        args.project,
        args.session,
        title=args.title or data.get("title", ""),
        owner=args.owner or data.get("owner_session", ""),
        acceptance=split_list(args.acceptance) or data.get("acceptance", []),
        goal=args.goal or data.get("goal", ""),
        background=args.background or data.get("background", ""),
        inputs=split_list(args.inputs) or data.get("inputs", []),
        reviewer=args.reviewer or data.get("reviewer_session") or None,
        scope=split_list(args.scope) or data.get("scope", []),
        forbidden=split_list(args.forbidden) or data.get("forbidden", []),
        dependencies=split_list(args.deps) or data.get("dependencies", []),
        deliverables=split_list(args.deliverables) or data.get("deliverables", []),
    )
    out(f"OK 任务已创建: {task['task_id']} owner={task['owner_session']} status={task['status']}")


def cmd_task_reassign(args):
    task = actions.reassign_task(
        args.project,
        args.session,
        args.task,
        args.owner,
        args.reason,
        new_reviewer=args.reviewer,
    )
    out(
        f"OK {task['task_id']} 已改派给 {task['owner_session']}"
        f"（reviewer={task.get('reviewer_session') or '-'}）"
    )


def cmd_task_status(args):
    ev = actions.update_task_status(
        args.project, args.task, args.status, args.session, args.reason, args.evidence
    )
    out(f"OK {ev['event_id']} {ev['summary']}")


def cmd_review(args):
    ev = actions.report_review(
        args.project, args.session, args.task, args.result, args.note, args.evidence
    )
    out(f"OK {ev['event_id']} 审查结果已记录")


def cmd_handoff(args):
    ev = actions.handoff(
        args.project,
        args.session,
        args.task,
        args.title,
        read_text_arg(args),
        files=split_list(args.files),
        checks=args.checks,
        limits=args.limits,
    )
    out(f"OK {ev['event_id']} 交接已登记: {ev.get('body_file')}")


def cmd_finish(args):
    ev = actions.finish_project(args.project, args.session, args.summary, args.evidence)
    out(f"OK {ev['event_id']} 项目已完成")


def cmd_reopen(args):
    ev = actions.reopen_project(args.project, args.session, args.reason)
    out(f"OK {ev['event_id']} 项目已重新打开，等待新的讨论/任务")


def cmd_ask(args):
    ev = actions.user_message(args.project, read_text_arg(args), args.to)
    out(f"OK {ev['event_id']} 用户消息已写入总管收件箱")


def cmd_chat(args):
    ev = actions.manager_chat(args.project, args.session, read_text_arg(args))
    out(f"OK {ev['event_id']} 总管回复已记录")


def cmd_answer(args):
    ev = actions.record_user_answer(args.project, args.session, read_text_arg(args))
    out(f"OK {ev['event_id']} 用户答复已记录")


def cmd_ping(args):
    heartbeat(args.project, args.session, args.status)
    out(f"OK 心跳: {args.session} {args.status}")


def cmd_state(args):
    st = project_state(args.project, event_limit=args.limit)
    if args.json:
        dump(st)
        return
    plan = st["plan"]
    out(f"项目: {args.project}  {st['project'].get('title', '')}")
    ws = st["project"].get("workspace")
    out(f"工作区: {ws or '（未设置）'}" + ("" if not ws or st.get("workspace_exists") else "  [不存在]"))
    out(f"阶段: {st['phase']['label']}（{st['phase']['key']}）")
    if plan.get("pending"):
        out(f"方案: {plan['pending']['version']} 等待用户批准（自 {plan['pending'].get('since')}）")
    elif plan.get("approved_version"):
        out(f"方案: 已批准 {plan['approved_version']}")
    else:
        out("方案: 未提交")
    turn = st["turn"]
    out(f"当前发言: {turn['current'] or '无'}" + (f"（第 {turn['round']} 轮）" if turn.get("round") else ""))
    if st.get("stalled"):
        out("警告: 讨论停滞，总管没有安排下一位发言；在 UI 里发消息提醒总管。")
    if st["meeting"]["active"]:
        out("临时讨论: 进行中（受影响任务应暂停新增修改）")
    ts = st["task_summary"]
    out(f"任务: {ts['progress_text']}  " + " ".join(f"{k}={v}" for k, v in ts["counts"].items() if v))
    for s in st["sessions"]:
        inbox = s["inbox"]
        flag = "" if s.get("enabled", True) else " [未启用]"
        out(
            f"会话 {s['session_id']} {s['name']}（{s['role']}/{s['engine']}）{flag}"
            f" {'在线' if s['online'] else '离线'}/{s['status']}  收件箱 ready={inbox['ready']}"
        )
    stf = st.get("staffing") or {}
    out(
        f"员工编制: {stf.get('employees_enabled')}/{stf.get('limit')} 启用"
        + (f"（已批准上限 {stf['approved_max']}）" if stf.get("approved_max") else "")
    )


def cmd_sessions(args):
    cfg = load_config()
    if args.project:
        st = project_state(args.project, event_limit=1)
        dump(st["sessions"])
    else:
        for sid, c in cfg.get("sessions", {}).items():
            out(f"{sid}\t{c.get('role')}\t{c.get('engine')}\t{c.get('name')}\tenabled={c.get('enabled', True)}")


def cmd_projects(args):
    projects = list_projects()
    if args.json:
        dump(projects)
    else:
        out("\n".join(projects) if projects else "（暂无项目）")


def cmd_staff(args):
    info = staffing_info()
    if args.json:
        dump(info)
        return
    out(
        f"员工: {info['employees_enabled']}/{info['limit']} 启用"
        f"（配置里共 {info['employees_total']} 个员工会话）"
    )
    out("启用的员工: " + (", ".join(info["employee_ids"]) or "（无）"))
    if info["approved_max"]:
        out(
            f"用户批准的上限: {info['approved_max']}"
            f"（{info['approval_at']}；备注: {info['approval_note'] or '无'}）"
        )
    else:
        out(f"默认上限: {info['default_limit']}；超过需用户批准")
    if info["over_limit"]:
        out("状态: 已超限，worker 不会启动。先运行 approve-staff。")
    else:
        out("状态: 未超限")


def cmd_approve_staff(args):
    info_before = staffing_info()
    data = approve_staff(args.max_employees, args.note)
    out(f"OK 已批准员工上限为 {data['max_employees']}（用户授权，{data['approved_at']}）")
    if info_before["employees_enabled"] > data["max_employees"]:
        out(
            f"注意: 当前已启用 {info_before['employees_enabled']} 人，仍超过批准上限；"
            "请禁用部分会话或重新批准。"
        )


def cmd_revoke_staff(args):
    revoke_staff()
    out("OK 已撤销额外员工批准，回到默认上限")


def _staff_actor() -> tuple[str, dict]:
    bound = os.environ.get("AI_STATION_SESSION")
    if bound:
        if session_config(bound).get("role") != "manager":
            raise PermissionError("只有总管或用户能启用/停用员工")
        return bound, make_actor(bound)
    return "用户", USER_ACTOR


def _staff_target_project(args) -> str | None:
    bound_pid = os.environ.get("AI_STATION_PROJECT")
    if bound_pid and args.project and args.project != bound_pid:
        raise PermissionError(f"项目绑定不匹配: 当前会话绑定 {bound_pid}")
    # A manager always records the change in its own project even if it omits
    # --project; the user CLI only records when --project is explicitly given.
    target = args.project or bound_pid
    if target and not (project_dir(target) / "meta.json").exists():
        raise FileNotFoundError(f"项目不存在: {target}")
    return target


def _record_staffing_change(args, actor: dict, res: dict, action: str) -> None:
    project_id = _staff_target_project(args)
    if not project_id:
        return
    verb = "启用" if action == "enable" else "停用"
    append_event(
        project_id,
        "staffing_change",
        actor,
        f"{verb}员工 {args.target}（{res['employees_enabled']}/{res['limit']} 启用）"
        + (f"：{args.note}" if args.note else ""),
        data={
            "session_id": args.target,
            "enabled": action == "enable",
            "employees_enabled": res["employees_enabled"],
            "limit": res["limit"],
            "note": args.note,
        },
    )


def _supervisor_hint() -> None:
    if (ROOT / "run" / "station.pid.json").exists():
        out("start_all 会在几秒内自动同步（前提：启动时没有用 --sessions 限定）。")
    else:
        out("提示: 没检测到运行中的 start_all；需要启动它，worker 才会被拉起。")


def cmd_staff_enable(args):
    actor_id, actor = _staff_actor()
    _staff_target_project(args)  # validate before writing config
    res = set_employee_enabled(args.target, True, actor_id, args.note)
    _record_staffing_change(args, actor, res, "enable")
    out(f"OK 已启用 {args.target}；员工 {res['employees_enabled']}/{res['limit']} 启用。")
    _supervisor_hint()


def cmd_staff_disable(args):
    actor_id, actor = _staff_actor()
    _staff_target_project(args)
    res = set_employee_enabled(args.target, False, actor_id, args.note)
    _record_staffing_change(args, actor, res, "disable")
    out(f"OK 已停用 {args.target}；员工 {res['employees_enabled']}/{res['limit']} 启用。")
    out("如果它正在处理消息，该消息可能留在 processing/，需要人工移回收件箱重试。")
    _supervisor_hint()


def cmd_inbox(args):
    if args.session:
        for m in list_inbox(args.project, args.session):
            out(f"{m.get('created_at')} {m.get('message_id')} {m.get('type')} {m.get('subject')}")
    else:
        for sid in all_sessions():
            out(f"{sid}: {len(list_inbox(args.project, sid))} 条待处理")


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AI 协作工作站 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_project(sp, required=False):
        sp.add_argument("--project", default=None, help="项目 ID")
        return sp

    def add_text(sp):
        sp.add_argument("--text", help="正文")
        sp.add_argument("--text-file", help="从文件读取正文")
        return sp

    sp = sub.add_parser("init", help="建立项目并登记初始需求")
    add_project(sp, True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--request-title", default="初始需求")
    sp.add_argument(
        "--workspace",
        default=None,
        help="项目工作区（你自己建好的项目文件夹，如 F:\\Desktop\\Study\\#Project\\我的项目）",
    )
    add_text(sp)
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("request", help="追加需求记录")
    add_project(sp, True)
    sp.add_argument("--title", default="需求")
    add_text(sp)
    sp.set_defaults(func=cmd_request)

    sp = sub.add_parser("event", help="写一条通用事件")
    add_project(sp, True)
    sp.add_argument("--session", required=True, help="会话 ID 或 USER")
    sp.add_argument("--type", required=True, choices=sorted(EVENT_TYPES))
    sp.add_argument("--summary", required=True)
    sp.add_argument("--body")
    sp.add_argument("--body-file")
    sp.add_argument("--data", help="JSON 对象字符串")
    sp.add_argument("--data-file", help="从文件读取 JSON 对象")
    sp.add_argument("--refs", help="逗号分隔")
    sp.add_argument("--round", type=int)
    sp.set_defaults(func=cmd_event)

    sp = sub.add_parser("speak", help="发言（写入讨论记录并通知总管）")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--round", type=int, required=True)
    sp.add_argument("--type", default="proposal", choices=sorted(actions.SPEAK_TYPES))
    sp.add_argument("--title")
    sp.add_argument("--choice", help="投票选项")
    sp.add_argument("--force", action="store_true", help="非轮次发言时强制写入")
    add_text(sp)
    sp.set_defaults(func=cmd_speak)

    sp = sub.add_parser("msg", help="向某会话投递消息")
    add_project(sp, True)
    sp.add_argument("--from", dest="from_session", required=True)
    sp.add_argument("--to", required=True)
    sp.add_argument("--type", required=True, choices=sorted(MESSAGE_TYPES))
    sp.add_argument("--subject", required=True)
    sp.add_argument("--body", required=True)
    sp.add_argument("--task-id")
    sp.add_argument("--refs")
    sp.set_defaults(func=cmd_msg)

    sp = sub.add_parser("turn", help="总管分配发言轮次")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--target", required=True)
    sp.add_argument("--round", type=int, required=True)
    sp.add_argument("--topic", required=True)
    sp.set_defaults(func=cmd_turn)

    sp = sub.add_parser("round-close", help="总管结束讨论轮次")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--round", type=int, required=True)
    sp.add_argument("--summary", required=True)
    sp.set_defaults(func=cmd_round_close)

    sp = sub.add_parser("opening", help="总管开场")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--order", help="发言顺序，如 S01,S02")
    add_text(sp)
    sp.set_defaults(func=cmd_opening)

    sp = sub.add_parser("plan", help="提交初始方案（等待用户批准）")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--version", required=True)
    sp.add_argument("--title", required=True)
    add_text(sp)
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("revision", help="发布实施中的修订")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--version", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--changes", default="")
    sp.add_argument("--requires-user-approval", action="store_true")
    add_text(sp)
    sp.set_defaults(func=cmd_revision)

    sp = sub.add_parser("approve", help="用户批准/驳回方案（也可在 UI 操作）")
    add_project(sp, True)
    sp.add_argument("--decision", required=True, choices=["approve", "reject"])
    sp.add_argument("--note", default="")
    sp.add_argument("--by", default="用户")
    sp.set_defaults(func=cmd_approve)

    sp = sub.add_parser("decision", help="记录决策")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--title", required=True)
    add_text(sp)
    sp.set_defaults(func=cmd_decision)

    sp = sub.add_parser("meeting-request", help="申请临时讨论")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--evidence", default="")
    sp.set_defaults(func=cmd_meeting_request)

    sp = sub.add_parser("meeting-open", help="总管宣布临时讨论")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_meeting_open)

    sp = sub.add_parser("meeting-close", help="总管结束临时讨论")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--summary", required=True)
    sp.set_defaults(func=cmd_meeting_close)

    sp = sub.add_parser("task-create", help="创建实施任务（需方案已批准）")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--file", help="任务卡 JSON")
    sp.add_argument("--title")
    sp.add_argument("--owner")
    sp.add_argument("--reviewer")
    sp.add_argument("--goal")
    sp.add_argument("--background")
    sp.add_argument("--acceptance", help="分号分隔")
    sp.add_argument("--scope", help="分号分隔")
    sp.add_argument("--forbidden", help="分号分隔")
    sp.add_argument("--deps", help="分号分隔")
    sp.add_argument("--deliverables", help="分号分隔")
    sp.add_argument("--inputs", help="分号分隔")
    sp.set_defaults(func=cmd_task_create)

    sp = sub.add_parser("task-reassign", help="总管改派任务 owner/reviewer")
    add_project(sp, True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--owner", required=True)
    sp.add_argument("--reviewer", default=None)
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_task_reassign)

    sp = sub.add_parser("task-status", help="更新任务状态")
    add_project(sp, True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--status", required=True, choices=["in_progress", "review", "done", "blocked"])
    sp.add_argument("--session", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--evidence", default="")
    sp.set_defaults(func=cmd_task_status)

    sp = sub.add_parser("review", help="记录交叉审查结果")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--result", required=True, choices=["pass", "approved", "fail", "changes_requested"])
    sp.add_argument("--note", required=True)
    sp.add_argument("--evidence", default="")
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("handoff", help="提交交付件")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--files", help="分号分隔")
    sp.add_argument("--checks", default="")
    sp.add_argument("--limits", default="")
    add_text(sp)
    sp.set_defaults(func=cmd_handoff)

    sp = sub.add_parser("finish", help="总管宣布项目验收完成")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--summary", required=True)
    sp.add_argument("--evidence", default="")
    sp.set_defaults(func=cmd_finish)

    sp = sub.add_parser("reopen", help="用户追加需求时，重新打开已完成项目")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_reopen)

    sp = sub.add_parser("ask", help="用户消息 -> 总管")
    add_project(sp, True)
    sp.add_argument("--to", help="默认总管")
    add_text(sp)
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("chat", help="总管在 UI 回复用户")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    add_text(sp)
    sp.set_defaults(func=cmd_chat)

    sp = sub.add_parser("answer", help="记录用户答复")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    add_text(sp)
    sp.set_defaults(func=cmd_answer)

    sp = sub.add_parser("ping", help="写心跳")
    add_project(sp, True)
    sp.add_argument("--session", required=True)
    sp.add_argument("--status", default="idle", choices=["idle", "working", "offline"])
    sp.set_defaults(func=cmd_ping)

    sp = sub.add_parser("state", help="查看项目状态")
    add_project(sp, True)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--limit", type=int, default=500)
    sp.set_defaults(func=cmd_state)

    sp = sub.add_parser("sessions", help="列会话")
    add_project(sp)
    sp.set_defaults(func=cmd_sessions)

    sp = sub.add_parser("projects", help="列项目")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_projects)

    sp = sub.add_parser("staff", help="查看员工数量与上限")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_staff)

    sp = sub.add_parser("approve-staff", help="用户批准超过默认上限的员工数")
    sp.add_argument("--max-employees", type=int, required=True)
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_approve_staff)

    sp = sub.add_parser("revoke-staff", help="撤销额外员工批准")
    sp.set_defaults(func=cmd_revoke_staff)

    sp = sub.add_parser("staff-enable", help="启用员工（总管或用户；最多 4 人）")
    sp.add_argument("--target", required=True, help="要启用的员工会话，如 S03")
    sp.add_argument("--note", default="")
    sp.add_argument("--project", default=None, help="可选：把这次编制变更写进该项目时间线")
    sp.set_defaults(func=cmd_staff_enable)

    sp = sub.add_parser("staff-disable", help="停用员工（总管或用户）")
    sp.add_argument("--target", required=True, help="要停用的员工会话，如 S03")
    sp.add_argument("--note", default="")
    sp.add_argument("--project", default=None, help="可选：把这次编制变更写进该项目时间线")
    sp.set_defaults(func=cmd_staff_disable)

    sp = sub.add_parser("inbox", help="查看收件箱")
    add_project(sp, True)
    sp.add_argument("--session")
    sp.set_defaults(func=cmd_inbox)

    return p


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    # These commands are site-level and may run without a project.
    site_cmds = (
        "projects",
        "sessions",
        "staff",
        "approve-staff",
        "revoke-staff",
        "staff-enable",
        "staff-disable",
    )
    if args.cmd not in site_cmds:
        if not getattr(args, "project", None):
            args.project = default_project()
        if not args.project:
            parser.error("需要 --project")

    # A worker-spawned CLI turn carries its identity in the environment. Refuse
    # writes made in another session's name, and refuse user-only actions.
    bound_sid = os.environ.get("AI_STATION_SESSION")
    bound_pid = os.environ.get("AI_STATION_PROJECT")
    if bound_pid and args.cmd not in site_cmds:
        if args.project and args.project != bound_pid:
            parser.error(f"项目绑定不匹配: 当前会话绑定 {bound_pid}，不能操作 {args.project}")
        args.project = bound_pid
    if bound_sid:
        if args.cmd in ("init", "request", "approve", "approve-staff", "revoke-staff"):
            parser.error(f"会话 {bound_sid} 不能执行用户专属操作: {args.cmd}")
        claimed = getattr(args, "session", None) or getattr(args, "from_session", None)
        if claimed and claimed != bound_sid:
            parser.error(
                f"会话身份不匹配: 当前 worker 绑定 {bound_sid}，"
                f"不能以 {claimed} 身份写入（禁止总管代员工发言）"
            )
    try:
        args.func(args)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
