#!/usr/bin/env python
"""Local UI server for the AI Communication Station.

Binds to 127.0.0.1 only. Serves the static UI and a small JSON API that reads the
event log and writes user messages/approvals through the same library the CLI uses.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stationlib import actions  # noqa: E402
from stationlib.config import load_config  # noqa: E402
from stationlib.paths import UI_DIR, list_projects, project_dir  # noqa: E402
from stationlib.state import project_state  # noqa: E402
from stationlib.store import atomic_write_json, now_iso, read_json  # noqa: E402

ACTIVE_FILE = Path(
    os.environ.get("STATION_ACTIVE_FILE") or (ROOT / "run" / "active_project.json")
)


def project_id_from_workspace(workspace: str) -> str:
    """Use the project folder's name as the station record id when possible."""
    name = Path(workspace).name
    slug = re.sub(r"[^\w.-]+", "-", name, flags=re.UNICODE).strip(".-_")[:48]
    if not slug:
        slug = "p-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    pid = slug
    n = 2
    while (project_dir(pid) / "meta.json").exists():
        pid = f"{slug}-{n}"
        n += 1
    return pid


def write_active(pid: str) -> None:
    atomic_write_json(ACTIVE_FILE, {"project": pid, "updated_at": now_iso()})

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "AIStationUI/1.0"

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("[ui] " + (fmt % args) + "\n")

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json_bytes(obj))

    def _error(self, code: int, msg: str):
        self._json(code, {"error": msg})

    # -------------------------------------------------------------- GET

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in STATIC:
            name, ctype = STATIC[path]
            f = UI_DIR / name
            if not f.exists():
                return self._error(404, f"缺少界面文件: {name}")
            return self._send(200, f.read_bytes(), ctype)

        if path == "/api/health":
            return self._json(200, {"ok": True})

        if path == "/api/projects":
            items = []
            for pid in list_projects():
                meta = read_json(project_dir(pid) / "meta.json", {}) or {}
                items.append({"id": pid, "title": meta.get("title", pid), "created_at": meta.get("created_at")})
            cfg = load_config()
            default = cfg.get("default_project")
            if default not in [i["id"] for i in items]:
                default = items[0]["id"] if items else None
            active = (read_json(ACTIVE_FILE, {}) or {}).get("project")
            return self._json(200, {"projects": items, "default": default, "active": active})

        if path == "/api/state":
            pid = (qs.get("project") or [""])[0]
            if not pid:
                return self._error(400, "缺少 project 参数")
            try:
                limit = max(1, min(int((qs.get("limit") or ["500"])[0]), 5000))
            except ValueError:
                limit = 500
            try:
                return self._json(200, project_state(pid, event_limit=limit))
            except Exception as exc:
                return self._error(404, f"{type(exc).__name__}: {exc}")

        if path == "/api/body":
            pid = (qs.get("project") or [""])[0]
            rel = (qs.get("path") or [""])[0]
            if not pid or not rel:
                return self._error(400, "缺少 project/path 参数")
            try:
                pdir = project_dir(pid).resolve()
                target = (pdir / rel).resolve()
                if not target.is_relative_to(pdir):
                    return self._error(403, "路径越界")
                if target.suffix.lower() not in (".md", ".json", ".txt", ".log"):
                    return self._error(403, "只允许读取文本文件")
                if not target.exists():
                    return self._error(404, "文件不存在")
                return self._send(200, target.read_bytes(), "text/plain; charset=utf-8")
            except Exception as exc:
                return self._error(400, f"{type(exc).__name__}: {exc}")

        return self._error(404, "未知路径")

    # -------------------------------------------------------------- POST

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > 2_000_000:
            return self._error(413, "请求体过大")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._error(400, "请求体不是合法 JSON")

        pid = (payload.get("project") or "").strip()

        try:
            if parsed.path == "/api/projects":
                title = (payload.get("title") or "").strip()
                request = (payload.get("request") or "").strip()
                workspace = (payload.get("workspace") or "").strip()
                if not title or not request or not workspace:
                    return self._error(400, "项目名、需求、项目文件夹都不能为空")
                if len(title) > 200 or len(request) > 50000:
                    return self._error(400, "标题或需求过长")
                new_pid = (payload.get("project") or "").strip() or project_id_from_workspace(
                    workspace
                )
                try:
                    actions.init_project(
                        new_pid, title, request, "用户需求", workspace=workspace
                    )
                except FileExistsError as exc:
                    return self._error(409, str(exc))
                except (ValueError, FileNotFoundError) as exc:
                    return self._error(400, str(exc))
                write_active(new_pid)
                return self._json(
                    200,
                    {
                        "ok": True,
                        "project": {"id": new_pid, "title": title, "workspace": workspace},
                    },
                )

            if parsed.path == "/api/active-project":
                target = (payload.get("project") or "").strip()
                if not target:
                    return self._error(400, "缺少 project")
                if not (project_dir(target) / "meta.json").exists():
                    return self._error(404, f"项目不存在: {target}")
                write_active(target)
                return self._json(200, {"ok": True, "project": target})

            if parsed.path == "/api/message":
                if not pid:
                    return self._error(400, "缺少 project")
                text = (payload.get("text") or "").strip()
                if not text:
                    return self._error(400, "消息不能为空")
                ev = actions.user_message(pid, text)
                return self._json(200, {"ok": True, "event_id": ev["event_id"]})

            if parsed.path == "/api/approve":
                if not pid:
                    return self._error(400, "缺少 project")
                decision = payload.get("decision")
                if decision not in ("approve", "reject"):
                    return self._error(400, "decision 必须是 approve/reject")
                ev = actions.approve_plan(
                    pid,
                    decision == "approve",
                    (payload.get("note") or "").strip(),
                    by="用户",
                )
                return self._json(200, {"ok": True, "event_id": ev["event_id"]})

            if parsed.path == "/api/heartbeat":
                # Reserved for future browser-side presence; no state change today.
                return self._json(200, {"ok": True})

            return self._error(404, "未知接口")
        except PermissionError as exc:
            return self._error(403, str(exc))
        except Exception as exc:
            return self._error(400, f"{type(exc).__name__}: {exc}")


def main(argv=None) -> int:
    cfg = load_config()
    ui = cfg.get("ui", {})
    ap = argparse.ArgumentParser(description="AI 协作工作站 UI")
    ap.add_argument("--host", default=ui.get("host", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(ui.get("port", 8877)))
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"AI 协作工作站 UI: {url}")
    print("按 Ctrl+C 停止")
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
