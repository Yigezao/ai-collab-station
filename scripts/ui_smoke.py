#!/usr/bin/env python
"""End-to-end check of the local UI API (no model calls)."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stationlib import actions  # noqa: E402
from stationlib.paths import project_dir  # noqa: E402
from stationlib.state import project_state  # noqa: E402


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def req(url: str, data: dict | None = None) -> tuple[int, bytes]:
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    pid = "uitest-" + uuid.uuid4().hex[:6]
    actions.init_project(pid, "UI 自检", "自检项目", "UI 自检")
    actions.manager_opening(pid, "MGR", "开场", ["S01"])
    actions.propose_plan(pid, "MGR", "v1", "UI 自检方案", "正文：等待批准。")
    temp_active_dir = Path(tempfile.mkdtemp(prefix="station_active_"))
    active_file = temp_active_dir / "active.json"
    new_pid = None
    new_workspace = None

    port = free_port()
    env = os.environ.copy()
    env["STATION_ACTIVE_FILE"] = str(active_file)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "ui" / "server.py"), "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    checks = []

    def check(name, cond):
        checks.append({"check": name, "ok": bool(cond)})
        print(("  ✓ " if cond else "  ✗ ") + name)

    try:
        for _ in range(50):
            try:
                if req(base + "/api/health")[0] == 200:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            print("服务器未启动", file=sys.stderr)
            return 1

        code, body = req(base + "/")
        check("首页 HTML 可访问", code == 200 and b"AI \xe5\x8d\x8f\xe4\xbd\x9c\xe5\xb7\xa5\xe4\xbd\x9c\xe7\xab\x99" in body)
        code, body = req(base + "/app.js")
        check("app.js 可访问", code == 200)
        code, body = req(base + "/style.css")
        check("style.css 可访问", code == 200)

        code, body = req(base + "/api/projects")
        data = json.loads(body)
        check("项目列表包含自检项目", code == 200 and any(p["id"] == pid for p in data["projects"]))

        code, body = req(base + f"/api/state?project={pid}")
        st = json.loads(body)
        check("状态接口返回待批准", code == 200 and st["plan"]["pending"]["version"] == "v1")
        check("状态包含事件与任务字段", "events" in st and "tasks" in st and "sessions" in st)
        check(
            "状态包含员工上限",
            st.get("staffing", {}).get("limit") == 2
            and st["staffing"]["employees_enabled"] >= 1,
        )

        plan_body = f"decisions/PLAN-v1.md"
        code, body = req(base + f"/api/body?project={pid}&path={plan_body}")
        check("可读取方案全文", code == 200 and "等待批准" in body.decode("utf-8"))

        code, _ = req(base + f"/api/body?project={pid}&path=../../config/station.json")
        check("路径越界被拒绝", code == 403)

        code, body = req(
            base + "/api/message",
            {"project": pid, "text": "这是 UI 自检消息"},
        )
        check("POST 用户消息成功", code == 200 and json.loads(body)["ok"])
        mgr_inbox = list((project_dir(pid) / "inbox" / "MGR").glob("*.json"))
        check("消息进入总管收件箱", len(mgr_inbox) >= 1)

        code, body = req(
            base + "/api/approve",
            {"project": pid, "decision": "approve", "note": "UI 自检批准"},
        )
        check("POST 批准成功", code == 200 and json.loads(body)["ok"])
        st = project_state(pid)
        check("批准后进入实施阶段", st["phase"]["key"] == "implementation")
        check("批准事件有用户与版本", st["plan"]["approved_version"] == "v1")

        code, body = req(base + "/api/approve", {"project": pid, "decision": "approve"})
        check("重复批准被拒绝", code == 403)

        new_workspace = Path(tempfile.mkdtemp(prefix="station_ui_ws_"))
        code, body = req(
            base + "/api/projects",
            {
                "title": "浏览器新建自检",
                "request": "从 UI 新建项目的自检需求，不会唤醒模型。",
                "workspace": str(new_workspace),
            },
        )
        data = json.loads(body)
        new_pid = (data.get("project") or {}).get("id")
        check("浏览器新建项目", code == 200 and bool(new_pid))
        if new_pid:
            check("新建项目写入需求", (project_dir(new_pid) / "request").exists())
            code, body = req(base + f"/api/state?project={new_pid}")
            st2 = json.loads(body)
            check(
                "新建项目聊天包含需求",
                any(
                    m.get("type") == "user_message" and "自检需求" in (m.get("body") or "")
                    for m in st2.get("chat", [])
                ),
            )
            code, body = req(base + "/api/active-project", {"project": new_pid})
            active = json.loads(active_file.read_text(encoding="utf-8"))
            check(
                "可设置活动项目",
                code == 200 and active.get("project") == new_pid,
            )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(project_dir(pid), ignore_errors=True)
        if new_pid:
            shutil.rmtree(project_dir(new_pid), ignore_errors=True)
        if new_workspace:
            shutil.rmtree(new_workspace, ignore_errors=True)
        shutil.rmtree(temp_active_dir, ignore_errors=True)
        print(f"清理自检项目: {pid}" + (f", {new_pid}" if new_pid else ""))

    ok = all(c["ok"] for c in checks)
    print("UI 接口自检通过。" if ok else "UI 接口自检失败。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
