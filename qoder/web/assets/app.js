/* 拾忆 · Recall Coach 前端：页面只与本项目服务端通信，服务端再调用真实 PowerContext 公开接口。 */
"use strict";

const CTX = { context: null, session: null, learner: null, ops: { entries: [], path: "" } };
const LS_KEY = "recall.learner";
const KIND_LABEL = { weakness: "薄弱点", progress: "掌握变化", preference: "复习偏好", plan: "复习安排" };

/* ---------------- 基础工具 ---------------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function el(tag, cls, value) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (value !== undefined && value !== null) {
    node.appendChild(value.nodeType ? value : document.createTextNode(String(value)));
  }
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let data = null;
  try { data = await response.json(); } catch (_) { data = null; }
  if (!response.ok) {
    const error = new Error((data && data.error && data.error.message) || `请求失败（HTTP ${response.status}）`);
    error.payload = data;
    error.status = response.status;
    throw error;
  }
  return data;
}

function state(node, kind, text) {
  if (!node) return;
  node.innerHTML = "";
  if (!text) return;
  node.appendChild(el("div", `msg ${kind || ""}`, text));
}

function pill(text, cls) {
  return el("span", `pill ${cls || ""}`.trim(), text);
}

function artifactLabel(artifact) {
  if (!artifact) return "—";
  return `${artifact.family}/${artifact.artifact_id}@${artifact.revision}`;
}

function describeError(error) {
  const payload = error.payload && error.payload.error;
  const notice = error.payload && error.payload.notice;
  if (!payload) return notice ? `${error.message}　·　${notice}` : error.message;
  const bits = [payload.message];
  if (payload.code) bits.push(`错误码 ${payload.code}`);
  if (payload.operation) bits.push(`操作 ${payload.operation}`);
  if (payload.status_code) bits.push(`HTTP ${payload.status_code}`);
  if (payload.request_id) bits.push(`request-id ${payload.request_id}`);
  const written = (error.payload.written_receipts || []).map((r) => artifactLabel(r.artifact)).join("　");
  if (written) bits.push(`已写入并保留：${written}`);
  if (notice) bits.push(notice);
  return bits.join("　·　");
}

function bar(value) {
  const track = el("div", "bar");
  const fill = el("i");
  fill.style.width = `${Math.round(value * 100)}%`;
  track.appendChild(fill);
  return track;
}

/* ---------------- 顶栏与共享数据 ---------------- */
async function loadContext(force = false) {
  if (CTX.context && !force) return CTX.context;
  try {
    CTX.context = await api("/api/context");
    paintBackend(CTX.context);
    return CTX.context;
  } catch (error) {
    CTX.context = null;
    const pillBox = $("#backend-pill");
    if (pillBox) {
      pillBox.classList.remove("ok");
      pillBox.classList.add("bad");
    }
    const text = $("#backend-text");
    if (text) {
      const payload = error.payload && error.payload.error;
      text.textContent = payload
        ? `PowerContext 不可用：${payload.code}（服务端不会用模拟数据代替真实记忆）`
        : "无法连接本项目后端：请先执行 scripts/start_app.sh";
    }
    throw error;
  }
}

function paintBackend(context) {
  const pillBox = $("#backend-pill");
  if (!pillBox) return;
  pillBox.classList.remove("bad");
  pillBox.classList.add("ok");
  const backend = context.powercontext;
  const modes = (backend.search_modes || []).join("/");
  const text = $("#backend-text");
  if (text) text.textContent = `PowerContext 已就绪 · ${backend.base_url} · 检索模式 ${modes}`;
}

async function loadOps(limit = 15) {
  CTX.ops = await api(`/api/operations?limit=${limit}`);
  return CTX.ops;
}

function renderOps(selector) {
  const body = $(selector);
  if (!body) return;
  body.innerHTML = "";
  const entries = CTX.ops.entries || [];
  if (!entries.length) {
    const row = el("tr");
    const cell = el("td", "muted", "暂无调用留痕：完成一次提交或写入后，这里会出现真实回执。留痕文件 " + (CTX.ops.path || ""));
    cell.colSpan = 3;
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  entries.forEach((entry) => {
    const row = el("tr");
    row.appendChild(el("td", null, (entry.at || "").replace("T", " ").slice(0, 19)));
    row.appendChild(el("td", null, entry.operation || ""));
    const detail = el("td", "id");
    const bits = [];
    if (entry.artifact) bits.push(artifactLabel(entry.artifact));
    if (entry.source_ref) bits.push(entry.source_ref);
    if (entry.kind) bits.push(`kind=${entry.kind}`);
    if (entry.state) bits.push(`state=${entry.state}`);
    if (entry.scope_id) bits.push(entry.scope_id);
    if (entry.hits !== undefined) bits.push(`命中 ${entry.hits} 条`);
    if (entry.status) bits.push(`status=${entry.status}`);
    if (entry.duration_ms !== undefined) bits.push(`${entry.duration_ms}ms`);
    detail.textContent = bits.join("　");
    row.appendChild(detail);
    body.appendChild(row);
  });
}

/* ---------------- 概览页 ---------------- */
async function renderHome() {
  const context = await loadContext();
  const backend = context.powercontext;

  const modules = $("#modules");
  modules.innerHTML = "";
  context.bank.modules.forEach((module) => {
    const row = el("div", "module");
    row.appendChild(el("span", null, module.name));
    row.appendChild(el("span", "muted small", module.summary));
    row.appendChild(el("span", "count", `${module.question_count} 题 · ${module.id}`));
    modules.appendChild(row);
  });

  const detail = $("#backend-detail");
  detail.innerHTML = "";
  [
    ["服务地址", backend.base_url],
    ["/health/ready", backend.health ? backend.health.status : "不可达"],
    ["可用检索模式", (backend.search_modes || []).join("、")],
    ["自动抽取（需要推理模型）", backend.memory_extraction ? "已启用" : "未配置：如实关闭"],
    ["证据来源类型", (backend.source_types || []).join("、")],
    ["上下文版本", (backend.context_versions || []).join("、")],
    ["内置题库", `${context.bank.question_total} 题 / ${context.bank.modules.length} 个知识点`],
    ["本机已建立身份", `${context.learners.length} 个`],
    ["应用版本", `${context.app.name} v${context.app.version}`],
  ].forEach(([key, value]) => {
    const box = el("div");
    box.appendChild(el("div", "k", key));
    box.appendChild(el("div", "v", value));
    detail.appendChild(box);
  });

  $("#stat-bank").textContent =
    `内置 ${context.bank.question_total} 道原创题、${context.bank.modules.length} 个知识点（data/questions.json），判分在服务端完成，全流程不调用任何模型服务，因此核心闭环无需模型 API 密钥。`;

  await loadOps(8);
  renderOps("#ops-body");
}

/* ---------------- 诊断与复习 ---------------- */
function renderKnownLearners() {
  const box = $("#known-learners");
  if (!box) return;
  box.innerHTML = "";
  const learners = (CTX.context && CTX.context.learners) || [];
  if (!learners.length) {
    box.appendChild(el("span", "muted small", "本机还没有学习身份：填写标识后点「进入」即可在 PowerContext 建立独立 Scope。"));
    return;
  }
  box.appendChild(el("span", "muted small", "本机已有身份："));
  learners.forEach((learner) => {
    const chip = el("button", "mini", `${learner.display_name}（${learner.learner_id}）`);
    chip.type = "button";
    chip.onclick = () => {
      $("#learner-id").value = learner.learner_id;
      $("#display-name").value = learner.display_name;
      enterLearner();
    };
    box.appendChild(chip);
  });
}

async function enterLearner() {
  const box = $("#learner-state");
  const learnerId = $("#learner-id").value.trim().toLowerCase();
  const displayName = $("#display-name").value.trim();
  if (!learnerId || !displayName) {
    state(box, "warn", "请填写学习身份标识与显示名称。");
    return;
  }
  state(box, "", "正在 PowerContext 中建立或复用该身份的 Scope…");
  $("#enter").disabled = true;
  try {
    const data = await api("/api/learners", { method: "POST", body: { learner_id: learnerId, display_name: displayName } });
    CTX.learner = data.learner;
    localStorage.setItem(LS_KEY, data.learner.learner_id);
    state(box, "ok", `身份就绪：Scope ${data.scope.scope_id}（标题 ${data.scope.title}，version ${data.scope.version}）· 服务 ${data.backend}`);
    $("#identity-card").classList.add("hidden");
    $("#plan-card").classList.remove("hidden");
    await loadPlan();
    renderKnownLearners();
  } catch (error) {
    state(box, "err", describeError(error));
  } finally {
    $("#enter").disabled = false;
  }
}

async function loadPlan() {
  if (!CTX.learner) return;
  const box = $("#learner-state");
  state(box, "", "正在读取该身份在 PowerContext 中的记忆并生成安排…");
  $("#submit").disabled = true;
  try {
    const session = await api(`/api/learners/${CTX.learner.learner_id}/sessions`, { method: "POST" });
    CTX.session = session;
    $("#result-card").classList.add("hidden");
    $("#plan-card").classList.remove("hidden");
    $("#plan-title").textContent = `本次安排（会话 ${session.session_id}）`;
    $("#plan-mode").textContent = session.mode === "diagnosis" ? "基础诊断 · 无历史记忆" : "记忆驱动复习";
    $("#plan-count").textContent = `${session.question_count} 题`;
    $("#plan-summary").textContent = session.summary;
    state(
      box,
      "ok",
      `本次安排已写入记忆：${artifactLabel(session.plan_receipt.artifact)}（kind=plan，changed=${session.plan_receipt.changed}，${session.plan_receipt.duration_ms}ms）`
    );
    renderBasis(session);
    renderQuiz(session);
    await loadOps(6);
    renderOps("#ops-body");
  } catch (error) {
    state(box, "err", describeError(error));
  } finally {
    $("#submit").disabled = false;
    $("#submit").textContent = "提交答卷并写入记忆";
  }
}

function renderBasis(session) {
  const box = $("#basis");
  box.innerHTML = "";
  const chosen = new Set(session.items.map((item) => item.topic));
  const table = el("table");
  const head = el("tr");
  ["候选知识点", "掌握度", "累计作答/正确", "连错", "优先级分", "本次选题依据", "引用的记忆标识"].forEach((text) =>
    head.appendChild(el("th", null, text))
  );
  table.appendChild(head);

  session.ranking
    .filter((row) => chosen.has(row.topic))
    .forEach((row) => {
      const item = session.items.find((entry) => entry.topic === row.topic);
      const tr = el("tr");
      tr.appendChild(el("td", null, row.name + (row.weak ? "（薄弱）" : row.tested ? "" : "（未测）")));

      const masteryCell = el("td");
      masteryCell.appendChild(el("div", "small muted", (row.mastery * 100).toFixed(0) + "%"));
      masteryCell.appendChild(bar(row.mastery));
      tr.appendChild(masteryCell);

      tr.appendChild(el("td", null, row.tested ? `${row.attempts} 题` : "尚无记录"));
      tr.appendChild(el("td", null, String(row.wrong_streak)));
      tr.appendChild(el("td", null, row.score.toFixed(2)));
      tr.appendChild(el("td", "small", item ? item.reasons.join("；") : "本轮未安排题目"));
      const refs = el("td", "id small");
      refs.textContent = row.evidence.length
        ? row.evidence.map((e) => `${e.artifact_id}@${e.revision}（${e.kind}）`).join("、")
        : "无（该知识点尚无记忆）";
      tr.appendChild(refs);
      table.appendChild(tr);
    });
  box.appendChild(table);
}

function renderQuiz(session) {
  const quiz = $("#quiz");
  quiz.innerHTML = "";
  quiz.className = "quiz";
  session.items.forEach((item) => {
    const card = el("div", "question");
    const head = el("div", "q-head");
    head.appendChild(el("span", "q-index", `第 ${item.order} 题 · ${item.question.id}`));
    head.appendChild(pill(item.topic_name, "progress"));
    head.appendChild(el("span", "muted small", `难度 ${"★".repeat(item.question.difficulty)}`));
    head.appendChild(el("span", "muted small", item.memory_refs.length ? `依据 ${item.memory_refs.length} 条记忆` : "无历史记忆 · 用于建立基线"));
    card.appendChild(head);
    card.appendChild(el("p", "q-stem", item.question.stem));

    const options = el("div", "options");
    item.question.options.forEach((option) => {
      const label = el("label");
      const input = el("input");
      input.type = "radio";
      input.name = `q-${item.question.id}`;
      input.value = option.key;
      input.dataset.question = item.question.id;
      label.appendChild(input);
      label.appendChild(el("span", null, `${option.key}. ${option.text}`));
      options.appendChild(label);
    });
    card.appendChild(options);

    const reasons = el("ul", "reasons");
    item.reasons.forEach((text) => reasons.appendChild(el("li", null, text)));
    card.appendChild(reasons);

    if (item.memory_refs.length) {
      card.appendChild(el("div", "refs", "依据记忆：" + item.memory_refs.map(artifactLabel).join("　")));
    }
    quiz.appendChild(card);
  });
}

async function submitAnswers() {
  const learnerId = CTX.learner.learner_id;
  const sessionId = CTX.session.session_id;
  const box = $("#submit-state");
  const chosen = {};
  $$("#quiz input[type=radio]:checked").forEach((input) => { chosen[input.dataset.question] = input.value; });
  const total = CTX.session.items.length;
  if (Object.keys(chosen).length < total) {
    state(box, "warn", `还有 ${total - Object.keys(chosen).length} 题未作答；全部作答后才会判分并写入记忆。`);
    return;
  }
  state(box, "", "服务端判分中，并正在把掌握变化写入 PowerContext…");
  $("#submit").disabled = true;
  let committed = false;
  try {
    const answers = Object.entries(chosen).map(([question_id, c]) => ({ question_id, chosen: c }));
    const result = await api(`/api/learners/${learnerId}/sessions/${sessionId}/submit`, { method: "POST", body: { answers } });
    renderResult(result);
    if (result.replay) {
      state(box, "warn", `本次提交此前已完成（${result.replay.recorded_at}），服务端直接回放当时留存的判分结果与 ${result.memory_receipts.length} 条记忆回执，没有重复累计。`);
    } else {
      const writes = result.memory_writes || {};
      const reused = writes.reused_from_ledger || 0;
      state(
        box,
        "ok",
        `判分完成：${result.score.correct}/${result.score.total} 正确，本次新写入 ${result.memory_receipts.length - reused} 条真实记忆`
          + (reused ? `，另有 ${reused} 条按账本回放（补齐上次未写入的部分）。` : "。"),
      );
    }
    await loadOps(6);
    renderOps("#ops-body");
    committed = true;
  } catch (error) {
    state(box, "err", describeError(error));
  } finally {
    // 已判分的会话不再允许重复点击提交（服务端另有持久化的回放保护）；
    // 未完成的提交保持可点击，重试只补齐账本中缺失的条目
    $("#submit").disabled = committed;
    if (committed) $("#submit").textContent = "本轮已判分并写入记忆";
  }
}

function renderResult(result) {
  $("#result-card").classList.remove("hidden");
  const summary = $("#result-summary");
  summary.innerHTML = "";
  summary.appendChild(pill(`本次得分 ${result.score.correct} / ${result.score.total}`, result.score.correct === result.score.total ? "active" : "weakness"));
  summary.appendChild(pill(result.mode === "diagnosis" ? "基础诊断" : "记忆驱动复习", "plan"));
  summary.appendChild(el("span", "muted small", result.next_plan_basis));

  const list = $("#result-list");
  list.innerHTML = "";
  list.className = "quiz";
  result.results.forEach((row) => {
    const card = el("div", `question ${row.correct ? "right" : "wrong"}`);
    const head = el("div", "q-head");
    head.appendChild(pill(row.topic_name, "progress"));
    head.appendChild(pill(row.correct ? "回答正确" : `回答错误（正确 ${row.answer}）`, row.correct ? "active" : "weakness"));
    head.appendChild(el("span", "q-index", `你的选择 ${row.chosen || "未作答"} · ${row.question_id}`));
    card.appendChild(head);
    card.appendChild(el("p", "q-stem", row.stem));
    card.appendChild(el("div", "explain", "解析：" + row.explain));
    const reasons = el("ul", "reasons");
    row.plan_reasons.forEach((text) => reasons.appendChild(el("li", null, text)));
    card.appendChild(reasons);
    list.appendChild(card);
  });

  const body = $("#receipt-body");
  body.innerHTML = "";
  result.memory_receipts.forEach((receipt) => {
    const tr = el("tr");
    tr.appendChild(el("td", null, `${KIND_LABEL[receipt.kind] || receipt.kind}（${receipt.kind}）`));
    tr.appendChild(el("td", "id", artifactLabel(receipt.artifact)));
    tr.appendChild(el("td", null, `${receipt.state} / state_version ${receipt.state_version}${receipt.changed ? "" : "（内容未变化）"}`));
    tr.appendChild(el("td", null, `${receipt.text_bytes} B`));
    tr.appendChild(el("td", "small muted", `${receipt.verified_by} 回读校验 · ${receipt.duration_ms}ms`));
    body.appendChild(tr);
  });

  const ranking = $("#ranking-after");
  ranking.innerHTML = "";
  const table = el("table");
  const head = el("tr");
  ["知识点", "掌握度", "连错", "下一轮优先级分"].forEach((text) => head.appendChild(el("th", null, text)));
  table.appendChild(head);
  result.ranking_after.forEach((row) => {
    const tr = el("tr");
    tr.appendChild(el("td", null, row.name + (row.weak ? "（薄弱）" : "")));
    tr.appendChild(el("td", null, (row.mastery * 100).toFixed(0) + "%"));
    tr.appendChild(el("td", null, String(row.wrong_streak)));
    tr.appendChild(el("td", null, row.score.toFixed(2)));
    table.appendChild(tr);
  });
  ranking.appendChild(table);
  ranking.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function resumeLearner() {
  const learnerId = localStorage.getItem(LS_KEY);
  if (!learnerId) return false;
  const known = ((CTX.context && CTX.context.learners) || []).find((row) => row.learner_id === learnerId);
  if (!known) return false;
  $("#learner-id").value = known.learner_id;
  $("#display-name").value = known.display_name;
  await enterLearner();
  return true;
}

/* ---------------- 学习记忆页 ---------------- */
async function memoryPage() {
  const context = await loadContext();

  const picker = $("#learner-picker");
  picker.innerHTML = "";
  picker.appendChild(el("span", "muted small", "选择身份："));
  const learners = context.learners || [];
  learners.forEach((learner) => {
    const chip = el("button", "mini", `${learner.display_name}（${learner.learner_id}）`);
    chip.type = "button";
    chip.onclick = () => { CTX.learner = learner; localStorage.setItem(LS_KEY, learner.learner_id); refreshMemory(); };
    picker.appendChild(chip);
  });
  if (!learners.length) {
    picker.appendChild(el("span", "muted small", "本机还没有学习身份，请先到「诊断与复习」页面建立。"));
    return;
  }
  const saved = localStorage.getItem(LS_KEY);
  CTX.learner = learners.find((row) => row.learner_id === saved) || learners[0];

  const focus = $("#pref-focus");
  focus.innerHTML = "";
  context.bank.modules.forEach((module) => {
    const option = el("option", null, `${module.name}（${module.id}）`);
    option.value = module.id;
    focus.appendChild(option);
  });

  $("#export-json").href = `/api/learners/${CTX.learner.learner_id}/export?fmt=json`;
  $("#export-md").href = `/api/learners/${CTX.learner.learner_id}/export?fmt=md`;
  $("#export-json").setAttribute("download", `recall-${CTX.learner.learner_id}.json`);
  $("#export-md").setAttribute("download", `recall-${CTX.learner.learner_id}.md`);
  await refreshMemory();
}

async function refreshMemory() {
  if (!CTX.learner) return;
  const learnerId = CTX.learner.learner_id;
  const box = $("#memory-state");
  state(box, "", "正在从 PowerContext 读回该身份的记忆…");
  try {
    const kind = $("#filter-kind").value;
    const stateChoice = $("#filter-state").value;
    const params = new URLSearchParams();
    if (kind) params.set("kind", kind);
    (stateChoice === "all" ? ["active", "forgotten", "merged", "retired"] : [stateChoice]).forEach((s) => params.append("states", s));
    const [listed, overview] = await Promise.all([
      api(`/api/learners/${learnerId}/memories?${params.toString()}`),
      api(`/api/learners/${learnerId}`),
    ]);
    CTX.learner = overview.learner;

    state(
      box,
      "ok",
      `Scope ${listed.scope_id}：本筛选读回 ${listed.entries.length} 条（kind=${kind || "全部"}，state=${stateChoice === "all" ? "全部" : stateChoice}）；`
      + `该身份激活记忆共 ${overview.memory_entry_count} 条，服务 ${CTX.context.powercontext.base_url}`
    );
    $("#scope-badge").textContent = `Scope ${listed.scope_id}`;

    const stats = $("#memory-stats");
    stats.innerHTML = "";
    const weak = overview.topics.filter((row) => row.weak);
    const totalModules = CTX.context.bank.modules.length;
    [
      ["掌握记忆覆盖", `${overview.topics.length} / ${totalModules}`, "已在 PowerContext 中留下掌握记忆的知识点"],
      ["薄弱知识点", `${weak.length}`, weak.length ? weak.map((row) => row.name).join("、") : "当前没有低于阈值或连错的知识点"],
      ["最近一次安排", overview.last_plan ? `${overview.last_plan.mode === "diagnosis" ? "基础诊断" : "复习"} · ${overview.last_plan.question_ids.length} 题` : "尚无",
        overview.last_plan ? artifactLabel(overview.last_plan.artifact) : "写入第一条 plan 记忆后显示"],
    ].forEach(([title, value, note]) => {
      const card = el("div", "card");
      card.appendChild(el("div", "k muted small", title));
      card.appendChild(el("div", "v", value));
      card.appendChild(el("div", "small muted", note));
      stats.appendChild(card);
    });

    $("#pref-count").value = overview.preference.daily_questions || 5;
    $("#pref-note").value = overview.preference.note || "";
    const focusTopics = overview.preference.focus_topics || [];
    Array.from($("#pref-focus").options).forEach((option) => { option.selected = focusTopics.includes(option.value); });

    renderMemoryTable(listed.entries);
  } catch (error) {
    state(box, "err", describeError(error));
  }
}

function renderMemoryTable(entries) {
  const body = $("#memory-body");
  body.innerHTML = "";
  if (!entries.length) {
    const row = el("tr");
    const cell = el("td", "muted", "该筛选条件下没有记忆条目：这是 PowerContext 的真实空结果，页面不填充示例数据。");
    cell.colSpan = 5;
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  entries.forEach((entry) => {
    const payload = entry.payload || {};
    const tr = el("tr");
    tr.appendChild(el("td", null, pill(entry.kind, entry.kind)));

    const content = el("td");
    content.appendChild(el("div", null, payload.summary || entry.text.slice(0, 200)));
    if (payload.topic) {
      content.appendChild(el("div", "small muted", `知识点 ${payload.topic_name || payload.topic} · 掌握度 ${(payload.mastery ?? 0).toFixed(2)} · 累计作答 ${payload.attempts ?? 0} · 连错 ${payload.wrong_streak ?? 0}`));
    }
    if (payload.daily_questions) {
      content.appendChild(el("div", "small muted", `每次 ${(payload.daily_questions || 5)} 题 · 侧重 ${(payload.focus_topics || []).join("、") || "未指定"}`));
    }
    if (entry.parse_error) content.appendChild(el("div", "small muted", `该条目正文不是本项目 JSON：${entry.parse_error}`));
    content.appendChild(el("div", "id small", entry.text.length > 260 ? entry.text.slice(0, 260) + "…" : entry.text));
    tr.appendChild(content);

    tr.appendChild(el("td", "id", artifactLabel(entry.artifact)));
    tr.appendChild(el("td", null, pill(entry.state, entry.state)));

    const actions = el("td");
    if (entry.state === "active") {
      const button = el("button", "mini", "遗忘此条");
      button.type = "button";
      button.onclick = () => forgetOne(entry);
      actions.appendChild(button);
    } else {
      actions.appendChild(el("span", "muted small", "已退出召回"));
    }
    tr.appendChild(actions);
    body.appendChild(tr);
  });
}

async function forgetOne(entry) {
  const box = $("#danger-state");
  const params = new URLSearchParams({ revision: String(entry.artifact.revision), state_version: String(entry.state_version) });
  state(box, "", "正在通过真实 lifecycle 接口遗忘该条记忆…");
  try {
    const receipt = await api(
      `/api/learners/${CTX.learner.learner_id}/memories/${entry.artifact.artifact_id}/forget?${params}`,
      { method: "POST" }
    );
    state(box, "ok", `已遗忘 ${entry.artifact.artifact_id}：state=${receipt.state}，state_version=${receipt.state_version}，changed=${receipt.changed}`);
    await refreshMemory();
  } catch (error) {
    state(box, "err", describeError(error));
  }
}

async function savePreference() {
  const box = $("#pref-state");
  const focus = Array.from($("#pref-focus").selectedOptions).map((option) => option.value);
  state(box, "", "正在把复习偏好写入 PowerContext…");
  try {
    const data = await api(`/api/learners/${CTX.learner.learner_id}/preference`, {
      method: "PUT",
      body: {
        daily_questions: Number($("#pref-count").value) || 5,
        focus_topics: focus,
        note: $("#pref-note").value.trim(),
      },
    });
    state(box, "ok", `${data.text}　→　${artifactLabel(data.receipt.artifact)}`);
    await refreshMemory();
  } catch (error) {
    state(box, "err", describeError(error));
  }
}

async function forgetAll() {
  const box = $("#danger-state");
  if (!confirm(`确认把 ${CTX.learner.display_name}（${CTX.learner.learner_id}）在 PowerContext 中的全部激活记忆置为 forgotten？历史条目仍可核验，不会被删除。`)) return;
  state(box, "", "正在逐条调用 lifecycle 接口遗忘…");
  try {
    const data = await api(`/api/learners/${CTX.learner.learner_id}/memories/forget-all`, { method: "POST" });
    state(box, "ok", `已遗忘 ${data.forgotten} 条激活记忆（每条都带真实 state_version 递增回执）。`);
    await refreshMemory();
  } catch (error) {
    state(box, "err", describeError(error));
  }
}

async function runSearch() {
  const box = $("#search-result");
  box.textContent = "检索中…";
  try {
    const data = await api(`/api/learners/${CTX.learner.learner_id}/memories/search`, {
      method: "POST",
      body: { query: $("#search-query").value.trim() || "薄弱点", limit: 8 },
    });
    const lines = [`mode=${data.mode}（PowerContext 真实检索模式）　命中 ${data.hits.length} 条　${data.duration_ms}ms`, ""];
    if (!data.hits.length) lines.push("（该 Scope 内没有匹配的激活记忆：返回真实空结果）");
    data.hits.forEach((hit) => {
      const payload = hit.payload || {};
      lines.push(`· ${artifactLabel(hit.artifact)}  kind=${hit.kind}  state=${hit.state}  score=${hit.score.toFixed(5)}  matched_by=${hit.matched_by.join(",")}`);
      lines.push(`  ${payload.summary || hit.text.slice(0, 140)}`);
    });
    box.textContent = lines.join("\n");
  } catch (error) {
    box.textContent = describeError(error);
  }
}

async function runPrepare() {
  const box = $("#prepare-result");
  box.textContent = "生成中…";
  try {
    const data = await api(`/api/learners/${CTX.learner.learner_id}/memories/prepare`, {
      method: "POST",
      body: { query: $("#prepare-query").value.trim() || "薄弱知识点" },
    });
    box.textContent = `schema=${data.schema}　status=${data.status}　content_bytes=${data.content_bytes}　${data.duration_ms}ms\n\n${data.content || "（无内容）"}`;
  } catch (error) {
    box.textContent = describeError(error);
  }
}

/* ---------------- 验证说明页 ---------------- */
async function verifyPage() {
  const context = await loadContext();
  const backend = context.powercontext;
  const saved = localStorage.getItem(LS_KEY);
  const current = (context.learners || []).find((row) => row.learner_id === saved) || null;

  let counts = { memory_entry_count: 0, topics: 0, weak: 0 };
  if (current) {
    const overview = await api(`/api/learners/${current.learner_id}`);
    counts = { memory_entry_count: overview.memory_entry_count, topics: overview.topics.length, weak: overview.topics.filter((row) => row.weak).length };
  }

  const health = backend.health || {};
  const checks = health.checks || {};
  const ops = await loadOps(400).catch(() => ({ entries: [], path: "" }));
  const writes = (ops.entries || []).filter((entry) => entry.operation === "remember_memory").length;

  const box = $("#live-status");
  box.innerHTML = "";
  [
    ["PowerContext 地址", backend.base_url],
    ["服务状态", backend.ready ? "ready" : (health.status || "不可达")],
    ["runtime / database", `${checks.runtime || "—"} / ${checks.database || "—"}`],
    ["检索模式", (backend.search_modes || []).join("、")],
    ["模型抽取能力", backend.memory_extraction ? "已配置" : "未配置（核心闭环不依赖模型）"],
    ["Artifact 家族", (backend.artifact_families || []).join("、")],
    ["题库题量 / 知识点", `${context.bank.question_total} / ${context.bank.modules.length}`],
    ["本机学习身份", `${context.learners.length} 个`],
    ["当前身份激活记忆", current ? `${counts.memory_entry_count} 条（${counts.topics} 个知识点，${counts.weak} 个薄弱）` : "未选择"],
    ["留痕文件中的写入次数", `${writes} 次（窗口 ${ops.entries.length} 行）`],
  ].forEach(([key, value]) => {
    const item = el("div");
    item.appendChild(el("div", "k", key));
    item.appendChild(el("div", "v", value));
    box.appendChild(item);
  });

  renderOps("#ops-body");
}

/* ---------------- 启动 ---------------- */
async function boot() {
  const page = document.body.dataset.page;

  if (page === "diagnose") {
    $("#enter").addEventListener("click", enterLearner);
    $("#submit").addEventListener("click", submitAnswers);
    $("#reload-plan").addEventListener("click", loadPlan);
    $("#next-round").addEventListener("click", loadPlan);
    const learner = localStorage.getItem(LS_KEY);
    const opsRow = document.createElement("section");
    opsRow.className = "card";
    opsRow.innerHTML = "";
    opsRow.appendChild(el("h3", null, "本次会话的真实调用留痕"));
    opsRow.appendChild(el("p", "muted small", "下面每行都来自 var/log/powercontext-operations.jsonl：服务端每次调用 PowerContext 公开接口都会落一条回执。"));
    const wrap = el("div", "table-wrap");
    const table = el("table", "ops");
    const head = el("tr");
    ["时间", "操作", "记忆标识 / 结果"].forEach((text) => head.appendChild(el("th", null, text)));
    table.appendChild(head);
    const body = el("tbody");
    body.id = "ops-body";
    table.appendChild(body);
    wrap.appendChild(table);
    opsRow.appendChild(wrap);
    $("main.page").appendChild(opsRow);
    if (learner) $("#learner-id").value = learner;
  }

  if (page === "memory") {
    $("#save-preference").addEventListener("click", savePreference);
    $("#run-search").addEventListener("click", runSearch);
    $("#run-prepare").addEventListener("click", runPrepare);
    $("#forget-all").addEventListener("click", forgetAll);
    $("#filter-kind").addEventListener("change", refreshMemory);
    $("#filter-state").addEventListener("change", refreshMemory);
  }

  try {
    if (page === "home") await renderHome();
    if (page === "verify") await verifyPage();
    if (page === "memory") await memoryPage();
    if (page === "diagnose") {
      await loadContext();
      renderKnownLearners();
      await loadOps(6);
      renderOps("#ops-body");
      await resumeLearner();
    }
  } catch (error) {
    const box = $("#learner-state") || $("#memory-state");
    if (box) state(box, "err", describeError(error));
  }
}

document.addEventListener("DOMContentLoaded", boot);
