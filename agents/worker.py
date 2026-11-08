#!/usr/bin/env python
"""File-driven worker for one station session.

It watches `projects/<project>/inbox/<session>/`. When a ready message appears it
claims it, invokes one CLI turn (Claude or Codex), then moves it to processed/.
No messages = no model calls, so idle polling costs no tokens.

This is the honest wake-up mechanism: the filesystem alone cannot wake a CLI;
this process does the waking. If it is not running, the UI must say the session is
offline (heartbeat).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.prompts import build_prompt  # noqa: E402
from stationlib.config import (  # noqa: E402
    SYSTEM_ACTOR,
    load_config,
    manager_session,
    session_config,
)
from stationlib.paths import CONFIG_FILE, project_dir  # noqa: E402
from stationlib.staffing import check_staffing  # noqa: E402
from stationlib.store import (  # noqa: E402
    append_event,
    atomic_write_json,
    claim_messages,
    fail_message,
    file_lock,
    finish_message,
    heartbeat,
    list_events,
    now_iso,
    read_json,
    send_message,
)

ACTION_TYPES = {
    "discussion_turn",
    "assignment",
    "correction",
    "blocker",
    "review_result",
    "plan_approval",
    "plan_rejection",
    "user_request",
}


@dataclass
class EngineResult:
    ok: bool
    text: str
    error: str
    duration: float
    cost_usd: float = 0.0


def workspace_dirs() -> list[str]:
    roots = load_config().get("workspace_roots") or []
    out = []
    for raw in roots:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        if p.exists():
            out.append(str(p))
    return out


def _env(session_id: str, project_id: str) -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # Bind this CLI turn to its station identity. station.py refuses writes made
    # in the name of another session, so MGR cannot impersonate S01/S02.
    env["AI_STATION_SESSION"] = session_id
    env["AI_STATION_PROJECT"] = project_id
    return env


def engine_prefix(name: str) -> list[str]:
    """Resolve an npm shim to a direct executable.

    On Windows `claude` resolves to claude.CMD, which runs through cmd.exe. Long
    multi-line arguments (the role system prompt) and parenthesized permission
    patterns get mangled by cmd's `%*` expansion, so call claude.exe / node
    codex.js directly instead.
    """
    if not name:
        return [name]
    found = name if os.path.isabs(name) or os.sep in name or "/" in name else (shutil.which(name) or name)
    p = Path(found)
    if p.suffix.lower() not in (".cmd", ".bat") or not p.exists():
        return [found]
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return [found]
    base = str(p.parent)
    for raw in re.findall(r'"([^"]+\.(?:exe|js))"', text, flags=re.IGNORECASE):
        target = raw.replace("%dp0%", base).replace("%~dp0%", base)
        if "%" in target:
            continue
        tp = Path(target)
        if not tp.exists():
            continue
        if tp.suffix.lower() == ".exe":
            return [str(tp)]
        if tp.suffix.lower() == ".js":
            node = shutil.which("node")
            if node:
                return [node, str(tp)]
    return [found]


def run_claude(
    prompt: str,
    system_prompt: str,
    timeout: int,
    session_id: str,
    project_id: str,
    workspace: Path,
) -> EngineResult:
    cfg = load_config()["engines"]["claude"]
    cmd = engine_prefix(cfg.get("bin", "claude")) + [
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        cfg.get("permission_mode", "acceptEdits"),
        "--add-dir",
        str(ROOT),
    ]
    seen = {str(ROOT.resolve()), str(workspace.resolve())}
    for d in workspace_dirs():
        p = str(Path(d).resolve())
        if p not in seen:
            cmd += ["--add-dir", d]
            seen.add(p)
    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]
    if cfg.get("model"):
        cmd += ["--model", cfg["model"]]
    tools = list(cfg.get("allowed_tools") or [])
    tools += list(session_config(session_id).get("allowed_tools") or [])
    if tools:
        cmd += ["--allowedTools", ",".join(dict.fromkeys(tools))]

    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(workspace),
            env=_env(session_id, project_id),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return EngineResult(False, "", f"超时（{timeout}s）", time.time() - started)
    except Exception as exc:
        return EngineResult(False, "", f"启动失败: {type(exc).__name__}: {exc}", time.time() - started)

    data = None
    stdout = (proc.stdout or "").strip()
    try:
        data = json.loads(stdout)
    except Exception:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except Exception:
                    continue
    if data is not None:
        text = str(data.get("result") or "")
        ok = proc.returncode == 0 and not data.get("is_error")
        err = "" if ok else str(data.get("result") or data.get("subtype") or "engine error")
        if not ok and (proc.stderr or "").strip():
            err += "\n" + proc.stderr.strip()[-2000:]
        try:
            cost = float(data.get("total_cost_usd") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        return EngineResult(ok, text, err, time.time() - started, cost)

    ok = proc.returncode == 0
    return EngineResult(
        ok,
        stdout[-4000:],
        "" if ok else ((proc.stderr or "").strip()[-4000:] or f"退出码 {proc.returncode}"),
        time.time() - started,
    )


def run_codex(
    prompt: str, timeout: int, session_id: str, project_id: str, workspace: Path
) -> EngineResult:
    cfg = load_config()["engines"]["codex"]
    out_file = ROOT / "projects" / "_engine_out" / f"{session_id}-{int(time.time() * 1000)}.md"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = engine_prefix(cfg.get("bin", "codex")) + [
        "exec",
        "--skip-git-repo-check",
        "-C",
        str(workspace),
        "--sandbox",
        cfg.get("sandbox", "workspace-write"),
        "-c",
        'approval_policy="never"',
        "-o",
        str(out_file),
        "--add-dir",
        str(ROOT),
    ]
    seen = {str(ROOT.resolve()), str(workspace.resolve())}
    for d in workspace_dirs():
        p = str(Path(d).resolve())
        if p not in seen:
            cmd += ["--add-dir", d]
            seen.add(p)
    if cfg.get("model"):
        cmd += ["-m", cfg["model"]]
    cmd.append("-")

    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(workspace),
            env=_env(session_id, project_id),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return EngineResult(False, "", f"超时（{timeout}s）", time.time() - started)
    except Exception as exc:
        return EngineResult(False, "", f"启动失败: {type(exc).__name__}: {exc}", time.time() - started)

    text = ""
    try:
        if out_file.exists():
            text = out_file.read_text(encoding="utf-8").strip()
    finally:
        out_file.unlink(missing_ok=True)
    ok = proc.returncode == 0
    err = "" if ok else ((proc.stderr or "").strip()[-4000:] or f"退出码 {proc.returncode}")
    if not text:
        text = (proc.stdout or "")[-4000:]
    return EngineResult(ok, text, err, time.time() - started)


def run_engine(
    engine: str,
    prompt: str,
    system_prompt: str,
    timeout: int,
    session_id: str,
    project_id: str,
    workspace: Path,
) -> EngineResult:
    if engine == "claude":
        return run_claude(prompt, system_prompt, timeout, session_id, project_id, workspace)
    if engine == "codex":
        return run_codex(prompt, timeout, session_id, project_id, workspace)
    raise ValueError(f"未知引擎: {engine}")


# ---------------------------------------------------------------- logging / locks


def add_cost(project_id: str, session_id: str, cost_usd: float) -> None:
    """Accumulate Claude-reported cost per project for visibility/budgeting."""
    if not cost_usd or cost_usd <= 0:
        return
    path = project_dir(project_id) / "logs" / "cost.json"
    try:
        with file_lock(path.with_suffix(".lock")):
            data = read_json(path, {}) or {}
            total = round(float(data.get("total_usd", 0.0)) + cost_usd, 6)
            by = data.get("by_session") or {}
            by[session_id] = round(float(by.get(session_id, 0.0)) + cost_usd, 6)
            data.update({"total_usd": total, "by_session": by, "updated_at": now_iso()})
            atomic_write_json(path, data)
    except Exception as exc:
        print(f"[worker] 成本记录失败: {exc}", file=sys.stderr)


def log_line(project_id: str, session_id: str, record: dict) -> None:
    p = project_dir(project_id) / "logs" / "sessions" / f"{session_id}.invocations.jsonl"
    line = json.dumps({"ts": now_iso(), **record}, ensure_ascii=False) + "\n"
    try:
        with file_lock(p.with_suffix(".lock")):
            with p.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception as exc:
        print(f"[worker] 日志写入失败: {exc}", file=sys.stderr)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def acquire_worker_lock(project_id: str, session_id: str) -> Path | None:
    pdir = project_dir(project_id)
    lock = pdir / "logs" / "sessions" / f"{session_id}.worker.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(), "started_at": now_iso()}).encode("utf-8"))
            os.close(fd)
            return lock
        except FileExistsError:
            old = {}
            try:
                old = json.loads(lock.read_text(encoding="utf-8"))
            except Exception:
                pass
            old_pid = int(old.get("pid", 0) or 0)
            if old_pid and pid_alive(old_pid):
                return None
            lock.unlink(missing_ok=True)
    return None


def release_worker_lock(lock: Path | None) -> None:
    if not lock:
        return
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        if int(data.get("pid", 0)) == os.getpid():
            lock.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------- main loop


def process_message(project_id: str, session_id: str, engine: str, timeout: int, path: Path, msg: dict) -> bool:
    cfg = session_config(session_id)
    before = list_events(project_id)
    before_seq = before[-1]["seq"] if before else 0

    print(
        f"[worker {session_id}] 处理 {msg.get('message_id')} ({msg.get('type')})",
        file=sys.stderr,
        flush=True,
    )
    heartbeat(
        project_id,
        session_id,
        "working",
        current_message=msg.get("message_id"),
        current_subject=msg.get("subject"),
    )
    meta = read_json(project_dir(project_id) / "meta.json", {}) or {}
    ws_raw = meta.get("workspace")
    workspace = Path(ws_raw) if ws_raw else ROOT

    # Keep the heartbeat fresh during long model calls (Codex manager turns can
    # take 2-3 minutes; without this the UI shows the session as offline).
    stop_hb = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_hb.wait(10):
            try:
                heartbeat(
                    project_id,
                    session_id,
                    "working",
                    current_message=msg.get("message_id"),
                    current_subject=msg.get("subject"),
                )
            except Exception:
                pass

    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    try:
        if ws_raw and not workspace.is_dir():
            result = EngineResult(False, "", f"项目工作区不存在: {workspace}", 0.0)
        else:
            prompt, system_prompt = build_prompt(project_id, session_id, msg, engine, workspace)
            result = run_engine(
                engine, prompt, system_prompt, timeout, session_id, project_id, workspace
            )
    finally:
        stop_hb.set()
        hb_thread.join(timeout=2)

    after = list_events(project_id)
    after_seq = after[-1]["seq"] if after else 0
    add_cost(project_id, session_id, result.cost_usd)
    log_line(
        project_id,
        session_id,
        {
            "message_id": msg.get("message_id"),
            "message_type": msg.get("type"),
            "engine": engine,
            "workspace": str(workspace),
            "ok": result.ok,
            "duration_sec": round(result.duration, 1),
            "cost_usd": result.cost_usd,
            "events_written": after_seq - before_seq,
            "output_tail": (result.text or "")[-1500:],
            "error": (result.error or "")[-2000:],
        },
    )

    if result.ok:
        finish_message(path, msg)
        if after_seq == before_seq and msg.get("type") in ACTION_TYPES:
            append_event(
                project_id,
                "worker_error",
                SYSTEM_ACTOR,
                f"{session_id} 处理 {msg.get('message_id')} 后未写入任何事件",
                data={
                    "session_id": session_id,
                    "message_id": msg.get("message_id"),
                    "message_type": msg.get("type"),
                },
            )
            if session_id != manager_session():
                send_message(
                    project_id,
                    session_id,
                    manager_session(),
                    "blocker",
                    f"{session_id} 未按协议写事件",
                    f"消息 {msg.get('message_id')}（{msg.get('type')}）已被处理，但没有写任何工作站事件。"
                    f"输出片段：\n{(result.text or '')[-800:]}",
                )
        heartbeat(project_id, session_id, "idle", last_message=msg.get("message_id"), last_ok=True)
        print(f"[worker {session_id}] 完成，用时 {result.duration:.1f}s", file=sys.stderr, flush=True)
        return True

    status = fail_message(path, msg, max_attempts=load_config().get("worker", {}).get("max_attempts", 3))
    err = (result.error or "未知错误")[-3000:]
    append_event(
        project_id,
        "worker_error",
        SYSTEM_ACTOR,
        f"{session_id} 处理消息失败（{status}）：{msg.get('subject')}",
        body=err,
        data={
            "session_id": session_id,
            "message_id": msg.get("message_id"),
            "message_type": msg.get("type"),
            "retry": status,
            "engine": engine,
        },
    )
    if status == "failed" and session_id != manager_session():
        send_message(
            project_id,
            session_id,
            manager_session(),
            "blocker",
            f"{session_id} 处理消息失败",
            f"消息 {msg.get('message_id')}（{msg.get('type')}）重试 {load_config().get('worker', {}).get('max_attempts', 3)} 次后失败。\n错误：{err[:1200]}",
        )
    heartbeat(project_id, session_id, "idle", last_message=msg.get("message_id"), last_ok=False)
    print(f"[worker {session_id}] 失败（{status}）：{err[:300]}", file=sys.stderr, flush=True)
    return False


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg_all = load_config()
    ap = argparse.ArgumentParser(description="工作站会话 worker")
    ap.add_argument("--project", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--engine", choices=["claude", "codex"])
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--interval", type=float)
    ap.add_argument("--once", action="store_true", help="处理至多一条消息后退出")
    args = ap.parse_args(argv)

    sid = args.session
    parent_sid = os.environ.get("AI_STATION_SESSION")
    if parent_sid and parent_sid != sid:
        print(
            f"ERROR: 不允许从会话 {parent_sid} 的 shell 内启动 {sid} 的 worker。"
            "员工应由用户/start_all 启动，不能由总管代跑。",
            file=sys.stderr,
        )
        return 4
    cfg = session_config(sid)
    if cfg.get("role", "worker") != "manager":
        staff_ok, staff = check_staffing()
        if not staff_ok:
            print(
                f"ERROR: 已启用 {staff['employees_enabled']} 名员工，超过上限 {staff['limit']}；"
                "需要用户运行 python station.py approve-staff 批准。",
                file=sys.stderr,
            )
            return 5
    engine = args.engine or cfg.get("engine", cfg_all.get("default_engine", "claude"))
    timeout = args.timeout or int(cfg.get("timeout_sec", 900))
    interval = args.interval or float(cfg_all.get("worker", {}).get("poll_seconds", 3))
    stop_flag = ROOT / "run" / "stop.flag"

    if not (project_dir(args.project) / "meta.json").exists():
        print(f"ERROR: 项目不存在: {args.project}", file=sys.stderr)
        return 2

    lock = acquire_worker_lock(args.project, sid)
    if not lock:
        print(f"ERROR: 会话 {sid} 已有 worker 在运行（{args.project}）", file=sys.stderr)
        return 3

    heartbeat(args.project, sid, "idle")
    print(
        f"[worker] {sid} 启动 project={args.project} engine={engine} timeout={timeout}s interval={interval}s",
        file=sys.stderr,
        flush=True,
    )
    processed = 0
    try:
        while True:
            if stop_flag.exists():
                print("[worker] 收到停止标记，退出", file=sys.stderr)
                break
            claimed = claim_messages(args.project, sid, limit=1)
            if not claimed:
                heartbeat(args.project, sid, "idle")
                if args.once:
                    break
                time.sleep(interval)
                continue
            path, msg = claimed[0]
            try:
                process_message(args.project, sid, engine, timeout, path, msg)
            except Exception:
                err = traceback.format_exc()
                print(f"[worker {sid}] 未捕获异常:\n{err}", file=sys.stderr)
                log_line(args.project, sid, {"ok": False, "error": err[-3000:], "message_id": msg.get("message_id")})
                heartbeat(args.project, sid, "idle", last_ok=False)
                try:
                    fail_message(path, msg)
                except Exception:
                    pass
            processed += 1
            if args.once:
                break
    except KeyboardInterrupt:
        print("\n[worker] 已停止", file=sys.stderr)
    finally:
        release_worker_lock(lock)
        try:
            heartbeat(args.project, sid, "offline")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
