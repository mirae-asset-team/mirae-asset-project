import {
  answerText,
  askDisclosure,
  blockedReason,
  citationTitle,
  classifyAnswer,
  dartUrl,
  evidenceStatus,
  evidenceStatusLabel,
  loadingLabel,
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

const STORAGE_KEY = "mirae-disclosure-hcx-history-v1";
const REQUEST_TIMEOUT_MS = 60_000;

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
const sendLabel = document.querySelector(".send-label");
const serviceStatus = document.querySelector("#service-status");
const exampleButtons = document.querySelectorAll("[data-example]");

let store = loadStore();
let selectedConversationId = store.conversations[0]?.id ?? null;
let transientFailure = null;
let requestPending = false;

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
    setServiceStatus("기록 저장 불가", "error");
  }
}

function selectedConversation() {
  return store.conversations.find(({id}) => id === selectedConversationId) ?? null;
}

function isMobile() {
  return window.matchMedia("(max-width: 48rem)").matches;
}

function closeSidebar(returnFocus = false) {
  sidebar.classList.remove("sidebar-open");
  if (!isMobile()) {
    appShell.classList.add("sidebar-collapsed");
  }
  sidebarToggle.setAttribute("aria-expanded", "false");
  if (returnFocus && sidebarToggle.offsetParent !== null) {
    sidebarToggle.focus();
  }
}

function setSidebarOpen(open) {
  sidebar.classList.toggle("sidebar-open", isMobile() && open);
  appShell.classList.toggle("sidebar-collapsed", !isMobile() && !open);
  sidebarToggle.setAttribute("aria-expanded", String(open));
}

function setServiceStatus(text, state) {
  serviceStatus.replaceChildren();
  const dot = document.createElement("span");
  dot.className = "status-dot";
  dot.setAttribute("aria-hidden", "true");
  serviceStatus.append(dot, document.createTextNode(text));
  serviceStatus.dataset.state = state;
}

function addText(parent, tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) {
    element.className = className;
  }
  element.textContent = text;
  parent.append(element);
  return element;
}

function renderCitation(citation, index) {
  const item = document.createElement("li");
  item.className = "citation-card";

  const header = document.createElement("div");
  header.className = "citation-card-header";
  addText(header, "span", "citation-index", String(index + 1).padStart(2, "0"));
  const title = document.createElement("p");
  title.className = "citation-title";
  title.textContent = citationTitle(citation) || "DART 공시 근거";
  if (typeof citation.filed_at === "string" && citation.filed_at) {
    addText(title, "span", "citation-date", citation.filed_at);
  }
  header.append(title);
  item.append(header);

  if (typeof citation.rcept_no === "string" && citation.rcept_no) {
    const receipt = document.createElement("div");
    receipt.className = "receipt-row";
    addText(receipt, "span", "", "접수번호");
    addText(receipt, "code", "", citation.rcept_no);
    item.append(receipt);
  }

  const filingUrl = dartUrl(citation.rcept_no);
  if (filingUrl) {
    const link = document.createElement("a");
    link.className = "dart-link";
    link.href = filingUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "DART 원문 열기 ↗";
    item.append(link);
  }
  return item;
}

function renderCitations(parent, citations) {
  if (!Array.isArray(citations) || citations.length === 0) {
    return;
  }
  const section = document.createElement("section");
  section.className = "citation-section";
  const heading = document.createElement("div");
  heading.className = "citation-heading";
  addText(heading, "h4", "", "근거 공시");
  addText(heading, "span", "", `${citations.length}건의 검증된 citation`);
  section.append(heading);

  const list = document.createElement("ol");
  list.className = "citation-list";
  citations.forEach((citation, index) => list.append(renderCitation(citation, index)));
  section.append(list);
  parent.append(section);
}

function renderMeta(parent, message) {
  const values = [
    ["선택 Tool", message.tool_name],
    ["응답 시간", typeof message.latency_ms === "number" ? `${message.latency_ms} ms` : null],
    ["Prompt", message.prompt_version],
  ].filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (values.length === 0) {
    return;
  }
  const strip = document.createElement("div");
  strip.className = "meta-strip";
  for (const [label, value] of values) {
    const row = document.createElement("span");
    row.append(document.createTextNode(`${label} `));
    addText(row, "code", "", String(value));
    strip.append(row);
  }
  parent.append(strip);
}

function renderAssistant(message) {
  const item = document.createElement("li");
  item.className = "message message-assistant";
  const answered = message.answer_state === "answered";
  const currentEvidenceStatus = message.evidence_status || "unknown";

  const header = document.createElement("div");
  header.className = "answer-header";
  addText(header, "h3", "", answered ? "공시 기반 답변" : "답변을 제공할 수 없습니다");
  addText(
    header,
    "span",
    `answer-badge ${answered ? "is-answered" : "is-blocked"}`,
    answered ? "답변 허용" : "답변 불가",
  );
  addText(
    header,
    "span",
    `evidence-badge is-${currentEvidenceStatus}`,
    evidenceStatusLabel(currentEvidenceStatus),
  );
  item.append(header);

  if (answered) {
    const body = document.createElement("div");
    body.className = "answer-body";
    addText(body, "p", "", message.text);
    item.append(body);
  } else {
    const panel = document.createElement("div");
    panel.className = "blocked-panel";
    addText(panel, "strong", "", "근거 검증 단계에서 답변이 차단되었습니다.");
    addText(panel, "p", "", message.blocked_reason || message.text);
    item.append(panel);
  }

  renderCitations(item, message.citations);
  renderMeta(item, message);
  return item;
}

function renderMessage(message) {
  if (message.role === "assistant") {
    return renderAssistant(message);
  }
  const item = document.createElement("li");
  item.className = "message message-user";
  addText(item, "p", "", message.text);
  return item;
}

function renderEmptyState() {
  const item = document.createElement("li");
  item.className = "empty-state";
  addText(item, "div", "empty-icon", "D");
  addText(item, "h2", "", "공시에서 확인하고 싶은 내용을 질문하세요");
  addText(
    item,
    "p",
    "",
    "회사명, 기간, 재무계정 또는 공시 유형을 함께 입력하면 더 정확하게 찾을 수 있습니다.",
  );
  messages.append(item);
}

function renderLoading() {
  if (!requestPending || !selectedConversationId) {
    return;
  }
  const item = document.createElement("li");
  item.className = "loading-card";
  item.setAttribute("role", "status");
  const dots = document.createElement("span");
  dots.className = "loading-dots";
  for (let index = 0; index < 3; index += 1) {
    dots.append(document.createElement("span"));
  }
  dots.setAttribute("aria-hidden", "true");
  item.append(dots, document.createTextNode("HCX가 공시 Tool을 선택하고 근거를 확인하고 있습니다."));
  messages.append(item);
}

function renderFailure() {
  if (!transientFailure || transientFailure.conversationId !== selectedConversationId) {
    return;
  }
  const item = document.createElement("li");
  item.className = "message message-error";
  item.setAttribute("role", "alert");
  addText(item, "p", "error-title", "요청을 완료하지 못했습니다.");
  addText(item, "p", "", transientFailure.error.message);
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
  renderLoading();
  renderFailure();
  window.requestAnimationFrame(() => {
    messages.lastElementChild?.scrollIntoView({behavior: "auto", block: "nearest"});
  });
}

function renderConversations() {
  conversationList.replaceChildren();
  for (const conversation of searchConversations(store, historySearch.value)) {
    const item = document.createElement("li");
    item.className = "conversation-row";

    const select = document.createElement("button");
    select.type = "button";
    select.className = "conversation-select";
    select.textContent = conversation.title || "제목 없는 질문";
    select.setAttribute("aria-current", String(conversation.id === selectedConversationId));
    select.addEventListener("click", () => {
      selectedConversationId = conversation.id;
      transientFailure = null;
      if (isMobile()) {
        closeSidebar(true);
      }
      render();
    });

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "conversation-delete";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `${conversation.title || "제목 없는 질문"} 삭제`);
    remove.addEventListener("click", () => {
      store = deleteConversation(store, conversation.id);
      if (selectedConversationId === conversation.id) {
        selectedConversationId = store.conversations[0]?.id ?? null;
        transientFailure = null;
      }
      saveStore();
      render();
    });
    item.append(select, remove);
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
  sendLabel.textContent = pending ? "확인 중" : "질문하기";
  setServiceStatus(loadingLabel(pending), pending ? "busy" : "idle");
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
    const answerState = classifyAnswer(body);
    store = appendMessage(store, conversationId, {
      role: "assistant",
      text: answerText(body),
      created_at: new Date().toISOString(),
      answer_state: answerState,
      status: body.status,
      answer_allowed: body.answer_allowed,
      evidence_status: evidenceStatus(body),
      blocked_reason: answerState === "blocked" ? blockedReason(body) : null,
      citations: Array.isArray(body.citations) ? body.citations : [],
      tool_name: typeof body.tool_name === "string" ? body.tool_name : null,
      latency_ms: typeof body.latency_ms === "number" ? body.latency_ms : null,
      prompt_version: typeof body.metadata?.prompt_version === "string"
        ? body.metadata.prompt_version
        : null,
    });
    saveStore();
    setServiceStatus(answerState === "answered" ? "답변 완료" : "근거 부족", "ready");
  } catch (error) {
    const safeError = error
      && typeof error.message === "string"
      && typeof error.retryable === "boolean"
      ? error
      : {kind: "client_error", message: "응답을 처리하지 못했습니다.", retryable: true};
    transientFailure = {conversationId, question: text, error: safeError};
    setServiceStatus("요청 오류", "error");
  } finally {
    window.clearTimeout(timeout);
    requestPending = false;
    sendQuestion.disabled = false;
    questionInput.disabled = false;
    questionForm.setAttribute("aria-busy", "false");
    messages.setAttribute("aria-busy", "false");
    sendLabel.textContent = "질문하기";
    render();
    questionInput.focus();
  }
}

newChat.addEventListener("click", () => {
  selectedConversationId = null;
  transientFailure = null;
  questionInput.value = "";
  setServiceStatus("질문 준비됨", "idle");
  if (isMobile()) {
    closeSidebar(true);
  }
  render();
  questionInput.focus();
});

historySearch.addEventListener("input", renderConversations);

clearHistory.addEventListener("click", () => {
  if (!window.confirm("이 브라우저에 저장된 질문 기록을 모두 삭제할까요?")) {
    return;
  }
  store = clearConversations();
  selectedConversationId = null;
  transientFailure = null;
  saveStore();
  render();
});

sidebarToggle.addEventListener("click", () => {
  const open = isMobile()
    ? !sidebar.classList.contains("sidebar-open")
    : appShell.classList.contains("sidebar-collapsed");
  setSidebarOpen(open);
});

sidebarClose.addEventListener("click", () => closeSidebar(true));

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && sidebar.classList.contains("sidebar-open")) {
    closeSidebar(true);
  }
});

for (const button of exampleButtons) {
  button.addEventListener("click", () => {
    questionInput.value = button.dataset.example || "";
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
