from __future__ import annotations

import os
import re
from pathlib import Path

from .config import USER_ACTOR, all_sessions, make_actor, manager_session, session_config
from .paths import ROOT, list_projects, project_dir
from .state import plan_state, project_state, turn_state
from .store import (
    TASK_TRANSITIONS,
    append_event,
    atomic_write_json,
    atomic_write_text,
    compact_stamp,
    ensure_project,
    get_task,
    list_events,
    list_tasks,
    next_task_id,
    now_iso,
    read_json,
    send_message,
    validate_task,
    write_task,
)

SPEAK_TYPES = {"proposal", "critique", "response", "vote", "question"}
SPEAK_TO_MESSAGE = {
    "proposal": "proposal",
    "critique": "critique",
    "response": "proposal",
    "vote": "vote",
    "question": "blocker",
}


def _short(text: str, n: int = 160) -> str:
    text = (text or "").strip().replace("\r\n", "\n")
    one = " ".join(line.strip() for line in text.splitlines() if line.strip())
    return one if len(one) <= n else one[: n - 1] + "…"


def _write_body(project_id: str, rel_path: str, title: str, text: str) -> str:
    pdir = ensure_project(project_id)
    full = pdir / rel_path
    atomic_write_text(full, f"# {title}\n\n{text.strip()}\n")
    return rel_path.replace("\\", "/")


# ---------------------------------------------------------------- project


def init_project(
    project_id: str,
    title: str,
    request_text: str,
    request_title: str | None = None,
    workspace: str | None = None,
) -> dict:
    pdir = project_dir(project_id)
    if (pdir / "meta.json").exists():
        raise FileExistsError(f"项目已存在，不覆盖: {project_id}")

    ws_path: Path | None = None
    if workspace:
        ws_path = Path(workspace).expanduser().resolve()
        if not ws_path.is_dir():
            raise FileNotFoundError(f"项目工作区不存在或不是文件夹: {ws_path}")
        if ws_path.is_relative_to(ROOT):
            raise ValueError("项目工作区不能放在工作站目录内；请在 #Project 下单独建项目文件夹")
        for other in list_projects():
            other_meta = read_json(project_dir(other) / "meta.json", {}) or {}
            other_ws = other_meta.get("workspace")
            if not other_ws:
                continue
            try:
                same = os.path.normcase(str(Path(other_ws).resolve())) == os.path.normcase(
                    str(ws_path)
                )
            except OSError:
                same = False
            if same:
                raise FileExistsError(f"这个工作区已被项目 {other} 使用")

    ensure_project(project_id, create=True)
    meta = {
        "project_id": project_id,
        "title": title,
        "workspace": str(ws_path) if ws_path else None,
        "created_at": now_iso(),
        "created_by": "user",
        "schema_version": 1,
        "timezone": "local",
    }
    atomic_write_json(pdir / "meta.json", meta)
    atomic_write_text(
        pdir / "PROJECT.md",
        f"# {title}\n\n- 项目 ID: `{project_id}`\n- 建立时间: {meta['created_at']}\n"
        f"- 项目工作区: `{ws_path or '（未设置，旧/演示项目）'}`\n"
        f"- 协议: `../../protocol.md`\n- 需求以 `request/` 与事件日志为准\n",
    )
    append_event(
        project_id,
        "project_created",
        USER_ACTOR,
        f"建立项目「{title}」",
        data={"title": title},
    )
    req = register_request(project_id, request_text, request_title or "初始需求")
    # Wake the manager as soon as workers start: the initial request is a message,
    # not just a file the manager might never notice.
    send_message(
        project_id,
        "USER",
        manager_session(),
        "user_request",
        f"新项目需求：{title}",
        request_text,
        refs=[req["body_file"]],
    )
    # Also show the initial request in the UI chat. This is a chat event only;
    # the message above is the one that wakes the manager.
    append_event(
        project_id,
        "user_message",
        USER_ACTOR,
        _short(request_text),
        body=request_text,
        data={"source": "init", "request_id": req["request_id"]},
    )
    return meta


def register_request(project_id: str, text: str, title: str = "需求") -> dict:
    pdir = ensure_project(project_id)
    existing = sorted((pdir / "request").glob("REQ-*.json"))
    num = len(existing) + 1
    req_id = f"REQ-{num:04d}"
    rel_md = f"request/{req_id}.md"
    atomic_write_text(pdir / rel_md, f"# {title}\n\n{text.strip()}\n")
    req = {
        "request_id": req_id,
        "project_id": project_id,
        "title": title,
        "text": text.strip(),
        "created_at": now_iso(),
        "created_by": "user",
        "body_file": rel_md,
    }
    atomic_write_json(pdir / "request" / f"{req_id}.json", req)
    append_event(
        project_id,
        "request_registered",
        USER_ACTOR,
        f"{req_id}: {title}".strip(),
        body_file=rel_md,
        data={"request_id": req_id},
    )
    return req


def user_message(project_id: str, text: str, target: str | None = None) -> dict:
    """User's only channel is the manager session."""
    target = target or manager_session()
    send_message(project_id, "USER", target, "user_request", _short(text, 80), text)
    return append_event(project_id, "user_message", USER_ACTOR, _short(text), body=text)


def manager_chat(project_id: str, session_id: str, text: str) -> dict:
    return append_event(
        project_id,
        "manager_chat",
        make_actor(session_id),
        _short(text),
        body=text,
    )


def user_question(project_id: str, session_id: str, text: str) -> dict:
    append_event(project_id, "user_question", make_actor(session_id), _short(text), body=text)
    # Wake the manager so it can relay the question in the UI. The manager asking
    # itself would create a self-loop, so skip the message in that case.
    if session_id != manager_session():
        send_message(project_id, session_id, manager_session(), "blocker", "需要用户澄清", text)
    return {"ok": True}


def record_user_answer(project_id: str, session_id: str, text: str) -> dict:
    return append_event(project_id, "user_answer", make_actor(session_id), _short(text), body=text)


# ---------------------------------------------------------------- discussion


def manager_opening(project_id: str, session_id: str, text: str, order: list[str] | None = None) -> dict:
    rel = f"discussion/rounds/OPENING_{compact_stamp()}.md"
    _write_body(project_id, rel, "总管开场", text)
    return append_event(
        project_id,
        "manager_opening",
        make_actor(session_id),
        _short(text),
        body_file=rel,
        data={"order": order or []},
    )


def grant_turn(project_id: str, session_id: str, target: str, round_no: int, topic: str) -> dict:
    if session_config(session_id).get("role") != "manager":
        raise PermissionError("只有总管可以分配发言顺序")
    target_cfg = session_config(target)
    if not target_cfg.get("enabled", True):
        raise ValueError(f"目标会话未启用，不能分配发言: {target}")
    if target_cfg.get("role") == "manager":
        raise ValueError("发言顺序只能分配给员工，不能分配给总管")
    ev = append_event(
        project_id,
        "turn_granted",
        make_actor(session_id),
        f"第 {round_no} 轮，轮到 {target} 发言：{_short(topic, 80)}",
        round_no=round_no,
        data={"session_id": target, "topic": topic},
    )
    send_message(
        project_id,
        session_id,
        target,
        "discussion_turn",
        f"第 {round_no} 轮轮到你发言",
        f"主题：{topic}\n\n请按角色职责发言，使用 station.py speak 写入讨论记录，然后向总管报告。",
        refs=[ev["event_id"]],
    )
    return ev


def close_round(project_id: str, session_id: str, round_no: int, summary: str) -> dict:
    if session_config(session_id).get("role") != "manager":
        raise PermissionError("只有总管可以结束讨论轮次")
    return append_event(
        project_id,
        "round_closed",
        make_actor(session_id),
        summary,
        round_no=round_no,
        data={"round": round_no},
    )


def speak(
    project_id: str,
    session_id: str,
    text: str,
    round_no: int,
    speak_type: str = "proposal",
    title: str | None = None,
    choice: str | None = None,
    force: bool = False,
) -> dict:
    if speak_type not in SPEAK_TYPES:
        raise ValueError(f"speak 类型必须是: {', '.join(sorted(SPEAK_TYPES))}")
    cfg = session_config(session_id)
    role = cfg.get("role", "worker")
    if role != "manager" and not force:
        turn = turn_state(list_events(project_id))
        if turn["current"] != session_id:
            raise PermissionError(
                f"当前未轮到 {session_id} 发言（当前: {turn['current'] or '无人'}）。"
                "请等待总管分配发言；若为人工补救可加 --force。"
            )
    title = title or f"第 {round_no} 轮 {session_id} 发言"
    rel = f"discussion/rounds/R{round_no:04d}_{session_id}_{compact_stamp()}.md"
    _write_body(project_id, rel, title, text)
    data = {"speak_type": speak_type}
    if choice:
        data["choice"] = choice
    ev = append_event(
        project_id,
        speak_type,
        make_actor(session_id),
        _short(text),
        body_file=rel,
        round_no=round_no,
        data=data,
    )
    if role != "manager":
        send_message(
            project_id,
            session_id,
            manager_session(),
            SPEAK_TO_MESSAGE[speak_type],
            f"{session_id} 第 {round_no} 轮发言",
            text,
            refs=[rel, ev["event_id"]],
        )
    return ev


# ---------------------------------------------------------------- plans & approval


def propose_plan(
    project_id: str,
    session_id: str,
    version: str,
    title: str,
    text: str,
    requires_user_approval: bool = True,
) -> dict:
    if session_config(session_id).get("role") != "manager":
        raise PermissionError("只有总管可以提交方案")
    safe_version = "".join(c for c in version if c.isalnum() or c in "-_.") or "v1"
    rel = f"decisions/PLAN-{safe_version}.md"
    if (ensure_project(project_id) / rel).exists():
        raise ValueError(f"方案版本 {version} 已存在；请用新版本号，不要覆盖旧方案")
    _write_body(project_id, rel, f"方案 {version}：{title}", text)
    ev = append_event(
        project_id,
        "plan_proposed",
        make_actor(session_id),
        f"提交方案 {version}：{title}（等待用户批准）",
        body_file=rel,
        data={
            "version": version,
            "title": title,
            "requires_user_approval": requires_user_approval,
        },
    )
    for sid, cfg in all_sessions().items():
        if sid == session_id or not cfg.get("enabled", True):
            continue
        send_message(
            project_id,
            session_id,
            sid,
            "request_broadcast",
            f"方案 {version} 已提交，等待用户批准",
            "批准前不得开始实施。请阅读 decisions 下的方案文件，准备审批后的任务。",
            refs=[rel, ev["event_id"]],
        )
    return ev


def approve_plan(project_id: str, approve: bool, note: str = "", by: str = "用户") -> dict:
    events = list_events(project_id)
    plan = plan_state(events)
    if not plan.get("pending"):
        raise PermissionError("当前没有等待用户批准的方案")
    version = plan["pending"]["version"]
    etype = "user_approval" if approve else "user_rejection"
    ev = append_event(
        project_id,
        etype,
        USER_ACTOR,
        ("用户批准方案 " if approve else "用户驳回方案 ") + str(version) + (f"：{_short(note, 80)}" if note else ""),
        body=note or None,
        data={"plan_version": version, "note": note, "by": by},
    )
    send_message(
        project_id,
        "USER",
        manager_session(),
        "plan_approval" if approve else "plan_rejection",
        "用户批准方案" if approve else "用户驳回方案",
        f"方案版本 {version}\n备注：{note or '无'}",
        refs=[ev["event_id"]],
    )
    return ev


def publish_revision(
    project_id: str,
    session_id: str,
    version: str,
    title: str,
    text: str,
    requires_user_approval: bool = False,
    changes: str = "",
) -> dict:
    if session_config(session_id).get("role") != "manager":
        raise PermissionError("只有总管可以发布修订")
    safe_version = "".join(c for c in version if c.isalnum() or c in "-_.") or "rev1"
    rel = f"decisions/REV-{safe_version}.md"
    if (ensure_project(project_id) / rel).exists():
        raise ValueError(f"修订版本 {version} 已存在；请用新版本号，不要覆盖旧修订")
    _write_body(project_id, rel, f"修订 {version}：{title}", text)
    ev = append_event(
        project_id,
        "revision_published",
        make_actor(session_id),
        f"发布修订 {version}：{title}",
        body_file=rel,
        data={
            "version": version,
            "title": title,
            "requires_user_approval": requires_user_approval,
            "changes": changes,
        },
    )
    for sid, cfg in all_sessions().items():
        if sid == session_id or not cfg.get("enabled", True):
            continue
        send_message(
            project_id,
            session_id,
            sid,
            "request_broadcast",
            f"修订 {version} 已发布",
            text,
            refs=[rel, ev["event_id"]],
        )
    return ev


def record_decision(project_id: str, session_id: str, title: str, text: str, data: dict | None = None) -> dict:
    pdir = ensure_project(project_id)
    nums = [int(p.stem[1:]) for p in (pdir / "decisions").glob("D*.md") if p.stem[1:].isdigit()]
    rel = f"decisions/D{max(nums, default=0) + 1:04d}_{compact_stamp()}.md"
    _write_body(project_id, rel, title, text)
    return append_event(
        project_id,
        "decision",
        make_actor(session_id),
        title,
        body_file=rel,
        data=data or {},
    )


# ---------------------------------------------------------------- tasks


def _require_implementation(project_id: str) -> None:
    plan = plan_state(list_events(project_id))
    if plan.get("pending"):
        raise PermissionError("方案尚未获得用户批准，不能创建或开始实施任务")
    if not plan.get("implementation_allowed"):
        raise PermissionError("没有已批准方案，不能创建或开始实施任务")


def _dependency_ids(task: dict) -> set[str]:
    """Extract T001-style ids; dependency text may contain explanations."""
    ids: set[str] = set()
    for raw in task.get("dependencies") or []:
        ids.update(re.findall(r"\bT\d{3}\b", str(raw)))
    return ids


def _require_dependencies_done(project_id: str, task: dict) -> None:
    deps = _dependency_ids(task)
    if not deps:
        return
    by_id = {t["task_id"]: t for t in list_tasks(project_id)}
    not_done = [
        d
        for d in sorted(deps)
        if d != task["task_id"] and (by_id.get(d) or {}).get("status") != "done"
    ]
    if not_done:
        raise PermissionError(
            "依赖未完成: "
            + ", ".join(not_done)
            + "。请先拆分不依赖的部分为独立任务，或等依赖 done；不要在未解阻的任务上偷跑。"
        )


def create_task(
    project_id: str,
    manager_sid: str,
    *,
    title: str,
    owner: str,
    acceptance: list[str],
    goal: str = "",
    background: str = "",
    inputs: list[str] | None = None,
    reviewer: str | None = None,
    scope: list[str] | None = None,
    forbidden: list[str] | None = None,
    dependencies: list[str] | None = None,
    deliverables: list[str] | None = None,
) -> dict:
    if session_config(manager_sid).get("role") != "manager":
        raise PermissionError("只有总管可以创建任务")
    _require_implementation(project_id)
    tid = next_task_id(project_id)
    ts = now_iso()
    task = {
        "task_id": tid,
        "project_id": project_id,
        "title": title,
        "goal": goal,
        "background": background,
        "inputs": inputs or [],
        "owner_session": owner,
        "reviewer_session": reviewer or "",
        "scope": scope or [],
        "forbidden": forbidden or [],
        "dependencies": dependencies or [],
        "deliverables": deliverables or [],
        "acceptance": acceptance,
        "status": "open",
        "created_at": ts,
        "updated_at": ts,
        "result_links": [],
        "blocked_reason": "",
    }
    validate_task(project_id, task)
    write_task(project_id, task)
    assignment_ev = append_event(
        project_id,
        "assignment",
        make_actor(manager_sid),
        f"{tid} 分派给 {owner}：{title}",
        data={"task_id": tid, "owner_session": owner, "reviewer_session": task["reviewer_session"]},
    )
    task["created_seq"] = assignment_ev["seq"]
    write_task(project_id, task)
    send_message(
        project_id,
        manager_sid,
        owner,
        "assignment",
        f"任务 {tid}：{title}",
        f"任务卡: projects/{project_id}/tasks/{tid}.json\n请先读任务卡，确认范围与验收标准后开始。",
        task_id=tid,
        refs=[f"tasks/{tid}.json"],
    )
    return task


def reassign_task(
    project_id: str,
    manager_sid: str,
    task_id: str,
    new_owner: str,
    reason: str,
    new_reviewer: str | None = None,
) -> dict:
    """Manager-only reassignment when an owner is disabled/overloaded."""
    if session_config(manager_sid).get("role") != "manager":
        raise PermissionError("只有总管可以改派任务")
    task = get_task(project_id, task_id)
    if task.get("status") == "done":
        raise PermissionError("done 任务不能改派；如需返工请重新建任务")
    cfg = session_config(new_owner)
    if cfg.get("role") == "manager":
        raise ValueError("不能把实施任务改派给总管")
    if not cfg.get("enabled", True):
        raise ValueError(f"新 owner 会话未启用: {new_owner}")
    if new_reviewer:
        rcfg = session_config(new_reviewer)
        if not rcfg.get("enabled", True):
            raise ValueError(f"新 reviewer 会话未启用: {new_reviewer}")
        if new_reviewer == new_owner:
            raise ValueError("owner 与 reviewer 不能是同一会话")
    old_owner = task.get("owner_session")
    old_reviewer = task.get("reviewer_session")
    if old_owner == new_owner and (not new_reviewer or new_reviewer == old_reviewer):
        return task
    task["owner_session"] = new_owner
    if new_reviewer:
        task["reviewer_session"] = new_reviewer
    task["updated_at"] = now_iso()
    write_task(project_id, task)
    ev = append_event(
        project_id,
        "assignment",
        make_actor(manager_sid),
        f"{task_id} 改派: {old_owner} → {new_owner}（{_short(reason, 80)}）",
        data={
            "task_id": task_id,
            "from_owner": old_owner,
            "to_owner": new_owner,
            "from_reviewer": old_reviewer,
            "to_reviewer": task.get("reviewer_session"),
            "reason": reason,
        },
    )
    send_message(
        project_id,
        manager_sid,
        new_owner,
        "assignment",
        f"任务 {task_id} 已改派给你",
        f"{reason}\n请先读任务卡 tasks/{task_id}.json，确认范围/依赖/验收再接手。",
        task_id=task_id,
        refs=[f"tasks/{task_id}.json", ev["event_id"]],
    )
    if old_owner and old_owner != new_owner and old_owner in all_sessions():
        send_message(
            project_id,
            manager_sid,
            old_owner,
            "correction",
            f"任务 {task_id} 已改派给 {new_owner}",
            reason,
            task_id=task_id,
        )
    return task


def update_task_status(
    project_id: str,
    task_id: str,
    new_status: str,
    session_id: str,
    reason: str,
    evidence: str = "",
) -> dict:
    task = get_task(project_id, task_id)
    old = task.get("status", "open")
    if new_status not in TASK_TRANSITIONS.get(old, set()):
        raise ValueError(f"非法状态流转: {old} -> {new_status}（允许: {sorted(TASK_TRANSITIONS.get(old, set()))}）")
    if new_status == "in_progress":
        _require_implementation(project_id)
        _require_dependencies_done(project_id, task)
    if new_status == "done":
        ok = [
            e
            for e in list_events(project_id)
            if e.get("type") == "review_result"
            and (e.get("data", {}) or {}).get("task_id") == task_id
            and (e.get("data", {}) or {}).get("result") in ("pass", "approved")
            and e["seq"] > task.get("created_seq", 0)
        ]
        if not ok:
            raise PermissionError("任务进入 done 前必须有 review_result=pass 的审查事件")
        if task.get("reviewer_session") and session_id not in (task["reviewer_session"], manager_session()):
            raise PermissionError("只有审查者或总管可以确认任务完成")
    task["status"] = new_status
    task["updated_at"] = now_iso()
    if new_status == "blocked":
        task["blocked_reason"] = reason
    elif old == "blocked":
        task["blocked_reason"] = ""
    if evidence:
        task.setdefault("result_links", []).append(evidence)
    write_task(project_id, task)
    ev = append_event(
        project_id,
        "task_status",
        make_actor(session_id),
        f"{task_id} {old} → {new_status}：{_short(reason, 100)}",
        data={
            "task_id": task_id,
            "from": old,
            "to": new_status,
            "reason": reason,
            "evidence": evidence,
        },
    )
    if new_status == "blocked":
        send_message(
            project_id,
            session_id,
            manager_session(),
            "blocker",
            f"{task_id} 阻塞",
            reason,
            task_id=task_id,
        )
    elif new_status in ("review", "done"):
        send_message(
            project_id,
            session_id,
            manager_session(),
            "progress",
            f"{task_id} → {new_status}",
            reason,
            task_id=task_id,
        )
    return ev


def report_review(
    project_id: str,
    session_id: str,
    task_id: str,
    result: str,
    note: str,
    evidence: str = "",
) -> dict:
    if result not in ("pass", "approved", "fail", "changes_requested"):
        raise ValueError("review result 必须是 pass/approved/fail/changes_requested")
    get_task(project_id, task_id)
    ev = append_event(
        project_id,
        "review_result",
        make_actor(session_id),
        f"{task_id} 审查结果：{result}",
        body=note,
        data={"task_id": task_id, "result": result, "evidence": evidence},
    )
    task = get_task(project_id, task_id)
    for target, mtype in ((task["owner_session"], "review_result"), (manager_session(), "review_result")):
        if target == session_id:
            continue
        send_message(
            project_id,
            session_id,
            target,
            mtype,
            f"{task_id} 审查：{result}",
            note,
            task_id=task_id,
        )
    return ev


# ---------------------------------------------------------------- meetings & handoff


def request_meeting(project_id: str, session_id: str, reason: str, evidence: str = "") -> dict:
    ev = append_event(
        project_id,
        "meeting_request",
        make_actor(session_id),
        f"申请临时讨论：{_short(reason, 100)}",
        body=reason,
        data={"evidence": evidence},
    )
    send_message(project_id, session_id, manager_session(), "blocker", "临时讨论申请", reason)
    return ev


def open_meeting(project_id: str, session_id: str, reason: str) -> dict:
    if session_config(session_id).get("role") != "manager":
        raise PermissionError("只有总管可以宣布临时讨论")
    ev = append_event(
        project_id,
        "meeting_open",
        make_actor(session_id),
        f"临时讨论开始：{_short(reason, 100)}",
        body=reason,
    )
    for sid, cfg in all_sessions().items():
        if sid == session_id or not cfg.get("enabled", True):
            continue
        send_message(
            project_id,
            session_id,
            sid,
            "request_broadcast",
            "临时讨论开始",
            f"{reason}\n受影响任务先暂停新增修改，保存工作点，等待发言顺序。",
        )
    return ev


def close_meeting(project_id: str, session_id: str, summary: str) -> dict:
    if session_config(session_id).get("role") != "manager":
        raise PermissionError("只有总管可以结束临时讨论")
    ev = append_event(
        project_id,
        "meeting_closed",
        make_actor(session_id),
        f"临时讨论结束：{_short(summary, 100)}",
        body=summary,
    )
    for sid, cfg in all_sessions().items():
        if sid == session_id or not cfg.get("enabled", True):
            continue
        send_message(
            project_id,
            session_id,
            sid,
            "request_broadcast",
            "临时讨论结束",
            summary,
        )
    return ev


def handoff(
    project_id: str,
    session_id: str,
    task_id: str,
    title: str,
    text: str,
    files: list[str] | None = None,
    checks: str = "",
    limits: str = "",
) -> dict:
    stamp = compact_stamp()
    handoff_id = f"H-{stamp}-{task_id}"
    rel = f"handoffs/{handoff_id}.md"
    files = files or []
    content = (
        f"## 交接信息\n\n- handoff_id: `{handoff_id}`\n"
        f"- task_id: `{task_id}`\n- 发送者: `{session_id}`\n- 时间: {now_iso()}\n\n"
        f"## 文件清单\n\n" + ("\n".join(f"- `{f}`" for f in files) or "- 无") + "\n\n"
        f"## 验证结果\n\n{checks or '未报告'}\n\n## 已知限制\n\n{limits or '无'}\n\n"
        f"## 正文\n\n{text.strip()}\n"
    )
    atomic_write_text(ensure_project(project_id) / rel, f"# {title}\n\n{content}")
    ev = append_event(
        project_id,
        "handoff",
        make_actor(session_id),
        f"{task_id} 交付：{title}",
        body_file=rel,
        data={"task_id": task_id, "files": files, "checks": checks, "limits": limits},
    )
    send_message(
        project_id,
        session_id,
        manager_session(),
        "handoff_notice",
        f"{task_id} 交付",
        text,
        task_id=task_id,
        refs=[rel],
    )
    task = get_task(project_id, task_id)
    if task.get("reviewer_session"):
        send_message(
            project_id,
            session_id,
            task["reviewer_session"],
            "handoff_notice",
            f"{task_id} 待审查",
            f"请按验收标准审查 {task_id}。交接件: {rel}",
            task_id=task_id,
            refs=[rel],
        )
    return ev


def reopen_project(project_id: str, session_id: str, reason: str) -> dict:
    """Reopen a completed project for a follow-up request.

    Small follow-ups can then be handled under the existing approved plan; if
    the scope changes, the manager should submit a new plan and get approval.
    """
    if session_config(session_id).get("role") != "manager":
        raise PermissionError("只有总管可以重新打开项目")
    events = list_events(project_id)
    done = next((e for e in reversed(events) if e.get("type") == "project_done"), None)
    if not done:
        raise PermissionError("项目还没有完成，不需要重新打开")
    reopened = next((e for e in reversed(events) if e.get("type") == "project_reopened"), None)
    if reopened and reopened["seq"] > done["seq"]:
        return reopened
    if not reason.strip():
        raise ValueError("重新打开项目需要说明原因")
    ev = append_event(
        project_id,
        "project_reopened",
        make_actor(session_id),
        f"重新打开项目：{_short(reason, 100)}",
        body=reason,
    )
    for sid, cfg in all_sessions().items():
        if sid == session_id or not cfg.get("enabled", True):
            continue
        send_message(
            project_id,
            session_id,
            sid,
            "request_broadcast",
            "项目重新打开",
            f"{reason}\n先不要自行开工；等待总管安排讨论或任务。",
            refs=[ev["event_id"]],
        )
    return ev


def finish_project(project_id: str, session_id: str, summary: str, evidence: str = "") -> dict:
    if session_config(session_id).get("role") != "manager":
        raise PermissionError("只有总管可以宣布项目完成")
    tasks = list_tasks(project_id)
    unfinished = [t["task_id"] for t in tasks if t.get("status") != "done"]
    if unfinished:
        raise PermissionError(f"仍有未验收任务: {', '.join(unfinished)}")
    append_event(
        project_id,
        "acceptance",
        make_actor(session_id),
        f"验收通过：{_short(summary, 100)}",
        body=summary,
        data={"evidence": evidence, "task_count": len(tasks)},
    )
    ev = append_event(
        project_id,
        "project_done",
        make_actor(session_id),
        f"项目完成：{_short(summary, 100)}",
        body=summary,
    )
    return ev


def project_snapshot(project_id: str) -> dict:
    return project_state(project_id)
