const $ = (selector) => document.querySelector(selector);
const state = {cases: [], categories: [], runs: [], selectedResult: null, quickResult: null};

function publicError(status) {
  if (status === 400) return "요청 내용을 확인해 주세요.";
  if (status === 404) return "요청한 QA를 찾을 수 없습니다.";
  if (status === 429) return "요청이 많습니다. 잠시 후 다시 시도해 주세요.";
  return "Agent 서버 요청에 실패했습니다.";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {"Content-Type": "application/json", Accept: "application/json"},
    ...options,
  });
  if (!response.ok) throw new Error(`${publicError(response.status)} (HTTP ${response.status})`);
  return response.status === 204 ? null : response.json();
}

function latestRun() { return state.runs[0] || null; }
function resultById(id) { return latestRun()?.results?.find((row) => row.qa_id === id) || null; }
function text(value) { return value === null || value === undefined ? "-" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value); }
function setStatus(message) { $("#qa-status").textContent = message; }

function renderStats() {
  const run = latestRun();
  $("#stat-total").textContent = run?.total ?? state.cases.length;
  $("#stat-pass").textContent = run?.pass ?? 0;
  $("#stat-fail").textContent = run?.fail ?? 0;
  $("#stat-rate").textContent = `${run?.pass_rate ?? 0}%`;
  $("#stat-time").textContent = run?.created_at ? new Date(run.created_at).toLocaleString("ko-KR") : "없음";
}

function actionButton(label, action) {
  const button = document.createElement("button");
  button.type = "button"; button.textContent = label; button.addEventListener("click", action);
  return button;
}

function renderCases() {
  const body = $("#case-list"); body.replaceChildren();
  for (const qaCase of state.cases) {
    const result = resultById(qaCase.id);
    const row = document.createElement("tr");
    const selectCell = document.createElement("td");
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.dataset.qaId = qaCase.id; selectCell.append(checkbox);
    const statusCell = document.createElement("td"); statusCell.className = `status ${result?.status || "NOT_RUN"}`; statusCell.textContent = result?.status || "NOT RUN";
    const questionCell = document.createElement("td");
    const question = actionButton(qaCase.question, () => showDetail(result)); question.className = "question-button"; questionCell.append(question);
    const category = document.createElement("td"); category.textContent = qaCase.category;
    const layer = document.createElement("td"); layer.textContent = result?.failure_layer || "-";
    const latency = document.createElement("td"); latency.textContent = result?.latency_ms === undefined ? "-" : `${result.latency_ms} ms`;
    const manage = document.createElement("td");
    manage.append(actionButton("수정", () => openForm(qaCase)), actionButton("삭제", () => removeCase(qaCase.id)));
    row.append(selectCell, statusCell, questionCell, category, layer, latency, manage); body.append(row);
  }
}

function appendDetail(list, label, value) {
  const term = document.createElement("dt"); term.textContent = label;
  const description = document.createElement("dd"); description.textContent = text(value);
  list.append(term, description);
}

function showDetail(result) {
  state.selectedResult = result;
  const panel = $("#detail-panel");
  if (!result) { panel.hidden = true; return; }
  panel.hidden = false;
  const summary = $("#failure-summary"); summary.replaceChildren();
  appendDetail(summary, "Question", result.question);
  appendDetail(summary, "Actual Agent Answer", result.agent_answer || "-");
  appendDetail(summary, "Expected", result.expected);
  appendDetail(summary, "Actual resolver/workflow", result.actual);
  appendDetail(summary, "Evidence status", result.evidence_status || result.trace?.evidence);
  appendDetail(summary, "PASS/FAIL", result.status);
  appendDetail(summary, "Failure Layer", result.failure_layer || "-");
  appendDetail(summary, "Failure Reason", result.failure_reason || result.short_reason || "-");
  appendDetail(summary, "Latency", result.latency_ms === undefined ? "-" : `${result.latency_ms} ms`);
  $("#resolver-trace").textContent = text(result.trace?.resolver);
  $("#executor-trace").textContent = text(result.trace?.executors);
  $("#evidence-trace").textContent = text(result.trace?.evidence);
  $("#calls-trace").textContent = text(result.trace?.calls);
}

function csv(value) { return value ? value.split(",").map((item) => item.trim()).filter(Boolean) : []; }
function suggestedCaseId() {
  const stamp = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
  return `qa_${stamp}`;
}
function inferredCategory(workflow) { return state.categories.includes(workflow) ? workflow : "search"; }
function openForm(qaCase = null, quick = null) {
  $("#form-title").textContent = qaCase ? "QA 수정" : "QA 추가";
  $("#case-original-id").value = qaCase?.id || ""; $("#case-id").value = qaCase?.id || (quick ? suggestedCaseId() : "");
  $("#case-id").disabled = Boolean(qaCase); $("#case-question").value = qaCase?.question || quick?.question || "";
  const quickResolver = quick?.actual?.resolver || {};
  $("#case-category").value = qaCase?.category || inferredCategory(quick?.actual?.workflow);
  const expected = qaCase?.expected || (quick ? {
    companies: quickResolver.companies, period: quickResolver.period,
    metric: quickResolver.metric, workflow: quick.actual?.workflow,
    answer_allowed: quick.evidence_status?.answer_allowed,
  } : {});
  $("#case-company").value = expected.company || ""; $("#case-companies").value = (expected.companies || []).join(", ");
  $("#case-period").value = expected.period || ""; $("#case-metric").value = expected.metric || ""; $("#case-workflow").value = expected.workflow || "";
  $("#case-forbidden").value = (qaCase?.forbidden_phrases || []).join(", ");
  $("#flag-numeric").checked = expected.backend_display_value_required === true || expected.numeric_answer_required === true;
  $("#flag-citation").checked = expected.citation_required === true; $("#flag-answer").checked = expected.answer_allowed === true;
  $("#case-dense").value = expected.dense_max_calls ?? 0; $("#case-fc").value = expected.function_calling_max_calls ?? 0;
  $("#case-dialog").showModal();
}

function closeCaseDialog() { if ($("#case-dialog").open) $("#case-dialog").close(); }

async function saveCase(event) {
  event.preventDefault();
  const original = $("#case-original-id").value;
  const companies = csv($("#case-companies").value);
  const expected = {
    period: $("#case-period").value || undefined, metric: $("#case-metric").value || undefined,
    workflow: $("#case-workflow").value || undefined,
    backend_display_value_required: $("#flag-numeric").checked,
    citation_required: $("#flag-citation").checked, answer_allowed: $("#flag-answer").checked || undefined,
    dense_max_calls: Number($("#case-dense").value), function_calling_max_calls: Number($("#case-fc").value),
  };
  if (companies.length) expected.companies = companies; else if ($("#case-company").value) expected.companies = [$("#case-company").value];
  Object.keys(expected).forEach((key) => expected[key] === undefined && delete expected[key]);
  const payload = {id: original || $("#case-id").value, question: $("#case-question").value, category: $("#case-category").value, expected, forbidden_phrases: csv($("#case-forbidden").value)};
  try {
    await api(original ? `/v1/eval/cases/${original}` : "/v1/eval/cases", {method: original ? "PUT" : "POST", body: JSON.stringify(payload)});
    closeCaseDialog(); await load(); setStatus("QA를 저장했습니다.");
  } catch (error) { setStatus(`저장 실패: ${error.message}`); }
}

async function removeCase(id) { if (confirm(`${id} QA를 삭제할까요?`)) { await api(`/v1/eval/cases/${id}`, {method:"DELETE"}); await load(); } }
function infraDetail(question, reason) {
  return {qa_id:"infra", question, status:"FAIL", agent_answer:"-", expected:"-", actual:{runtime_status:"error"}, evidence_status:{status:"insufficient", answer_allowed:false}, failure_layer:"INFRA", failure_reason:reason, latency_ms:0, trace:{}};
}
async function run(payload) {
  setStatus("QA 실행 중…");
  try { await api("/v1/eval/run", {method:"POST", body:JSON.stringify(payload)}); await load(); setStatus("실행 완료"); }
  catch (error) { const failure=infraDetail("QA 실행 요청", error.message); showDetail(failure); setStatus(`INFRA FAIL: ${error.message}`); }
}
function selectedIds() { return [...document.querySelectorAll("[data-qa-id]:checked")].map((item) => item.dataset.qaId); }

function renderQuick(result) {
  state.quickResult = result; $("#quick-result").hidden = false;
  $("#quick-status").textContent = result.status === "FAIL" ? `INFRA FAIL · ${result.failure_layer || "INFRA"}` : "실행 완료";
  $("#quick-status").className = `status ${result.status}`;
  $("#quick-latency").textContent = `${result.latency_ms ?? 0} ms`;
  $("#quick-answer").textContent = result.answer || "답변이 생성되지 않았습니다.";
  const error = $("#quick-error"); error.hidden = !result.failure_reason; error.textContent = result.failure_reason ? "실제 Agent 실행에 실패했습니다. 잠시 후 다시 시도해 주세요." : "";
  const citations = $("#quick-citations"); citations.replaceChildren();
  for (const citation of result.citations || []) {
    const item = document.createElement("li");
    const receipt = citation.rcept_no || citation.filing_id || "접수번호 없음";
    item.textContent = `${citation.report_name || "공시"} · 접수번호 ${receipt}${citation.section_title ? ` · ${citation.section_title}` : ""}`;
    citations.append(item);
  }
  if (!citations.children.length) { const item=document.createElement("li"); item.textContent="표시할 근거 공시가 없습니다."; citations.append(item); }
}

async function runQuick(event) {
  event.preventDefault();
  const question = $("#quick-question").value.trim(); if (!question) return;
  const button = $("#quick-run"); button.disabled = true; button.textContent = "실행 중…";
  try {
    const result = await api("/v1/eval/quick-answer", {method:"POST", body:JSON.stringify({question})});
    renderQuick(result); setStatus(result.status === "FAIL" ? "INFRA FAIL" : "빠른 질문 실행 완료");
  } catch (error) {
    const failure = {...infraDetail(question, "agent_api_request_failed"), answer:"", citations:[]};
    renderQuick(failure); setStatus(`INFRA FAIL: ${error.message}`);
  } finally { button.disabled = false; button.textContent = "실제 Agent 실행"; }
}

async function copyTextWithFallback(value) {
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(value); return true; } catch {}
  }
  const area = document.createElement("textarea");
  area.value = value; area.setAttribute("readonly", ""); area.className = "copy-helper";
  document.body.append(area); area.select(); area.setSelectionRange(0, area.value.length);
  let copied = false;
  try { copied = document.execCommand("copy"); } catch { copied = false; }
  area.remove(); return copied;
}

function showCopyFallback(report) {
  $("#copy-text").value = report; $("#copy-dialog").showModal(); $("#copy-text").select();
}

async function copyReport() {
  const failures = latestRun()?.results?.filter((row) => row.status === "FAIL") || [];
  const chosen = state.selectedResult ? [state.selectedResult] : failures;
  if (!chosen.length) { setStatus("복사할 QA 결과가 없습니다."); return; }
  const report = chosen.flatMap((row) => [
    `QA id: ${row.qa_id}`, `question: ${row.question}`, `expected: ${text(row.expected)}`,
    `actual: ${text(row.actual)}`, `failure layer: ${row.failure_layer || "-"}`,
    `failure reason: ${row.failure_reason || row.short_reason || "-"}`,
    `agent answer: ${row.agent_answer || "-"}`,
    `trace summary: ${text({resolver:row.trace?.resolver, executors:row.trace?.executors, evidence:row.trace?.evidence, calls:row.trace?.calls})}`, "",
  ]).join("\n");
  if (await copyTextWithFallback(report)) setStatus("Codex용 오류 보고서를 복사했습니다.");
  else { showCopyFallback(report); setStatus("자동 복사가 차단되어 직접 복사 창을 열었습니다."); }
}

async function load() {
  const [casePayload, results] = await Promise.all([api("/v1/eval/cases"), api("/v1/eval/results")]);
  state.cases = casePayload.cases; state.categories = casePayload.categories; state.runs = results;
  const category = $("#case-category"); category.replaceChildren(...casePayload.categories.map((name) => { const option=document.createElement("option"); option.value=name; option.textContent=name; return option; }));
  renderStats(); renderCases(); if (state.selectedResult && state.selectedResult.qa_id !== "infra") showDetail(resultById(state.selectedResult.qa_id));
}

$("#add-case").addEventListener("click", () => openForm()); $("#case-form").addEventListener("submit", saveCase);
$("#close-case").addEventListener("click", closeCaseDialog); $("#cancel-case").addEventListener("click", closeCaseDialog);
$("#case-dialog").addEventListener("cancel", () => setStatus("QA 추가를 취소했습니다."));
$("#run-all").addEventListener("click", () => run({})); $("#run-selected").addEventListener("click", () => run({ids:selectedIds()}));
$("#run-failed").addEventListener("click", () => run({failed_only:true})); $("#copy-report").addEventListener("click", copyReport);
$("#quick-form").addEventListener("submit", runQuick); $("#quick-save").addEventListener("click", () => openForm(null, state.quickResult));
$("#close-copy").addEventListener("click", () => $("#copy-dialog").close()); $("#dismiss-copy").addEventListener("click", () => $("#copy-dialog").close());
$("#select-copy").addEventListener("click", () => { $("#copy-text").focus(); $("#copy-text").select(); });
load().catch((error) => { const failure=infraDetail("Dashboard 초기화", error.message); showDetail(failure); setStatus(`INFRA FAIL: ${error.message}`); });
