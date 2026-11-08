"""Staffing policy: default max 4 employees, more only with user approval.

"Employee" means every configured session whose role is not manager.
The approval is a small file written by the user-only CLI command
`python station.py approve-staff --max-employees N`.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import all_sessions, load_config, reload_config
from .paths import CONFIG_FILE, ROOT
from .store import atomic_write_json, file_lock, now_iso, read_json

STAFF_FILE = ROOT / "config" / "staff_approval.json"


def default_max_employees() -> int:
    try:
        return int(load_config().get("staffing", {}).get("default_max_employees", 2))
    except (TypeError, ValueError):
        return 4


def _approval(path: Path | None = None) -> tuple[dict, int]:
    p = Path(path) if path else STAFF_FILE
    data = read_json(p, {}) or {}
    try:
        approved = int(data.get("max_employees") or 0)
    except (TypeError, ValueError):
        approved = 0
    return data, max(0, approved)


def staff_limit(path: Path | None = None) -> int:
    _, approved = _approval(path)
    return max(default_max_employees(), approved)


def employee_sessions() -> dict:
    return {
        sid: cfg
        for sid, cfg in all_sessions().items()
        if cfg.get("role", "worker") != "manager"
    }


def enabled_employees() -> dict:
    return {
        sid: cfg
        for sid, cfg in employee_sessions().items()
        if cfg.get("enabled", True)
    }


def staffing_info(path: Path | None = None) -> dict:
    total = employee_sessions()
    enabled = enabled_employees()
    data, approved = _approval(path)
    limit = staff_limit(path)
    count = len(enabled)
    return {
        "employees_total": len(total),
        "employees_enabled": count,
        "employee_ids": sorted(enabled),
        "limit": limit,
        "default_limit": default_max_employees(),
        "approved_max": approved or None,
        "approval_note": data.get("note", ""),
        "approval_at": data.get("approved_at"),
        "over_limit": count > limit,
    }


def count_allowed(count: int, path: Path | None = None) -> bool:
    return count <= staff_limit(path)


def check_staffing(path: Path | None = None) -> tuple[bool, dict]:
    info = staffing_info(path)
    return not info["over_limit"], info


def approve_staff(max_employees: int, note: str = "", path: Path | None = None) -> dict:
    if int(max_employees) < 1:
        raise ValueError("max_employees 必须 >= 1")
    data = {
        "max_employees": int(max_employees),
        "approved_at": now_iso(),
        "approved_by": "用户",
        "note": note,
    }
    atomic_write_json(Path(path) if path else STAFF_FILE, data)
    return data


def revoke_staff(path: Path | None = None) -> None:
    (Path(path) if path else STAFF_FILE).unlink(missing_ok=True)


def _count_enabled(cfg: dict, override_sid: str | None = None, override_enabled: bool | None = None) -> int:
    n = 0
    for sid, sc in (cfg.get("sessions") or {}).items():
        if sc.get("role", "worker") == "manager":
            continue
        enabled = sc.get("enabled", True)
        if sid == override_sid:
            enabled = bool(override_enabled)
        if enabled:
            n += 1
    return n


def set_employee_enabled(
    session_id: str,
    enabled: bool,
    actor: str = "用户",
    note: str = "",
    *,
    config_path: Path | None = None,
    approval_path: Path | None = None,
    log_path: Path | None = None,
) -> dict:
    """Flip enabled for one employee, enforcing the staffing limit.

    The caller (CLI) is responsible for authorization: only the manager or the
    user may call this. This function only enforces the count and writes config.
    """
    path = Path(config_path) if config_path else CONFIG_FILE
    cfg = json.loads(path.read_text(encoding="utf-8"))
    sessions = cfg.setdefault("sessions", {})
    if session_id not in sessions:
        raise KeyError(f"未登记会话: {session_id}")
    sc = sessions[session_id]
    if sc.get("role", "worker") == "manager":
        raise ValueError("不能启用/停用总管；总管由用户管理")

    after = _count_enabled(cfg, session_id, bool(enabled))
    limit = staff_limit(approval_path)
    if enabled and after > limit:
        raise PermissionError(
            f"启用后员工数为 {after}，超过上限 {limit}；"
            f"需要用户先运行 approve-staff --max-employees {after}"
        )

    sc["enabled"] = bool(enabled)
    atomic_write_json(path, cfg)
    if path.resolve() == CONFIG_FILE.resolve():
        reload_config()

    entry = {
        "ts": now_iso(),
        "actor": actor,
        "action": "enable" if enabled else "disable",
        "session_id": session_id,
        "employees_enabled": after,
        "limit": limit,
        "note": note,
    }
    log = Path(log_path) if log_path else ROOT / "logs" / "staff_changes.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(log.with_suffix(".lock")):
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {
        "session_id": session_id,
        "enabled": bool(enabled),
        "employees_enabled": after,
        "limit": limit,
    }
