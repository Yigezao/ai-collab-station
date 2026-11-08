from __future__ import annotations

import json
import threading

from .paths import CONFIG_FILE

_CACHE: dict = {"mtime": None, "data": None}
_LOCK = threading.Lock()


def load_config() -> dict:
    """Load station.json, refreshing automatically when the file changes.

    Long-running UI and worker processes must see staff changes made by another
    process (e.g. MGR enabling S03) without a restart.
    """
    mtime = CONFIG_FILE.stat().st_mtime_ns
    with _LOCK:
        if _CACHE["data"] is None or _CACHE["mtime"] != mtime:
            _CACHE["data"] = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            _CACHE["mtime"] = mtime
        return _CACHE["data"]


def reload_config() -> dict:
    with _LOCK:
        _CACHE["mtime"] = None
        _CACHE["data"] = None
    return load_config()


def session_config(session_id: str) -> dict:
    sessions = load_config().get("sessions", {})
    if session_id not in sessions:
        raise KeyError(f"未登记会话: {session_id}（请先在 config/station.json 中登记）")
    cfg = dict(sessions[session_id])
    cfg["session_id"] = session_id
    return cfg


def all_sessions() -> dict:
    return dict(load_config().get("sessions", {}))


def manager_session() -> str:
    for sid, cfg in all_sessions().items():
        if cfg.get("role") == "manager":
            return sid
    raise RuntimeError("config/station.json 中没有 role=manager 的会话")


def worker_sessions() -> list[str]:
    return [sid for sid, cfg in all_sessions().items() if cfg.get("enabled", True)]


def make_actor(session_id: str) -> dict:
    cfg = session_config(session_id)
    return {
        "session_id": session_id,
        "role": cfg.get("role", "worker"),
        "name": cfg.get("name", session_id),
        "kind": "ai",
    }


USER_ACTOR = {"session_id": "USER", "role": "user", "name": "用户", "kind": "user"}
SYSTEM_ACTOR = {"session_id": "SYSTEM", "role": "system", "name": "系统", "kind": "system"}
