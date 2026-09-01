const $ = (selector) => document.querySelector(selector);
const state = {cases: [], runs: [], selectedResult: null};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {"Content-Type": "application/json", Accept: "application/json"},
    ...options,
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
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
    const latency = document.createElement("td"); latency.textContent = result?.trace?.latency_ms === undefined ? "-" : `${result.trace.latency_ms} ms`;
    const manage = document.createElement("td");
    manage.append(actionButton("수정", () => openForm(qaCase)), actionButton("삭제", () => removeCase(qaCase.id)));
    row.append(selectCell, statusCell, questionCell, category, layer, latency, manage); body.append(row);
  }
}

function showDetail(result) {
  state.selectedResult = result;
  const panel = $("#detail-panel");
  if (!result) { panel.hidden = true; return; }
  panel.hidden = false;
  $("#failure-summary").textContent = `Expected\n${text(result.expected)}\n\nActual\n${text(result.actual)}\n\nFailure Layer\n${text(result.failure_layer)}\n\nReason\n${text(result.short_reason)}`;
  $("#resolver-trace").textContent = text(result.trace?.resolver);
  $("#executor-trace").textContent = text(result.trace?.executors);
  $("#evidence-trace").textContent = text(result.trace?.evidence);
  $("#calls-trace").textContent = text(result.trace?.calls);
}

function csv(value) { return value ? value.split(",").map((item) => item.trim()).filter(Boolean) : []; }
function openForm(qaCase = null) {
  $("#form-title").textContent = qaCase ? "QA 수정" : "QA 추가";
  $("#case-original-id").value = qaCase?.id || ""; $("#case-id").value = qaCase?.id || "";
  $("#case-id").disabled = Boolean(qaCase); $("#case-question").value = qaCase?.question || "";
  $("#case-category").value = qaCase?.category || "financial_direct";
  const expected = qaCase?.expected || {};
  $("#case-company").value = expected.company || ""; $("#case-companies").value = (expected.companies || []).join(", ");
  $("#case-period").value = expected.period || ""; $("#case-metric").value = expected.metric || ""; $("#case-workflow").value = expected.workflow || "";
  $("#case-forbidden").value = (qaCase?.forbidden_phrases || []).join(", ");
  $("#flag-numeric").checked = expected.backend_display_value_required === true || expected.numeric_answer_required === true;
  $("#flag-citation").checked = expected.citation_required === true; $("#flag-answer").checked = expected.answer_allowed === true;
  $("#case-dense").value = expected.dense_max_calls ?? 0; $("#case-fc").value = expected.function_calling_max_calls ?? 0;
  $("#case-dialog").showModal();
}

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
  await api(original ? `/v1/eval/cases/${original}` : "/v1/eval/cases", {method: original ? "PUT" : "POST", body: JSON.stringify(payload)});
  $("#case-dialog").close(); await load();
}

async function removeCase(id) { if (confirm(`${id} QA를 삭제할까요?`)) { await api(`/v1/eval/cases/${id}`, {method:"DELETE"}); await load(); } }
async function run(payload) { setStatus("QA 실행 중…"); try { await api("/v1/eval/run", {method:"POST", body:JSON.stringify(payload)}); await load(); setStatus("실행 완료"); } catch (error) { setStatus(`실패: ${error.message}`); } }
function selectedIds() { return [...document.querySelectorAll("[data-qa-id]:checked")].map((item) => item.dataset.qaId); }

async function copyReport() {
  const failures = latestRun()?.results?.filter((row) => row.status === "FAIL") || [];
  const chosen = state.selectedResult?.status === "FAIL" ? [state.selectedResult] : failures;
  const report = ["공통 root cause를 찾아 최소 수정하고 regression을 실행하라.", "", ...chosen.flatMap((row) => [
    `- 질문: ${row.question}`, `- expected: ${text(row.expected)}`, `- actual: ${text(row.actual)}`,
    `- failure layer: ${row.failure_layer}`, `- trace summary: ${text(row.trace)}`, `- 관련 QA ids: ${row.qa_id}`, "",
  ])].join("\n");
  await navigator.clipboard.writeText(report); setStatus("Codex용 오류 보고서를 복사했습니다.");
}

async function load() {
  const [casePayload, results] = await Promise.all([api("/v1/eval/cases"), api("/v1/eval/results")]);
  state.cases = casePayload.cases; state.runs = results;
  const category = $("#case-category"); category.replaceChildren(...casePayload.categories.map((name) => { const option=document.createElement("option"); option.value=name; option.textContent=name; return option; }));
  renderStats(); renderCases(); if (state.selectedResult) showDetail(resultById(state.selectedResult.qa_id));
}

$("#add-case").addEventListener("click", () => openForm()); $("#case-form").addEventListener("submit", saveCase);
$("#run-all").addEventListener("click", () => run({})); $("#run-selected").addEventListener("click", () => run({ids:selectedIds()}));
$("#run-failed").addEventListener("click", () => run({failed_only:true})); $("#copy-report").addEventListener("click", copyReport);
load().catch((error) => setStatus(`초기화 실패: ${error.message}`));
