from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stationlib import actions  # noqa: E402
from stationlib.config import SYSTEM_ACTOR  # noqa: E402
from stationlib.paths import project_dir  # noqa: E402
from stationlib.staffing import (  # noqa: E402
    approve_staff,
    count_allowed,
    revoke_staff,
    set_employee_enabled,
    staff_limit,
    staffing_info,
)
from stationlib.state import plan_state, project_state, turn_state  # noqa: E402
from stationlib.store import (  # noqa: E402
    append_event,
    claim_messages,
    finish_message,
    list_events,
    list_inbox,
    send_message,
)


def new_project(title="测试项目"):
    pid = f"test-{uuid.uuid4().hex[:8]}"
    actions.init_project(pid, title, "虚拟需求：验证工作站协议。")
    return pid


class StationTest(unittest.TestCase):
    def setUp(self):
        self.pid = new_project()
        self.addCleanup(lambda: shutil.rmtree(project_dir(self.pid), ignore_errors=True))

    def test_event_sequence_and_atomic_files(self):
        self.assertEqual([e["seq"] for e in list_events(self.pid)], [1, 2, 3])
        for i in range(5):
            append_event(self.pid, "system", SYSTEM_ACTOR, f"事件 {i}")
        events = list_events(self.pid)
        self.assertEqual([e["seq"] for e in events], list(range(1, 9)))
        leftovers = [p for p in (project_dir(self.pid) / "events").iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [])

    def test_concurrent_writes_get_unique_contiguous_seq(self):
        errors = []

        def writer(n):
            try:
                for i in range(15):
                    append_event(self.pid, "system", SYSTEM_ACTOR, f"w{n}-{i}")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        events = list_events(self.pid)
        seqs = [e["seq"] for e in events]
        self.assertEqual(len(seqs), len(set(seqs)))
        self.assertEqual(seqs, list(range(1, 3 + 6 * 15 + 1)))

    def test_approval_gate_blocks_tasks_until_user_approval(self):
        actions.manager_opening(self.pid, "MGR", "开场，讨论需求。", ["S01", "S02"])
        actions.propose_plan(
            self.pid, "MGR", "v1", "初始方案", "方案正文", requires_user_approval=True
        )
        st = project_state(self.pid)
        self.assertEqual(st["phase"]["key"], "awaiting_approval")
        self.assertIsNotNone(st["plan"]["pending"])

        with self.assertRaises(PermissionError):
            actions.create_task(
                self.pid,
                "MGR",
                title="实施",
                owner="S01",
                acceptance=["检查通过"],
            )

        actions.approve_plan(self.pid, True, "同意")
        st = project_state(self.pid)
        self.assertEqual(st["phase"]["key"], "implementation")
        task = actions.create_task(
            self.pid,
            "MGR",
            title="实施",
            owner="S01",
            reviewer="S02",
            acceptance=["检查通过"],
        )
        self.assertEqual(task["status"], "open")
        self.assertEqual(task["task_id"], "T001")

    def test_task_transitions_require_review(self):
        actions.propose_plan(self.pid, "MGR", "v1", "方案", "正文")
        actions.approve_plan(self.pid, True, "同意")
        t = actions.create_task(
            self.pid, "MGR", title="实现A", owner="S01", reviewer="S02", acceptance=["通过"]
        )
        tid = t["task_id"]

        with self.assertRaises(ValueError):
            actions.update_task_status(self.pid, tid, "done", "S01", "直接完成")

        actions.update_task_status(self.pid, tid, "in_progress", "S01", "开工")
        actions.update_task_status(self.pid, tid, "review", "S01", "提交")
        with self.assertRaises(PermissionError):
            actions.update_task_status(self.pid, tid, "done", "S02", "无审查直接完成")

        actions.report_review(self.pid, "S02", tid, "pass", "检查通过", "pytest")
        actions.update_task_status(self.pid, tid, "done", "S02", "审查通过")
        self.assertEqual(project_state(self.pid)["tasks"][0]["status"], "done")

    def test_dependency_gate_and_task_reassign(self):
        actions.propose_plan(self.pid, "MGR", "v1", "方案", "正文")
        actions.approve_plan(self.pid, True, "同意")
        a = actions.create_task(
            self.pid, "MGR", title="A", owner="S01", reviewer="S02", acceptance=["ok"]
        )
        b = actions.create_task(
            self.pid,
            "MGR",
            title="B",
            owner="S02",
            reviewer="S01",
            acceptance=["ok"],
            dependencies=[a["task_id"]],
        )
        with self.assertRaises(PermissionError):
            actions.update_task_status(self.pid, b["task_id"], "in_progress", "S02", "抢跑")

        actions.update_task_status(self.pid, a["task_id"], "in_progress", "S01", "开工")
        actions.update_task_status(self.pid, a["task_id"], "review", "S01", "提交")
        actions.report_review(self.pid, "S02", a["task_id"], "pass", "通过")
        actions.update_task_status(self.pid, a["task_id"], "done", "S02", "验收")
        actions.update_task_status(self.pid, b["task_id"], "in_progress", "S02", "依赖已完成")

        t = actions.reassign_task(
            self.pid,
            "MGR",
            b["task_id"],
            "S01",
            "人员调整",
            new_reviewer="S02",
        )
        self.assertEqual(t["owner_session"], "S01")
        self.assertEqual(t["reviewer_session"], "S02")
        with self.assertRaises(ValueError):
            actions.reassign_task(
                self.pid,
                "MGR",
                b["task_id"],
                "S03",
                "未启用会话",
                new_reviewer="S02",
            )

    def test_manager_cannot_own_task(self):
        actions.propose_plan(self.pid, "MGR", "v1", "方案", "正文")
        actions.approve_plan(self.pid, True, "同意")
        with self.assertRaises(ValueError):
            actions.create_task(self.pid, "MGR", title="错", owner="MGR", acceptance=["x"])

    def test_turn_enforcement(self):
        actions.manager_opening(self.pid, "MGR", "开场", ["S01", "S02"])
        actions.grant_turn(self.pid, "MGR", "S01", 1, "提方案")
        with self.assertRaises(PermissionError):
            actions.speak(self.pid, "S02", "抢话", 1)
        actions.speak(self.pid, "S01", "我的方案", 1)
        with self.assertRaises(PermissionError):
            actions.speak(self.pid, "S02", "还是抢话", 1)
        actions.grant_turn(self.pid, "MGR", "S02", 1, "回应")
        actions.speak(self.pid, "S02", "我反对", 1, speak_type="critique")
        self.assertIsNone(turn_state(list_events(self.pid))["current"])

    def test_message_claim_is_exclusive(self):
        for i in range(3):
            send_message(self.pid, "MGR", "S01", "assignment", f"消息{i}", f"正文{i}")
        self.assertEqual(len(list_inbox(self.pid, "S01")), 3)
        first = claim_messages(self.pid, "S01", limit=2)
        self.assertEqual(len(first), 2)
        self.assertEqual(len(list_inbox(self.pid, "S01")), 1)
        for path, msg in first:
            finish_message(path, msg)
        second = claim_messages(self.pid, "S01", limit=5)
        self.assertEqual(len(second), 1)
        for path, msg in second:
            finish_message(path, msg)
        self.assertEqual(len(list_inbox(self.pid, "S01")), 0)

    def test_internal_revision_keeps_approval(self):
        actions.propose_plan(self.pid, "MGR", "v1", "方案", "正文")
        actions.approve_plan(self.pid, True, "同意")
        actions.publish_revision(
            self.pid, "MGR", "v1.1", "接口调整", "正文", requires_user_approval=False
        )
        plan = plan_state(list_events(self.pid))
        self.assertTrue(plan["implementation_allowed"])
        self.assertIsNone(plan["pending"])

    def test_revision_can_require_new_user_approval(self):
        actions.propose_plan(self.pid, "MGR", "v1", "方案", "正文")
        actions.approve_plan(self.pid, True, "同意")
        actions.publish_revision(
            self.pid, "MGR", "v2", "扩大范围", "正文", requires_user_approval=True
        )
        st = project_state(self.pid)
        self.assertEqual(st["phase"]["key"], "awaiting_approval")
        with self.assertRaises(PermissionError):
            actions.create_task(self.pid, "MGR", title="新任务", owner="S01", acceptance=["x"])

    def test_reopen_completed_project_for_followup(self):
        actions.propose_plan(self.pid, "MGR", "v1", "方案", "正文")
        actions.approve_plan(self.pid, True, "同意")
        actions.finish_project(self.pid, "MGR", "完成")
        self.assertEqual(project_state(self.pid)["phase"]["key"], "done")

        # A new user message implicitly reopens a completed project.
        actions.user_message(self.pid, "再加一个小优化")
        self.assertEqual(project_state(self.pid)["phase"]["key"], "discussion")

        ev = actions.reopen_project(self.pid, "MGR", "用户追加小优化")
        self.assertEqual(ev["type"], "project_reopened")
        self.assertEqual(project_state(self.pid)["phase"]["key"], "discussion")

        actions.propose_plan(self.pid, "MGR", "v2", "追加优化", "正文")
        self.assertEqual(project_state(self.pid)["phase"]["key"], "awaiting_approval")
        actions.approve_plan(self.pid, True, "同意 v2")
        self.assertEqual(project_state(self.pid)["phase"]["key"], "implementation")

    def test_finish_requires_all_tasks_done(self):
        actions.propose_plan(self.pid, "MGR", "v1", "方案", "正文")
        actions.approve_plan(self.pid, True, "同意")
        actions.create_task(self.pid, "MGR", title="任务", owner="S01", acceptance=["x"])
        with self.assertRaises(PermissionError):
            actions.finish_project(self.pid, "MGR", "完成")
        self.assertEqual(project_state(self.pid)["phase"]["key"], "implementation")

    def test_invalid_project_id_rejected(self):
        with self.assertRaises(ValueError):
            project_dir("../evil")
        with self.assertRaises(ValueError):
            project_dir("..")
        self.assertEqual(project_dir("中文项目").name, "中文项目")

    def test_workspace_validation_and_storage(self):
        ws = Path(tempfile.mkdtemp(prefix="station_ws_"))
        self.addCleanup(lambda: shutil.rmtree(ws, ignore_errors=True))
        pid = f"ws-{uuid.uuid4().hex[:6]}"
        self.addCleanup(lambda: shutil.rmtree(project_dir(pid), ignore_errors=True))
        actions.init_project(pid, "工作区测试", "需求", workspace=str(ws))
        meta = json.loads((project_dir(pid) / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(Path(meta["workspace"]).resolve(), ws.resolve())

        with self.assertRaises(FileExistsError):
            actions.init_project(
                f"ws2-{uuid.uuid4().hex[:6]}", "重复", "需求", workspace=str(ws)
            )
        with self.assertRaises(ValueError):
            actions.init_project(
                f"ws3-{uuid.uuid4().hex[:6]}",
                "站内",
                "需求",
                workspace=str(ROOT / "projects"),
            )
        with self.assertRaises(FileNotFoundError):
            actions.init_project(
                f"ws4-{uuid.uuid4().hex[:6]}",
                "不存在",
                "需求",
                workspace=str(ws / "nope"),
            )

    def test_staffing_limit_needs_user_approval(self):
        tmp = Path(tempfile.gettempdir()) / f"staff-{uuid.uuid4().hex}.json"
        try:
            self.assertEqual(staff_limit(tmp), 2)
            self.assertTrue(count_allowed(2, tmp))
            self.assertFalse(count_allowed(3, tmp))
            approve_staff(3, "测试批准", tmp)
            self.assertEqual(staff_limit(tmp), 3)
            self.assertTrue(count_allowed(3, tmp))
        finally:
            revoke_staff(tmp)
        # The live config must still expose the staffing summary for the UI/manager.
        info = staffing_info()
        self.assertIn("employees_enabled", info)
        self.assertIn("limit", info)

    def test_enable_disable_employee_respects_limit(self):
        tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        cfg_path = tmpdir / "station.json"
        approval = tmpdir / "staff_approval.json"
        log = tmpdir / "staff_changes.jsonl"
        cfg = json.loads((ROOT / "config" / "station.json").read_text(encoding="utf-8"))
        cfg["sessions"]["S03"]["enabled"] = False
        cfg["sessions"]["S04"]["enabled"] = False
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

        with self.assertRaises(PermissionError):
            set_employee_enabled(
                "S03", True, "MGR", "test", config_path=cfg_path, approval_path=approval, log_path=log
            )
        approve_staff(3, "当前项目批准 3 人", approval)
        r = set_employee_enabled(
            "S03", True, "MGR", "test", config_path=cfg_path, approval_path=approval, log_path=log
        )
        self.assertEqual(r["employees_enabled"], 3)
        with self.assertRaises(PermissionError):
            set_employee_enabled(
                "S04", True, "MGR", "test", config_path=cfg_path, approval_path=approval, log_path=log
            )

        r = set_employee_enabled(
            "S03", False, "MGR", "test", config_path=cfg_path, approval_path=approval, log_path=log
        )
        self.assertEqual(r["employees_enabled"], 2)
        with self.assertRaises(ValueError):
            set_employee_enabled(
                "MGR", False, "MGR", "test", config_path=cfg_path, approval_path=approval, log_path=log
            )
        self.assertTrue(log.exists())
        # The real site config must not have been touched by this test.
        real = json.loads((ROOT / "config" / "station.json").read_text(encoding="utf-8"))
        self.assertFalse(real["sessions"]["S03"]["enabled"])

    def test_worker_identity_guard(self):
        env = dict(os.environ)
        env["AI_STATION_SESSION"] = "MGR"
        env["AI_STATION_PROJECT"] = self.pid
        cmd = [
            sys.executable,
            str(ROOT / "station.py"),
            "event",
            "--project",
            self.pid,
            "--session",
            "S01",
            "--type",
            "progress",
            "--summary",
            "冒充员工",
        ]
        r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("身份不匹配", r.stderr)

        ok = subprocess.run(
            [sys.executable, str(ROOT / "station.py"), "state", "--project", self.pid],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)

        blocked = subprocess.run(
            [sys.executable, str(ROOT / "station.py"), "approve", "--project", self.pid, "--decision", "approve"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertNotEqual(blocked.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
