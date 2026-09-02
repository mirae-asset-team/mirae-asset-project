import test from "node:test";
import assert from "node:assert/strict";

import {
  appendMessage,
  clearConversations,
  createConversation,
  deleteConversation,
  emptyStore,
  normalizeStore,
  searchConversations,
} from "../src/disclosure_db/web/history.js";


test("creates a normalized title and finds assistant message text", () => {
  let store = createConversation(
    emptyStore(),
    "  삼성전자   매출액을 알려줘  ",
    "2026-08-20T00:00:00.000Z",
    "c1",
  );
  store = appendMessage(store, "c1", {
    role: "assistant",
    text: "검증된 답변 본문",
    created_at: "2026-08-20T00:00:01.000Z",
  });

  assert.equal(store.conversations[0].title, "삼성전자 매출액을 알려줘");
  assert.equal(store.conversations[0].messages[0].text, "삼성전자 매출액을 알려줘");
  assert.equal(searchConversations(store, "답변").length, 1);
  assert.equal(searchConversations(store, "없는 문구").length, 0);
});


test("preserves Function Calling evidence in saved assistant messages", () => {
  let store = createConversation(
    emptyStore(),
    "삼성전자 매출액",
    "2026-08-20T00:00:00.000Z",
    "c1",
  );
  store = appendMessage(store, "c1", {
    role: "assistant",
    text: "검증된 답변",
    created_at: "2026-08-20T00:00:01.000Z",
    status: "answered",
    answer_allowed: true,
    evidence_status: "sufficient",
    tool_name: "get_financial_facts",
    latency_ms: 842,
    citations: [{
      evidence_id: "ev-1",
      rcept_no: "20250318000001",
      report_name: "사업보고서",
    }],
  });

  const saved = normalizeStore(JSON.parse(JSON.stringify(store)));
  const assistant = saved.conversations[0].messages[1];
  assert.equal(assistant.answer_allowed, true);
  assert.equal(assistant.evidence_status, "sufficient");
  assert.equal(assistant.tool_name, "get_financial_facts");
  assert.equal(assistant.latency_ms, 842);
  assert.equal(assistant.citations[0].rcept_no, "20250318000001");
});


test("rejects an unknown or malformed store version", () => {
  assert.deepEqual(normalizeStore({version: 2, conversations: []}), emptyStore());
  assert.deepEqual(normalizeStore({version: 1, conversations: "bad"}), emptyStore());
  assert.deepEqual(
    normalizeStore({version: 1, conversations: [{id: "c1", messages: "bad"}]}),
    emptyStore(),
  );
});


test("evicts the oldest conversation and oldest messages", () => {
  let store = emptyStore();
  for (let index = 0; index < 51; index += 1) {
    store = createConversation(
      store,
      `질문 ${index}`,
      new Date(index * 1000).toISOString(),
      `c${index}`,
    );
  }

  assert.equal(store.conversations.length, 50);
  assert.equal(store.conversations.some((item) => item.id === "c0"), false);

  for (let index = 0; index < 101; index += 1) {
    store = appendMessage(store, "c50", {
      role: "assistant",
      text: `답 ${index}`,
      created_at: new Date((100 + index) * 1000).toISOString(),
    });
  }

  const selected = store.conversations.find((item) => item.id === "c50");
  assert.equal(selected.messages.length, 100);
  assert.equal(selected.messages[0].text, "답 1");
});


test("deletes one conversation or clears the store", () => {
  let store = createConversation(emptyStore(), "첫 질문", "2026-08-20T00:00:00.000Z", "c1");
  store = createConversation(store, "둘째 질문", "2026-08-20T00:00:01.000Z", "c2");

  const deleted = deleteConversation(store, "c2");
  assert.deepEqual(deleted.conversations.map((item) => item.id), ["c1"]);
  assert.deepEqual(clearConversations(), emptyStore());
});
