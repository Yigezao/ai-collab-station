#!/usr/bin/env python
"""Stop a station started by run/start_all.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = ROOT / "run"
STOP_FLAG = RUN_DIR / "stop.flag"
PID_FILE = RUN_DIR / "station.pid.json"
SUPERVISOR_LOCK = RUN_DIR / "start_all.lock"


def kill(pid: int) -> None:
    if not pid:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    STOP_FLAG.write_text("stop\n", encoding="utf-8")
    if not PID_FILE.exists():
        print("没有找到运行记录；已写入 stop.flag。")
        return 0
    try:
        info = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"运行记录损坏: {exc}")
        PID_FILE.unlink(missing_ok=True)
        return 1

    workers = info.get("workers", {})
    print("正在停止:", ", ".join(workers.keys()) or "（无 worker）")
    # Give workers a few seconds to finish the current message.
    time.sleep(3)
    for sid, pid in workers.items():
        kill(int(pid or 0))
    kill(int(info.get("ui_pid") or 0))
    PID_FILE.unlink(missing_ok=True)
    STOP_FLAG.unlink(missing_ok=True)
    SUPERVISOR_LOCK.unlink(missing_ok=True)
    print("已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
