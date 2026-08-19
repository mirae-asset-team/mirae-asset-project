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

const status = document.querySelector("#service-status");
const newChat = document.querySelector("#new-chat");
const clearHistory = document.querySelector("#clear-history");
const historySearch = document.querySelector("#history-search");
const conversationList = document.querySelector("#conversation-list");
const messages = document.querySelector("#messages");
const questionForm = document.querySelector("#question-form");
const questionInput = document.querySelector("#question-input");

let store = loadStore();
let selectedConversationId = store.conversations[0]?.id ?? null;

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
  }
}

function createId() {
  return `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function selectedConversation() {
  return store.conversations.find(({id}) => id === selectedConversationId) ?? null;
}

function renderMessages() {
  messages.replaceChildren();
  const conversation = selectedConversation();
  if (!conversation) {
    const empty = document.createElement("li");
    empty.className = "empty-message";
    empty.textContent = "새 질문을 입력하면 이 브라우저에 대화가 저장됩니다.";
    messages.append(empty);
    return;
  }

  for (const message of conversation.messages) {
    const item = document.createElement("li");
    item.className = `message message-${message.role}`;
    item.textContent = message.text;
    messages.append(item);
  }
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

newChat.addEventListener("click", () => {
  selectedConversationId = null;
  questionInput.value = "";
  questionInput.focus();
  render();
});

historySearch.addEventListener("input", renderConversations);

clearHistory.addEventListener("click", () => {
  if (!window.confirm("이 브라우저에 저장된 모든 대화 기록을 삭제할까요?")) {
    return;
  }
  store = clearConversations();
  selectedConversationId = null;
  saveStore();
  render();
});

questionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = questionInput.value.trim();
  if (!text) {
    return;
  }

  const now = new Date().toISOString();
  if (selectedConversationId) {
    store = appendMessage(store, selectedConversationId, {
      role: "user",
      text,
      created_at: now,
    });
  } else {
    selectedConversationId = createId();
    store = createConversation(store, text, now, selectedConversationId);
  }
  questionInput.value = "";
  saveStore();
  render();
});

status.textContent = "서버 상태 확인 중";
render();
