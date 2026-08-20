export function classifyAnswer(body) {
  return body.answerable === true && body.verified === true ? "verified" : "abstained";
}

export function answerText(body) {
  if (
    Array.isArray(body.reason_codes)
    && body.reason_codes.includes("corpus_wide_financial_coverage_required")
  ) {
    return "현재 검증된 재무 데이터가 전체 기업을 포괄하지 않아 기업 수 집계를 제공할 수 없습니다.";
  }
  return body.answer;
}

export function healthLabel(body) {
  if (body.ready !== true) {
    return "공시 DB 준비 중";
  }
  const count = Number.isInteger(body.company_count) && body.company_count >= 0
    ? body.company_count
    : 0;
  return `공시 DB 준비됨 · ${count}개 기업`;
}

export function providerLabel(configured) {
  return configured === true
    ? "HyperCLOVA X 설명 연결됨"
    : "검증형 기본 엔진 사용 · HyperCLOVA X 설명 미사용";
}

export function locatorLabel(locator) {
  if (!locator || typeof locator !== "object") {
    return "";
  }
  const parts = [];
  if (locator.table !== undefined && locator.table !== null) {
    parts.push(`표 ${locator.table}`);
  }
  if (locator.row !== undefined && locator.row !== null) {
    parts.push(`행 ${locator.row}`);
  }
  if (parts.length > 0) {
    return parts.join(" · ");
  }
  if (locator.page !== undefined && locator.page !== null) {
    return `${locator.page}쪽`;
  }
  if (typeof locator.sheet === "string" && locator.sheet) {
    return locator.sheet;
  }
  return "";
}

export function dartUrl(receiptNo) {
  if (typeof receiptNo !== "string" || !/^\d{14}$/.test(receiptNo)) {
    return null;
  }
  return `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${receiptNo}`;
}

export function classifyFailure(status, detail) {
  if (status === 429) {
    return {
      kind: "rate_limited",
      message: "요청이 많습니다. 잠시 후 다시 시도해 주세요.",
      retryable: true,
    };
  }
  if (status === 503 && detail === "runtime_not_ready") {
    return {
      kind: "runtime_not_ready",
      message: "서버가 아직 준비되지 않았습니다.",
      retryable: true,
    };
  }
  if (status === 503 && detail === "server_busy") {
    return {
      kind: "server_busy",
      message: "서버 사용량이 많습니다. 잠시 후 다시 시도해 주세요.",
      retryable: true,
    };
  }
  if (status === 422) {
    return {
      kind: "invalid_question",
      message: "질문 형식을 확인해 주세요.",
      retryable: false,
    };
  }
  return {
    kind: "server_error",
    message: "서버 응답을 처리하지 못했습니다.",
    retryable: true,
  };
}

export function classifyTransportFailure(error) {
  if (error && error.name === "AbortError") {
    return {
      kind: "timeout",
      message: "응답 시간이 초과되었습니다.",
      retryable: true,
    };
  }
  return {
    kind: "network_error",
    message: "서버에 연결할 수 없습니다.",
    retryable: true,
  };
}

export function evidenceLabel(item) {
  return [item.report_name, item.filed_at, item.receipt_no].filter(Boolean).join(" · ");
}

export function createBrowserId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export async function askDisclosure(
  question,
  {fetchImpl = globalThis.fetch, signal, questionIdFactory = createBrowserId} = {},
) {
  const normalized = question.trim();
  if (!normalized || normalized.length > 4000) {
    throw {
      kind: "invalid_question",
      message: "질문은 1자 이상 4,000자 이하여야 합니다.",
      retryable: false,
    };
  }

  let response;
  try {
    response = await fetchImpl("/query", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question_id: questionIdFactory(), question: normalized}),
      signal,
    });
  } catch (error) {
    throw classifyTransportFailure(error);
  }

  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  let body = {};
  if (isJson) {
    try {
      body = await response.json();
    } catch {
      if (response.ok) {
        throw {
          kind: "invalid_response",
          message: "서버 응답 형식이 올바르지 않습니다.",
          retryable: true,
        };
      }
    }
  }

  if (!response.ok) {
    throw classifyFailure(response.status, body.detail);
  }
  if (
    typeof body.answer !== "string"
    || typeof body.answerable !== "boolean"
    || typeof body.verified !== "boolean"
  ) {
    throw {
      kind: "invalid_response",
      message: "서버 응답 형식이 올바르지 않습니다.",
      retryable: true,
    };
  }
  return body;
}
