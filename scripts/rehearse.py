#!/usr/bin/env python
"""Scripted rehearsal of the full station workflow (no model calls).

It writes real events/messages/task cards exactly as the CLI would, then verifies
ordering, the approval gate, meeting/revision handling and final acceptance.
This is a protocol rehearsal, not a live CLI test (see scripts/smoke_wake.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stationlib import actions  # noqa: E402
from stationlib.config import make_actor  # noqa: E402
from stationlib.paths import project_dir  # noqa: E402
from stationlib.state import project_state  # noqa: E402
from stationlib.store import (  # noqa: E402
    append_event,
    claim_messages,
    finish_message,
    list_events,
    list_inbox,
    now_iso,
)


def consume(pid: str, sid: str, limit: int = 100) -> int:
    n = 0
    for path, msg in claim_messages(pid, sid, limit=limit):
        finish_message(path, msg)
        n += 1
    return n


def expect(cond: bool, message: str, checks: list[dict]) -> None:
    checks.append({"check": message, "ok": bool(cond)})
    if not cond:
        print(f"  ✗ {message}")
    else:
        print(f"  ✓ {message}")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="演练完整协作流程")
    ap.add_argument("--project", default="demo")
    ap.add_argument("--title", default="演示：工作站首页文案改版")
    ap.add_argument("--with-pending", action="store_true", help="额外建立一个停在“等待批准”的演示项目")
    args = ap.parse_args(argv)
    pid = args.project

    if (project_dir(pid) / "meta.json").exists():
        print(f"ERROR: 项目 {pid} 已存在，不覆盖。换一个 --project 名字，或先自行归档/删除。", file=sys.stderr)
        return 2

    checks: list[dict] = []
    print(f"演练项目: {pid}")

    request = (
        "【演示用虚拟需求，不是真实项目】\n"
        "工作站首页现在信息太密，用户看不懂当前该做什么。\n"
        "目标：把首页改成一眼能看懂「阶段 / 当前发言人 / 待你批准 / 任务进度」，"
        "并保证窄屏可用。\n"
        "约束：不引入外部依赖；不改事件文件格式；用户仍只通过总管沟通。"
    )
    actions.init_project(pid, args.title, request, "虚拟需求")
    print("1. 接需求")

    opening = (
        "收到需求。我的理解：这次只改 UI 呈现，不动协议和事件格式。\n"
        "需要讨论三个问题：\n"
        "1) 首屏优先展示什么；\n"
        "2) 窄屏如何折叠；\n"
        "3) 等待用户批准时如何提示，避免员工提前开工。\n"
        "本轮发言顺序：S01 → S02。"
    )
    actions.manager_opening(pid, "MGR", opening, ["S01", "S02"])
    consume(pid, "MGR")
    print("2. 全员讨论（第 1 轮）")

    actions.grant_turn(pid, "MGR", "S01", 1, "首屏信息优先级 + 窄屏方案")
    consume(pid, "S01")
    actions.speak(
        pid,
        "S01",
        "提案 A：首屏固定四块——阶段、当前发言人、待批准、任务计数。\n"
        "窄屏时左栏折叠到顶部，时间线保持单列。\n"
        "风险：用户可能更需要看聊天，因此聊天入口要始终可见。",
        1,
        "proposal",
        title="S01 提案：四块信息 + 单列时间线",
    )
    consume(pid, "MGR")

    actions.grant_turn(pid, "MGR", "S02", 1, "回应 S01 提案")
    consume(pid, "S02")
    actions.speak(
        pid,
        "S02",
        "我认同四块信息，但反对把“待批准”做成普通卡片。\n"
        "理由：批准是唯一闸门，需要整块高亮并带备注输入，否则用户容易忽略。\n"
        "另外窄屏下不要隐藏当前发言人，最少保留一行。",
        1,
        "critique",
        title="S02 质疑：批准入口必须成为阻断式提示",
    )
    consume(pid, "MGR")
    actions.close_round(pid, "MGR", 1, "第一轮：方向一致，分歧在批准提示的视觉权重。")

    print("3. 第二轮收敛 + 用户澄清")
    actions.grant_turn(pid, "MGR", "S01", 2, "回应 S02，给出折中")
    consume(pid, "S01")
    actions.speak(
        pid,
        "S01",
        "接受 S02 的点：把批准做成独立高亮区，带备注。\n"
        "折中：四块信息保留，但批准区在待批时置顶并占满右栏宽度。\n"
        "另外需要用户确认：是否允许窄屏隐藏左侧任务明细、只保留计数？",
        2,
        "response",
        title="S01 折中方案",
    )
    consume(pid, "MGR")

    actions.user_question(
        pid,
        "S01",
        "窄屏下能否只显示任务计数，不显示每条任务明细？",
    )
    consume(pid, "MGR")
    print("   → 总管把问题转给用户（user_question 事件）")

    actions.user_message(pid, "可以，窄屏只显示计数，点开再看明细。")
    consume(pid, "MGR")
    actions.manager_chat(pid, "MGR", "收到：窄屏只保留任务计数，明细可展开。")

    print("4. 第三轮投票")
    actions.grant_turn(pid, "MGR", "S01", 3, "对最终方案投票")
    consume(pid, "S01")
    actions.speak(pid, "S01", "同意折中方案，可以进入实施。", 3, "vote", choice="方案A-折中")
    consume(pid, "MGR")
    actions.grant_turn(pid, "MGR", "S02", 3, "对最终方案投票")
    consume(pid, "S02")
    actions.speak(pid, "S02", "同意，但保留“批准区必须独立”的验收项。", 3, "vote", choice="方案A-折中")
    consume(pid, "MGR")
    actions.close_round(pid, "MGR", 3, "第三轮：2/2 同意方案A-折中。")
    actions.record_decision(
        pid,
        "MGR",
        "采用方案A-折中",
        "结论：四块信息 + 待批准时独立高亮 + 窄屏计数折叠。\n"
        "票数：2/2。\n分歧：无遗留。\n落选：无。",
        data={"votes": {"S01": "方案A-折中", "S02": "方案A-折中"}},
    )

    print("5. 提交方案，验证批准闸门")
    plan_text = (
        "## 范围\n只改 ui/ 下的呈现与交互；不动事件格式、不动 worker。\n\n"
        "## 实施步骤\n1. S01：整理信息层级并改首页结构；\n2. S02：响应式与可访问性检查。\n\n"
        "## 分工\n- T001 owner=S01 reviewer=S02\n- T002 owner=S02 reviewer=S01\n\n"
        "## 验收\n- 首屏四块信息可见；\n- 待批准时独立高亮且带备注；\n"
        "- 820px 下无横向滚动；\n- 窄屏只显示任务计数。\n\n"
        "## 风险\n- 改动 index.html 时两人冲突 → 由 T001 先落地，T002 后改。"
    )
    actions.propose_plan(pid, "MGR", "v1", "首页呈现改版", plan_text)
    consume(pid, "MGR")
    consume(pid, "S01")
    consume(pid, "S02")

    st = project_state(pid)
    expect(st["phase"]["key"] == "awaiting_approval", "方案提交后进入等待用户批准", checks)
    gate_blocked = False
    try:
        actions.create_task(pid, "MGR", title="偷跑任务", owner="S01", acceptance=["x"])
    except PermissionError:
        gate_blocked = True
    expect(gate_blocked, "批准前创建实施任务被拒绝", checks)

    actions.approve_plan(pid, True, "同意 v1，按折中方案执行")
    consume(pid, "MGR")
    append_event(pid, "plan_approved", make_actor("MGR"), "用户批准方案 v1，向全员广播开工")
    actions.manager_chat(pid, "MGR", "方案 v1 已获批准。开始拆分任务，S01/S02 按任务卡实施。")
    expect(project_state(pid)["phase"]["key"] == "implementation", "用户批准后进入实施阶段", checks)

    print("6. 拆分任务与实施")
    t1 = actions.create_task(
        pid,
        "MGR",
        title="重排首页信息层级（四块信息 + 窄屏计数）",
        owner="S01",
        reviewer="S02",
        goal="首屏四块信息清晰，窄屏不横向滚动",
        background="方案A-折中：四块信息 + 待批准独立高亮 + 窄屏计数折叠",
        inputs=["ui/index.html", "ui/style.css", "ui/app.js"],
        scope=["ui/index.html", "ui/app.js", "ui/style.css"],
        forbidden=["stationlib/", "protocol.md", "projects/"],
        deliverables=["改版后的 UI 文件", "自检截图/说明"],
        acceptance=["首屏四块信息可见", "窄屏只显示任务计数", "无外部依赖"],
    )
    consume(pid, "S01")
    t2 = actions.create_task(
        pid,
        "MGR",
        title="响应式与可访问性检查",
        owner="S02",
        reviewer="S01",
        goal="820px 及以下可用，键盘可达",
        background="方案A-折中的验收项",
        inputs=["ui/index.html", "ui/style.css"],
        scope=["ui/style.css", "ui/index.html"],
        forbidden=["ui/app.js", "stationlib/"],
        deliverables=["检查记录"],
        acceptance=["820px 下无横向滚动", "焦点可见", "控制台无报错"],
    )
    consume(pid, "S02")

    actions.update_task_status(pid, t1["task_id"], "in_progress", "S01", "开始重排首页")
    consume(pid, "MGR")
    actions.update_task_status(pid, t2["task_id"], "in_progress", "S02", "开始窄屏检查")
    consume(pid, "MGR")
    append_event(pid, "progress", make_actor("S01"), "T001 完成四块信息重排，待自检", data={"task_id": t1["task_id"]})
    consume(pid, "MGR")

    print("7. 阻塞 + 临时拉会 + 内部修订")
    actions.update_task_status(
        pid,
        t2["task_id"],
        "blocked",
        "S02",
        "T002 与 T001 都要改 index.html，出现结构冲突",
    )
    consume(pid, "MGR")
    actions.request_meeting(
        pid,
        "S02",
        "T001 与 T002 同时修改 index.html，需要约定落地顺序",
        "diff: ui/index.html 同时被两个任务修改",
    )
    consume(pid, "MGR")
    actions.open_meeting(pid, "MGR", "暂停 UI 新增修改，讨论 index.html 的修改顺序")
    consume(pid, "S01")
    consume(pid, "S02")

    actions.grant_turn(pid, "MGR", "S02", 4, "说明冲突")
    consume(pid, "S02")
    actions.speak(pid, "S02", "冲突点：T001 重排结构，T002 改响应式样式，但两者都碰 index.html。建议 T001 先落地，T002 基于新结构再检查。", 4, "critique")
    consume(pid, "MGR")
    actions.grant_turn(pid, "MGR", "S01", 4, "回应顺序建议")
    consume(pid, "S01")
    actions.speak(pid, "S01", "同意 T001 先落地；完成后通知 S02，T002 只在新结构上做样式与无障碍修正。", 4, "response")
    consume(pid, "MGR")
    actions.grant_turn(pid, "MGR", "S01", 4, "对顺序方案投票")
    consume(pid, "S01")
    actions.speak(pid, "S01", "同意：T001 先落地，T002 后接。", 4, "vote", choice="顺序A")
    consume(pid, "MGR")
    actions.grant_turn(pid, "MGR", "S02", 4, "对顺序方案投票")
    consume(pid, "S02")
    actions.speak(pid, "S02", "同意顺序A。", 4, "vote", choice="顺序A")
    consume(pid, "MGR")
    actions.close_meeting(pid, "MGR", "决议：T001 先落地；T002 在其后基于新结构修改。")
    actions.record_decision(
        pid,
        "MGR",
        "实施顺序调整",
        "T001 先落地 index.html 结构，T002 之后只做样式与无障碍修正。\n票数 2/2。未改变用户目标与范围，属于内部修订。",
        data={"votes": {"S01": "顺序A", "S02": "顺序A"}},
    )
    actions.publish_revision(
        pid,
        "MGR",
        "v1.1",
        "调整 index.html 修改顺序",
        "T001 先落地；T002 基于新结构修改，禁止回退 T001 的结构改动。",
        requires_user_approval=False,
        changes="T002 的输入改为 T001 完成后的 index.html",
    )
    consume(pid, "S01")
    consume(pid, "S02")
    actions.update_task_status(pid, t2["task_id"], "in_progress", "S02", "按修订恢复工作")
    consume(pid, "MGR")

    print("8. 交付、交叉审查、验收")
    actions.update_task_status(pid, t1["task_id"], "review", "S01", "重排完成", evidence="ui/index.html")
    consume(pid, "MGR")
    actions.handoff(
        pid,
        "S01",
        t1["task_id"],
        "T001 交付：首页信息层级",
        "已按方案A-折中重排四块信息；窄屏只显示任务计数，明细可展开。",
        files=["ui/index.html", "ui/app.js", "ui/style.css"],
        checks="手动检查 1370px/1180px/820px 三档；无外部依赖",
        limits="未做浏览器截图对比",
    )
    consume(pid, "MGR")
    consume(pid, "S02")
    actions.report_review(pid, "S02", t1["task_id"], "pass", "四块信息与窄屏折叠均符合验收；未发现结构回退。", "人工检查")
    consume(pid, "MGR")
    consume(pid, "S01")
    actions.update_task_status(pid, t1["task_id"], "done", "S02", "审查通过")
    consume(pid, "MGR")

    actions.update_task_status(pid, t2["task_id"], "review", "S02", "检查完成", evidence="检查记录")
    consume(pid, "MGR")
    actions.handoff(
        pid,
        "S02",
        t2["task_id"],
        "T002 交付：响应式与可访问性",
        "820px 无横向滚动；键盘焦点可见；控制台无报错。",
        files=["ui/style.css"],
        checks="浏览器手动缩放 820px/1180px；Tab 键走查",
        limits="未跑自动化无障碍工具",
    )
    consume(pid, "MGR")
    consume(pid, "S01")
    actions.report_review(pid, "S01", t2["task_id"], "pass", "验收项均满足；T001 结构未被回退。", "人工检查")
    consume(pid, "MGR")
    consume(pid, "S02")
    actions.update_task_status(pid, t2["task_id"], "done", "S01", "审查通过")
    consume(pid, "MGR")

    actions.finish_project(
        pid,
        "MGR",
        "首页呈现改版完成：四块信息、待批准高亮、窄屏计数折叠。",
        "T001/T002 均通过交叉审查；协议文件未改动。",
    )
    consume(pid, "MGR")
    consume(pid, "S01")
    consume(pid, "S02")

    # ---------------------------------------------------------------- verify
    print("9. 校验")
    events = list_events(pid)
    seqs = [e["seq"] for e in events]
    expect(seqs == list(range(1, len(seqs) + 1)), f"事件序号连续且唯一（1..{len(seqs)}）", checks)
    expect(all(e.get("ts") for e in events), "每条事件都有时间戳", checks)
    st = project_state(pid)
    expect(st["phase"]["key"] == "done", "最终阶段为已完成", checks)
    expect(st["plan"]["approved_version"] == "v1", "方案 v1 已被用户批准", checks)
    expect(all(t["status"] == "done" for t in st["tasks"]), "所有任务均为 done", checks)
    expect(st["task_summary"]["total"] == 2, "任务数为 2", checks)
    expect(st["meeting"]["active"] is False, "临时会议已关闭", checks)
    expect(
        all(len(list_inbox(pid, sid)) == 0 for sid in ("MGR", "S01", "S02")),
        "所有收件箱已清空（无未处理消息）",
        checks,
    )
    missing_bodies = [
        e["body_file"]
        for e in events
        if e.get("body_file") and not (project_dir(pid) / e["body_file"]).exists()
    ]
    expect(not missing_bodies, "所有 body_file 实际存在", checks)

    if args.with_pending:
        pend_pid = args.project + "-approval"
        if not (project_dir(pend_pid) / "meta.json").exists():
            actions.init_project(
                pend_pid,
                "演示：等待用户批准",
                "【演示】一个停在“等待批准”的项目，用于试用 UI 的批准按钮。",
                "虚拟需求：等待批准演示",
            )
            actions.manager_opening(pend_pid, "MGR", "演示开场：先给一个方案，等待用户批准。", ["S01"])
            actions.grant_turn(pend_pid, "MGR", "S01", 1, "给一个初版方案")
            consume(pend_pid, "S01")
            actions.speak(
                pend_pid,
                "S01",
                "建议只做一个小改动，验证批准流程即可。",
                1,
                "proposal",
                title="演示提案",
            )
            consume(pend_pid, "MGR")
            actions.close_round(pend_pid, "MGR", 1, "讨论结束，提交方案。")
            actions.propose_plan(
                pend_pid,
                "MGR",
                "v1",
                "演示方案：小步改版",
                "范围：只改一处文案。\n验收：文案清晰。\n风险：无。",
            )
            consume(pend_pid, "MGR")
            consume(pend_pid, "S01")
            consume(pend_pid, "S02")
            print(f"   已建立待批准演示项目: {pend_pid}")

    report = {
        "project": pid,
        "generated_at": now_iso(),
        "mode": "scripted-rehearsal (no model calls)",
        "event_count": len(seqs),
        "phase": st["phase"],
        "checks": checks,
        "passed": all(c["ok"] for c in checks),
    }
    report_path = project_dir(pid) / "logs" / "rehearsal_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print()
    print(f"事件数: {len(seqs)}  任务: {st['task_summary']['progress_text']}")
    print(f"报告: {report_path}")
    if report["passed"]:
        print("全部校验通过。")
        print("下一步: python ui/server.py --open")
        return 0
    print("有校验失败，请查看报告。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
