import test from "node:test";
import assert from "node:assert/strict";

import {
  FUNCTION_ANSWER_ENDPOINT,
  answerText,
  askDisclosure,
  blockedReason,
  citationTitle,
  classifyAnswer,
  classifyFailure,
  classifyTransportFailure,
  dartUrl,
  evidenceStatus,
  evidenceStatusLabel,
  loadingLabel,
  validateFunctionAnswer,
} from "../src/disclosure_db/web/api.js";

const answeredFixture = {
  status: "answered",
  answer: "삼성전자의 2024년 연결 매출액은 300조 원입니다.",
  tool_name: "get_financial_facts",
  tool_response: {metadata: {sufficiency_check: {status: "sufficient"}}},
  answer_allowed: true,
  recommended_action: "answer",
  citations: [{
    evidence_id: "ev-1",
    rcept_no: "20250318000001",
    report_name: "사업보고서",
    filed_at: "2025-03-18",
    correction_role: "최초공시",
  }],
  latency_ms: 842,
};

const abstainedFixture = {
  status: "abstained",
  answer: "",
  tool_name: "search_disclosures",
  tool_response: {metadata: {sufficiency_check: {status: "insufficient"}}},
  answer_allowed: false,
  recommended_action: "ask_clarification",
  citations: [],
  latency_ms: 41,
};

test("classifies answered and abstained Function Calling responses", () => {
  assert.equal(classifyAnswer(answeredFixture), "answered");
  assert.equal(answerText(answeredFixture), answeredFixture.answer);
  assert.equal(evidenceStatus(answeredFixture), "sufficient");
  assert.equal(evidenceStatusLabel("sufficient"), "Evidence 충분");
  assert.equal(classifyAnswer(abstainedFixture), "blocked");
  assert.equal(answerText(abstainedFixture), "");
  assert.equal(evidenceStatus(abstainedFixture), "insufficient");
  assert.match(blockedReason(abstainedFixture), /더 구체적으로/);
});

test("renders only structured citation fields supplied by the backend", () => {
  const citation = answeredFixture.citations[0];
  assert.equal(citationTitle(citation), "최초공시 · 사업보고서");
  assert.equal(
    dartUrl(citation.rcept_no),
    "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250318000001",
  );
  assert.equal(dartUrl("ev-1"), null);
  assert.equal(dartUrl("20250318000001&x=1"), null);
});

test("validates the answer gate and receipt-number invariant", () => {
  assert.equal(validateFunctionAnswer(answeredFixture), true);
  assert.equal(validateFunctionAnswer(abstainedFixture), true);
  assert.equal(validateFunctionAnswer({...answeredFixture, citations: []}), false);
  assert.equal(
    validateFunctionAnswer({
      ...answeredFixture,
      citations: [{evidence_id: "ev-1", rcept_no: "made-up"}],
    }),
    false,
  );
  assert.equal(validateFunctionAnswer({...answeredFixture, status: "abstained"}), false);
});

test("posts only the normalized question to the HCX Function Calling endpoint", async () => {
  let request;
  const body = await askDisclosure("  삼성전자 매출액  ", {
    fetchImpl: async (url, options) => {
      request = {url, options};
      return new Response(JSON.stringify(answeredFixture), {
        status: 200,
        headers: {"Content-Type": "application/json"},
      });
    },
  });

  assert.equal(FUNCTION_ANSWER_ENDPOINT, "/v1/hcx/function-answer");
  assert.equal(request.url, "/v1/hcx/function-answer");
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.headers.Accept, "application/json");
  assert.deepEqual(JSON.parse(request.options.body), {question: "삼성전자 매출액"});
  assert.equal(body.tool_name, "get_financial_facts");
});

test("maps API, timeout, and network failures without exposing internals", () => {
  assert.deepEqual(classifyFailure(429, "rate_limited"), {
    kind: "rate_limited",
    message: "요청이 많습니다. 잠시 후 다시 시도해 주세요.",
    retryable: true,
  });
  assert.equal(classifyFailure(503, "runtime_not_ready").kind, "runtime_not_ready");
  assert.equal(
    classifyFailure(503, "hcx_function_calling_not_configured").kind,
    "function_calling_not_configured",
  );
  assert.equal(classifyFailure(503, "server_busy").kind, "server_busy");
  assert.equal(classifyFailure(422, []).retryable, false);
  assert.equal(classifyFailure(500, "internal stack").kind, "server_error");
  assert.equal(classifyTransportFailure({name: "AbortError"}).kind, "timeout");
  assert.equal(classifyTransportFailure(new TypeError("fetch failed")).kind, "network_error");
});

test("reports loading state explicitly", () => {
  assert.equal(loadingLabel(true), "공시 근거 확인 중");
  assert.equal(loadingLabel(false), "질문 준비됨");
});

test("returns safe typed failures for invalid input and malformed responses", async () => {
  await assert.rejects(askDisclosure("   ", {fetchImpl: assert.fail}), {kind: "invalid_question"});
  await assert.rejects(
    askDisclosure("질문", {
      fetchImpl: async () => new Response("internal stack", {status: 500}),
    }),
    {kind: "server_error", message: "서버 응답을 처리하지 못했습니다."},
  );
  await assert.rejects(
    askDisclosure("질문", {
      fetchImpl: async () => new Response(JSON.stringify({answer: "답"}), {
        status: 200,
        headers: {"Content-Type": "application/json"},
      }),
    }),
    {kind: "invalid_response"},
  );
});
