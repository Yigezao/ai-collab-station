from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .config import session_config
from .paths import PROJECT_SUBDIRS, check_session_id, project_dir

EVENT_RE = re.compile(r"^E(\d{6})\.json$")

EVENT_TYPES = {
    "project_created",
    "request_registered",
    "manager_opening",
    "turn_granted",
    "round_closed",
    "proposal",
    "critique",
    "response",
    "vote",
    "question",
    "user_question",
    "user_answer",
    "user_message",
    "manager_chat",
    "plan_proposed",
    "user_approval",
    "user_rejection",
    "plan_approved",
    "plan_rejected",
    "assignment",
    "task_status",
    "progress",
    "blocker",
    "staffing_change",
    "meeting_request",
    "meeting_open",
    "meeting_closed",
    "revision_proposed",
    "revision_published",
    "decision",
    "handoff",
    "review_result",
    "acceptance",
    "project_done",
    "project_reopened",
    "worker_error",
    "system",
}

MESSAGE_TYPES = {
    "request_broadcast",
    "user_request",
    "discussion_turn",
    "proposal",
    "critique",
    "vote",
    "plan_approval",
    "plan_rejection",
    "plan_approved",
    "assignment",
    "progress",
    "blocker",
    "correction",
    "handoff_notice",
    "review_result",
    "manager_chat",
    "system",
}

TASK_STATUSES = {"open", "in_progress", "review", "done", "blocked"}

TASK_TRANSITIONS = {
    "open": {"in_progress", "blocked"},
    "in_progress": {"review", "blocked"},
    "review": {"done", "in_progress"},
    "blocked": {"in_progress"},
    "done": set(),
}

TASK_FIELDS = [
    "task_id",
    "project_id",
    "title",
    "goal",
    "background",
    "inputs",
    "owner_session",
    "reviewer_session",
    "scope",
    "forbidden",
    "dependencies",
    "deliverables",
    "acceptance",
    "status",
    "created_at",
    "updated_at",
    "result_links",
    "blocked_reason",
]


# ---------------------------------------------------------------- basics


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def compact_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _replace_with_retry(tmp: Path, path: Path, attempts: int = 30) -> None:
    """os.replace on Windows can hit a transient sharing violation while a reader
    has the target open. Retry briefly instead of losing the write."""
    last = None
    for _ in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(0.01)
    raise last


def atomic_write_text(path: Path, text: str) -> None:
    """Write then rename in the same directory, so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(text, encoding="utf-8")
        _replace_with_retry(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def file_lock(lock_path: Path, timeout: float = 15.0, stale: float = 60.0):
    """Cross-process short lock. Stale locks are broken after `stale` seconds."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.time():.3f}\n".encode())
            os.close(fd)
            break
        except (FileExistsError, PermissionError):
            # PermissionError here is Windows' transient sharing violation while
            # another writer releases the lock file. Treat it as contention.
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > stale:
                    try:
                        lock_path.unlink()
                    except OSError:
                        pass
                    continue
            except FileNotFoundError:
                continue
            except PermissionError:
                pass
            if time.time() > deadline:
                raise TimeoutError(f"获取锁超时: {lock_path}")
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def ensure_project(project_id: str, create: bool = False) -> Path:
    pdir = project_dir(project_id)
    if not pdir.exists():
        if not create:
            raise FileNotFoundError(f"项目不存在: {project_id}")
        pdir.mkdir(parents=True)
    if create:
        for sub in PROJECT_SUBDIRS:
            (pdir / sub).mkdir(parents=True, exist_ok=True)
        for sid in [s for s in _known_sessions()]:
            (pdir / "inbox" / sid).mkdir(parents=True, exist_ok=True)
    return pdir


def _known_sessions() -> list[str]:
    try:
        from .config import all_sessions

        return list(all_sessions().keys())
    except Exception:
        return []


# ---------------------------------------------------------------- events


def _max_seq(events_dir: Path) -> int:
    top = 0
    for p in events_dir.iterdir():
        m = EVENT_RE.match(p.name)
        if m:
            top = max(top, int(m.group(1)))
    return top


def append_event(
    project_id: str,
    event_type: str,
    actor: dict,
    summary: str,
    *,
    body: str | None = None,
    body_file: str | None = None,
    data: dict | None = None,
    refs: list | None = None,
    round_no: int | None = None,
) -> dict:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"未知事件类型: {event_type}（允许: {', '.join(sorted(EVENT_TYPES))}）")
    pdir = ensure_project(project_id)
    events_dir = pdir / "events"
    with file_lock(events_dir / ".lock"):
        seq = _max_seq(events_dir) + 1
        ev = {
            "seq": seq,
            "event_id": f"E{seq:06d}",
            "project_id": project_id,
            "type": event_type,
            "ts": now_iso(),
            "actor": actor,
            "summary": summary,
        }
        if round_no is not None:
            ev["round"] = round_no
        if body:
            ev["body"] = body
        if body_file:
            ev["body_file"] = body_file.replace("\\", "/")
        if data:
            ev["data"] = data
        if refs:
            ev["refs"] = refs
        atomic_write_json(events_dir / f"E{seq:06d}.json", ev)
    return ev


def list_events(project_id: str, after_seq: int = 0) -> list[dict]:
    pdir = ensure_project(project_id)
    out = []
    for p in (pdir / "events").iterdir():
        m = EVENT_RE.match(p.name)
        if not m:
            continue
        seq = int(m.group(1))
        if seq <= after_seq:
            continue
        ev = read_json(p)
        if ev:
            out.append(ev)
    out.sort(key=lambda e: e["seq"])
    return out


def event_by_id(project_id: str, event_id: str) -> dict | None:
    pdir = ensure_project(project_id)
    return read_json(pdir / "events" / f"{event_id}.json")


# ---------------------------------------------------------------- inbox


def inbox_dir(project_id: str, session_id: str) -> Path:
    check_session_id(session_id)
    d = ensure_project(project_id) / "inbox" / session_id
    for sub in ("processing", "processed", "failed"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def send_message(
    project_id: str,
    from_session: str,
    to_session: str,
    msg_type: str,
    subject: str,
    body: str,
    *,
    task_id: str | None = None,
    refs: list | None = None,
    from_role: str | None = None,
    message_id: str | None = None,
) -> dict:
    if msg_type not in MESSAGE_TYPES:
        raise ValueError(f"未知消息类型: {msg_type}")
    check_session_id(to_session)
    if from_session != "USER":
        check_session_id(from_session)
    if from_role is None:
        from_role = "user" if from_session == "USER" else session_config(from_session).get("role", "worker")
    mid = message_id or f"MSG-{compact_stamp()}-{from_session}-{uuid.uuid4().hex[:6]}"
    msg = {
        "message_id": mid,
        "project_id": project_id,
        "from_session": from_session,
        "from_role": from_role,
        "to_session": to_session,
        "created_at": now_iso(),
        "type": msg_type,
        "subject": subject,
        "body": body,
        "task_id": task_id,
        "refs": refs or [],
        "status": "ready",
    }
    d = inbox_dir(project_id, to_session)
    atomic_write_json(d / f"{compact_stamp()}_{mid}.json", msg)
    return msg


def list_inbox(project_id: str, session_id: str) -> list[dict]:
    d = inbox_dir(project_id, session_id)
    items = []
    for p in sorted(d.glob("*.json")):
        m = read_json(p)
        if m:
            m["_file"] = p.name
            items.append(m)
    items.sort(key=lambda m: (m.get("created_at", ""), m.get("message_id", "")))
    return items


def claim_messages(project_id: str, session_id: str, limit: int = 1) -> list[tuple[Path, dict]]:
    """Atomically move up to `limit` ready messages into processing/."""
    d = inbox_dir(project_id, session_id)
    claimed: list[tuple[Path, dict]] = []
    for p in sorted(d.glob("*.json")):
        if len(claimed) >= limit:
            break
        dest = d / "processing" / p.name
        try:
            os.rename(p, dest)  # fails if already claimed (unlike os.replace)
        except (FileNotFoundError, FileExistsError, PermissionError):
            continue
        msg = read_json(dest)
        if not msg:
            continue
        claimed.append((dest, msg))
    return claimed


def finish_message(path: Path, msg: dict) -> None:
    msg = dict(msg)
    msg.pop("_file", None)
    msg["status"] = "processed"
    msg["processed_at"] = now_iso()
    dest = path.parent.parent / "processed" / path.name
    atomic_write_json(dest, msg)
    path.unlink(missing_ok=True)


def fail_message(path: Path, msg: dict, max_attempts: int = 3) -> str:
    d = path.parent.parent
    msg = dict(msg)
    msg.pop("_file", None)
    attempts = int(msg.get("attempts", 0)) + 1
    msg["attempts"] = attempts
    msg["last_error_at"] = now_iso()
    if attempts >= max_attempts:
        dest = d / "failed" / path.name
        msg["status"] = "failed"
        atomic_write_json(dest, msg)
        path.unlink(missing_ok=True)
        return "failed"
    msg["status"] = "ready"
    atomic_write_json(d / path.name, msg)
    path.unlink(missing_ok=True)
    return "retry"


# ---------------------------------------------------------------- heartbeat


def heartbeat(project_id: str, session_id: str, status: str, **extra) -> None:
    pdir = ensure_project(project_id)
    cfg = session_config(session_id)
    payload = {
        "session_id": session_id,
        "name": cfg.get("name", session_id),
        "role": cfg.get("role", "worker"),
        "engine": cfg.get("engine", ""),
        "status": status,
        "updated_at": now_iso(),
        "epoch": time.time(),
    }
    payload.update(extra)
    atomic_write_json(pdir / "logs" / "sessions" / f"{session_id}.json", payload)


def read_heartbeat(project_id: str, session_id: str, offline_after: int = 90) -> dict:
    pdir = ensure_project(project_id)
    p = pdir / "logs" / "sessions" / f"{session_id}.json"
    hb = read_json(p, {}) or {}
    if hb:
        age = time.time() - float(hb.get("epoch", 0))
        hb["age_sec"] = int(age)
        hb["online"] = age <= offline_after and hb.get("status") != "offline"
    else:
        hb = {"session_id": session_id, "status": "never", "online": False, "age_sec": None}
    return hb


# ---------------------------------------------------------------- tasks


def task_path(project_id: str, task_id: str) -> Path:
    return ensure_project(project_id) / "tasks" / f"{task_id}.json"


def list_tasks(project_id: str) -> list[dict]:
    pdir = ensure_project(project_id)
    out = []
    for p in sorted((pdir / "tasks").glob("T*.json")):
        t = read_json(p)
        if t:
            out.append(t)
    out.sort(key=lambda t: t.get("task_id", ""))
    return out


def get_task(project_id: str, task_id: str) -> dict:
    t = read_json(task_path(project_id, task_id))
    if not t:
        raise FileNotFoundError(f"任务不存在: {task_id}")
    return t


def next_task_id(project_id: str) -> str:
    existing = [t.get("task_id", "") for t in list_tasks(project_id)]
    nums = [int(t[1:]) for t in existing if t.startswith("T") and t[1:].isdigit()]
    return f"T{max(nums, default=0) + 1:03d}"


def write_task(project_id: str, task: dict) -> dict:
    with file_lock(ensure_project(project_id) / "tasks" / ".lock"):
        atomic_write_json(task_path(project_id, task["task_id"]), task)
    return task


def validate_task(project_id: str, task: dict) -> None:
    missing = [k for k in ("title", "owner_session", "acceptance") if not task.get(k)]
    if missing:
        raise ValueError(f"任务卡缺少必填字段: {', '.join(missing)}")
    if not list(task.get("acceptance") or []):
        raise ValueError("验收标准不能为空")
    if task.get("owner_session") not in _known_sessions():
        raise ValueError(f"owner_session 未登记: {task['owner_session']}")
    if task.get("reviewer_session") and task["reviewer_session"] not in _known_sessions():
        raise ValueError(f"reviewer_session 未登记: {task['reviewer_session']}")
    if not session_config(task["owner_session"]).get("enabled", True):
        raise ValueError(f"owner 会话未启用: {task['owner_session']}")
    if task.get("reviewer_session") and not session_config(task["reviewer_session"]).get("enabled", True):
        raise ValueError(f"reviewer 会话未启用: {task['reviewer_session']}")
    if task["owner_session"] == "MGR" or session_config(task["owner_session"]).get("role") == "manager":
        raise ValueError("总管不能成为实施任务 owner")
    if task.get("status", "open") not in TASK_STATUSES:
        raise ValueError(f"非法任务状态: {task.get('status')}")


def task_summary(tasks: list[dict]) -> dict:
    counts = {s: 0 for s in TASK_STATUSES}
    for t in tasks:
        counts[t.get("status", "open")] = counts.get(t.get("status", "open"), 0) + 1
    total = len(tasks)
    return {
        "total": total,
        "counts": counts,
        "done": counts.get("done", 0),
        "in_progress": counts.get("in_progress", 0),
        "blocked": counts.get("blocked", 0),
        "review": counts.get("review", 0),
        "open": counts.get("open", 0),
        # count-based only; no invented weighting
        "progress_text": f"{counts.get('done', 0)}/{total} 任务已验收",
    }
