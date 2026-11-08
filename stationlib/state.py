from __future__ import annotations

from pathlib import Path

from .config import all_sessions
from .paths import project_dir
from .staffing import staffing_info
from .store import (
    list_events,
    list_inbox,
    list_tasks,
    read_heartbeat,
    read_json,
    task_summary,
    now_iso,
)

SPEECH_TYPES = {"proposal", "critique", "response", "vote", "question"}
CHAT_TYPES = {
    "user_message",
    "manager_chat",
    "user_question",
    "user_answer",
    "user_approval",
    "user_rejection",
}
PLAN_TYPES = {"plan_proposed", "revision_published"}

PHASE_LABELS = {
    "intake": "接需求",
    "discussion": "全员讨论",
    "awaiting_approval": "等待用户批准",
    "implementation": "实施中",
    "revision": "临时拉会/修订",
    "done": "已完成",
}


def _latest(events: list[dict], types: set[str] | str) -> dict | None:
    if isinstance(types, str):
        types = {types}
    for e in reversed(events):
        if e.get("type") in types:
            return e
    return None


def _after(events: list[dict], seq: int) -> list[dict]:
    return [e for e in events if e.get("seq", 0) > seq]


def plan_state(events: list[dict]) -> dict:
    """Derive plan/approval state from the event log only."""
    latest = _latest(events, PLAN_TYPES)
    out = {
        "version": None,
        "seq": None,
        "title": None,
        "requires_user_approval": None,
        "pending": None,
        "approved_version": None,
        "approved_seq": None,
        "last_decision": None,
        "implementation_allowed": False,
        "body_file": None,
    }
    if not latest:
        return out

    data = latest.get("data", {}) or {}
    version = data.get("version") or latest.get("event_id")
    requires = latest["type"] == "plan_proposed" or bool(data.get("requires_user_approval"))
    out.update(
        {
            "version": version,
            "seq": latest["seq"],
            "title": data.get("title") or latest.get("summary"),
            "requires_user_approval": requires,
            "body_file": latest.get("body_file"),
        }
    )

    if requires:
        decision = None
        for e in _after(events, latest["seq"]):
            if e.get("type") in ("user_approval", "user_rejection"):
                decided_version = (e.get("data", {}) or {}).get("plan_version")
                if decided_version in (None, version):
                    decision = e
        if decision:
            out["last_decision"] = decision
            if decision["type"] == "user_approval":
                out["implementation_allowed"] = True
                out["approved_version"] = version
                out["approved_seq"] = decision["seq"]
        else:
            out["pending"] = {
                "version": version,
                "seq": latest["seq"],
                "title": out["title"],
                "since": latest.get("ts"),
            }
    else:
        # Internal revision: inherits the latest user approval of the baseline plan.
        approval = _latest(events, "user_approval")
        if approval:
            out["implementation_allowed"] = True
            out["approved_version"] = (approval.get("data", {}) or {}).get("plan_version")
            out["approved_seq"] = approval["seq"]
    return out


def turn_state(events: list[dict]) -> dict:
    turn = _latest(events, "turn_granted")
    if not turn:
        return {"current": None, "round": None, "topic": None, "since": None}
    later = _after(events, turn["seq"])
    if any(e.get("type") == "round_closed" for e in later):
        return {"current": None, "round": turn.get("round"), "topic": None, "since": None}
    sid = (turn.get("data", {}) or {}).get("session_id")
    if any(
        e.get("type") in SPEECH_TYPES and (e.get("actor", {}) or {}).get("session_id") == sid
        for e in later
    ):
        return {"current": None, "round": turn.get("round"), "topic": None, "since": None}
    return {
        "current": sid,
        "round": turn.get("round"),
        "topic": (turn.get("data", {}) or {}).get("topic"),
        "since": turn.get("ts"),
    }


def meeting_state(events: list[dict]) -> dict:
    opened = _latest(events, "meeting_open")
    closed = _latest(events, "meeting_closed")
    requested = _latest(events, "meeting_request")
    active = bool(opened and (not closed or opened["seq"] > closed["seq"]))
    pending_request = bool(
        requested
        and (not opened or requested["seq"] > opened["seq"])
        and (not closed or requested["seq"] > closed["seq"])
    )
    return {
        "active": active,
        "pending_request": pending_request,
        "event": opened if active else None,
    }


def pending_user_question(events: list[dict]) -> dict | None:
    q = _latest(events, "user_question")
    if not q:
        return None
    if any(e.get("type") in ("user_answer", "user_message") for e in _after(events, q["seq"])):
        return None
    return {
        "seq": q["seq"],
        "question": q.get("summary") or q.get("body") or "",
        "since": q.get("ts"),
    }


def derive_phase(events: list[dict], plan: dict, meeting: dict) -> dict:
    done = _latest(events, "project_done")
    reopened_seq = None
    if done:
        reopen = _latest(events, "project_reopened")
        if reopen and reopen["seq"] > done["seq"]:
            reopened_seq = reopen["seq"]
        # A user message after completion also reopens the project implicitly.
        user_after = [
            e for e in _after(events, done["seq"]) if e.get("type") == "user_message"
        ]
        if user_after:
            reopened_seq = max(reopened_seq or 0, user_after[-1]["seq"])
        if reopened_seq is None:
            return {"key": "done", "label": PHASE_LABELS["done"]}
    if meeting.get("active"):
        return {"key": "revision", "label": PHASE_LABELS["revision"]}
    if reopened_seq is not None:
        work_types = PLAN_TYPES | {"assignment", "task_status"}
        has_new_work = any(
            e["seq"] > reopened_seq and e.get("type") in work_types for e in events
        )
        if not has_new_work:
            return {"key": "discussion", "label": PHASE_LABELS["discussion"]}
    if plan.get("pending"):
        return {"key": "awaiting_approval", "label": PHASE_LABELS["awaiting_approval"]}
    if plan.get("implementation_allowed"):
        return {"key": "implementation", "label": PHASE_LABELS["implementation"]}
    if _latest(events, "manager_opening") or _latest(events, "user_message"):
        return {"key": "discussion", "label": PHASE_LABELS["discussion"]}
    return {"key": "intake", "label": PHASE_LABELS["intake"]}


def _inbox_counts(project_id: str, session_id: str) -> dict:
    try:
        ready = len(list_inbox(project_id, session_id))
        base = project_dir(project_id) / "inbox" / session_id
        processing = len(list((base / "processing").glob("*.json")))
        processed = len(list((base / "processed").glob("*.json")))
        failed = len(list((base / "failed").glob("*.json")))
    except Exception:
        ready = processing = processed = failed = 0
    return {
        "ready": ready,
        "processing": processing,
        "processed": processed,
        "failed": failed,
    }


def project_state(project_id: str, event_limit: int = 500) -> dict:
    pdir = project_dir(project_id)
    meta = read_json(pdir / "meta.json")
    if not meta:
        raise FileNotFoundError(f"项目不存在或缺少 meta.json: {project_id}")
    workspace = meta.get("workspace")
    workspace_exists = bool(workspace and Path(workspace).is_dir())

    events = list_events(project_id)
    plan = plan_state(events)
    turn = turn_state(events)
    meeting = meeting_state(events)
    phase = derive_phase(events, plan, meeting)
    tasks = list_tasks(project_id)
    proj_cfg = all_sessions()

    sessions = []
    for sid, cfg in proj_cfg.items():
        hb = read_heartbeat(project_id, sid)
        enabled = cfg.get("enabled", True)
        if not enabled:
            # A recently stopped worker can still have a fresh heartbeat file;
            # a disabled session is never "online".
            hb["online"] = False
            hb["status"] = "disabled"
        activity = None
        for e in reversed(events):
            if (e.get("actor", {}) or {}).get("session_id") == sid:
                activity = {"seq": e["seq"], "ts": e.get("ts"), "type": e.get("type"), "summary": e.get("summary")}
                break
        sessions.append(
            {
                "session_id": sid,
                "name": cfg.get("name", sid),
                "role": cfg.get("role", "worker"),
                "engine": cfg.get("engine", ""),
                "enabled": enabled,
                "status": hb.get("status", "never"),
                "online": hb.get("online", False),
                "last_heartbeat": hb.get("updated_at"),
                "last_activity": activity,
                "inbox": _inbox_counts(project_id, sid),
            }
        )

    chat = []
    for e in events:
        if e.get("type") not in CHAT_TYPES:
            continue
        chat.append(
            {
                "seq": e["seq"],
                "type": e["type"],
                "ts": e.get("ts"),
                "actor": e.get("actor"),
                "summary": e.get("summary"),
                "body": e.get("body") or e.get("summary"),
                "data": e.get("data"),
            }
        )

    timeline = []
    for e in events[-event_limit:]:
        item = {
            "seq": e["seq"],
            "event_id": e.get("event_id"),
            "type": e.get("type"),
            "ts": e.get("ts"),
            "actor": e.get("actor"),
            "summary": e.get("summary"),
            "round": e.get("round"),
            "body": e.get("body"),
            "body_file": e.get("body_file"),
            "has_body": bool(e.get("body") or e.get("body_file")),
            "data": e.get("data"),
        }
        timeline.append(item)

    summary = task_summary(tasks)
    cost = read_json(pdir / "logs" / "cost.json", {}) or {}
    pending_q = pending_user_question(events)
    mgr_ready = next(
        (s["inbox"]["ready"] for s in sessions if s["role"] == "manager"), 0
    )
    stalled = bool(
        phase["key"] in ("discussion", "revision")
        and not turn.get("current")
        and not plan.get("pending")
        and not pending_q
        and mgr_ready == 0
        and _latest(events, "manager_opening")
    )
    return {
        "project": meta,
        "workspace_exists": workspace_exists,
        "phase": phase,
        "plan": plan,
        "turn": turn,
        "stalled": stalled,
        "stall_reason": "讨论停滞：总管没有安排下一位发言人" if stalled else None,
        "meeting": meeting,
        "pending_user_question": pending_q,
        "events": timeline,
        "event_count": len(events),
        "tasks": tasks,
        "task_summary": summary,
        "cost": cost,
        "staffing": staffing_info(),
        "sessions": sessions,
        "chat": chat,
        "decisions": [
            {
                "seq": e["seq"],
                "ts": e.get("ts"),
                "summary": e.get("summary"),
                "body_file": e.get("body_file"),
                "data": e.get("data"),
            }
            for e in events
            if e.get("type") == "decision"
        ],
        "updated_at": now_iso(),
    }
