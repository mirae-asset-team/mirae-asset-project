# Anonymous Public Agent Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로그인 없이 누구나 사용할 수 있는 공시 에이전트 웹 채팅 화면을 기존 FastAPI/NCP 서비스에 추가하고, 브라우저별 대화 기록·근거 표시·익명 요청 제한·공개망 재배포를 검증한다.

**Architecture:** FastAPI가 package data로 포함된 정적 HTML/CSS/ES module을 같은 origin에서 제공하고 기존 `POST /query`를 호출한다. 대화 기록은 브라우저 `localStorage`에만 두며, Python 표준 라이브러리 기반 limiter middleware가 실제 socket client IP와 동시 처리량을 제한한다. 기존 planner/retrieval/generator/verifier와 read-only DB 세 파일은 변경하지 않는다.

**Tech Stack:** Python 3.11+, FastAPI/Starlette, setuptools package data, vanilla HTML/CSS/JavaScript ES modules, Node.js 20+ built-in test runner, pytest/unittest, Docker Compose, NCP.

**Spec:** `docs/superpowers/specs/2026-08-20-public-web-ui-design.md`

## Global Constraints

- 주소를 아는 사용자는 회원가입, 로그인, cookie, API key 없이 `GET /`와 `POST /query`를 사용한다.
- 웹 UI와 API는 같은 FastAPI 프로세스·origin에서 제공하며 별도 웹 서버, CDN, 외부 폰트, 분석 script를 추가하지 않는다.
- 질문과 답변은 서버 DB나 새 로그에 저장하지 않고 브라우저 `localStorage`에만 저장한다.
- D드라이브 원본 DB, live overlay, live search index와 NCP 사본은 read-only이며 hash, mtime, 파일 모드, mount gate를 낮추지 않는다.
- 기존 `POST /query` 요청/응답 schema와 `/v1/*` 동작을 회귀시키지 않는다.
- 사용자/API 문자열은 HTML로 삽입하지 않고 DOM `textContent`로만 렌더링한다.
- 정적 자산에는 inline/external script가 없어야 하며 CSP, `nosniff`, referrer, frame 보호 header를 반환한다.
- 기본 공개 제한은 IP별 120회/60초, IP별 동시 4회, 전체 동시 8회다. 임의 `X-Forwarded-For`는 신뢰하지 않는다.
- 데스크톱과 최소 360px 모바일에서 새 대화, 기록 검색, 질문, 답변, 근거, 삭제를 사용할 수 있어야 한다.
- 각 Task는 RED 확인, 최소 구현, focused test, 관련 회귀, 자체 검토, 한 개 커밋으로 끝낸다.
- NCP credential/session이 없으면 해당 외부 단계만 `BLOCKED_EXTERNAL`로 기록하고 로컬 구현·검증을 중단하지 않는다.
- 웹 UI 완료는 HyperCLOVA X provider schema/300-case gate 또는 최종 공모전 제출 GO를 의미하지 않는다.

## File Map

| 파일 | 책임 |
|---|---|
| `src/disclosure_db/api.py` | 루트/정적 경로, 보안 header, limiter middleware 연결 |
| `src/disclosure_db/public_limits.py` | rolling rate와 IP/global concurrency 상태 |
| `src/disclosure_db/web/index.html` | 접근 가능한 채팅 shell |
| `src/disclosure_db/web/app.css` | 데스크톱·360px 반응형 스타일 |
| `src/disclosure_db/web/history.js` | 순수 대화 store 생성·정규화·검색·삭제 |
| `src/disclosure_db/web/api.js` | `/query` 호출과 응답·오류 분류 |
| `src/disclosure_db/web/app.js` | DOM event, 안전한 렌더링, localStorage 연결 |
| `pyproject.toml` | `web/*` package data 포함 |
| `.env.example`, `compose.yaml` | 익명 요청 제한 기본값과 운영 override |
| `tests/test_public_web.py` | route, MIME, package data, CSP, 정적 계약 |
| `tests/test_public_limits.py` | rate/concurrency/실제 client IP 계약 |
| `tests/web_history.test.mjs` | 브라우저 대화 store 순수 함수 계약 |
| `tests/web_api.test.mjs` | API 응답·오류 분류 순수 함수 계약 |
| `scripts/smoke-agent.ps1` | 공개 UI·health·query 스모크 확장 |
| `docs/operations/contest-server.md` | 익명 UI 운영·요청 제한·장애 진단 |
| `docs/operations/contest-release-checklist.md` | 로컬/Docker/NCP 공개 UI evidence |
| `docs/development-log.md` | Task별 RED/GREEN/검증 기록 |

---

### Task 1: Serve a Packaged, Secure Web Shell

**Files:**
- Create: `src/disclosure_db/web/index.html`
- Create: `src/disclosure_db/web/app.css`
- Create: `src/disclosure_db/web/app.js`
- Modify: `src/disclosure_db/api.py:114-271`
- Modify: `pyproject.toml:1-27`
- Create: `tests/test_public_web.py`

**Interfaces:**
- Consumes: existing `create_app(agent)` and `GET /health`.
- Produces: `GET /`, `GET /static/app.css`, `GET /static/app.js`; DOM IDs `new-chat`, `history-search`, `conversation-list`, `messages`, `question-input`, `send-question`, `service-status`; `security_headers(request, call_next)` middleware.

- [ ] **Step 1: Write failing route and package-data tests**

```python
from pathlib import Path

from fastapi.testclient import TestClient

from disclosure_db.api import create_app


def test_root_serves_accessible_web_shell(ready_agent):
    response = TestClient(create_app(ready_agent)).get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    for element_id in (
        "new-chat", "history-search", "conversation-list", "messages",
        "question-input", "send-question", "service-status",
    ):
        assert f'id="{element_id}"' in response.text
    assert '<script type="module" src="/static/app.js"></script>' in response.text


def test_web_assets_are_declared_as_package_data():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.setuptools.package-data]" in text
    assert 'disclosure_db = ["web/*.html", "web/*.css", "web/*.js"]' in text
```

Use a `ready_agent` fixture in the same file with a temporary base SQLite path, `overlay_database=None`, `search_database=None`, and a deterministic `VerifiedAnswer`, matching `tests/test_agent_runtime.py`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_public_web.py -q`

Expected: FAIL because `/` returns `404` and package data is absent.

- [ ] **Step 3: Add the minimal HTML shell and placeholder-safe local assets**

`index.html` must contain semantic `aside`, `main`, `ol`, `form`, `textarea`, `button`, and an `aria-live="polite"` status element. It must reference only `/static/app.css` and `/static/app.js`; do not add inline script, inline event handlers, external URL, or user content.

```html
<form id="question-form">
  <label class="sr-only" for="question-input">공시 질문</label>
  <textarea id="question-input" maxlength="4000" required></textarea>
  <button id="send-question" type="submit">질문하기</button>
</form>
<script type="module" src="/static/app.js"></script>
```

Start `app.css` with responsive shell variables and a 360px media query. Start `app.js` with only shell event wiring and `textContent` assignments; no API call is added in this Task.

- [ ] **Step 4: Package and serve the assets**

Add:

```toml
[tool.setuptools.package-data]
disclosure_db = ["web/*.html", "web/*.css", "web/*.js"]
```

In `create_app`, import `FileResponse` and `StaticFiles`, resolve `web_dir = Path(__file__).with_name("web")`, mount `/static`, and return `web_dir / "index.html"` from `GET /`.

Add a middleware that sets exactly these baseline headers on all responses:

```python
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
```

- [ ] **Step 5: Add security and MIME assertions, then verify GREEN**

```python
def test_public_assets_have_security_headers_and_local_sources(ready_agent):
    client = TestClient(create_app(ready_agent))
    root = client.get("/")
    assert "frame-ancestors 'none'" in root.headers["content-security-policy"]
    assert root.headers["x-content-type-options"] == "nosniff"
    assert "http://" not in root.text and "https://" not in root.text
    assert client.get("/static/app.css").headers["content-type"].startswith("text/css")
    assert "javascript" in client.get("/static/app.js").headers["content-type"]
```

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_public_web.py tests/test_agent_runtime.py -q`

Expected: all focused and API regression tests PASS.

- [ ] **Step 6: Commit Task 1**

```powershell
git add pyproject.toml src/disclosure_db/api.py src/disclosure_db/web/index.html src/disclosure_db/web/app.css src/disclosure_db/web/app.js tests/test_public_web.py
git commit -m "feat: serve public disclosure agent web shell"
```

### Task 2: Add Browser-Local Conversation History

**Files:**
- Create: `src/disclosure_db/web/history.js`
- Modify: `src/disclosure_db/web/app.js`
- Create: `tests/web_history.test.mjs`
- Modify: `tests/test_public_web.py`

**Interfaces:**
- Consumes: DOM IDs from Task 1.
- Produces: `STORE_VERSION`, `MAX_CONVERSATIONS`, `MAX_MESSAGES`, `emptyStore()`, `normalizeStore(value)`, `createConversation(store, question, now, id)`, `appendMessage(store, conversationId, message)`, `searchConversations(store, query)`, `deleteConversation(store, conversationId)`, `clearConversations()`.

- [ ] **Step 1: Write failing Node tests for store boundaries**

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import {
  appendMessage, createConversation, emptyStore, normalizeStore,
  searchConversations,
} from "../src/disclosure_db/web/history.js";

test("creates a bounded title and finds message text", () => {
  let store = createConversation(emptyStore(), "  삼성전자   매출액을 알려줘  ", "2026-08-20T00:00:00.000Z", "c1");
  store = appendMessage(store, "c1", {role: "assistant", text: "답변 본문", created_at: "2026-08-20T00:00:01.000Z"});
  assert.equal(store.conversations[0].title, "삼성전자 매출액을 알려줘");
  assert.equal(searchConversations(store, "답변").length, 1);
});

test("rejects an unknown or malformed store version", () => {
  assert.deepEqual(normalizeStore({version: 2, conversations: []}), emptyStore());
  assert.deepEqual(normalizeStore({version: 1, conversations: "bad"}), emptyStore());
});
```

Add these boundary cases:

```javascript
test("evicts the oldest conversation and oldest messages", () => {
  let store = emptyStore();
  for (let index = 0; index < 51; index += 1) {
    store = createConversation(store, `질문 ${index}`, new Date(index * 1000).toISOString(), `c${index}`);
  }
  assert.equal(store.conversations.length, 50);
  assert.equal(store.conversations.some((item) => item.id === "c0"), false);
  for (let index = 0; index < 101; index += 1) {
    store = appendMessage(store, "c50", {role: "assistant", text: `답 ${index}`, created_at: new Date((100 + index) * 1000).toISOString()});
  }
  const selected = store.conversations.find((item) => item.id === "c50");
  assert.equal(selected.messages.length, 100);
  assert.equal(selected.messages[0].text, "답 1");
});
```

- [ ] **Step 2: Run the Node tests and verify RED**

Run: `node --experimental-default-type=module --test tests/web_history.test.mjs`

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `history.js`.

- [ ] **Step 3: Implement immutable, pure history functions**

Use no DOM or `localStorage` access in `history.js`. Every mutator returns a new object whose shape is `{version: 1, conversations: Array}`. Normalize whitespace with `question.trim().replace(/\s+/g, " ")`, title with `.slice(0, 40)`, order by descending `updated_at`, cap at 50 conversations and 100 messages, and return `emptyStore()` for malformed input.

```javascript
export const STORE_VERSION = 1;
export const MAX_CONVERSATIONS = 50;
export const MAX_MESSAGES = 100;

export function emptyStore() {
  return {version: STORE_VERSION, conversations: []};
}
```

- [ ] **Step 4: Connect localStorage and safe sidebar rendering**

In `app.js`, use key `mirae-disclosure-agent-history-v1`. Parse with `normalizeStore`, render titles using `document.createElement` and `textContent`, and persist only after a successful pure state transition. Implement new conversation, selection, single delete, confirmed clear-all, and case-insensitive search. Never use `innerHTML`, `insertAdjacentHTML`, `outerHTML`, `eval`, or `new Function`.

- [ ] **Step 5: Add static safety assertions and run GREEN**

```python
def test_frontend_never_uses_html_injection_sinks():
    scripts = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("src/disclosure_db/web/app.js", "src/disclosure_db/web/history.js")
    )
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function"):
        assert forbidden not in scripts
```

Run: `node --experimental-default-type=module --test tests/web_history.test.mjs`

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_public_web.py -q`

Expected: both commands PASS.

- [ ] **Step 6: Commit Task 2**

```powershell
git add src/disclosure_db/web/history.js src/disclosure_db/web/app.js tests/web_history.test.mjs tests/test_public_web.py
git commit -m "feat: keep anonymous chat history in the browser"
```

### Task 3: Connect the Chat UI to Verified Answers

**Files:**
- Create: `src/disclosure_db/web/api.js`
- Modify: `src/disclosure_db/web/app.js`
- Modify: `src/disclosure_db/web/app.css`
- Modify: `src/disclosure_db/web/index.html`
- Create: `tests/web_api.test.mjs`
- Modify: `tests/test_public_web.py`

**Interfaces:**
- Consumes: `POST /query`, `GET /health`, and Task 2 history functions.
- Produces: `askDisclosure(question, options) -> Promise<object>`, `classifyAnswer(body) -> "verified"|"abstained"`, `classifyFailure(status, detail) -> {kind, message, retryable}`, `classifyTransportFailure(error) -> {kind, message, retryable}`, `evidenceLabel(item) -> string`.

- [ ] **Step 1: Write failing API classification tests**

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import {classifyAnswer, classifyFailure, classifyTransportFailure, evidenceLabel} from "../src/disclosure_db/web/api.js";

test("distinguishes verified and abstained answers", () => {
  assert.equal(classifyAnswer({answerable: true, verified: true}), "verified");
  assert.equal(classifyAnswer({answerable: false, verified: false}), "abstained");
});

test("maps public failures without exposing internals", () => {
  assert.deepEqual(classifyFailure(429, "rate_limited"), {
    kind: "rate_limited", message: "요청이 많습니다. 잠시 후 다시 시도해 주세요.", retryable: true,
  });
  assert.equal(classifyFailure(503, "runtime_not_ready").kind, "runtime_not_ready");
});

test("uses only corpus-owned evidence metadata", () => {
  assert.equal(evidenceLabel({report_name: "사업보고서", filed_at: "2025-03-10", receipt_no: "f1"}), "사업보고서 · 2025-03-10 · f1");
});
```

Add these cases:

```javascript
test("maps validation, busy, unknown, timeout, and network failures", () => {
  assert.equal(classifyFailure(422, []).retryable, false);
  assert.equal(classifyFailure(503, "server_busy").kind, "server_busy");
  assert.equal(classifyFailure(500, "internal").kind, "server_error");
  assert.equal(classifyTransportFailure({name: "AbortError"}).kind, "timeout");
  assert.equal(classifyTransportFailure(new TypeError("fetch failed")).kind, "network_error");
});
```

- [ ] **Step 2: Run the Node tests and verify RED**

Run: `node --experimental-default-type=module --test tests/web_api.test.mjs`

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `api.js`.

- [ ] **Step 3: Implement the API module**

`askDisclosure` trims and validates 1..4,000 characters, creates a browser question ID, and calls same-origin `/query` with JSON. Accept injected `fetchImpl` and `AbortSignal` for tests. Parse error JSON only when content type is JSON, classify known failures, and never return raw stack/body text as a user message.

```javascript
export function classifyAnswer(body) {
  return body.answerable === true && body.verified === true ? "verified" : "abstained";
}

export function classifyFailure(status, detail) {
  if (status === 429) return {kind: "rate_limited", message: "요청이 많습니다. 잠시 후 다시 시도해 주세요.", retryable: true};
  if (status === 503 && detail === "runtime_not_ready") return {kind: "runtime_not_ready", message: "서버가 아직 준비되지 않았습니다.", retryable: true};
  if (status === 503 && detail === "server_busy") return {kind: "server_busy", message: "서버 사용량이 많습니다. 잠시 후 다시 시도해 주세요.", retryable: true};
  if (status === 422) return {kind: "invalid_question", message: "질문 형식을 확인해 주세요.", retryable: false};
  return {kind: "server_error", message: "서버 응답을 처리하지 못했습니다.", retryable: true};
}

export function classifyTransportFailure(error) {
  if (error && error.name === "AbortError") return {kind: "timeout", message: "응답 시간이 초과되었습니다.", retryable: true};
  return {kind: "network_error", message: "서버에 연결할 수 없습니다.", retryable: true};
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
  {fetchImpl = fetch, signal, questionIdFactory = createBrowserId} = {},
) {
  const normalized = question.trim();
  if (!normalized || normalized.length > 4000) {
    throw {kind: "invalid_question", message: "질문은 1자 이상 4,000자 이하여야 합니다.", retryable: false};
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
  const body = isJson ? await response.json() : {};
  if (!response.ok) {
    throw classifyFailure(response.status, body.detail);
  }
  if (typeof body.answer !== "string" || typeof body.answerable !== "boolean" || typeof body.verified !== "boolean") {
    throw {kind: "invalid_response", message: "서버 응답 형식이 올바르지 않습니다.", retryable: true};
  }
  return body;
}
```

- [ ] **Step 4: Implement safe chat and evidence rendering**

Wire form submit to disable duplicate submission, append the user message, call `askDisclosure`, append a verified/abstained assistant message, and persist it. Build every message and evidence card with `createElement`/`textContent`. Show `report_name`, `filed_at`, `receipt_no`, and serialized locator entries only when present. Show `request_id`, `latency_ms`, and `reason_codes` in a collapsed `<details>` element.

On retryable errors, retain the user message and add a retry button that reuses the exact question once. Do not save transport errors as assistant answers. Fetch `/health` on load and display `준비됨`, `준비 중`, and `HyperCLOVA X 미연결` without claiming final submission readiness.

- [ ] **Step 5: Finish responsive and accessible behavior**

Add a mobile sidebar toggle, focus return, 360px media query, `Enter` submit/`Shift+Enter` newline, `aria-busy`, and `aria-live`. Use text labels with status colors. Add example-question buttons that only fill the textarea; they must not auto-submit.

- [ ] **Step 6: Verify API and static contracts GREEN**

Run: `node --experimental-default-type=module --test tests/web_history.test.mjs tests/web_api.test.mjs`

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_public_web.py tests/test_agent_runtime.py -q`

Expected: all tests PASS, existing `/query` mapping remains unchanged.

- [ ] **Step 7: Commit Task 3**

```powershell
git add src/disclosure_db/web/index.html src/disclosure_db/web/app.css src/disclosure_db/web/app.js src/disclosure_db/web/api.js tests/web_api.test.mjs tests/test_public_web.py
git commit -m "feat: add verified disclosure chat experience"
```

### Task 4: Enforce Anonymous Public Request Limits

**Files:**
- Create: `src/disclosure_db/public_limits.py`
- Create: `tests/test_public_limits.py`

**Interfaces:**
- Consumes: monotonic seconds and socket client IP strings.
- Produces: `PublicLimitSettings`, `Admission`, `PublicRequestLimiter.try_acquire(client_ip, now=None) -> Admission`, `PublicRequestLimiter.release(client_ip) -> None`.

- [ ] **Step 1: Write failing rate and concurrency tests**

```python
import pytest

from disclosure_db.public_limits import PublicLimitSettings, PublicRequestLimiter


def test_rate_limit_reopens_after_rolling_window():
    limiter = PublicRequestLimiter(PublicLimitSettings(per_minute=2, per_ip_concurrency=4, global_concurrency=8))
    first = limiter.try_acquire("198.51.100.1", now=0.0)
    limiter.release("198.51.100.1")
    second = limiter.try_acquire("198.51.100.1", now=1.0)
    limiter.release("198.51.100.1")
    denied = limiter.try_acquire("198.51.100.1", now=2.0)
    assert first.allowed and second.allowed
    assert not denied.allowed and denied.status_code == 429 and denied.detail == "rate_limited"
    assert limiter.try_acquire("198.51.100.1", now=61.0).allowed


def test_per_ip_and_global_concurrency_release_cleanly():
    limiter = PublicRequestLimiter(PublicLimitSettings(per_minute=120, per_ip_concurrency=1, global_concurrency=2))
    assert limiter.try_acquire("198.51.100.1", now=0.0).allowed
    assert limiter.try_acquire("198.51.100.1", now=0.1).detail == "ip_busy"
    assert limiter.try_acquire("198.51.100.2", now=0.1).allowed
    assert limiter.try_acquire("198.51.100.3", now=0.1).detail == "server_busy"
    limiter.release("198.51.100.1")
    assert limiter.try_acquire("198.51.100.3", now=0.2).allowed
```

Add exact environment and cleanup cases:

```python
def test_limit_settings_parse_exact_environment_and_reject_disable_values():
    settings = PublicLimitSettings.from_env({
        "DISCLOSURE_PUBLIC_RATE_PER_MINUTE": "30",
        "DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY": "2",
        "DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY": "5",
    })
    assert settings == PublicLimitSettings(30, 2, 5)
    with pytest.raises(ValueError):
        PublicLimitSettings.from_env({"DISCLOSURE_PUBLIC_RATE_PER_MINUTE": "0"})
    with pytest.raises(ValueError):
        PublicLimitSettings.from_env({"DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY": "10001"})


def test_expired_idle_ip_state_is_removed_on_next_acquire():
    limiter = PublicRequestLimiter(PublicLimitSettings())
    assert limiter.try_acquire("198.51.100.1", now=0.0).allowed
    limiter.release("198.51.100.1")
    assert limiter.try_acquire("198.51.100.2", now=61.0).allowed
    assert limiter.tracked_ip_count == 1
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_public_limits.py -q`

Expected: FAIL with `ModuleNotFoundError: disclosure_db.public_limits`.

- [ ] **Step 3: Implement the locked rolling-window limiter**

Use `collections.deque`, `dataclasses`, `threading.Lock`, and `time.monotonic` only. `Admission` contains `allowed`, `status_code`, `detail`, and integer `retry_after`. Successful acquisition appends one timestamp and increments IP/global active counts atomically. `release` decrements active counts without going negative and removes an IP entry when it has no active requests and no live timestamps.

```python
import os
from collections import deque
from dataclasses import dataclass
from math import ceil
from threading import Lock
from time import monotonic
from typing import Callable, Mapping


@dataclass(frozen=True, slots=True)
class PublicLimitSettings:
    per_minute: int = 120
    per_ip_concurrency: int = 4
    global_concurrency: int = 8

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PublicLimitSettings":
        values = os.environ if env is None else env
        names = {
            "per_minute": "DISCLOSURE_PUBLIC_RATE_PER_MINUTE",
            "per_ip_concurrency": "DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY",
            "global_concurrency": "DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY",
        }
        defaults = cls()
        parsed = {
            field: int(values.get(name, str(getattr(defaults, field))))
            for field, name in names.items()
        }
        if any(value < 1 or value > 10_000 for value in parsed.values()):
            raise ValueError("public limit values must be between 1 and 10000")
        return cls(**parsed)


@dataclass(frozen=True, slots=True)
class Admission:
    allowed: bool
    status_code: int = 200
    detail: str = "ok"
    retry_after: int = 0


@dataclass(slots=True)
class _ClientState:
    timestamps: deque[float]
    active: int = 0


class PublicRequestLimiter:
    def __init__(
        self,
        settings: PublicLimitSettings,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.settings = settings
        self._clock = clock
        self._lock = Lock()
        self._clients: dict[str, _ClientState] = {}
        self._global_active = 0

    def try_acquire(self, client_ip: str, *, now: float | None = None) -> Admission:
        observed = self._clock() if now is None else now
        cutoff = observed - 60.0
        with self._lock:
            for ip, known in list(self._clients.items()):
                while known.timestamps and known.timestamps[0] <= cutoff:
                    known.timestamps.popleft()
                if known.active == 0 and not known.timestamps:
                    del self._clients[ip]
            state = self._clients.setdefault(client_ip, _ClientState(deque()))
            if len(state.timestamps) >= self.settings.per_minute:
                retry_after = max(1, ceil(state.timestamps[0] + 60.0 - observed))
                return Admission(False, 429, "rate_limited", retry_after)
            if state.active >= self.settings.per_ip_concurrency:
                return Admission(False, 429, "ip_busy", 1)
            if self._global_active >= self.settings.global_concurrency:
                if state.active == 0 and not state.timestamps:
                    del self._clients[client_ip]
                return Admission(False, 503, "server_busy", 1)
            state.timestamps.append(observed)
            state.active += 1
            self._global_active += 1
            return Admission(True)

    def release(self, client_ip: str) -> None:
        with self._lock:
            state = self._clients.get(client_ip)
            if state is None or state.active == 0:
                return
            state.active -= 1
            self._global_active = max(0, self._global_active - 1)

    @property
    def tracked_ip_count(self) -> int:
        with self._lock:
            return len(self._clients)
```

Environment names are exactly `DISCLOSURE_PUBLIC_RATE_PER_MINUTE`, `DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY`, and `DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY`. Invalid, zero, negative, or over-10,000 values raise `ValueError`; they never disable the gate.

- [ ] **Step 4: Run RED/GREEN and concurrency repetition**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_public_limits.py -q`

Run: `$env:PYTHONPATH='src'; 1..20 | ForEach-Object { python -m pytest tests/test_public_limits.py -q }`

Expected: all 20 repeated focused runs PASS with no leaked active counts.

- [ ] **Step 5: Commit Task 4**

```powershell
git add src/disclosure_db/public_limits.py tests/test_public_limits.py
git commit -m "feat: add bounded anonymous request limiter"
```

### Task 5: Integrate Limits with FastAPI and Deployment Configuration

**Files:**
- Modify: `src/disclosure_db/api.py:114-271`
- Modify: `compose.yaml:1-22`
- Modify: `.env.example`
- Modify: `tests/test_public_limits.py`
- Modify: `tests/test_agent_runtime.py:1-120`
- Modify: `tests/test_deployment_artifacts.py`

**Interfaces:**
- Consumes: `PublicLimitSettings.from_env()` and `PublicRequestLimiter` from Task 4.
- Produces: `create_app(agent, *, public_limits: PublicLimitSettings | None = None)`; middleware on `POST /query` and `POST /v1/answer` only.

- [ ] **Step 1: Write failing FastAPI integration tests**

```python
def test_public_query_uses_socket_ip_and_ignores_forwarded_header(ready_agent):
    settings = PublicLimitSettings(per_minute=1, per_ip_concurrency=4, global_concurrency=8)
    client = TestClient(create_app(ready_agent, public_limits=settings))
    assert client.post("/query", json={"question": "첫 질문"}, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    response = client.post("/query", json={"question": "둘째 질문"}, headers={"X-Forwarded-For": "2.2.2.2"})
    assert response.status_code == 429
    assert response.json()["detail"] == "rate_limited"
    assert response.headers["Retry-After"] == "60"


def test_health_and_static_assets_are_not_rate_limited(ready_agent):
    settings = PublicLimitSettings(per_minute=1, per_ip_concurrency=1, global_concurrency=1)
    client = TestClient(create_app(ready_agent, public_limits=settings))
    for _ in range(3):
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200
```

Add exact release cases. Two sequential successful requests with `per_ip_concurrency=1` must both return `200`. For exception release, use an agent whose `answer` raises, a client created with `raise_server_exceptions=False`, and assert two sequential requests both return `500` rather than the second returning `429 ip_busy`:

```python
def test_query_lease_releases_after_agent_exception(raising_agent):
    settings = PublicLimitSettings(per_minute=120, per_ip_concurrency=1, global_concurrency=1)
    client = TestClient(
        create_app(raising_agent, public_limits=settings),
        raise_server_exceptions=False,
    )
    assert client.post("/query", json={"question": "첫 실패"}).status_code == 500
    assert client.post("/query", json={"question": "둘째 실패"}).status_code == 500
```

- [ ] **Step 2: Run the integration tests and verify RED**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_public_limits.py tests/test_agent_runtime.py -q`

Expected: FAIL because `create_app` has no `public_limits` parameter and no middleware.

- [ ] **Step 3: Add middleware with fail-closed JSON responses**

For target paths only, read `request.client.host`; do not read `X-Forwarded-For`. Call `try_acquire`, return `JSONResponse({"detail": admission.detail}, status_code=admission.status_code, headers={"Retry-After": str(admission.retry_after)})` on denial, and release in `finally` after `await call_next(request)`.

Use `PublicLimitSettings.from_env()` only when the keyword argument is `None`, allowing deterministic tests to inject settings. Rate and IP busy return `429`; global busy returns `503 server_busy`.

- [ ] **Step 4: Add exact environment defaults to Compose and example env**

```yaml
DISCLOSURE_PUBLIC_RATE_PER_MINUTE: ${DISCLOSURE_PUBLIC_RATE_PER_MINUTE:-120}
DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY: ${DISCLOSURE_PUBLIC_PER_IP_CONCURRENCY:-4}
DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY: ${DISCLOSURE_PUBLIC_GLOBAL_CONCURRENCY:-8}
```

Add the same three names with values `120`, `4`, and `8` to `.env.example`. Do not add a login secret, cookie secret, or API key.

- [ ] **Step 5: Verify integration and deployment contracts GREEN**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_public_limits.py tests/test_public_web.py tests/test_agent_runtime.py tests/test_deployment_artifacts.py -q`

Run: `docker compose config`

Expected: tests PASS; rendered Compose contains exact three limit settings; DB mounts still end in `:ro`.

- [ ] **Step 6: Commit Task 5**

```powershell
git add src/disclosure_db/api.py compose.yaml .env.example tests/test_public_limits.py tests/test_agent_runtime.py tests/test_deployment_artifacts.py
git commit -m "feat: protect anonymous public query traffic"
```

### Task 6: Document and Automate Local UI Smoke

**Files:**
- Modify: `scripts/smoke-agent.ps1`
- Modify: `docs/operations/contest-server.md`
- Modify: `docs/development-log.md`
- Modify: `tests/test_deployment_artifacts.py`

**Interfaces:**
- Consumes: public `GET /`, `GET /health`, `POST /query`.
- Produces: smoke evidence for HTML shell, ready health, answerable query, abstention query; documented rate-limit tuning and local-history privacy boundary.

- [ ] **Step 1: Write a failing smoke-script contract test**

```python
def test_windows_smoke_checks_public_web_shell():
    smoke = Path("scripts/smoke-agent.ps1").read_text(encoding="utf-8")
    assert "text/html" in smoke
    assert "question-input" in smoke
    assert "Content-Security-Policy" in smoke
    assert "DISCLOSURE_PUBLIC_RATE_PER_MINUTE" in Path("docs/operations/contest-server.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the contract test and verify RED**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_deployment_artifacts.py::DeploymentArtifactTests::test_windows_smoke_checks_public_web_shell -q`

Expected: FAIL because the script checks only health/query.

- [ ] **Step 3: Extend the smoke script without writing service data**

Before existing health/query probes, request `/`, assert HTTP 200, `text/html`, `id="question-input"`, local module source, and CSP. Keep answerable, abstention, and injection checks. Output only status, request IDs, latency, and booleans; do not print provider keys, full public URL, raw corpus text, or admin password.

- [ ] **Step 4: Document anonymous operation and evidence**

Add task-oriented sections to `docs/operations/contest-server.md`:

- opening the public UI;
- browser-only history and clear-all behavior;
- exact three limit variables and `429`/`503` diagnosis;
- direct socket IP trust boundary;
- no login and no server-side conversation persistence;
- current final-submission NO-GO until provider gate passes.

Append Task 1-6 RED/GREEN commands and results to `docs/development-log.md`. Record measured outputs only.

- [ ] **Step 5: Run local fixture and full smoke prerequisites**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_deployment_artifacts.py tests/test_public_web.py tests/test_public_limits.py -q`

Run after starting the local agent with existing read-only paths: `powershell -ExecutionPolicy Bypass -File scripts/smoke-agent.ps1 -BaseUrl http://127.0.0.1:8000`

Expected: web, health, answerable, abstention, and injection probes PASS. If the real D-drive paths are unavailable, run the fixture app smoke and record `BLOCKED_LOCAL_RUNTIME` only for the real-path command.

- [ ] **Step 6: Commit Task 6**

```powershell
git add scripts/smoke-agent.ps1 docs/operations/contest-server.md docs/development-log.md tests/test_deployment_artifacts.py
git commit -m "docs: add anonymous web ui operations and smoke"
```

### Task 7: Run Full Local and Docker Release Gates

**Files:**
- Modify: `docs/operations/contest-release-checklist.md`
- Modify: `docs/development-log.md`

**Interfaces:**
- Consumes: Tasks 1-6 and existing 300-case deterministic runner.
- Produces: fresh unit, JavaScript, package, Docker, read-only hash, and deterministic stress evidence.

- [ ] **Step 1: Verify source and working-tree scope before expensive tests**

Run: `git status --short --branch`

Run: `git diff --check`

Expected: only intentional evidence-doc edits are uncommitted; no DB, credential, `.env`, or generated secret file is staged.

- [ ] **Step 2: Run all frontend and Python tests**

Run: `node --experimental-default-type=module --test tests/web_history.test.mjs tests/web_api.test.mjs`

Run: `$env:PYTHONPATH='src'; python -m pytest -q`

Run: `python -m compileall -q src scripts tests`

Expected: Node tests have 0 failures; Python suite has 0 failures with only documented optional skips; compileall exits 0.

- [ ] **Step 3: Verify package and image contents**

Build a wheel without dependencies into an ignored temporary directory:

```powershell
python -m pip wheel . --no-deps --wheel-dir tmp/web-wheel
python -c "import zipfile,glob; p=glob.glob('tmp/web-wheel/*.whl')[0]; names=zipfile.ZipFile(p).namelist(); assert all(any(n.endswith('/web/'+f) for n in names) for f in ['index.html','app.css','app.js','history.js','api.js'])"
```

Run: `docker compose build`

Run: `docker compose up -d`

Run: `powershell -ExecutionPolicy Bypass -File scripts/smoke-agent.ps1 -BaseUrl http://127.0.0.1:8000`

Expected: wheel has all five assets; container becomes healthy; UI/health/query smoke PASS.

- [ ] **Step 4: Re-run immutable and deterministic hard gates**

Before and after Docker smoke, record size/SHA-256/mtime for base, overlay, and search inputs and confirm Compose mounts remain `:ro`. Run the exact provider-disabled deterministic command against the existing immutable manifest:

```powershell
python scripts/evaluate_agent_stress.py --database D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite --overlay D:\mirae-asset-project\db\agent\agent_overlay.sqlite --search-index D:\mirae-asset-project\db\agent\agent_search.sqlite --attestation data\derived\database_distribution_manifest_semantic_v1.json --cases D:\mirae-asset-project\runs\evaluation\agent_stress_300.jsonl --contract config\stress_evaluation_contract.json --run-root D:\mirae-asset-project\runs\evaluation --summary data\derived\agent_stress_300_summary.json --failures data\derived\agent_stress_failures.jsonl --workers 1 --resume
```

Expected: `300/300` pass; all hard counters 0; three data artifacts unchanged. Any mismatch is a hard failure and stops deployment.

- [ ] **Step 5: Inspect the UI at desktop and 360px**

Open the local UI in an actual browser, submit one answerable and one abstention question, verify sidebar search/delete, inspect evidence cards, then repeat at a 360px viewport. Capture screenshots to an ignored runtime evidence directory; do not commit user history or a public IP.

Expected: no horizontal overflow, clipped controls, HTML injection, console error, or inaccessible keyboard path.

- [ ] **Step 6: Record measured gates and commit Task 7**

Update the release checklist and development log with exact test counts, wheel/image result, Docker request IDs, deterministic run ID, hashes, and any documented skip/block. Do not mark provider gates PASS.

```powershell
git add docs/operations/contest-release-checklist.md docs/development-log.md
git commit -m "docs: record public web ui local release gates"
```

### Task 8: Diagnose NCP Availability and Deploy the Verified Image

**Files:**
- Modify: `docs/operations/contest-release-checklist.md`
- Modify: `docs/development-log.md`

**Interfaces:**
- Consumes: Task 7 verified commit/image, existing NCP server/data disk/Compose deployment.
- Produces: root-cause evidence for the closed public ports, a recovered anonymous web endpoint when authorized access exists, restart evidence, and an honest GO/NO-GO boundary.

- [ ] **Step 1: Reproduce and isolate the external failure before changing state**

From the Windows development network, test TCP 22 and 8000 and `GET /health`. In the NCP console, read server state, public-IP attachment, ACG inbound rules, and data-disk attachment. Record only booleans/resource state; do not save admin passwords, PEM content, credential screenshots, or the public IP in tracked files.

Expected diagnostic branches:

- server stopped -> ports closed at compute layer;
- server running but public IP detached -> network layer;
- public IP attached but ACG lacks 8000 -> access-control layer;
- TCP 22 works and 8000 fails -> host/container layer;
- both ports work but health fails -> application/readiness layer.

- [ ] **Step 2: State one root-cause hypothesis and test the smallest variable**

Follow `superpowers:systematic-debugging`. Do not restart, reassign IP, edit ACG, or rebuild the container until console evidence identifies the failing boundary. If NCP login/session is unavailable, record `BLOCKED_EXTERNAL`, skip to Step 7, and do not use cached or chat-exposed credentials.

- [ ] **Step 3: Restore only the failed layer**

Examples of permitted single fixes after evidence:

- start the already-approved server when it is stopped;
- reattach its reserved public IP when detached;
- restore public TCP 8000 while retaining SSH `/32` restriction;
- restart only the Compose service when host and disk are healthy.

Do not create a second server, enlarge storage, relax SSH to the public internet, or replace the data volume. Starting the stopped server resumes NCP billing; record that operational fact.

- [ ] **Step 4: Re-attest remote data before deployment**

Verify `/srv/mirae` mount, all four immutable artifact modes `0444`, and the recorded base/overlay/search/attestation sizes and SHA-256 values. Verify Compose mounts are read-only. If hashes differ, stop; never overwrite from D as an automatic repair.

- [ ] **Step 5: Deploy code only**

Transfer a source archive from the exact Task 7 commit without `.git`, `.env`, runtime evidence, or DB files. Build the image on NCP, render Compose config, and replace only the application container. Reuse the existing mounted DB artifacts and current provider environment; do not inject the previously pasted HyperCLOVA credential.

- [ ] **Step 6: Run public and restart smoke**

From the external Windows network verify:

1. `GET /` returns HTML/CSP and contains the question input.
2. `GET /health` is ready and honestly reports provider configuration.
3. Answerable numeric, answerable text, out-of-scope, and injection `/query` cases preserve schema and safety.
4. Browser history/search/delete works and remains local to that browser.
5. A bounded test configuration proves `429`/`Retry-After`, then production defaults are restored and rechecked.
6. Container restart preserves UI/health/query.
7. Host reboot remounts data read-only and restores UI/health/query.

Expected: public UI and API PASS without login; no hash/mode change; no raw credential or question text in tracked evidence.

- [ ] **Step 7: Record final UI release decision and commit Task 8**

Update checklist/log with root cause, minimal fix, deployed commit, external request IDs, response times, restart/reboot status, read-only hashes/modes, and provider boolean. If external access remained blocked, state exactly which checks are not run.

Decision boundary:

- anonymous public web UI: GO only if Steps 4-6 pass;
- team demo: GO only if UI plus existing deterministic hard gates pass;
- final contest submission: remains NO-GO until rotated HyperCLOVA X schema smoke/provider 300-case gate and technical proposal are complete.

```powershell
git add docs/operations/contest-release-checklist.md docs/development-log.md
git commit -m "docs: record anonymous public web ui deployment"
```

## Post-Plan Handoff

After Task 8, do not stop the broader contest work. Start a separate spec/plan for the two independent final-submission tracks:

1. rotated HyperCLOVA X schema correction, provider 300-case quality/latency, and final API compliance;
2. technical proposal with problem definition, architecture, user scenario, measured evaluation, limitations, expected effect, and reproducible deployment instructions.

Neither track may reinterpret the deterministic 300/300 result as unseen-provider quality, and neither may lower existing numeric, citation, safety, lineage, or read-only gates.
