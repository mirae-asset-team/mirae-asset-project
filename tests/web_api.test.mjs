import test from "node:test";
import assert from "node:assert/strict";

import {
  answerText,
  askDisclosure,
  classifyAnswer,
  classifyFailure,
  classifyTransportFailure,
  evidenceLabel,
} from "../src/disclosure_db/web/api.js";

test("explains incomplete corpus-wide financial counts without changing other answers", () => {
  assert.equal(
    answerText({
      answer: "검증에 실패하여 답변을 보류합니다.",
      reason_codes: ["company_unresolved", "corpus_wide_financial_coverage_required"],
    }),
    "현재 검증된 재무 데이터가 전체 기업을 포괄하지 않아 기업 수 집계를 제공할 수 없습니다.",
  );
  assert.equal(answerText({answer: "검증된 답변", reason_codes: []}), "검증된 답변");
  assert.equal(
    answerText({answer: "일반 보류", reason_codes: ["company_unresolved"]}),
    "일반 보류",
  );
});

test("distinguishes verified and abstained answers", () => {
  assert.equal(classifyAnswer({answerable: true, verified: true}), "verified");
  assert.equal(classifyAnswer({answerable: false, verified: false}), "abstained");
});

test("maps public failures without exposing internals", () => {
  assert.deepEqual(classifyFailure(429, "rate_limited"), {
    kind: "rate_limited",
    message: "요청이 많습니다. 잠시 후 다시 시도해 주세요.",
    retryable: true,
  });
  assert.equal(classifyFailure(503, "runtime_not_ready").kind, "runtime_not_ready");
});

test("uses only corpus-owned evidence metadata", () => {
  assert.equal(
    evidenceLabel({report_name: "사업보고서", filed_at: "2025-03-10", receipt_no: "f1"}),
    "사업보고서 · 2025-03-10 · f1",
  );
});

test("maps validation, busy, unknown, timeout, and network failures", () => {
  assert.equal(classifyFailure(422, []).retryable, false);
  assert.equal(classifyFailure(503, "server_busy").kind, "server_busy");
  assert.equal(classifyFailure(500, "internal").kind, "server_error");
  assert.equal(classifyTransportFailure({name: "AbortError"}).kind, "timeout");
  assert.equal(classifyTransportFailure(new TypeError("fetch failed")).kind, "network_error");
});

test("posts a normalized question to the same-origin query route", async () => {
  let request;
  const body = await askDisclosure("  삼성전자 매출액  ", {
    questionIdFactory: () => "web-test",
    fetchImpl: async (url, options) => {
      request = {url, options};
      return new Response(JSON.stringify({answer: "답", answerable: true, verified: true}), {
        status: 200,
        headers: {"Content-Type": "application/json"},
      });
    },
  });

  assert.equal(request.url, "/query");
  assert.equal(request.options.method, "POST");
  assert.deepEqual(JSON.parse(request.options.body), {
    question_id: "web-test",
    question: "삼성전자 매출액",
  });
  assert.equal(body.answer, "답");
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
      fetchImpl: async () => new Response(JSON.stringify({answer: 3}), {
        status: 200,
        headers: {"Content-Type": "application/json"},
      }),
    }),
    {kind: "invalid_response"},
  );
});
