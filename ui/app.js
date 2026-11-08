(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const TYPE_LABEL = {
    project_created: "项目建立",
    request_registered: "需求登记",
    manager_opening: "总管开场",
    turn_granted: "分配发言",
    round_closed: "轮次结束",
    proposal: "提案",
    critique: "质疑",
    response: "回应",
    vote: "投票",
    question: "提问",
    user_question: "等待用户回答",
    user_answer: "用户答复",
    user_message: "用户消息",
    manager_chat: "总管回复",
    plan_proposed: "提交方案",
    user_approval: "用户批准",
    user_rejection: "用户驳回",
    plan_approved: "方案获批",
    plan_rejected: "方案驳回",
    assignment: "任务分派",
    task_status: "任务状态",
    progress: "进度",
    blocker: "阻塞",
    staffing_change: "编制变更",
    meeting_request: "申请拉会",
    meeting_open: "拉会开始",
    meeting_closed: "拉会结束",
    revision_proposed: "修订提案",
    revision_published: "修订发布",
    decision: "决策",
    handoff: "交付",
    review_result: "审查",
    acceptance: "验收",
    project_done: "项目完成",
    project_reopened: "重新打开",
    worker_error: "运行错误",
    system: "系统",
  };

  const GROUP = {
    discussion: new Set([
      "manager_opening",
      "turn_granted",
      "round_closed",
      "proposal",
      "critique",
      "response",
      "vote",
      "question",
      "decision",
      "meeting_request",
      "meeting_open",
      "meeting_closed",
      "revision_proposed",
      "revision_published",
      "plan_proposed",
      "project_reopened",
    ]),
    tasks: new Set([
      "assignment",
      "task_status",
      "progress",
      "blocker",
      "staffing_change",
      "handoff",
      "review_result",
      "acceptance",
      "project_done",
      "worker_error",
    ]),
    user: new Set([
      "user_message",
      "manager_chat",
      "user_question",
      "user_answer",
      "user_approval",
      "user_rejection",
    ]),
  };

  const STATUS_META = {
    open: { glyph: "○", label: "待开始" },
    in_progress: { glyph: "▶", label: "进行中" },
    review: { glyph: "◐", label: "待审查" },
    done: { glyph: "✓", label: "已完成" },
    blocked: { glyph: "⛔", label: "阻塞" },
  };

  const ROLE_LABEL = { manager: "总管", worker: "员工", reviewer: "审查", user: "用户", system: "系统" };
  const SESSION_STATUS = { working: "工作中", idle: "待命", offline: "离线", never: "未启动" };

  let project = null;
  let state = null;
  let filter = "all";
  let busy = false;
  let renderedApprovalKey = "";
  let timelineRendered = false;
  const bodyCache = new Map();
  const expandedEvents = new Set();
  const expandedChat = new Set();
  const actorColors = {};

  // ---------------------------------------------------------------- utils

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function mdToHtml(text) {
    let s = escapeHtml(text);
    s = s
      .replace(/^#{1,6}\s+(.+)$/gm, "<strong>$1</strong>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/^\s*[-*]\s+(.+)$/gm, "• $1");
    return s.replace(/\n{2,}/g, "<br><br>").replace(/\n/g, "<br>");
  }

  function timeHm(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function ago(iso) {
    if (!iso) return "—";
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return "—";
    const s = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (s < 60) return `${s} 秒前`;
    if (s < 3600) return `${Math.round(s / 60)} 分钟前`;
    if (s < 86400) return `${Math.round(s / 3600)} 小时前`;
    return `${Math.round(s / 86400)} 天前`;
  }

  function roleLabel(role) {
    return ROLE_LABEL[role] || role || "";
  }

  function sessionName(sid) {
    const s = state?.sessions?.find((x) => x.session_id === sid);
    return s ? `${s.name}` : sid;
  }

  function sessionColor(sid) {
    return actorColors[sid] || "var(--baseline)";
  }

  function showNotice(msg) {
    const n = $("#notice");
    n.textContent = msg;
    n.classList.add("show");
  }

  function hideNotice() {
    $("#notice").classList.remove("show");
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  // ---------------------------------------------------------------- boot

  async function boot() {
    restoreTheme();
    bindBasicEvents();
    try {
      const meta = await api("/api/projects");
      const list = meta.projects || [];
      const sel = $("#projectSelect");
      if (!list.length) {
        sel.innerHTML = '<option value="">（暂无项目）</option>';
        $("#timeline").innerHTML = '<div class="empty">还没有项目。运行 <code>python scripts/rehearse.py</code> 生成演示，或用 <code>station.py init</code> 新建项目。</div>';
        showNotice("站点里还没有项目。先初始化一个项目。");
        return;
      }
      sel.innerHTML = list
        .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.title)}</option>`)
        .join("");

      const hashProject = new URLSearchParams(location.hash.slice(1)).get("project");
      project =
        hashProject ||
        localStorage.getItem("station.project") ||
        meta.default ||
        list[0].id;
      if (!list.some((p) => p.id === project)) project = list[0].id;
      sel.value = project;
      localStorage.setItem("station.project", project);
      history.replaceState(null, "", `#project=${encodeURIComponent(project)}`);

      sel.addEventListener("change", () => {
        project = sel.value;
        localStorage.setItem("station.project", project);
        history.replaceState(null, "", `#project=${encodeURIComponent(project)}`);
        state = null;
        lastEventSeq = -1;
        timelineRendered = false;
        renderedApprovalKey = "";
        bodyCache.clear();
        expandedEvents.clear();
        expandedChat.clear();
        signalActive(project);
        load();
      });

      signalActive(project);
      await load();
      setInterval(() => {
        if (!document.hidden) load(true);
      }, 2000);
    } catch (e) {
      showNotice(`启动失败：${e.message}`);
    }
  }

  let lastEventSeq = -1;

  async function load(silent = false) {
    if (!project || busy) return;
    busy = true;
    try {
      const st = await api(`/api/state?project=${encodeURIComponent(project)}&limit=5000`);
      state = st;
      render();
      hideNotice();
    } catch (e) {
      if (!silent) showNotice(`读取失败：${e.message}`);
    } finally {
      busy = false;
    }
  }

  // ---------------------------------------------------------------- render

  function render() {
    if (!state) return;
    const st = state;
    const grown = st.events.length && st.events[st.events.length - 1].seq !== lastEventSeq;
    lastEventSeq = st.events.length ? st.events[st.events.length - 1].seq : -1;

    document.title = `${st.project.title} · AI 协作工作站`;
    $("#projectSub").textContent = `${st.project.title} · ${st.project.project_id}`;

    const phase = st.phase || { key: "intake", label: "接需求" };
    const pill = $("#phasePill");
    pill.textContent = phase.label;
    pill.dataset.phase = phase.key;

    $("#updatedAt").textContent = `${timeHm(st.updated_at)} 更新`;

    // stable actor colors: session order from config
    st.sessions.forEach((s, i) => {
      // Fixed order, never cycled: past the 8th session use neutral ink and
      // rely on the name label instead of repeating a hue.
      actorColors[s.session_id] = i < 8 ? `var(--s${i + 1})` : "var(--muted)";
    });
    actorColors.USER = "var(--ink-2)";

    renderTurn();
    renderSessions();
    renderTasks();
    renderPlan();
    renderMeta();
    renderApproval();
    // Do not rebuild the timeline/chat on every 2s poll; that would collapse
    // any event/message the user just expanded. Rebuild only when a new event
    // arrives (expanded sets still restore those states).
    if (grown || !timelineRendered) {
      renderTimeline(grown);
      renderChat();
      timelineRendered = true;
    }
  }

  function renderTurn() {
    const card = $("#turnCard");
    const t = state.turn || {};
    card.classList.toggle("idle", !t.current);
    card.classList.toggle("stalled", !!state.stalled);
    if (t.current) {
      card.innerHTML = `
        <div class="label">当前发言${t.round ? ` · 第 ${t.round} 轮` : ""}</div>
        <div class="who"><span class="dot online" style="background:${sessionColor(t.current)}"></span>${escapeHtml(sessionName(t.current))} <span class="muted mono">${escapeHtml(t.current)}</span></div>
        ${t.topic ? `<div class="topic">${escapeHtml(t.topic)}</div>` : ""}`;
    } else if (state.stalled) {
      card.innerHTML = `<div class="label">讨论停滞</div><div class="who">总管没有安排下一位</div><div class="topic">在右边给总管发一句话，提醒它继续分配发言。</div>`;
    } else if (state.phase.key === "done") {
      card.innerHTML = `<div class="label">项目状态</div><div class="who">已完成并验收</div><div class="topic">讨论、批准、实施与验收记录均保留在事件流。</div>`;
    } else if (state.phase.key === "awaiting_approval") {
      card.innerHTML = `<div class="label">项目状态</div><div class="who">方案已提交，等待你批准</div><div class="topic">批准前员工不能开工。右侧可查看方案、写备注并批准/驳回。</div>`;
    } else if (state.phase.key === "revision") {
      card.innerHTML = `<div class="label">项目状态</div><div class="who">临时讨论进行中</div><div class="topic">受影响任务先暂停新增修改，等待轮流发言与表决。</div>`;
    } else if (state.phase.key === "implementation") {
      card.innerHTML = `<div class="label">项目状态</div><div class="who">实施中</div><div class="topic">员工按已批准任务卡工作；总管监督进度与偏差。</div>`;
    } else if (t.round) {
      card.innerHTML = `<div class="label">讨论</div><div class="who">第 ${t.round} 轮已结束</div><div class="topic">等待总管决定下一轮或收敛方案。</div>`;
    } else {
      card.innerHTML = `<div class="label">当前发言</div><div class="who">暂无发言安排</div><div class="topic">${
        state.phase.key === "awaiting_approval"
          ? "方案已提交，等待你的批准。"
          : state.phase.key === "implementation"
          ? "员工按任务卡实施，总管在监督。"
          : "总管尚未开启讨论。"
      }</div>`;
    }
  }

  function sessionIsOnline(s) {
    if (s.online) return true;
    // A long model call only writes the heartbeat at its start. Do not call a
    // "working" session offline just because the call exceeds the 90s window.
    if (s.status === "working" && s.last_heartbeat) {
      const t = new Date(s.last_heartbeat).getTime();
      return Number.isFinite(t) && Date.now() - t < 10 * 60 * 1000;
    }
    return false;
  }

  function renderSessions() {
    const list = state.sessions || [];
    const online = list.filter(sessionIsOnline).length;
    const stf = state.staffing || {};
    $("#onlineCount").textContent = `在线 ${online} · 员工 ${stf.employees_enabled ?? "?"}/${stf.limit ?? "?"}`;
    $("#sessionsList").innerHTML = list
      .map((s) => {
        const isOnline = sessionIsOnline(s);
        const dotCls = isOnline ? (s.status === "working" ? "working" : "online") : "";
        const unread = s.inbox?.ready || 0;
        const failed = s.inbox?.failed || 0;
        const disabled = s.enabled === false;
        const shownStatus = !isOnline && s.status === "working" ? "offline" : s.status;
        const badge = disabled
          ? `<span class="badge">未启用</span>`
          : unread
          ? `<span class="badge alert">${unread} 待办</span>`
          : failed
          ? `<span class="badge alert">失败 ${failed}</span>`
          : `<span class="badge">${SESSION_STATUS[shownStatus] || shownStatus}</span>`;
        const last = s.last_activity
          ? `${ago(s.last_activity.ts)} · ${TYPE_LABEL[s.last_activity.type] || s.last_activity.type}`
          : "暂无活动";
        return `
          <div class="session" ${disabled ? 'style="opacity:.6"' : ""}>
            <span class="dot ${dotCls}"></span>
            <div>
              <div class="name">${escapeHtml(s.name)} <span class="muted mono">${escapeHtml(s.session_id)}</span></div>
              <div class="meta">${roleLabel(s.role)} · ${escapeHtml(s.engine)} · ${escapeHtml(last)}</div>
            </div>
            ${badge}
          </div>`;
      })
      .join("");
  }

  function renderTasks() {
    const tasks = state.tasks || [];
    const ts = state.task_summary || { total: 0, done: 0, progress_text: "暂无任务" };
    $("#taskMeterLabel").textContent = ts.progress_text || "暂无任务";
    const pct = ts.total ? Math.round((ts.done / ts.total) * 100) : 0;
    $("#taskMeter").style.width = `${pct}%`;
    if (!tasks.length) {
      $("#tasksList").innerHTML = '<div class="empty" style="padding:10px 0">方案批准后，总管会在这里建任务。</div>';
      return;
    }
    $("#tasksList").innerHTML = tasks
      .map((t) => {
        const meta = STATUS_META[t.status] || { glyph: "○", label: t.status };
        const owner = t.owner_session ? `${escapeHtml(t.owner_session)}` : "—";
        const reviewer = t.reviewer_session ? ` / 审 ${escapeHtml(t.reviewer_session)}` : "";
        const blocked = t.status === "blocked" && t.blocked_reason
          ? `<div class="tmeta" style="color:var(--critical)">${escapeHtml(t.blocked_reason)}</div>`
          : "";
        return `
          <div class="task">
            <span class="tid">${escapeHtml(t.task_id)}</span>
            <div class="tbody">
              <div class="ttitle">${escapeHtml(t.title)}</div>
              <div class="tmeta">owner ${owner}${reviewer}</div>
              ${blocked}
            </div>
            <span class="status-pill" data-status="${escapeHtml(t.status)}"><span class="glyph">${meta.glyph}</span>${meta.label}</span>
          </div>`;
      })
      .join("");
  }

  function renderPlan() {
    const p = state.plan || {};
    const box = $("#planCard");
    const link = p.body_file
      ? `<a class="linkish" href="/api/body?project=${encodeURIComponent(project)}&path=${encodeURIComponent(p.body_file)}" target="_blank" rel="noopener">查看方案全文</a>`
      : "";
    if (p.pending) {
      box.innerHTML = `
        <div class="plan-ver">方案 ${escapeHtml(p.pending.version)}
          <span class="status-pill" data-status="review"><span class="glyph">◐</span>等待批准</span>
        </div>
        <div class="plan-desc">${escapeHtml(p.pending.title || "")}</div>
        <div style="margin-top:8px">${link}</div>`;
    } else if (p.approved_version) {
      const rev = p.version && p.version !== p.approved_version ? ` · 修订 ${escapeHtml(p.version)}` : "";
      box.innerHTML = `
        <div class="plan-ver">方案 ${escapeHtml(p.approved_version)}
          <span class="status-pill" data-status="done"><span class="glyph">✓</span>已批准</span>
        </div>
        <div class="plan-desc">${escapeHtml(p.title || "")}${rev}</div>
        <div style="margin-top:8px">${link}</div>`;
    } else if (p.version) {
      box.innerHTML = `<div class="plan-ver">方案 ${escapeHtml(p.version)}</div><div class="plan-desc">${escapeHtml(p.title || "")}</div><div style="margin-top:8px">${link}</div>`;
    } else {
      box.innerHTML = '<div class="empty" style="padding:8px 0">尚未提交方案。先完成全员讨论。</div>';
    }
  }

  function renderMeta() {
    const m = state.project || {};
    const stf = state.staffing || {};
    const staffColor = stf.over_limit ? "var(--critical)" : "";
    const staffText = `${stf.employees_enabled ?? "?"}/${stf.limit ?? "?"} 启用${
      stf.approved_max ? ` · 已批准 ${stf.approved_max}` : ""
    }`;
    const ws = m.workspace || "";
    const wsColor = ws && !state.workspace_exists ? "var(--critical)" : "";
    const costUsd = Number((state.cost || {}).total_usd || 0);
    const costText = costUsd > 0 ? `$${costUsd.toFixed(2)}` : "—";
    $("#projectMeta").innerHTML = `
      <div class="kv"><span class="k">项目 ID</span><span class="v mono">${escapeHtml(m.project_id || "")}</span></div>
      <div class="kv"><span class="k">工作区</span><span class="v mono" ${wsColor ? `style="color:${wsColor}"` : ""} title="${escapeHtml(ws)}">${ws ? escapeHtml(ws) : "未设置"}</span></div>
      <div class="kv"><span class="k">建立时间</span><span class="v">${escapeHtml((m.created_at || "").slice(0, 16).replace("T", " "))}</span></div>
      <div class="kv"><span class="k">事件数</span><span class="v">${state.event_count}</span></div>
      <div class="kv"><span class="k">任务</span><span class="v">${state.task_summary.done}/${state.task_summary.total} 已验收</span></div>
      <div class="kv"><span class="k">员工</span><span class="v" ${staffColor ? `style="color:${staffColor}"` : ""}>${staffText}</span></div>
      <div class="kv"><span class="k">成本</span><span class="v">${costText}</span></div>
      <div class="kv"><span class="k">阶段</span><span class="v">${escapeHtml(state.phase.label)}</span></div>`;
  }

  function renderApproval() {
    const slot = $("#approvalSlot");
    const p = state.plan || {};
    if (!p.pending) {
      slot.innerHTML = "";
      renderedApprovalKey = "";
      return;
    }
    const key = `${p.pending.version}|${p.pending.seq}`;
    if (renderedApprovalKey === key) return;
    renderedApprovalKey = key;
    const link = p.body_file
      ? `<a class="linkish" href="/api/body?project=${encodeURIComponent(project)}&path=${encodeURIComponent(p.body_file)}" target="_blank" rel="noopener">先看方案全文</a>`
      : "";
    slot.innerHTML = `
      <div class="approval" role="region" aria-label="方案批准">
        <h2><span class="warning-icon" aria-hidden="true">▲</span> 方案待你批准</h2>
        <div class="plan-ver">${escapeHtml(p.pending.version)} <span class="muted" style="font-weight:400;font-size:12px">${escapeHtml(p.pending.title || "")}</span></div>
        <p>批准后总管才会拆分任务；批准前员工不能开工。${link ? " " + link : ""}</p>
        <textarea id="approvalNote" placeholder="备注（可选；驳回时建议写清原因）"></textarea>
        <div class="btn-row">
          <button class="btn primary" id="approveBtn" type="button">批准并开工</button>
          <button class="btn danger" id="rejectBtn" type="button">驳回，要求修改</button>
        </div>
      </div>`;
    $("#approveBtn").addEventListener("click", () => decide("approve"));
    $("#rejectBtn").addEventListener("click", () => decide("reject"));
  }

  async function decide(decision) {
    if (!state?.plan?.pending) return;
    const note = ($("#approvalNote")?.value || "").trim();
    const buttons = $$("#approveBtn, #rejectBtn, #approvalNote");
    buttons.forEach((b) => (b.disabled = true));
    try {
      await api("/api/approve", {
        method: "POST",
        body: JSON.stringify({ project, decision, note }),
      });
      renderedApprovalKey = "";
      await load();
    } catch (e) {
      showNotice(`操作失败：${e.message}`);
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  function eventColor(type) {
    if (["user_approval", "acceptance", "project_done"].includes(type)) return "var(--good)";
    if (["user_rejection", "blocker", "worker_error"].includes(type)) return "var(--critical)";
    if (["meeting_request", "meeting_open", "meeting_closed", "revision_published"].includes(type)) return "var(--serious)";
    if (["plan_proposed", "user_question", "review_result", "task_status"].includes(type)) return "var(--warning)";
    if (["proposal", "critique", "response", "vote", "manager_opening", "turn_granted", "decision"].includes(type)) return "var(--accent)";
    return "var(--baseline)";
  }

  function eventDetail(ev) {
    const d = ev.data || {};
    if (ev.type === "vote" && d.choice) return `投票：${d.choice}`;
    if (ev.type === "review_result" && d.evidence) return `证据：${d.evidence}`;
    if (ev.type === "handoff" && d.checks) return `自检：${d.checks}`;
    if (ev.type === "plan_proposed" && d.version) return `版本 ${d.version}`;
    if (ev.type === "meeting_open") return "受影响任务先暂停新增修改";
    return "";
  }

  function passesFilter(ev) {
    if (filter === "all") return true;
    return GROUP[filter]?.has(ev.type);
  }

  function renderTimeline(grown) {
    const col = $("#centerCol");
    const nearBottom = col.scrollTop + col.clientHeight >= col.scrollHeight - 120;
    const events = (state.events || []).filter(passesFilter);
    const box = $("#timeline");
    if (!events.length) {
      box.innerHTML = `<div class="empty">${filter === "all" ? "还没有事件。" : "该筛选下没有事件。"}</div>`;
      return;
    }
    box.innerHTML = events
      .map((ev) => {
        const actor = ev.actor || {};
        const name = actor.name || actor.session_id || "系统";
        const color =
          actor.kind === "user" ? "var(--ink-2)" : actor.kind === "system" ? "var(--muted)" : sessionColor(actor.session_id);
        const typeLabel = TYPE_LABEL[ev.type] || ev.type;
        const detail = eventDetail(ev);
        const canExpand = ev.has_body;
        const key = String(ev.seq);
        const isOpen = expandedEvents.has(key);
        const content = bodyCache.get(key) ?? ev.body ?? "";
        return `
          <article class="ev" style="--ev-color:${eventColor(ev.type)}" data-seq="${ev.seq}">
            <div class="ev-rail">
              <span class="ev-seq">#${ev.seq}</span>
              <span class="ev-time">${timeHm(ev.ts)}</span>
            </div>
            <div>
              <div class="ev-meta">
                <span class="actor-chip" style="--actor-color:${color}">${escapeHtml(name)}</span>
                <span class="type-chip">${escapeHtml(typeLabel)}</span>
                ${ev.round ? `<span class="round-chip">R${ev.round}</span>` : ""}
                <span class="muted" style="font-size:11px">${escapeHtml(roleLabel(actor.role))}</span>
              </div>
              <p class="ev-summary">${escapeHtml(ev.summary || "")}</p>
              ${detail ? `<div class="ev-detail">${escapeHtml(detail)}</div>` : ""}
              ${
                canExpand
                  ? `<div style="margin-top:6px"><button class="linkish" type="button" data-expand="${ev.seq}">${
                      isOpen ? "收起全文" : "查看全文"
                    }</button></div><div class="ev-full" ${isOpen ? "" : "hidden"} ${
                      content ? 'data-filled="1"' : ""
                    }>${content ? mdToHtml(content) : ""}</div>`
                  : ""
              }
            </div>
          </article>`;
      })
      .join("");
    if (grown && nearBottom) {
      requestAnimationFrame(() => {
        col.scrollTop = col.scrollHeight;
      });
    }
    hydrateExpanded(events);
  }

  async function hydrateExpanded(events) {
    for (const ev of events) {
      const key = String(ev.seq);
      if (!expandedEvents.has(key)) continue;
      const article = document.querySelector(`.ev[data-seq="${key}"]`);
      const box = article?.querySelector(".ev-full");
      if (!box || box.dataset.filled === "1") continue;
      let text = bodyCache.get(key);
      if (!text) {
        try {
          if (ev.body) {
            text = ev.body;
          } else if (ev.body_file) {
            const res = await fetch(
              `/api/body?project=${encodeURIComponent(project)}&path=${encodeURIComponent(ev.body_file)}`
            );
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            text = await res.text();
          } else {
            text = ev.summary || "";
          }
          bodyCache.set(key, text);
        } catch (e) {
          text = `读取全文失败：${e.message}`;
        }
      }
      if (!expandedEvents.has(key)) continue;
      box.innerHTML = mdToHtml(text);
      box.hidden = false;
      box.dataset.filled = "1";
    }
  }

  async function expandEvent(btn) {
    const key = String(btn.dataset.expand);
    const article = btn.closest(".ev");
    const box = article?.querySelector(".ev-full");
    const ev = state.events.find((x) => String(x.seq) === key);
    if (!box || !ev) return;
    if (expandedEvents.has(key)) {
      expandedEvents.delete(key);
      box.hidden = true;
      box.innerHTML = "";
      delete box.dataset.filled;
      btn.textContent = "查看全文";
      return;
    }
    expandedEvents.add(key);
    btn.textContent = "收起全文";
    box.hidden = false;
    let text = bodyCache.get(key);
    if (!text) {
      try {
        if (ev.body) {
          text = ev.body;
        } else if (ev.body_file) {
          const res = await fetch(
            `/api/body?project=${encodeURIComponent(project)}&path=${encodeURIComponent(ev.body_file)}`
          );
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          text = await res.text();
        } else {
          text = ev.summary || "";
        }
        bodyCache.set(key, text);
      } catch (e) {
        text = `读取全文失败：${e.message}`;
      }
    }
    if (!expandedEvents.has(key)) return;
    box.innerHTML = mdToHtml(text);
    box.hidden = false;
    box.dataset.filled = "1";
  }

  function renderChat() {
    const list = $("#chatList");
    const nearBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 80;
    const msgs = (state.chat || []).slice(-200);
    if (!msgs.length) {
      list.innerHTML = '<div class="empty">还没有对话。向总管提出需求即可开始。</div>';
    } else {
      list.innerHTML = msgs
        .map((m) => {
          const actor = m.actor || {};
          let cls = "manager";
          let who = actor.name || "总管";
          if (m.type === "user_message" || m.type === "user_answer") {
            cls = "user";
            who = "你";
          } else if (m.type === "user_question") {
            cls = "manager question";
            who = "总管 · 需要你回答";
          } else if (m.type === "user_approval") {
            cls = "manager";
            who = "系统 · 你批准了方案";
          } else if (m.type === "user_rejection") {
            cls = "manager";
            who = "系统 · 你驳回了方案";
          }
          const body = m.body || m.summary || "";
          const key = String(m.seq);
          const long = body.length > 500;
          const collapsed = long && !expandedChat.has(key);
          return `<div class="msg ${cls}${collapsed ? " collapsed" : ""}" data-seq="${m.seq}"><span class="who">${escapeHtml(who)} · ${timeHm(m.ts)}</span><div class="msg-text">${escapeHtml(body)}</div>${
            long
              ? `<button class="linkish msg-toggle" type="button">${
                  collapsed ? "展开全文" : "收起"
                }</button>`
              : ""
          }</div>`;
        })
        .join("");
      if (nearBottom) {
        requestAnimationFrame(() => {
          list.scrollTop = list.scrollHeight;
        });
      }
    }
    const hint = $("#chatHint");
    if (state.pending_user_question) hint.textContent = "总管在等你回答";
    else if (state.plan?.pending) hint.textContent = "有方案待批准";
    else hint.textContent = "总管是 AI";
  }

  // ---------------------------------------------------------------- events

  async function signalActive(pid) {
    if (!pid) return;
    try {
      await api("/api/active-project", {
        method: "POST",
        body: JSON.stringify({ project: pid }),
      });
    } catch (e) {
      // The project may have been removed; the next list refresh handles it.
    }
  }

  function bindNewProject() {
    const dlg = $("#newProjectDialog");
    if (!dlg) return;
    $("#newProjectBtn").addEventListener("click", () => {
      $("#newProjectError").textContent = "";
      dlg.showModal();
      setTimeout(() => $("#newProjectTitle").focus(), 0);
    });
    $("#newProjectCancel").addEventListener("click", () => dlg.close());
    $("#newProjectForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const title = $("#newProjectTitle").value.trim();
      const request = $("#newProjectRequest").value.trim();
      const workspace = $("#newProjectWorkspace").value.trim();
      const err = $("#newProjectError");
      if (!title || !request || !workspace) {
        err.textContent = "项目名、项目文件夹、需求都要填。";
        return;
      }
      const btn = $("#newProjectSubmit");
      btn.disabled = true;
      err.textContent = "";
      try {
        const res = await api("/api/projects", {
          method: "POST",
          body: JSON.stringify({ title, request, workspace }),
        });
        const pid = res.project.id;
        localStorage.setItem("station.project", pid);
        await signalActive(pid);
        dlg.close();
        location.hash = "project=" + encodeURIComponent(pid);
        location.reload();
      } catch (ex) {
        err.textContent = ex.message;
        btn.disabled = false;
      }
    });
  }

  function bindBasicEvents() {
    bindNewProject();
    $("#themeToggle").addEventListener("click", () => {
      const cur = document.documentElement.dataset.theme;
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      const next = cur ? (cur === "dark" ? "light" : "dark") : prefersDark ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      localStorage.setItem("station.theme", next);
    });

    $("#timeline").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-expand]");
      if (btn) expandEvent(btn);
    });

    $("#chatList").addEventListener("click", (e) => {
      const btn = e.target.closest(".msg-toggle");
      if (!btn) return;
      const msg = btn.closest(".msg");
      const key = msg.dataset.seq;
      const collapsed = msg.classList.toggle("collapsed");
      if (collapsed) expandedChat.delete(key);
      else expandedChat.add(key);
      btn.textContent = collapsed ? "展开全文" : "收起";
    });

    $$(".chip-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        filter = btn.dataset.filter;
        $$(".chip-btn").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
        if (state) renderTimeline(false);
      });
    });

    $("#chatInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        $("#chatForm").requestSubmit();
      }
    });

    $("#chatForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!project) return;
      const input = $("#chatInput");
      const text = input.value.trim();
      if (!text) return;
      const btn = $("#chatSend");
      btn.disabled = true;
      try {
        await api("/api/message", {
          method: "POST",
          body: JSON.stringify({ project, text }),
        });
        input.value = "";
        await load();
        const list = $("#chatList");
        list.scrollTop = list.scrollHeight;
      } catch (err) {
        showNotice(`发送失败：${err.message}`);
      } finally {
        btn.disabled = false;
      }
    });
  }

  function restoreTheme() {
    const fromQuery = new URLSearchParams(location.search).get("theme");
    const saved = fromQuery || localStorage.getItem("station.theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.dataset.theme = saved;
      if (fromQuery) localStorage.setItem("station.theme", saved);
    }
  }

  boot();
})();
