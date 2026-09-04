const REVIEWER_KEY = "qa-lab-reviewer";
const VERDICT_LABEL = {
  correct: "맞음",
  partial: "부분 맞음",
  abstain_ok: "거절이 맞음",
  incorrect: "틀림",
  unsafe: "근거 없이 답함",
  error: "서버 오류",
};
const GOLD_STATUS_LABEL = {
  candidate: "검수 중",
  approved: "승인",
  rejected: "반려",
};

const statusNode = document.getElementById("lab-status");
const reviewerNode = document.getElementById("qa-reviewer");
const summaryBoard = document.getElementById("summary-board");
const reviewMeta = document.getElementById("review-meta");
let goldCandidates = [];
let activeGoldCandidate = null;

reviewerNode.value = sessionStorage.getItem(REVIEWER_KEY) || "";

function headers() {
  return { "Content-Type": "application/json" };
}

function setStatus(text) {
  statusNode.textContent = text;
}

function fill(node, value) {
  node.textContent = value == null || value === "" ? "—" : String(value);
}

function table(headers, rows) {
  const tableNode = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const title of headers) {
    const cell = document.createElement("th");
    cell.textContent = title;
    headRow.append(cell);
  }
  head.append(headRow);
  const body = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of row) {
      const td = document.createElement("td");
      if (value instanceof Node) {
        td.append(value);
      } else if (value && typeof value === "object" && value.className) {
        td.className = value.className;
        td.textContent = value.text;
      } else {
        td.textContent = value == null ? "" : String(value);
      }
      tr.append(td);
    }
    body.append(tr);
  }
  tableNode.append(head, body);
  return tableNode;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...headers(), ...(options.headers || {}) },
  });
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { detail: text };
  }
  if (!response.ok) {
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  return payload;
}

async function refresh() {
  const summary = await api("/lab/api/summary");
  summaryBoard.hidden = false;
  fill(document.getElementById("stat-reviews"), summary.reviews.total);
  fill(document.getElementById("stat-correct"), summary.reviews.by_verdict.correct || 0);
  fill(
    document.getElementById("stat-bad"),
    (summary.reviews.by_verdict.incorrect || 0)
      + (summary.reviews.by_verdict.unsafe || 0)
      + (summary.reviews.by_verdict.error || 0),
  );
  fill(document.getElementById("stat-perf"), summary.perf_runs.total);
  fill(document.getElementById("stat-miles"), summary.milestones.total);
  fill(document.getElementById("stat-gold"), summary.gold.total);
  fill(document.getElementById("stat-gold-approved"), summary.gold.by_status.approved || 0);
  renderReviews(summary.recent_reviews);
  renderPerf(summary.recent_perf_runs);
  renderMiles(summary.recent_milestones);
  goldCandidates = summary.recent_gold_candidates || [];
  renderGoldCandidatePicker();
  setStatus(`원장 연결됨 · 검수 ${summary.reviews.total}건`);
}

function renderReviews(items) {
  const root = document.getElementById("review-ledger");
  root.replaceChildren();
  if (!items.length) {
    root.textContent = "아직 검수 기록이 없습니다.";
    return;
  }
  root.append(table(
    ["시각", "검수자", "판정", "질문", "답변", "지연 ms", "Gold"],
    items.map((item) => {
      const action = document.createElement("button");
      action.type = "button";
      action.className = "table-action";
      action.textContent = "Gold 후보";
      action.addEventListener("click", () => createGoldCandidate(item.review_id, action));
      return [
        item.created_at,
        item.reviewer,
        { className: `verdict-${item.verdict}`, text: VERDICT_LABEL[item.verdict] || item.verdict },
        item.question,
        item.answer,
        item.latency_ms == null ? "" : item.latency_ms,
        action,
      ];
    }),
  ));
}

function renderPerf(items) {
  const root = document.getElementById("perf-ledger");
  root.replaceChildren();
  if (!items.length) {
    root.textContent = "아직 성능 기록이 없습니다.";
    return;
  }
  root.append(table(
    ["시각", "스위트", "통과", "실패", "p95 ms", "메모"],
    items.map((item) => [item.created_at, item.suite, item.passed, item.failed, item.p95_ms, item.notes]),
  ));
}

function renderMiles(items) {
  const root = document.getElementById("mile-ledger");
  root.replaceChildren();
  if (!items.length) {
    root.textContent = "아직 발전 과정이 없습니다.";
    return;
  }
  const list = document.createElement("ol");
  for (const item of items) {
    const li = document.createElement("li");
    const title = document.createElement("strong");
    title.textContent = `${item.created_at} · ${item.title}`;
    const body = document.createElement("p");
    body.textContent = item.body;
    li.append(title, body);
    list.append(li);
  }
  root.append(list);
}

function reviewerName() {
  const name = reviewerNode.value.trim();
  if (!name) {
    throw new Error("상단에 검수자 이름을 먼저 입력하세요.");
  }
  sessionStorage.setItem(REVIEWER_KEY, name);
  return name;
}

async function createGoldCandidate(reviewId, button) {
  button.disabled = true;
  try {
    const payload = await api("/lab/api/gold-candidates", {
      method: "POST",
      body: JSON.stringify({ review_id: reviewId, annotator: reviewerName() }),
    });
    await refreshGoldCandidates(payload.candidate.candidate_id);
    activatePanel("gold-panel");
    setStatus("QA 기록에서 Gold 후보를 만들었습니다. 근거를 먼저 확인하세요.");
  } catch (error) {
    setStatus(`Gold 후보 생성 실패: ${friendlyGoldError(error.message)}`);
  } finally {
    button.disabled = false;
  }
}

async function refreshGoldCandidates(selectedId = null) {
  const payload = await api("/lab/api/gold-candidates");
  goldCandidates = payload.candidates || [];
  renderGoldCandidatePicker(selectedId || (activeGoldCandidate && activeGoldCandidate.candidate_id));
}

function renderGoldCandidatePicker(selectedId = null) {
  const select = document.getElementById("gold-candidate-select");
  const current = selectedId || select.value;
  select.replaceChildren(new Option("후보를 선택하세요", ""));
  for (const item of goldCandidates) {
    const label = `${GOLD_STATUS_LABEL[item.status] || item.status} · ${item.source_review.question}`;
    select.append(new Option(label, item.candidate_id));
  }
  const next = goldCandidates.some((item) => item.candidate_id === current)
    ? current
    : (goldCandidates[0] && goldCandidates[0].candidate_id) || "";
  select.value = next;
  document.getElementById("gold-empty").hidden = goldCandidates.length > 0;
  if (next) {
    showGoldCandidate(next);
  } else {
    activeGoldCandidate = null;
    document.getElementById("gold-workspace").hidden = true;
  }
}

function showGoldCandidate(candidateId) {
  const candidate = goldCandidates.find((item) => item.candidate_id === candidateId);
  if (!candidate) return;
  activeGoldCandidate = candidate;
  const annotation = candidate.annotation || {};
  const source = candidate.source_review || {};
  document.getElementById("gold-workspace").hidden = false;
  fill(document.getElementById("gold-question"), source.question);
  fill(document.getElementById("gold-model-answer"), source.answer);
  fill(
    document.getElementById("gold-candidate-meta"),
    `${candidate.candidate_id} · r${candidate.revision} · 작성 ${candidate.annotator}`,
  );
  const status = document.getElementById("gold-status");
  status.textContent = GOLD_STATUS_LABEL[candidate.status] || candidate.status;
  status.className = `status-chip gold-status-${candidate.status}`;

  document.getElementById("gold-question-id").value = annotation.question_id || "";
  document.getElementById("gold-question-type").value = annotation.question_type || "single_filing_fact";
  document.getElementById("gold-answerability").value = annotation.answerability || "answerable";
  const answer = annotation.answer || { kind: "text", text: "" };
  document.getElementById("gold-answer-kind").value = answer.kind || "text";
  document.getElementById("gold-answer-text").value = answer.text || "";
  document.getElementById("gold-answer-value").value = answer.value || "";
  document.getElementById("gold-answer-unit").value = answer.unit || "";
  document.getElementById("gold-answer-scale").value = answer.scale || 1;
  document.getElementById("gold-answer-values").value = answer.values
    ? JSON.stringify(answer.values, null, 2)
    : "";
  document.getElementById("gold-answer-reason").value = answer.reason || "";
  updateAnswerFields();

  const period = annotation.period || {};
  document.getElementById("gold-period-type").value = period.period_type || "not_applicable";
  document.getElementById("gold-period-start").value = period.start_date || "";
  document.getElementById("gold-period-end").value = period.end_date || "";
  document.getElementById("gold-period-instant").value = period.instant_date || "";
  document.getElementById("gold-scope").value = annotation.scope || "not_applicable";
  document.getElementById("gold-version-basis").value = annotation.version_basis || "not_applicable";
  document.getElementById("gold-as-of").value = annotation.as_of || "";
  document.getElementById("gold-attack-label").value = annotation.attack_label || "";
  document.getElementById("gold-notes").value = annotation.notes || "";
  document.getElementById("gold-table-validated").checked = Boolean(annotation.table_structure_validated);
  document.getElementById("gold-filing-ids").value = (annotation.candidate_filing_ids || []).join("\n");
  renderEvidencePacket(candidate);
  renderGoldChecks(candidate.automatic_checks || {});
  setGoldFormDisabled(candidate.status === "approved");
  fill(
    document.getElementById("gold-action-status"),
    candidate.status === "approved"
      ? `${candidate.reviewer} 검수자가 ${candidate.reviewed_at}에 승인했습니다.`
      : candidate.status === "rejected"
        ? `${candidate.reviewer} 검수자가 반려했습니다. 수정하면 다시 후보 상태가 됩니다.`
        : "초안을 저장하면 원문·계보·스키마 자동검사를 다시 실행합니다.",
  );
}

function renderEvidencePacket(candidate) {
  const root = document.getElementById("gold-evidence-list");
  root.replaceChildren();
  const packet = candidate.evidence_packet || {};
  const selected = new Set(candidate.annotation.evidence_ids || []);
  const roles = candidate.annotation.evidence_roles || {};
  const evidence = packet.evidence || [];
  if (!evidence.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "보존된 evidence_id가 없거나 코퍼스에서 찾지 못했습니다. 공시·근거 ID를 보완해야 승인할 수 있습니다.";
    root.append(empty);
    return;
  }
  for (const item of evidence) {
    const article = document.createElement("article");
    article.className = "evidence-item";
    const top = document.createElement("div");
    top.className = "evidence-topline";
    const choose = document.createElement("label");
    choose.className = "check-row";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.evidenceId = item.evidence_id;
    checkbox.checked = selected.has(item.evidence_id);
    choose.append(checkbox, document.createTextNode(item.evidence_id));
    const role = document.createElement("select");
    role.dataset.evidenceRole = item.evidence_id;
    role.setAttribute("aria-label", `${item.evidence_id} 근거 역할`);
    for (const [value, label] of [
      ["support", "직접 근거"],
      ["operand", "계산 피연산자"],
      ["version_before", "정정 전"],
      ["version_after", "정정 후"],
      ["distractor", "함정 근거"],
    ]) {
      role.append(new Option(label, value));
    }
    role.value = roles[item.evidence_id] || "support";
    top.append(choose, role);

    const excerpt = document.createElement("blockquote");
    excerpt.textContent = item.text || "원문 텍스트 없음";
    const meta = document.createElement("p");
    meta.className = "evidence-meta";
    meta.textContent = `${item.report_name || "공시"} · ${item.filed_at || "일자 미상"} · ${item.filing_id} · ${item.lineage_status} · ${item.is_current ? "현재본" : "과거본"}`;
    const locator = document.createElement("code");
    locator.className = "locator";
    locator.textContent = JSON.stringify(item.locator || {});
    article.append(top, excerpt, meta, locator);
    if (item.source_id) {
      const original = document.createElement("a");
      original.className = "source-link";
      original.href = `/lab/source?source_id=${encodeURIComponent(item.source_id)}`;
      original.target = "_blank";
      original.rel = "noopener";
      original.textContent = "원본 파일 열기";
      article.append(original);
    }
    root.append(article);
  }
}

function renderGoldChecks(checks) {
  const root = document.getElementById("gold-checks");
  root.replaceChildren();
  if (checks.passed) {
    const item = document.createElement("li");
    item.className = "check-pass";
    item.textContent = "현재 초안의 자동검사를 통과했습니다.";
    root.append(item);
    return;
  }
  const issues = checks.issues || [];
  if (!issues.length) {
    const item = document.createElement("li");
    item.textContent = "초안을 저장하면 검사 결과가 표시됩니다.";
    root.append(item);
    return;
  }
  for (const issue of issues.slice(0, 20)) {
    const item = document.createElement("li");
    item.className = "check-fail";
    item.textContent = `${issue.rule_id}: ${issue.message}`;
    root.append(item);
  }
}

function collectGoldAnnotation() {
  if (!activeGoldCandidate) throw new Error("Gold 후보를 선택하세요.");
  const kind = document.getElementById("gold-answer-kind").value;
  let answer;
  if (kind === "numeric") {
    answer = {
      kind,
      value: document.getElementById("gold-answer-value").value.trim(),
      unit: document.getElementById("gold-answer-unit").value.trim(),
      scale: Number(document.getElementById("gold-answer-scale").value),
    };
  } else if (kind === "multi_numeric") {
    let values;
    try {
      values = JSON.parse(document.getElementById("gold-answer-values").value || "[]");
    } catch {
      throw new Error("복수 숫자 JSON 형식을 확인하세요.");
    }
    answer = { kind, values };
  } else if (kind === "unanswerable") {
    answer = { kind, reason: document.getElementById("gold-answer-reason").value.trim() };
  } else {
    answer = { kind: "text", text: document.getElementById("gold-answer-text").value.trim() };
  }
  const evidenceIds = [];
  const evidenceRoles = {};
  for (const checkbox of document.querySelectorAll("[data-evidence-id]")) {
    if (!checkbox.checked) continue;
    const evidenceId = checkbox.dataset.evidenceId;
    evidenceIds.push(evidenceId);
    const role = document.querySelector(`[data-evidence-role="${CSS.escape(evidenceId)}"]`);
    evidenceRoles[evidenceId] = role ? role.value : "support";
  }
  const previous = activeGoldCandidate.annotation || {};
  return {
    question_id: document.getElementById("gold-question-id").value.trim(),
    question_type: document.getElementById("gold-question-type").value,
    answerability: document.getElementById("gold-answerability").value,
    answer,
    candidate_filing_ids: splitLines(document.getElementById("gold-filing-ids").value),
    period: {
      period_type: document.getElementById("gold-period-type").value,
      start_date: document.getElementById("gold-period-start").value || null,
      end_date: document.getElementById("gold-period-end").value || null,
      instant_date: document.getElementById("gold-period-instant").value || null,
    },
    scope: document.getElementById("gold-scope").value,
    as_of: document.getElementById("gold-as-of").value || null,
    version_basis: document.getElementById("gold-version-basis").value,
    formula: previous.formula || null,
    attack_label: document.getElementById("gold-attack-label").value.trim() || null,
    evidence_ids: evidenceIds,
    evidence_roles: evidenceRoles,
    table_structure_validated: document.getElementById("gold-table-validated").checked,
    notes: document.getElementById("gold-notes").value.trim(),
  };
}

function splitLines(value) {
  return [...new Set(value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean))];
}

async function saveGoldCandidate() {
  if (!activeGoldCandidate) throw new Error("Gold 후보를 선택하세요.");
  const payload = await api(`/lab/api/gold-candidates/${encodeURIComponent(activeGoldCandidate.candidate_id)}`, {
    method: "PUT",
    body: JSON.stringify({ editor: reviewerName(), annotation: collectGoldAnnotation() }),
  });
  await refreshGoldCandidates(payload.candidate.candidate_id);
  return payload.candidate;
}

async function decideGoldCandidate(decision) {
  const actionStatus = document.getElementById("gold-action-status");
  actionStatus.textContent = decision === "approve" ? "초안 저장 후 승인 검사 중…" : "초안 저장 후 반려 처리 중…";
  try {
    const saved = await saveGoldCandidate();
    const payload = await api(`/lab/api/gold-candidates/${encodeURIComponent(saved.candidate_id)}/decision`, {
      method: "POST",
      body: JSON.stringify({
        reviewer: reviewerName(),
        decision,
        notes: document.getElementById("gold-notes").value.trim(),
      }),
    });
    await refreshGoldCandidates(payload.candidate.candidate_id);
    await refresh();
    setStatus(decision === "approve" ? "Gold를 승인했습니다." : "Gold 후보를 반려했습니다.");
  } catch (error) {
    actionStatus.textContent = friendlyGoldError(error.message);
  }
}

function updateAnswerFields() {
  const kind = document.getElementById("gold-answer-kind").value;
  for (const node of document.querySelectorAll("[data-answer-kind]")) {
    node.hidden = node.dataset.answerKind !== kind;
  }
}

function setGoldFormDisabled(disabled) {
  for (const control of document.querySelectorAll("#gold-form input, #gold-form textarea, #gold-form select, #gold-form button")) {
    control.disabled = disabled;
  }
}

function friendlyGoldError(message) {
  if (message === "gold_candidate_exists") return "이미 연결된 Gold 후보가 있습니다.";
  if (message === "self_approval_forbidden") return "작성자와 다른 검수자 라벨을 입력하세요. 실제 신원 분리는 팀 운영자가 확인해야 합니다.";
  if (message.startsWith("gold_checks_failed:")) return `자동검사를 통과하지 못했습니다: ${message.split(":").slice(1).join(":")}`;
  return message;
}

function activatePanel(panelId) {
  for (const button of document.querySelectorAll(".tab")) {
    button.classList.toggle("is-active", button.dataset.panel === panelId);
  }
  for (const panel of document.querySelectorAll(".panel")) {
    panel.hidden = panel.id !== panelId;
  }
}

document.getElementById("gate-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  sessionStorage.setItem(REVIEWER_KEY, reviewerNode.value.trim());
  try {
    await refresh();
  } catch (error) {
    setStatus(`연결 실패: ${error.message}`);
  }
});

document.getElementById("ask-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = document.getElementById("ask-question").value.trim();
  const askStatus = document.getElementById("ask-status");
  askStatus.textContent = "질문 중…";
  try {
    const response = await fetch("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, question_id: `qa-${Date.now()}` }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    document.getElementById("review-question").value = question;
    document.getElementById("review-answer").value = payload.answer || "";
    reviewMeta.value = JSON.stringify({
      question_id: payload.question_id || null,
      answerable: payload.answerable,
      verified: payload.verified,
      latency_ms: payload.latency_ms,
      request_id: payload.request_id,
      corpus_revision: payload.corpus_revision,
      citations: payload.evidence || payload.citations || [],
      endpoint: "/query",
    });
    askStatus.textContent = payload.verified ? "검증된 답을 받았습니다. 판정 후 저장하세요." : "보류/미검증 답입니다. 판정 후 저장하세요.";
  } catch (error) {
    document.getElementById("review-question").value = question;
    document.getElementById("review-answer").value = String(error.message);
    reviewMeta.value = JSON.stringify({ endpoint: "/query" });
    document.querySelector('input[name="verdict"][value="error"]').checked = true;
    askStatus.textContent = `질문 실패: ${error.message}`;
  }
});

document.getElementById("review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  let meta = {};
  try {
    meta = reviewMeta.value ? JSON.parse(reviewMeta.value) : {};
  } catch {
    meta = {};
  }
  const verdict = document.querySelector('input[name="verdict"]:checked');
  try {
    await api("/lab/api/reviews", {
      method: "POST",
      body: JSON.stringify({
        reviewer: reviewerNode.value.trim(),
        question: document.getElementById("review-question").value,
        answer: document.getElementById("review-answer").value,
        verdict: verdict ? verdict.value : "",
        notes: document.getElementById("review-notes").value,
        ...meta,
      }),
    });
    document.getElementById("review-notes").value = "";
    setStatus("검수 기록을 저장했습니다.");
    await refresh();
  } catch (error) {
    setStatus(`저장 실패: ${error.message}`);
  }
});

document.getElementById("perf-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/lab/api/perf-runs", {
      method: "POST",
      body: JSON.stringify({
        recorder: reviewerNode.value.trim(),
        suite: document.getElementById("perf-suite").value,
        passed: numberOrNull("perf-passed"),
        failed: numberOrNull("perf-failed"),
        skipped: numberOrNull("perf-skipped"),
        p50_ms: numberOrNull("perf-p50"),
        p95_ms: numberOrNull("perf-p95"),
        git_commit: document.getElementById("perf-commit").value || null,
        notes: document.getElementById("perf-notes").value,
      }),
    });
    event.target.reset();
    setStatus("성능 기록을 저장했습니다.");
    await refresh();
  } catch (error) {
    setStatus(`저장 실패: ${error.message}`);
  }
});

document.getElementById("mile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/lab/api/milestones", {
      method: "POST",
      body: JSON.stringify({
        author: reviewerNode.value.trim(),
        title: document.getElementById("mile-title").value,
        body: document.getElementById("mile-body").value,
        category: document.getElementById("mile-category").value,
      }),
    });
    event.target.reset();
    setStatus("발전 과정을 저장했습니다.");
    await refresh();
  } catch (error) {
    setStatus(`저장 실패: ${error.message}`);
  }
});

function numberOrNull(id) {
  const value = document.getElementById(id).value;
  return value === "" ? null : Number(value);
}

document.getElementById("gold-candidate-select").addEventListener("change", (event) => {
  if (event.target.value) showGoldCandidate(event.target.value);
});

document.getElementById("gold-refresh").addEventListener("click", async () => {
  try {
    await refreshGoldCandidates();
    setStatus("Gold 후보 목록을 새로고침했습니다.");
  } catch (error) {
    setStatus(`Gold 새로고침 실패: ${error.message}`);
  }
});

document.getElementById("gold-answer-kind").addEventListener("change", updateAnswerFields);

document.getElementById("gold-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const actionStatus = document.getElementById("gold-action-status");
  actionStatus.textContent = "초안 저장과 자동검사 중…";
  try {
    await saveGoldCandidate();
    actionStatus.textContent = "초안을 저장하고 자동검사를 갱신했습니다.";
    setStatus("Gold 초안을 저장했습니다.");
  } catch (error) {
    actionStatus.textContent = friendlyGoldError(error.message);
  }
});

document.getElementById("gold-approve").addEventListener("click", () => decideGoldCandidate("approve"));
document.getElementById("gold-reject").addEventListener("click", () => decideGoldCandidate("reject"));

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => {
    activatePanel(tab.dataset.panel);
  });
}

refresh().catch((error) => setStatus(`연결 실패: ${error.message}`));
