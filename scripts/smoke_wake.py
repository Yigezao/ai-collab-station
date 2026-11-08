#!/usr/bin/env python
"""Live wake test: one real CLI turn for one session.

Costs model tokens. It checks the only claim that cannot be verified from files:
a worker process can wake a CLI session with a message, the session can use
`python station.py`, and the message is then marked processed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stationlib import actions  # noqa: E402
from stationlib.config import load_config, session_config  # noqa: E402
from stationlib.paths import project_dir  # noqa: E402
from stationlib.store import (  # noqa: E402
    claim_messages,
    finish_message,
    list_events,
    now_iso,
    send_message,
)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="真实 CLI 唤醒自检（会消耗模型 token）")
    ap.add_argument("--project", default=None)
    ap.add_argument("--session", default="S01")
    ap.add_argument("--engine", choices=["claude", "codex"])
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--clean", action="store_true", help="成功后删除自检项目")
    args = ap.parse_args(argv)

    sid = args.session
    cfg = session_config(sid)
    engine = args.engine or cfg.get("engine", "claude")
    token = f"smoke-ok-{uuid.uuid4().hex[:8]}"
    pid = args.project or f"smoke-{sid.lower()}-{time.strftime('%Y%m%d-%H%M%S')}"
    if (project_dir(pid) / "meta.json").exists():
        print(f"ERROR: 项目已存在: {pid}", file=sys.stderr)
        return 2

    actions.init_project(
        pid,
        f"自检：{sid} / {engine}",
        "这是自动生成的自检项目，不是真实需求。只验证消息唤醒与 station.py 调用。",
        "自检需求",
    )
    # Clear the manager's initial request so the test targets only our message.
    for path, msg in claim_messages(pid, "MGR", limit=100):
        finish_message(path, msg)

    body = (
        "自检（只做这一件事，不要做别的）：\n"
        f"1. 执行命令：python station.py event --project {pid} --session {sid} "
        f"--type progress --summary {token}\n"
        "2. 执行完就结束。不要向用户汇报，不要创建任务，不要修改其他文件。\n"
    )
    send_message(pid, "MGR", sid, "system", "真实唤醒自检", body)

    cmd = [
        sys.executable,
        str(ROOT / "agents" / "worker.py"),
        "--project",
        pid,
        "--session",
        sid,
        "--once",
        "--timeout",
        str(args.timeout),
    ]
    if args.engine:
        cmd += ["--engine", args.engine]

    print(f"项目: {pid}")
    print(f"会话: {sid}  引擎: {engine}")
    print(f"唤醒消息已投递，启动 worker --once（超时 {args.timeout}s）…")
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=args.timeout + 60,
    )
    duration = time.time() - started
    print(proc.stderr.strip()[-3000:])
    if proc.stdout.strip():
        print(proc.stdout.strip()[-1000:])

    events = list_events(pid)
    hit = [e for e in events if token in (e.get("summary") or "")]
    processed = list((project_dir(pid) / "inbox" / sid / "processed").glob("*.json"))
    failures = list((project_dir(pid) / "inbox" / sid / "failed").glob("*.json"))
    ok = bool(hit) and bool(processed) and not failures

    report = {
        "project": pid,
        "session": sid,
        "engine": engine,
        "generated_at": now_iso(),
        "duration_sec": round(duration, 1),
        "event_written": bool(hit),
        "message_processed": bool(processed),
        "failed_messages": len(failures),
        "worker_returncode": proc.returncode,
        "ok": ok,
    }
    log_dir = project_dir(pid) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "smoke_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print()
    print(f"事件写入: {'是' if hit else '否'}  ({token})")
    print(f"消息已归档: {'是' if processed else '否'}")
    print(f"用时: {duration:.1f}s")
    print(f"报告: {log_dir / 'smoke_report.json'}")

    if ok and args.clean:
        shutil.rmtree(project_dir(pid), ignore_errors=True)
        print("自检项目已删除。")
    elif ok:
        print("自检项目保留，可删除。")
    else:
        print("自检失败：看上面的 worker 输出和 logs/sessions/ 日志。", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
