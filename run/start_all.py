#!/usr/bin/env python
"""Start the UI and workers.

Two modes:
  * fixed:  `--project P` starts workers for P.
  * watch:  no --project. The UI writes run/active_project.json when the user
            selects or creates a project; the supervisor starts/stops workers
            for that project. This is the "open browser, create project, done"
            entry point.

In both modes (unless --sessions is used) staffing changes are followed: an
employee enabled by the manager is started within a few seconds.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stationlib.config import all_sessions, load_config, reload_config  # noqa: E402
from stationlib.paths import project_dir  # noqa: E402
from stationlib.staffing import check_staffing  # noqa: E402
from stationlib.store import heartbeat, now_iso, read_json  # noqa: E402

RUN_DIR = ROOT / "run"
STOP_FLAG = RUN_DIR / "stop.flag"
PID_FILE = RUN_DIR / "station.pid.json"
SUPERVISOR_LOCK = RUN_DIR / "start_all.lock"
ACTIVE_FILE = Path(os.environ.get("STATION_ACTIVE_FILE") or (RUN_DIR / "active_project.json"))


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def acquire_supervisor_lock() -> Path | None:
    """Only one start_all may supervise the station."""
    for _ in range(2):
        try:
            fd = os.open(SUPERVISOR_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(), "started_at": now_iso()}).encode("utf-8"))
            os.close(fd)
            return SUPERVISOR_LOCK
        except FileExistsError:
            try:
                old = json.loads(SUPERVISOR_LOCK.read_text(encoding="utf-8"))
                old_pid = int(old.get("pid", 0) or 0)
            except Exception:
                old_pid = 0
            if old_pid and pid_alive(old_pid):
                print(
                    f"ERROR: 已有 start_all 在运行（PID {old_pid}）。不要再开第二个启动器；"
                    "先关掉旧窗口或运行 run/stop_all.py。",
                    file=sys.stderr,
                )
                return None
            try:
                SUPERVISOR_LOCK.unlink()
            except OSError:
                pass
    return None


def terminate_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            text=True,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = load_config()
    ap = argparse.ArgumentParser(description="启动工作站 UI 与会话 worker")
    ap.add_argument("--project", default=None, help="固定项目；不填则跟随 UI 里选中的项目")
    ap.add_argument("--sessions", help="逗号分隔；指定后不自动跟随配置增减（需要 --project）")
    ap.add_argument("--no-ui", action="store_true")
    ap.add_argument("--ui-only", action="store_true")
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    ap.add_argument("--interval", type=float)
    args = ap.parse_args(argv)

    fixed_project = args.project
    watch = fixed_project is None and not args.ui_only
    if fixed_project and not (project_dir(fixed_project) / "meta.json").exists():
        print(f"ERROR: 项目不存在: {fixed_project}", file=sys.stderr)
        print("先建项目: python station.py init --project <id> --title <标题> --text <需求>", file=sys.stderr)
        return 2
    if watch and args.sessions:
        print("ERROR: --sessions 需要同时指定 --project。", file=sys.stderr)
        return 2

    staff_ok, staff = check_staffing()
    if not staff_ok:
        print(
            f"ERROR: 已启用 {staff['employees_enabled']} 名员工，超过上限 {staff['limit']}。\n"
            f"员工上限默认 {staff['limit']} 人；超过必须由用户批准：\n"
            f"  python station.py approve-staff --max-employees {staff['employees_enabled']} "
            '--note "为什么需要加人"\n'
            '或者把不用的会话改成 "enabled": false。',
            file=sys.stderr,
        )
        return 2
    print(
        f"员工: {staff['employees_enabled']}/{staff['limit']} 启用"
        + (f"（用户批准上限 {staff['approved_max']}）" if staff.get("approved_max") else "")
    )

    dynamic_staff = args.sessions is None and not args.ui_only
    current_project = fixed_project
    if fixed_project:
        known = all_sessions()
        if args.sessions:
            initial = [s.strip() for s in args.sessions.split(",") if s.strip()]
            unknown = [s for s in initial if s not in known]
            if unknown:
                print(f"ERROR: 未登记会话: {', '.join(unknown)}", file=sys.stderr)
                return 2
        else:
            initial = [sid for sid, sc in known.items() if sc.get("enabled", True)]
    else:
        initial = []

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    supervisor_lock = acquire_supervisor_lock()
    if not supervisor_lock:
        return 2
    STOP_FLAG.unlink(missing_ok=True)
    started = now_iso()

    ui_cfg = cfg.get("ui", {})
    ui_proc = None
    workers: dict[str, subprocess.Popen] = {}
    handles: dict[str, TextIO] = {}
    cooldowns: dict[str, float] = {}
    last_pid_sig: tuple = ()
    warned_over = False

    def spawn(sid: str) -> None:
        if not current_project:
            return
        old = workers.get(sid)
        if old and old.poll() is None:
            return
        old_fh = handles.pop(sid, None)
        if old_fh:
            try:
                old_fh.close()
            except Exception:
                pass
        log_path = project_dir(current_project) / "logs" / "sessions" / f"{sid}.console.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(log_path, "a", encoding="utf-8", buffering=1)
        handles[sid] = fh
        cmd = [
            sys.executable,
            str(ROOT / "agents" / "worker.py"),
            "--project",
            current_project,
            "--session",
            sid,
        ]
        if args.interval:
            cmd += ["--interval", str(args.interval)]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT)
        workers[sid] = proc
        print(f"worker {sid}: pid {proc.pid}  project={current_project}  log={log_path.relative_to(ROOT)}")

    def stop_worker(sid: str, reason: str) -> None:
        proc = workers.pop(sid, None)
        if proc and proc.poll() is None:
            print(f"停止 worker {sid}（{reason}）")
            terminate_tree(proc)
        if current_project:
            try:
                heartbeat(current_project, sid, "offline")
            except Exception:
                pass
        fh = handles.pop(sid, None)
        if fh:
            try:
                fh.close()
            except Exception:
                pass

    def write_pid(force: bool = False) -> None:
        nonlocal last_pid_sig
        sig = tuple(sorted((sid, p.pid) for sid, p in workers.items() if p.poll() is None))
        if not force and sig == last_pid_sig:
            return
        last_pid_sig = sig
        PID_FILE.write_text(
            json.dumps(
                {
                    "started_at": started,
                    "project": current_project,
                    "watch": watch,
                    "dynamic": dynamic_staff,
                    "ui_pid": ui_proc.pid if ui_proc else None,
                    "workers": {sid: pid for sid, pid in sig},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def prune_dead() -> bool:
        changed = False
        for sid, proc in list(workers.items()):
            if proc.poll() is None:
                continue
            print(f"[warn] worker {sid} 已退出（exit={proc.returncode}），10 秒后按配置重试。")
            workers.pop(sid, None)
            if current_project:
                try:
                    heartbeat(current_project, sid, "offline")
                except Exception:
                    pass
            cooldowns[sid] = time.time() + 10
            fh = handles.pop(sid, None)
            if fh:
                try:
                    fh.close()
                except Exception:
                    pass
            changed = True
        return changed

    def reconcile() -> None:
        nonlocal warned_over
        if not current_project:
            for sid in list(workers):
                stop_worker(sid, "没有活动项目")
            return
        reload_config()
        ok, info = check_staffing()
        sessions_now = all_sessions()
        enabled = {sid: sc for sid, sc in sessions_now.items() if sc.get("enabled", True)}
        employees = sorted(
            sid for sid, sc in enabled.items() if sc.get("role", "worker") != "manager"
        )
        limit = info["limit"]
        if len(employees) > limit:
            if not warned_over:
                print(
                    f"[warn] 已启用 {len(employees)} 名员工，超过上限 {limit}；"
                    f"只运行前 {limit} 名。需要更多请让用户批准。"
                )
                warned_over = True
            allowed = set(employees[:limit])
            target = {
                sid for sid, sc in enabled.items() if sc.get("role", "worker") == "manager"
            } | allowed
        else:
            if warned_over:
                print("[info] 员工数已回到上限内。")
                warned_over = False
            target = set(enabled)

        for sid in list(workers):
            if sid not in target:
                stop_worker(sid, "配置中已停用或被上限排除")
        for sid in sorted(target):
            if sid in workers:
                continue
            if time.time() < cooldowns.get(sid, 0):
                continue
            spawn(sid)
        write_pid()

    def read_active() -> str | None:
        data = read_json(ACTIVE_FILE, {}) or {}
        pid = (data.get("project") or "").strip()
        if pid and (project_dir(pid) / "meta.json").exists():
            return pid
        return None

    def switch_project(new_pid: str) -> None:
        nonlocal current_project
        if new_pid == current_project:
            return
        print(f"切换项目: {current_project or '（无）'} → {new_pid}")
        for sid in list(workers):
            stop_worker(sid, "切换项目")
        cooldowns.clear()
        current_project = new_pid
        reload_config()
        reconcile()
        write_pid(force=True)

    try:
        if not args.no_ui:
            ui_cmd = [sys.executable, str(ROOT / "ui" / "server.py")]
            if args.open:
                ui_cmd.append("--open")
            ui_proc = subprocess.Popen(ui_cmd, cwd=ROOT)
            print(f"UI:   http://{ui_cfg.get('host', '127.0.0.1')}:{ui_cfg.get('port', 8877)}/  (pid {ui_proc.pid})")
            time.sleep(1.2)
            if ui_proc.poll() is not None:
                print(
                    "ERROR: UI 启动失败（端口被占用？）。改 config/station.json 的 ui.port，"
                    "或先运行 run/stop_all.py。",
                    file=sys.stderr,
                )
                return 2

        if args.ui_only:
            print("只启动了 UI。按 Ctrl+C 停止。")
            if ui_proc:
                ui_proc.wait()
            return 0

        if fixed_project:
            for sid in initial:
                spawn(sid)
        write_pid(force=True)

        if watch:
            active = read_active()
            if active:
                switch_project(active)

        print(
            "运行中。Ctrl+C 停止（会先让 worker 处理完当前消息）。"
            + (" 跟随浏览器里选中的项目。" if watch else f" 固定项目: {fixed_project}。")
            + (" 员工增减会自动跟随 config。" if dynamic_staff else " 指定了 --sessions，不自动增减。")
        )
        last_reconcile = 0.0
        while True:
            time.sleep(1)
            if STOP_FLAG.exists():
                print("检测到 stop.flag，准备退出。")
                break
            if prune_dead():
                write_pid()
            if watch:
                active = read_active()
                if active and active != current_project:
                    switch_project(active)
            if current_project and dynamic_staff and time.time() - last_reconcile >= 2:
                reconcile()
                last_reconcile = time.time()
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在停止…")
    finally:
        STOP_FLAG.write_text(now_iso() + "\n", encoding="utf-8")
        deadline = time.time() + 25
        while time.time() < deadline and any(p.poll() is None for p in workers.values()):
            time.sleep(0.5)
        for sid in list(workers):
            stop_worker(sid, "退出")
        if ui_proc and ui_proc.poll() is None:
            terminate_tree(ui_proc)
        for fh in handles.values():
            try:
                fh.close()
            except Exception:
                pass
        PID_FILE.unlink(missing_ok=True)
        STOP_FLAG.unlink(missing_ok=True)
        if supervisor_lock:
            try:
                data = json.loads(supervisor_lock.read_text(encoding="utf-8"))
                if int(data.get("pid", 0)) == os.getpid():
                    supervisor_lock.unlink(missing_ok=True)
            except Exception:
                supervisor_lock.unlink(missing_ok=True)
        print("已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
