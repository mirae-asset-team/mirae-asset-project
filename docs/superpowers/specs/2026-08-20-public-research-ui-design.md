# Public disclosure research UI design

## Status and hard gates

This design replaces the anonymous public chat shell with a compact disclosure-research interface. It does not lower answerability, evidence, attestation, correction-lineage, provider, or read-only data gates.

- The immutable base database, live overlay, and live search index remain read-only.
- A corpus-owned citation is necessary but not sufficient: unsupported aggregate questions continue to abstain.
- The contest-issued CLOVA Studio test key is injected only through the server's untracked secret environment. It is never written to Git, an image, a response, or an execution log.
- Provider smoke and the provider-required evaluation remain distinct from deterministic local and public UI verification.

## User outcome

The first screen should answer three questions without requiring a query:

1. Is the disclosure database ready?
2. How broad is the searchable corpus?
3. Can I inspect the filing text behind an answer?

The current attested corpus exposes 76 distinct searchable company candidates. This is a corpus inventory count, not a claim that financial facts are complete for all 76 companies.

## Evidence, finding, and implementation path

| Evidence | Finding | Implementation path |
|---|---|---|
| The current desktop grid reserves 16–21 rem for history | The sidebar dominates the page on ordinary laptop widths | Use a 224 px desktop rail with a 208 px minimum and retain the existing mobile overlay |
| The subtitle repeats the product purpose | It consumes header height without helping a returning user | Remove `공시를 근거로 기업 정보를 검색하고 설명합니다.` |
| `/health` reports readiness and provider state but not corpus breadth | `HyperCLOVA X 미연결` looks like a service outage | Show `공시 DB 준비됨 · 76개 기업`; move provider mode into an expandable service-information panel |
| `/query` exposes citation metadata but not cited text | Users cannot verify why a filing supports an answer | Add a bounded, plain-text excerpt to each already-verified citation and render it inside a collapsed evidence card |
| Threshold questions across all companies currently abstain | The audited financial seed is not corpus-wide complete | Preserve abstention and display a specific coverage explanation; do not count partial facts |
| The existing UI uses generic system fonts and oversized message bubbles | It reads as a prototype rather than a research product | Self-host KoPubWorld webfont assets, use Dotum for the product title and Batang for all other visible text, and use compact research cards |

## Information architecture

### Compact navigation rail

The desktop rail is 224 px wide and contains, in order:

- a compact `새 대화` primary action;
- one search field with its visible label replaced by an accessible label;
- the bounded local conversation list;
- a quiet `기록 전체 삭제` action at the bottom.

Conversation rows use one line, a subtle selected state, and an icon-sized delete control. At widths below 704 px, the rail remains an off-canvas panel controlled by the existing accessible toggle.

### Research header

The header contains only:

- `미래에셋 공시 에이전트` in KoPubWorld Dotum;
- a green readiness chip reading `공시 DB 준비됨 · {company_count}개 기업`;
- a service-information disclosure containing corpus revision, answer mode, and provider connection status.

The provider is described as an answer mode, not as a warning. When no key is configured, the detail reads `검증형 기본 엔진 사용 · HyperCLOVA X 설명 미사용`. When configured, it reads `HyperCLOVA X 설명 연결됨`. Readiness remains green whenever the attested corpus is ready.

### Empty state and examples

The empty chat state uses three small factual chips:

- `{company_count}개 기업 검색 가능`;
- `정정 공시 계보 확인`;
- `인용 근거 열람`.

Examples are limited to queries that the audited data path can answer. Cross-company threshold counts are not advertised until corpus-wide financial coverage is proven.

### Answer and evidence cards

User questions use a compact navy bubble with a 42 rem maximum width. Assistant messages use a white research card with:

- a small `근거 확인됨` or `답변 보류` status;
- the answer in readable KoPubWorld Batang;
- one collapsed `공시 근거 N건` group;
- optional diagnostic details under a second disclosure.

Each evidence item displays:

- filing name, filing date, and receipt number;
- a normalized excerpt limited to 600 characters;
- human-readable location labels such as `표 7 · 행 16`;
- a DART filing link only when the receipt number is a valid 14-digit identifier.

All excerpt text is inserted with `textContent`; no corpus text is interpreted as HTML. Only citations already admitted by the verifier can receive an excerpt. The anonymous API does not add an arbitrary evidence-ID lookup endpoint.

## API contract changes

### GET `/health`

Add one additive field:

```json
{
  "ready": true,
  "company_count": 76,
  "provider_configured": false,
  "corpus_revision": "semantic-v1"
}
```

`company_count` is the length of the cached, attested `company_candidates()` result. If readiness is false, the value is `0`.

### POST `/query`

Add an optional `excerpt` field to each verified evidence object:

```json
{
  "evidence": [
    {
      "evidence_id": "ev1",
      "receipt_no": "20250822000109",
      "report_name": "사업보고서",
      "filed_at": "2025-08-22",
      "excerpt": "공시에서 확인된 근거 문장…",
      "locator": {"kind": "table_row", "table": 7, "row": 16}
    }
  ]
}
```

The verifier hydrates `excerpt` from the already-admitted `EvidenceRef`; provider output cannot supply or alter it. The excerpt is normalized and truncated at the API boundary.

## Unsupported aggregate questions

Questions such as `영업이익 10억 넘는 기업은 몇 개야?` require complete, comparable, latest-period financial facts across the target company universe. The current audited seed proves eight financial facts and explicitly has `corpus_wide_complete=false`; returning a count would therefore be a partial-data false claim.

The planner marks cross-company financial threshold/count intent with a stable reason code. The verifier still abstains. The public client renders:

> 현재 검증된 재무 데이터가 전체 기업을 포괄하지 않아 기업 수 집계를 제공할 수 없습니다.

Actual aggregate answering is deferred to a separate corpus-wide financial-fact ingestion, normalization, period-alignment, and completeness audit. This UI change neither estimates nor extrapolates missing companies.

## Typography and assets

- Product title: `KoPubWorld Dotum`, bold.
- All other visible text: `KoPubWorld Batang`, with system serif fallback.
- Controls that need dense Latin/numeric alignment inherit Batang unless accessibility testing demonstrates clipping.
- Webfont files are served locally from the application package; no runtime CDN request is allowed.
- The font package's license file is committed beside the assets. Only unmodified redistributable webfont files are included.

## Visual system

- Canvas: warm gray `#f5f3ee`.
- Navigation: deep navy `#111b2e`.
- Primary action: muted cobalt `#3451c7`.
- Verified state: forest green on pale green.
- Abstention: brown-gold on pale cream.
- Cards: white with a 1 px warm-gray border and restrained shadow.
- Body text remains at least 16 px; metadata remains at least 13 px; focus rings remain visible.

The page uses a centered 880 px reading column so messages do not stretch across wide monitors. Motion is limited to 150–180 ms opacity/transform transitions and is disabled under `prefers-reduced-motion`.

## Testing strategy

Implementation follows red-green-refactor in these independent slices:

1. Health metadata: failing API test for `company_count`, then the minimal cached count implementation.
2. Evidence excerpts: failing verifier/API tests proving the excerpt is corpus-owned, bounded, additive, and absent on abstention.
3. Aggregate transparency: failing planner/client tests for cross-company threshold questions and the specific safe explanation.
4. UI shell: failing static tests for subtitle removal, service-information controls, empty-state facts, local font assets, and the compact rail contract.
5. Client rendering: failing Node tests for readiness text, Korean locator labels, safe DART links, and evidence excerpt rendering inputs.

The final gate is the full Python suite, both Node web suites, `compileall`, `git diff --check`, a local browser-width review, and a code-only NCP deployment smoke. The public smoke covers `/`, `/health`, one answerable query, one unsupported aggregate query, one evidence expansion, container restart, and read-only mount inspection.

## Git and decision records

Every implementation slice receives its own test evidence and commit. The development log records:

- user-visible problem and design choice;
- rejected alternatives and why they were rejected;
- red and green test commands;
- corpus coverage caveats;
- provider mode and credential boundary;
- deployed commit and rollback location;
- post-deploy health, representative request IDs, and read-only mount results.

No credential value, server administrator password, private key material, or raw provider response is recorded.

## Deployment and rollback

Deployment is code-only to the existing NCP instance; no new server, disk, public IP, or paid database is created. Before image replacement, the remote base, overlay, search index, and attestation modes/hashes are rechecked. The existing untracked environment is preserved and receives the contest API key without display. The previous application directory and image remain available for rollback until public smoke and restart recovery pass.
