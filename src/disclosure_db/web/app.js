import {
  answerText,
  askDisclosure,
  classifyAnswer,
  dartUrl,
  evidenceLabel,
  fetchFinancialCoverage,
  financialFactLabel,
  financialValueLabel,
  healthLabel,
  locatorLabel,
  metricCoverageRows,
  providerLabel,
} from "/static/api.js";
import {
  appendMessage,
  clearConversations,
  createConversation,
  deleteConversation,
  emptyStore,
  normalizeStore,
  searchConversations,
} from "/static/history.js";

const STORAGE_KEY = "mirae-disclosure-agent-history-v1";
const REQUEST_TIMEOUT_MS = 30_000;

const status = document.querySelector("#service-status");
const providerMode = document.querySelector("#provider-mode");
const corpusRevision = document.querySelector("#corpus-revision");
const legalCompanyCount = document.querySelector("#legal-company-count");
const financialCoverageList = document.querySelector("#financial-coverage-list");
const appShell = document.querySelector("#app-shell");
const sidebar = document.querySelector("#sidebar");
const sidebarToggle = document.querySelector("#sidebar-toggle");
const sidebarClose = document.querySelector("#sidebar-close");
const newChat = document.querySelector("#new-chat");
const clearHistory = document.querySelector("#clear-history");
const historySearch = document.querySelector("#history-search");
const conversationList = document.querySelector("#conversation-list");
const messages = document.querySelector("#messages");
const questionForm = document.querySelector("#question-form");
const questionInput = document.querySelector("#question-input");
const sendQuestion = document.querySelector("#send-question");
const exampleButtons = document.querySelectorAll("[data-example]");

let store = loadStore();
let selectedConversationId = store.conversations[0]?.id ?? null;
let transientFailure = null;
let requestPending = false;
let healthSnapshot = null;
let coverageSnapshot = null;

function loadStore() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value ? normalizeStore(JSON.parse(value)) : emptyStore();
  } catch {
    return emptyStore();
  }
}

function saveStore() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  } catch {
    status.textContent = "이 브라우저에서는 대화 기록을 저장할 수 없습니다.";
    status.dataset.state = "warning";
  }
}

function selectedConversation() {
  return store.conversations.find(({id}) => id === selectedConversationId) ?? null;
}

function closeSidebar(returnFocus = false) {
  sidebar.classList.remove("sidebar-open");
  appShell.classList.add("sidebar-collapsed");
  sidebarToggle.setAttribute("aria-expanded", "false");
  if (returnFocus && sidebarToggle.offsetParent !== null) {
    sidebarToggle.focus();
  }
}

function setSidebarOpen(open) {
  const mobile = window.matchMedia("(max-width: 44rem)").matches;
  sidebar.classList.toggle("sidebar-open", mobile && open);
  appShell.classList.toggle("sidebar-collapsed", !mobile && !open);
  sidebarToggle.setAttribute("aria-expanded", String(open));
}

function addText(parent, tagName, className, text) {
  const element = document.createElement(tagName);
  element.className = className;
  element.textContent = text;
  parent.append(element);
  return element;
}

function renderEvidence(parent, evidence) {
  if (!Array.isArray(evidence) || evidence.length === 0) {
    return;
  }
  const section = document.createElement("details");
  section.className = "evidence-section";
  addText(section, "summary", "evidence-heading", `공시 근거 ${evidence.length}건`);

  const list = document.createElement("ol");
  list.className = "evidence-list";
  for (const item of evidence) {
    const evidenceItem = document.createElement("li");
    addText(evidenceItem, "strong", "evidence-label", evidenceLabel(item) || "공시 근거");
    if (typeof item.excerpt === "string" && item.excerpt.trim()) {
      addText(evidenceItem, "p", "evidence-excerpt", item.excerpt.trim());
    }
    const location = locatorLabel(item.locator);
    if (location) {
      addText(evidenceItem, "p", "evidence-meta", location);
    }
    const filingUrl = dartUrl(item.receipt_no);
    if (filingUrl) {
      const link = document.createElement("a");
      link.className = "evidence-link";
      link.href = filingUrl;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "DART 원문 열기";
      evidenceItem.append(link);
    }
    list.append(evidenceItem);
  }
  section.append(list);
  parent.append(section);
}

function renderFinancialFacts(parent, financialFacts, evidence) {
  if (!Array.isArray(financialFacts) || financialFacts.length === 0) {
    return;
  }
  const section = document.createElement("details");
  section.className = "financial-facts-section";
  section.open = financialFacts.length <= 3;
  addText(section, "summary", "financial-facts-heading", `재무 수치·근거 ${financialFacts.length}건`);
  const list = document.createElement("div");
  list.className = "financial-facts-list";
  for (const fact of financialFacts) {
    const card = document.createElement("article");
    card.className = "financial-fact-card";
    addText(card, "p", "financial-fact-label", financialFactLabel(fact));
    addText(card, "strong", "financial-fact-value", financialValueLabel(fact));
    const filingName = fact.report_name_raw || "사업보고서";
    const filingMeta = [filingName, fact.filed_at, fact.filing_id].filter(Boolean).join(" · ");
    if (filingMeta) {
      addText(card, "p", "financial-fact-filing", filingMeta);
    }
    const evidenceIds = Array.isArray(fact.evidence_ids) ? fact.evidence_ids : [];
    const matching = Array.isArray(evidence)
      ? evidence.filter((item) => evidenceIds.includes(item.evidence_id))
      : [];
    const excerpts = matching.length > 0
      ? matching
      : (Array.isArray(fact.evidence_texts)
        ? fact.evidence_texts.map((excerpt) => ({excerpt}))
        : []);
    if (excerpts.length > 0) {
      const source = document.createElement("details");
      source.className = "financial-fact-source";
      addText(source, "summary", "", "근거 표 셀 보기");
      for (const item of excerpts) {
        if (typeof item.excerpt === "string" && item.excerpt.trim()) {
          addText(source, "p", "evidence-excerpt", item.excerpt.trim());
        }
        const location = locatorLabel(item.locator);
        if (location) {
          addText(source, "p", "evidence-meta", location);
        }
      }
      card.append(source);
    }
    list.append(card);
  }
  section.append(list);
  parent.append(section);
}

function renderMessageCoverage(parent, coverage) {
  const rows = metricCoverageRows(coverage);
  if (rows.length === 0) {
    return;
  }
  const details = document.createElement("details");
  details.className = "message-coverage";
  addText(details, "summary", "", "전체 데이터 검증 현황");
  const list = document.createElement("ul");
  for (const row of rows) {
    addText(
      list,
      "li",
      row.complete ? "coverage-complete" : "coverage-incomplete",
      `${row.label} ${row.validated}/${row.expected}개 법인`,
    );
  }
  details.append(list);
  parent.append(details);
}

function renderResponseDetails(parent, message) {
  const values = [
    ["요청 ID", message.request_id],
    ["응답 시간", typeof message.latency_ms === "number" ? `${message.latency_ms} ms` : null],
    ["판정 코드", Array.isArray(message.reason_codes) ? message.reason_codes.join(", ") : null],
  ].filter(([, value]) => value);
  if (values.length === 0) {
    return;
  }

  const details = document.createElement("details");
  details.className = "response-details";
  addText(details, "summary", "", "응답 정보");
  const list = document.createElement("dl");
  for (const [label, value] of values) {
    addText(list, "dt", "", label);
    addText(list, "dd", "", value);
  }
  details.append(list);
  parent.append(details);
}

function renderMessage(message) {
  const item = document.createElement("li");
  item.className = `message message-${message.role}`;
  if (message.role === "assistant") {
    const badgeText = message.answer_state === "verified" ? "근거 확인됨" : "답변 보류";
    addText(item, "span", `answer-badge answer-${message.answer_state || "abstained"}`, badgeText);
  }
  addText(item, "p", "message-text", message.text);
  if (message.role === "assistant") {
    renderFinancialFacts(item, message.financial_facts, message.evidence);
    renderMessageCoverage(item, message.coverage);
    renderEvidence(item, message.evidence);
    renderResponseDetails(item, message);
  }
  return item;
}

function renderFailure() {
  if (!transientFailure || transientFailure.conversationId !== selectedConversationId) {
    return;
  }
  const item = document.createElement("li");
  item.className = "message message-error";
  item.setAttribute("role", "alert");
  addText(item, "p", "message-text", transientFailure.error.message);
  if (transientFailure.error.retryable) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "retry-button";
    retry.textContent = "다시 시도";
    retry.disabled = requestPending;
    retry.addEventListener("click", () => submitQuestion(transientFailure.question, false));
    item.append(retry);
  }
  messages.append(item);
}

function renderEmptyState() {
  const item = document.createElement("li");
  item.className = "empty-message";
  addText(item, "p", "empty-kicker", "DISCLOSURE RESEARCH");
  addText(item, "h2", "", "기업 공시를 근거와 함께 살펴보세요");
  addText(
    item,
    "p",
    "",
    "회사명과 궁금한 항목을 함께 입력하면 검증 가능한 공시만 찾아 답합니다.",
  );
  const facts = document.createElement("div");
  facts.id = "corpus-facts";
  facts.className = "corpus-facts";
  facts.setAttribute("aria-label", "공시 데이터 특징");
  const companyFact = addText(
    facts,
    "span",
    "",
    coverageSnapshot?.snapshot
      ? `${healthSnapshot?.company_count ?? coverageSnapshot.snapshot.searchable_alias_count ?? 0}개 검색 대상 · ${coverageSnapshot.snapshot.source_company_count ?? 0}개 법인`
      : healthSnapshot?.ready === true
        ? `${healthSnapshot.company_count ?? 0}개 검색명`
      : "검색 가능한 기업 확인 중",
  );
  companyFact.id = "company-count-fact";
  addText(facts, "span", "", "정정 공시 계보 확인");
  addText(facts, "span", "", "인용 근거 열람");
  item.append(facts);
  messages.append(item);
}

function renderMessages() {
  messages.replaceChildren();
  const conversation = selectedConversation();
  if (!conversation) {
    renderEmptyState();
    return;
  }
  for (const message of conversation.messages) {
    messages.append(renderMessage(message));
  }
  renderFailure();
  messages.scrollTop = messages.scrollHeight;
}

function renderConversations() {
  conversationList.replaceChildren();
  for (const conversation of searchConversations(store, historySearch.value)) {
    const item = document.createElement("li");
    item.className = "conversation-row";

    const selectButton = document.createElement("button");
    selectButton.type = "button";
    selectButton.className = "conversation-select";
    selectButton.textContent = conversation.title || "제목 없는 대화";
    selectButton.setAttribute("aria-current", String(conversation.id === selectedConversationId));
    selectButton.addEventListener("click", () => {
      selectedConversationId = conversation.id;
      transientFailure = null;
      closeSidebar(true);
      render();
    });

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "conversation-delete";
    deleteButton.textContent = "삭제";
    deleteButton.setAttribute("aria-label", `${conversation.title || "제목 없는 대화"} 삭제`);
    deleteButton.addEventListener("click", () => {
      store = deleteConversation(store, conversation.id);
      if (selectedConversationId === conversation.id) {
        selectedConversationId = store.conversations[0]?.id ?? null;
        transientFailure = null;
      }
      saveStore();
      render();
    });

    item.append(selectButton, deleteButton);
    conversationList.append(item);
  }
}

function render() {
  renderConversations();
  renderMessages();
}

function setPending(pending) {
  requestPending = pending;
  sendQuestion.disabled = pending;
  questionInput.disabled = pending;
  questionForm.setAttribute("aria-busy", String(pending));
  messages.setAttribute("aria-busy", String(pending));
  sendQuestion.textContent = pending ? "확인 중…" : "질문하기";
}

async function submitQuestion(question, appendUser = true) {
  const text = question.trim();
  if (!text || requestPending) {
    return;
  }

  const now = new Date().toISOString();
  if (appendUser) {
    if (selectedConversationId) {
      store = appendMessage(store, selectedConversationId, {role: "user", text, created_at: now});
    } else {
      selectedConversationId = `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      store = createConversation(store, text, now, selectedConversationId);
    }
    saveStore();
  }
  const conversationId = selectedConversationId;

  transientFailure = null;
  setPending(true);
  render();
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const body = await askDisclosure(text, {signal: controller.signal});
    store = appendMessage(store, conversationId, {
      role: "assistant",
      text: answerText(body),
      created_at: new Date().toISOString(),
      answer_state: classifyAnswer(body),
      evidence: Array.isArray(body.evidence) ? body.evidence : [],
      financial_facts: Array.isArray(body.financial_facts) ? body.financial_facts : [],
      coverage: body.coverage && typeof body.coverage === "object" ? body.coverage : {},
      aggregate_result: body.aggregate_result && typeof body.aggregate_result === "object"
        ? body.aggregate_result
        : {},
      request_id: body.request_id,
      latency_ms: body.latency_ms,
      reason_codes: Array.isArray(body.reason_codes) ? body.reason_codes : [],
    });
    saveStore();
  } catch (error) {
    const safeError = error
      && typeof error.message === "string"
      && typeof error.retryable === "boolean"
      ? error
      : {kind: "client_error", message: "응답을 처리하지 못했습니다.", retryable: true};
    transientFailure = {conversationId, question: text, error: safeError};
  } finally {
    window.clearTimeout(timeout);
    setPending(false);
    render();
    questionInput.focus();
  }
}

function renderCoverageStatus() {
  const snapshot = coverageSnapshot?.snapshot;
  const rows = metricCoverageRows(coverageSnapshot);
  if (!snapshot || rows.length === 0) {
    legalCompanyCount.textContent = "재무 DB 법인 수 확인 불가";
    financialCoverageList.replaceChildren();
    addText(financialCoverageList, "li", "coverage-incomplete", "검증 현황 확인 불가");
    return;
  }
  legalCompanyCount.textContent = [
    `${healthSnapshot?.company_count ?? snapshot.searchable_alias_count ?? 0}개 검색 대상`,
    `${snapshot.source_company_count ?? 0}개 법인`,
    `${snapshot.selected_filing_company_count ?? 0}개 사업보고서 선택`,
  ].join(" · ");
  financialCoverageList.replaceChildren();
  for (const row of rows) {
    addText(
      financialCoverageList,
      "li",
      row.complete ? "coverage-complete" : "coverage-incomplete",
      `${row.label} ${row.validated}/${row.expected}`,
    );
  }
}

async function refreshCoverage() {
  try {
    coverageSnapshot = await fetchFinancialCoverage();
  } catch {
    coverageSnapshot = null;
  }
  renderCoverageStatus();
  if (!selectedConversation()) {
    renderMessages();
  }
}

async function refreshHealth() {
  try {
    const response = await fetch("/health", {headers: {Accept: "application/json"}});
    const body = response.ok ? await response.json() : {};
    healthSnapshot = body;
    status.textContent = healthLabel(body);
    status.dataset.state = body.ready === true ? "ready" : "waiting";
    providerMode.textContent = providerLabel(body.provider_configured === true);
    corpusRevision.textContent = typeof body.corpus_revision === "string"
      ? body.corpus_revision
      : "확인 불가";
    if (!selectedConversation()) {
      renderMessages();
    }
  } catch {
    status.textContent = "상태 확인 불가";
    status.dataset.state = "warning";
    providerMode.textContent = "답변 엔진 상태 확인 불가";
    corpusRevision.textContent = "확인 불가";
  }
}

newChat.addEventListener("click", () => {
  selectedConversationId = null;
  transientFailure = null;
  questionInput.value = "";
  closeSidebar(true);
  render();
  questionInput.focus();
});

historySearch.addEventListener("input", renderConversations);

clearHistory.addEventListener("click", () => {
  if (!window.confirm("이 브라우저에 저장된 모든 대화 기록을 삭제할까요?")) {
    return;
  }
  store = clearConversations();
  selectedConversationId = null;
  transientFailure = null;
  saveStore();
  render();
});

sidebarToggle.addEventListener("click", () => {
  const mobile = window.matchMedia("(max-width: 44rem)").matches;
  const isOpen = mobile
    ? sidebar.classList.contains("sidebar-open")
    : !appShell.classList.contains("sidebar-collapsed");
  const willOpen = !isOpen;
  setSidebarOpen(willOpen);
  if (willOpen) {
    newChat.focus();
  } else {
    sidebarToggle.focus();
  }
});

sidebarClose.addEventListener("click", () => closeSidebar(true));

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && sidebar.classList.contains("sidebar-open")) {
    closeSidebar(true);
  }
});

for (const button of exampleButtons) {
  button.addEventListener("click", () => {
    questionInput.value = button.dataset.example;
    questionInput.focus();
  });
}

questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    questionForm.requestSubmit();
  }
});

questionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = questionInput.value;
  if (!text.trim()) {
    return;
  }
  questionInput.value = "";
  submitQuestion(text);
});

render();
refreshHealth();
refreshCoverage();
