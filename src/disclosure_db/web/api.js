export const FUNCTION_ANSWER_ENDPOINT = "/v1/hcx/function-answer";
const VALID_EVIDENCE_STATES = new Set(["sufficient", "partial", "insufficient"]);

export function classifyAnswer(body) {
  return body?.status === "answered" && body?.answer_allowed === true
    ? "answered"
    : "blocked";
}

export function answerText(body) {
  return typeof body?.answer === "string" ? body.answer : "";
}

export function evidenceStatus(body) {
  const value = body?.tool_response?.metadata?.sufficiency_check?.status;
  return VALID_EVIDENCE_STATES.has(value) ? value : "unknown";
}

export function evidenceStatusLabel(status) {
  return {
    sufficient: "Evidence 충분",
    partial: "Evidence 일부",
    insufficient: "Evidence 부족",
    unknown: "Evidence 확인 불가",
  }[status] ?? "Evidence 확인 불가";
}

export function blockedReason(body) {
  if (body?.recommended_action === "ask_clarification") {
    return "회사명, 기간, 재무계정 또는 공시 범위를 더 구체적으로 입력해 주세요.";
  }
  if (body?.status === "provider_unavailable") {
    return "답변 생성 서비스가 설정되지 않았습니다. 운영 담당자에게 확인해 주세요.";
  }
  if (body?.status === "invalid_request") {
    return "질문 형식이나 Tool 입력을 확인해 주세요.";
  }
  if (body?.status === "error") {
    return "공시 근거를 확인했지만 최종 답변을 안전하게 생성하지 못했습니다.";
  }
  return "검증 가능한 공시 근거가 충분하지 않아 답변을 제공하지 않습니다.";
}

export function citationTitle(citation) {
  return [citation?.correction_role, citation?.report_name].filter(Boolean).join(" · ");
}

export function dartUrl(rceptNo) {
  if (typeof rceptNo !== "string" || !/^\d{14}$/.test(rceptNo)) {
    return null;
  }
  return `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${rceptNo}`;
}

export function loadingLabel(pending) {
  return pending ? "공시 근거 확인 중" : "질문 준비됨";
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
      message: "공시 데이터 서버가 아직 준비되지 않았습니다.",
      retryable: true,
    };
  }
  if (status === 503 && detail === "hcx_function_calling_not_configured") {
    return {
      kind: "function_calling_not_configured",
      message: "공시 Q&A 서비스가 아직 설정되지 않았습니다.",
      retryable: false,
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
      message: "질문은 1자 이상 2,000자 이하로 입력해 주세요.",
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
      message: "답변 대기 시간이 초과되었습니다.",
      retryable: true,
    };
  }
  return {
    kind: "network_error",
    message: "서버에 연결할 수 없습니다. 네트워크 상태를 확인해 주세요.",
    retryable: true,
  };
}

function isCitation(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

export function validateFunctionAnswer(body) {
  if (
    !body
    || typeof body !== "object"
    || typeof body.status !== "string"
    || typeof body.answer !== "string"
    || typeof body.answer_allowed !== "boolean"
    || !Array.isArray(body.citations)
    || !body.citations.every(isCitation)
    || (body.tool_name !== null && body.tool_name !== undefined && typeof body.tool_name !== "string")
  ) {
    return false;
  }
  if (body.answer_allowed === true) {
    return body.status === "answered"
      && body.citations.length > 0
      && body.citations.every((citation) => (
        typeof citation.evidence_id === "string"
        && /^\d{14}$/.test(citation.rcept_no)
      ));
  }
  return true;
}

export async function askDisclosure(
  question,
  {fetchImpl = globalThis.fetch, signal} = {},
) {
  const normalized = question.trim();
  if (!normalized || normalized.length > 2000) {
    throw {
      kind: "invalid_question",
      message: "질문은 1자 이상 2,000자 이하여야 합니다.",
      retryable: false,
    };
  }

  let response;
  try {
    response = await fetchImpl(FUNCTION_ANSWER_ENDPOINT, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({question: normalized}),
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
  if (!validateFunctionAnswer(body)) {
    throw {
      kind: "invalid_response",
      message: "검증 가능한 답변 형식을 받지 못했습니다.",
      retryable: true,
    };
  }
  return body;
}
