from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"
CONFIG_FILE = ROOT / "config" / "station.json"
ROLES_DIR = ROOT / "config" / "roles"
TEMPLATES_DIR = ROOT / "templates"
UI_DIR = ROOT / "ui"

# Project IDs are derived from the workspace folder name, so Chinese is allowed.
# Keep it filesystem-safe: word characters (including CJK), dots and dashes.
PID_RE = re.compile(r"^[\w.-]{1,64}$", re.UNICODE)
SID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,23}$")

PROJECT_SUBDIRS = [
    "request",
    "discussion/proposals",
    "discussion/rounds",
    "decisions",
    "tasks",
    "inbox/manager",
    "handoffs",
    "artifacts",
    "events",
    "logs",
    "logs/sessions",
    "archive",
]


def project_dir(project_id: str) -> Path:
    if (
        not PID_RE.match(project_id or "")
        or project_id in (".", "..")
        or project_id.endswith(".")
    ):
        raise ValueError(
            f"非法项目 ID: {project_id!r}（只允许字母、数字、中文、- _ .，1-64 位）"
        )
    return PROJECTS_DIR / project_id


def check_session_id(session_id: str) -> str:
    if not SID_RE.match(session_id or ""):
        raise ValueError(f"非法会话 ID: {session_id!r}")
    return session_id


def list_projects() -> list[str]:
    if not PROJECTS_DIR.exists():
        return []
    out = []
    for p in PROJECTS_DIR.iterdir():
        if p.is_dir() and (p / "meta.json").exists():
            out.append(p.name)
    return sorted(out)
