import test from "node:test";
import assert from "node:assert/strict";

import {
  answerText,
  askDisclosure,
  classifyAnswer,
  classifyFailure,
  classifyTransportFailure,
  dartUrl,
  evidenceLabel,
  fetchFinancialCoverage,
  financialFactLabel,
  financialValueLabel,
  healthLabel,
  locatorLabel,
  metricCoverageRows,
  providerLabel,
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
  assert.equal(
    answerText({
      answer: "검증에 실패하여 답변을 보류합니다.",
      reason_codes: ["corpus_wide_financial_coverage_incomplete"],
      coverage: {
        snapshot: {source_company_count: 70},
        metrics: [{
          account_id: "operating_income",
          latest_validated_company_count: 67,
          missing_companies: ["KB금융", "하나금융지주", "한화솔루션"],
        }],
      },
    }),
    "영업이익은 70개 법인 중 67개가 검증되어 전체 집계를 보류합니다. 누락: KB금융, 하나금융지주, 한화솔루션",
  );
});

test("formats financial facts and metric coverage without losing raw filing labels", () => {
  assert.equal(
    financialFactLabel({
      fiscal_year: 2025,
      scope: "consolidated",
      account_id: "revenue",
      account_name_raw: "매출액 (주30)",
      unit_raw: "백만원",
    }),
    "2025년 · 연결 · 매출액 (주30) · 백만원",
  );
  assert.equal(
    financialValueLabel({value_numeric: "333605938", unit_raw: "백만원"}),
    "333,605,938 백만원",
  );
  assert.equal(
    financialValueLabel({value_numeric: "9007199254740993.50", unit_raw: "원"}),
    "9,007,199,254,740,993.50 원",
  );
  assert.deepEqual(
    metricCoverageRows({
      snapshot: {source_company_count: 70},
      metrics: [
        {account_id: "revenue", latest_validated_company_count: 63, aggregate_eligible: false},
        {account_id: "total_assets", latest_validated_company_count: 67, aggregate_eligible: false},
      ],
    }),
    [
      {account_id: "revenue", label: "매출 계열", validated: 63, expected: 70, complete: false},
      {account_id: "total_assets", label: "자산총계", validated: 67, expected: 70, complete: false},
    ],
  );
});

test("formats research readiness and provider mode without false warnings", () => {
  assert.equal(
    healthLabel({ready: true, company_count: 76}),
    "공시 DB 준비됨 · 검색명 76개",
  );
  assert.equal(healthLabel({ready: false, company_count: 76}), "공시 DB 준비 중");
  assert.equal(
    providerLabel(false),
    "검증형 기본 엔진 사용 · HyperCLOVA X 설명 미사용",
  );
  assert.equal(providerLabel(true), "HyperCLOVA X 설명 연결됨");
});

test("formats corpus locators and allows only valid DART receipt links", () => {
  assert.equal(
    locatorLabel({kind: "table_row", table: 7, row: 16}),
    "표 7 · 행 16",
  );
  assert.equal(locatorLabel({kind: "page", page: 3}), "3쪽");
  assert.equal(
    dartUrl("20250822000109"),
    "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250822000109",
  );
  assert.equal(dartUrl("f1"), null);
  assert.equal(dartUrl("20250822000109&x=1"), null);
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

test("loads attested coverage from the same-origin coverage route", async () => {
  let requestedUrl = null;
  const coverage = await fetchFinancialCoverage({
    fetchImpl: async (url) => {
      requestedUrl = url;
      return new Response(JSON.stringify({
        snapshot: {source_company_count: 70, searchable_alias_count: 76},
        metrics: [],
        companies: [],
      }), {status: 200, headers: {"Content-Type": "application/json"}});
    },
  });
  assert.equal(requestedUrl, "/financial-coverage");
  assert.equal(coverage.snapshot.source_company_count, 70);
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
