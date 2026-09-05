# Judge Stress V2 staging → 8000 인수인계 — 2026-09-05

## 먼저 읽을 결론

운영 8000 승격은 **아직 하지 않았다**. 정확한 후보 commit `0f2fb7af72b8e3cddcbf5096948bb30ea4c29d91`과 image `sha256:0dfbb950ced65f8641a272d9dc8b696ac27142ebc58e0b5892eea1bb3f3577f7`는 NCP 8001에서 healthy이지만, 600문항 Judge Stress V2 실서버 평가는 사용자 요청으로 `2,644/5,328` API 요청에서 중단됐다. 최종 `staging_evaluation.json`이 생성되지 않았으므로 PASS나 FAIL로 해석하지 않는다.

다음 작업자는 private 입력을 바꾸거나 gate를 낮추지 말고 평가를 처음부터 다시 실행해야 한다. 평가와 통합 release gate가 모두 PASS한 경우에만 **동일한 image ID**를 8000으로 승격한다. D 드라이브와 NCP의 base DB, live overlay, live search index는 계속 read-only다.

이 문서는 [이전 Judge Stress V2 인수인계](2026-09-02-judge-stress-v2-handoff.md)의 2026-09-05 18:10 KST 시점 후속 문서다.

## 바로 이어서 할 일

1. PR #8의 head가 아래 Git·image identity와 같은지 확인한다.
2. `eval/judge_stress_v2/holdout.jsonl` 120행과 `retrieval_hidden.jsonl` 120행의 SHA-256을 확인한다.
3. NCP 8001로 SSH tunnel을 열고 600문항 evaluator를 **처음부터** 실행한다. 이전 2,644회는 재사용할 수 없다.
4. evaluator PASS 후 fresh financial/retrieval 보고서를 같은 identity에 결속해 통합 release gate를 실행한다.
5. 통합 gate가 `PASS`, `hard_gate_passed=true`, `hard_gate_reasons=[]`일 때만 PR #8을 merge하고 준비된 동일 image를 8000으로 승격한다.
6. 공개 health, 웹, 삼성전자 최신 매출, 수익성 판단, SM alias, 주입 차단, 20동시 요청을 검증한다. 하나라도 실패하면 기존 8000으로 rollback한다.

## 현재 상태

| 항목 | 2026-09-05 18:10 KST 상태 |
|---|---|
| Git branch | `perf/search-index-validation-v2` |
| candidate source commit | `0f2fb7af72b8e3cddcbf5096948bb30ea4c29d91` |
| handoff branch head | 이 파일을 추가한 docs-only commit; `git log -1 --oneline`으로 확인 |
| 원격 main | `382f168a86bf9c2708f9a7dca1b49776e5fe0a1a` |
| PR | [#8 Harden retrieval validation and release evidence](https://github.com/ksm12030-sudo/mirae-asset-project/pull/8), draft, mergeable/CLEAN, CI SUCCESS |
| main 대비 구현 변경 | candidate까지 15 commits, 50 files; 이 handoff는 docs-only 후속 commit |
| 8001 candidate | `mirae-0f2fb7a-staging`, running, restart `0`, host `8001 → container 8000` |
| candidate image | `sha256:0dfbb950ced65f8641a272d9dc8b696ac27142ebc58e0b5892eea1bb3f3577f7` |
| 8000 production | `qa-growth-v4-prod-prod-agent-1`, running, restart `0` |
| 현재 production image | `sha256:32b8d88cb5bdbae0cc69c7ab3d19e1a7d7d0790bffa5eb1cac8740a3d90eed19` |
| 평가 프로세스 | 종료됨; 관련 local Python process `0`, SSH tunnel 종료 |
| 부분 실행 | `2,644/5,328 = 49.62%`; 429 `0`, 5xx `0`, 입력 검증용 422 `38` |
| 최종 staging artifact | 없음; 중단 때문에 원자적 출력 전 종료됨 |
| NCP 디스크 | `/` 9.8GB 중 8.9GB 사용, 355MB 여유, 97% |

8001 `/health`는 `ready`, base/overlay/search attestation, provider, Function Calling, evaluation mode가 모두 true이고 회사 수는 76개다. 네 데이터 mount는 모두 `rw=false`, `/runtime`만 `rw=true`다. 외부 8001은 ACG를 변경하지 않아 공개되지 않았고 SSH tunnel로만 평가했다. 공개 8000은 로그인 없이 계속 접근 가능하다.

## 고정 identity와 private 입력

| 대상 | SHA-256 또는 값 |
|---|---|
| source commit | `0f2fb7af72b8e3cddcbf5096948bb30ea4c29d91` |
| candidate image | `sha256:0dfbb950ced65f8641a272d9dc8b696ac27142ebc58e0b5892eea1bb3f3577f7` |
| immutable base DB | `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` |
| live overlay | `a4491f2072766fcc11db65bad8c592c78696aea87132f3e7420857924938cb55` |
| live sparse index | `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793` |
| Judge manifest | `0fb8e0a3adb880183211d7bb1683c8f28e97dd4896c43dd8b8cfb7fadaae1f50` |
| Judge private holdout, 120행 | `475452500d8eec2126bdb198f3343b191267cdb4e6dd6789fce82b4dcf404f1d` |
| independent retrieval Gold, 120행 | `52d4e76173f7541aaa281bf0435f4be73e79ed8539a65031f933625ee60e9525` |
| tracked retrieval summary | `945d99c83b538da441eeb729bd777979baf0abc4a60f755cc7ed068b9cfaa887` |
| tracked financial summary | `9e14dbd440ae1978f73aa8036a8e8af802bec06f20a95b2c813d043b67c1d3b5` |
| candidate source archive | `2d65920910926897fa52922b03a1ba2fc65cab63b0e280994cc8bc7e354507ab` |

Private 원문은 Git에 넣지 않는다. 현재 작업 PC에는 `eval/judge_stress_v2/holdout.jsonl`과 `eval/judge_stress_v2/retrieval_hidden.jsonl`이 git-ignored 상태로 있다. NCP에는 Judge holdout의 별도 사본이 `/srv/mirae/eval-private/holdout-68e06cf8.jsonl`에 있으며 image에는 포함되지 않는다. 다른 PC로 넘길 때는 팀의 승인된 비공개 채널로 전달하고 위 hash를 대조한다.

NCP의 source archive는 `/srv/mirae/staging/source-0f2fb7a.tar.gz`, 전개 디렉터리는 `/srv/mirae/staging/candidate-0f2fb7a`다. 이미지를 다시 build하면 디스크 부족 위험이 있으므로 현재 동일 image를 재사용한다.

## 지금까지 바뀐 점 — 다섯 줄

1. 38GB immutable search DB의 무결성 검사를 매 요청이 아니라 startup 1회로 옮겨 구조화·검색 응답 지연을 줄였다.
2. 다중 기업·기간·계정의 mandatory evidence slot을 완전하게 요구하고, 검증 가능한 수익성 판단은 SQLite fact와 Decimal 계산으로 결정론적으로 답하게 했다.
3. 제품 출력에서 만들지 않은 private retrieval Gold 120건으로 Sparse Recall@20 `357/373 = 95.71%`, wrong issuer/version `0`, p95 `1.42s`를 확인했다.
4. staging `/health`에 allowlist release identity를 결속하고, 실제 provider·보안·동시성 결과와 data/image identity를 통합 gate가 fail-closed로 재검산하도록 했다.
5. exact candidate를 8001에 올리고 read-only mount와 provider 연결을 확인했지만, 600문항 실서버 평가는 중단됐으므로 8000은 의도적으로 기존판을 유지했다.

## 이미 통과한 검증

- 전체 Python: `982 passed, 2 skipped, 104 warnings, 266 subtests passed`
- Judge/release/deploy focused: `171 passed, 94 subtests passed`
- Web: `13/13 passed`
- `compileall`, `git diff --check`, credential/secret scan: pass
- GitHub Actions PR #8 `contracts-and-tests`: SUCCESS
- financial release: `856/856`, structured accuracy `100%`, false numeric claim `0`, ungrounded verified answer `0`, 20동시 오류 `0`, p95 `125.713ms`
- independent hidden retrieval: 120 cases, 19 issuers, 98 filings, 373 targets, Recall@20 `95.7105%`, wrong issuer `0`, wrong version `0`, hard failure `0`, p95 `1,421.305ms`
- embedding: Sparse가 독립 gate를 통과했으므로 `DEFERRED_NO_EVIDENCE`; Dense를 운영에 강제하지 않는다.

위 결과는 600문항 실서버 evaluator와 통합 release gate를 대체하지 않는다. 보고서 freshness 제한은 24시간이므로 다음 작업 시각에 따라 financial/retrieval 평가를 다시 생성해야 한다.

## 600문항 staging 평가 재실행

### 1. Git과 private 입력 확인

```powershell
git fetch origin
git switch perf/search-index-validation-v2
git pull --ff-only
git merge-base --is-ancestor '0f2fb7af72b8e3cddcbf5096948bb30ea4c29d91' HEAD
if ($LASTEXITCODE -ne 0) { throw 'Candidate source commit is not an ancestor' }

$judge = 'eval/judge_stress_v2/holdout.jsonl'
$retrieval = 'eval/judge_stress_v2/retrieval_hidden.jsonl'
if ((Get-FileHash -LiteralPath $judge -Algorithm SHA256).Hash.ToLower() -ne '475452500d8eec2126bdb198f3343b191267cdb4e6dd6789fce82b4dcf404f1d') {
  throw 'Unexpected Judge holdout'
}
if ((Get-FileHash -LiteralPath $retrieval -Algorithm SHA256).Hash.ToLower() -ne '52d4e76173f7541aaa281bf0435f4be73e79ed8539a65031f933625ee60e9525') {
  throw 'Unexpected retrieval Gold'
}
```

### 2. 별도 터미널에서 SSH tunnel 시작

```powershell
ssh -N -L 18001:127.0.0.1:8001 root@101.79.31.221
```

팀의 승인된 SSH key가 있으면 `-i`로 지정한다. 비밀번호, PEM, API key를 Git·명령 출력·문서에 기록하지 않는다. `http://127.0.0.1:18001/health`가 candidate identity를 반환하는지 확인한다.

### 3. tracked 배포 스크립트에서 공식 evaluator 추출

```powershell
$source = Get-Content -LiteralPath 'scripts/deploy_staging.ps1' -Raw
$match = [regex]::Match(
  $source,
  '(?s)\$stagingEvaluatorSource = @''\r?\n(.*?)\r?\n''@'
)
if (-not $match.Success) { throw 'Embedded staging evaluator not found' }

$runner = Join-Path $env:TEMP 'mirae-run-staging-evaluator-0f2fb7a.py'
[System.IO.File]::WriteAllText(
  $runner,
  $match.Groups[1].Value,
  [System.Text.UTF8Encoding]::new($false)
)

$identity = [ordered]@{
  commit = '0f2fb7af72b8e3cddcbf5096948bb30ea4c29d91'
  image_id = 'sha256:0dfbb950ced65f8641a272d9dc8b696ac27142ebc58e0b5892eea1bb3f3577f7'
  base_sha256 = 'b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563'
  overlay_sha256 = 'a4491f2072766fcc11db65bad8c592c78696aea87132f3e7420857924938cb55'
  search_index_sha256 = 'e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793'
}
$identityPath = Join-Path $env:TEMP 'mirae-trusted-identity-0f2fb7a.json'
[System.IO.File]::WriteAllText(
  $identityPath,
  ($identity | ConvertTo-Json),
  [System.Text.UTF8Encoding]::new($false)
)
```

### 4. evaluator를 처음부터 실행

```powershell
$artifactDir = Join-Path $env:TEMP 'mirae-release-artifacts-0f2fb7a'
New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
$env:PYTHONPATH = (Resolve-Path -LiteralPath 'src').Path

python $runner `
  --repository-root . `
  --base-url 'http://127.0.0.1:18001' `
  --manifest 'data/derived/judge_stress_v2_manifest.json' `
  --private-holdout 'eval/judge_stress_v2/holdout.jsonl' `
  --identity $identityPath `
  --output (Join-Path $artifactDir 'staging_evaluation.json')
```

이전 실행은 16:13 KST에 시작해 18:06 KST 중단 시점까지 2,644회 요청을 처리했다. 전체 5,328회이며 개발 policy, 20동시성, fault, hidden provider 120회가 뒤에 있으므로 약 4시간을 확보한다. runner는 case checkpoint를 쓰지 않으므로 중단하면 다시 처음부터 실행해야 한다. 질문 원문을 로그로 출력하지 않는다.

서버가 계속 움직이는지만 확인하려면 질문 내용 대신 요청 수와 restart count만 본다.

```bash
docker logs --since '<평가 시작 UTC>' mirae-0f2fb7a-staging 2>&1 | grep -c 'POST '
docker inspect --format 'running={{.State.Running}} restart_count={{.RestartCount}}' mirae-0f2fb7a-staging
```

### 5. staging 결과와 통합 release gate 확인

`staging_evaluation.json`은 질문·답변·provider body 없이 case hash와 지표만 담아야 한다. 다음 조건을 모두 확인한다.

```powershell
$stagingPath = Join-Path $artifactDir 'staging_evaluation.json'
$staging = Get-Content -LiteralPath $stagingPath -Raw | ConvertFrom-Json
$staging | Select-Object status,release_state,case_count,evaluated_count,pass_count,failure_count,hard_gate_passed,hard_gate_reasons,provider_call_count,provider_p95_ms,concurrency_p95_ms

if ($staging.release_state -ne 'PASS' -or
    $staging.hard_gate_passed -ne $true -or
    @($staging.hard_gate_reasons).Count -ne 0) {
  throw 'Staging Judge gate did not pass; do not promote'
}
```

financial/retrieval 보고서가 24시간보다 오래됐으면 [독립 hidden 검색 평가 기록](../operations/2026-09-05-independent-hidden-retrieval.md)의 명령과 아래 financial 명령으로 다시 만든다.

```powershell
$env:PYTHONPATH = (Resolve-Path -LiteralPath 'src').Path
python scripts/evaluate_financial_release.py `
  --database 'D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite' `
  --overlay 'D:\mirae-asset-project\db\agent\agent_overlay.sqlite' `
  --search-index 'D:\mirae-asset-project\db\agent\agent_search.sqlite' `
  --attestation 'data/derived/database_distribution_manifest_semantic_v1.json' `
  --manifest 'data/derived/financial_company_universe.json' `
  --facts 'data/derived/financial_fact_agent_audited_seed.jsonl' `
  --output 'data/derived/financial_release_evaluation.json'
```

같은 identity를 보고서 사본에 결속하고 최종 gate를 실행한다.

```powershell
$financial = Get-Content -LiteralPath 'data/derived/financial_release_evaluation.json' -Raw | ConvertFrom-Json
$retrievalReport = Get-Content -LiteralPath 'data/derived/freeform_retrieval_summary.json' -Raw | ConvertFrom-Json
$identityObject = [pscustomobject]$identity
$financial | Add-Member -NotePropertyName identity -NotePropertyValue $identityObject -Force
$retrievalReport | Add-Member -NotePropertyName identity -NotePropertyValue $identityObject -Force

$financialBound = Join-Path $artifactDir 'financial.bound.json'
$retrievalBound = Join-Path $artifactDir 'retrieval.bound.json'
[System.IO.File]::WriteAllText($financialBound, ($financial | ConvertTo-Json -Depth 100), [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText($retrievalBound, ($retrievalReport | ConvertTo-Json -Depth 100), [System.Text.UTF8Encoding]::new($false))

python scripts/evaluate_release_candidate.py `
  --repository-root . `
  --contract 'config/release_gate_contract.json' `
  --financial $financialBound `
  --judge $stagingPath `
  --retrieval $retrievalBound `
  --deployment $stagingPath `
  --expected-identity $identityPath `
  --json-summary (Join-Path $artifactDir 'release_gate_summary.json') `
  --html-summary (Join-Path $artifactDir 'release_gate_summary.html')

$gate = Get-Content -LiteralPath (Join-Path $artifactDir 'release_gate_summary.json') -Raw | ConvertFrom-Json
if ($gate.release_state -ne 'PASS' -or
    $gate.hard_gate_passed -ne $true -or
    @($gate.hard_gate_reasons).Count -ne 0) {
  throw 'Integrated release gate did not pass; do not promote'
}
```

## PASS 후 Git과 8000 승격

PR #8은 현재 draft다. 통합 gate PASS와 최신 CI SUCCESS를 확인한 후에만 다음을 실행한다.

```powershell
gh pr ready 8
gh pr checks 8 --watch
gh pr merge 8 --merge
```

현재 수동 staging layout은 tracked `deploy_release.ps1`이 기대하는 Compose candidate directory와 다르다. 디스크 97% 상태에서 재build하지 않도록, 서버에 exact-ID 검사와 자동 rollback을 포함한 `/tmp/mirae_promote_0f2fb7a.sh`를 준비했다. 파일 SHA-256은 `b6e140d1cbe04117111b3dc26b3412bd627788270b7e47e50301aa3eeb72c225`이고 `bash -n`을 통과했지만 **아직 실행하지 않았다**. 통합 gate PASS 후 서버에서 hash와 현재 8000 image를 다시 확인한 다음 실행한다.

```bash
sha256sum /tmp/mirae_promote_0f2fb7a.sh
docker inspect --format '{{.Image}} {{.State.Running}} {{.RestartCount}}' qa-growth-v4-prod-prod-agent-1
bash /tmp/mirae_promote_0f2fb7a.sh
```

스크립트는 다음을 수행한다.

- base/overlay/search SHA-256과 candidate image ID를 다시 검사한다.
- 기존 `qa-growth-v4-prod-prod-agent-1`을 삭제하지 않고 `rollback-qa-growth-v4-prod-pre-0f2fb7a`로 정지·rename한다.
- 같은 image ID를 `mirae-0f2fb7a-production`으로 8000에 기동한다.
- production에서 `EVAL_ENABLED=0`, provider/Function Calling enabled, 네 data mount read-only, public root HTTP 200을 검사한다.
- 전환 중 오류가 나면 새 container를 제거하고 기존 container 이름과 실행 상태를 자동 복구한다.

승격 후에는 공개 주소에서 검증한다.

```powershell
$baseUrl = 'http://101.79.31.221:8000'
Invoke-RestMethod "$baseUrl/health"
Invoke-WebRequest "$baseUrl/" -UseBasicParsing
powershell -ExecutionPolicy Bypass -File scripts/smoke-agent.ps1 -BaseUrl $baseUrl
```

health identity는 이 문서의 commit/image/base/overlay/search 값과 정확히 같아야 하고 `eval_enabled=false`여야 한다. 삼성전자 최신 사업보고서 매출액, 삼성전자 최근 2개년 수익성, `SM엔터테인먼트` alias 비교, 직접·인코딩 prompt injection 차단, 20개 동시 구조화 요청 오류 0·p95 2초 이하를 별도로 확인한다. post-promotion 검증이 실패하면 아래 exact 대상만 확인한 뒤 rollback한다.

```bash
docker rm -f mirae-0f2fb7a-production
docker rename rollback-qa-growth-v4-prod-pre-0f2fb7a qa-growth-v4-prod-prod-agent-1
docker start qa-growth-v4-prod-prod-agent-1
curl -fsS http://127.0.0.1:8000/health
```

rollback container와 이전 image는 공모전 종료 전까지 삭제하지 않는다.

## Evidence → Finding → Path

### Evidence

| ID | 불변 관찰 | 재현 경로 |
|---|---|---|
| E-001 | candidate source가 `0f2fb7a...`이고 handoff 작성 전 PR #8은 CLEAN/CI SUCCESS | `git merge-base --is-ancestor`, `git ls-remote`, `gh pr view 8 --json ...` |
| E-002 | independent hidden Sparse Recall@20 95.71%, wrong issuer/version 0 | `data/derived/freeform_retrieval_summary.json`, SHA `945d99...` |
| E-003 | 8001 exact identity, restart 0, 네 data mount `rw=false` | NCP 내부 `curl 127.0.0.1:8001/health`, allowlist `docker inspect` |
| E-004 | evaluator 종료 시 2,644/5,328, 최종 artifact 없음, evaluator/tunnel process 0 | timestamped container access count, local process와 artifact 존재 확인 |
| E-005 | 8000은 기존 image `sha256:32b8...`로 healthy, restart 0 | NCP 내부 8000 health와 allowlist `docker inspect` |

### Findings

| ID | 결론 | 근거 | 상태 |
|---|---|---|---|
| F-001 | candidate는 staging 가능한 상태지만 production 승격 권한은 아직 없다. | E-002, E-003, E-004 | validated, high confidence |
| F-002 | 이번 작업은 live DB를 쓰지 않았고 운영 8000을 변경하지 않았다. | E-003, E-005 | validated, high confidence |
| F-003 | 남은 blocker는 600문항 실서버 평가 완주와 identity-bound 통합 gate다. | E-001, E-004 | validated, high confidence |

### Path P-001 — 검증된 동일 image를 8000으로 승격

1. E-001의 exact candidate source commit과 E-003의 staging identity를 대조한다.
2. private holdout hash를 대조하고 5,328회 평가를 처음부터 완주한다.
3. E-002와 fresh financial report를 staging 결과·external identity에 결속해 통합 gate를 실행한다.
4. gate PASS일 때만 PR #8을 merge하고 준비된 exact image를 8000으로 전환한다.
5. public smoke·동시성·citation 검증을 통과하면 배포를 확정하고, 실패하면 E-005의 기존 container로 rollback한다.

Residual risk는 장시간 실제 provider 평가가 아직 미완료이고, NCP root filesystem 여유가 355MB뿐이라는 점이다.

## 절대 하지 말 것

- private holdout 질문·oracle을 Git에 commit하거나 질문 원문을 로그·HTML에 노출하지 않는다.
- `.env`, API key, SSH password, PEM, NCP credential을 읽어 출력하거나 Git에 넣지 않는다.
- D 드라이브와 NCP의 base DB, live overlay, live search index를 수정하지 않는다.
- evaluator 중간 요청 수를 PASS로 해석하지 않는다.
- tracked 개발 Gold나 제품 출력으로 private Gold를 대체하지 않는다.
- 600건 수, 120 hidden provider observation, Recall/latency/보안 기준을 낮추지 않는다.
- 통합 gate PASS 전에 `/tmp/mirae_promote_0f2fb7a.sh`를 실행하지 않는다.
- 디스크를 확보한다는 이유로 현재 production·rollback image/container를 먼저 삭제하지 않는다.

## 작업 종료 상태

2026-09-05 18:10 KST 기준 evaluator와 SSH tunnel은 종료했다. 8001 candidate와 기존 8000 production은 모두 running/restart 0이다. Git working tree는 이 handoff 문서를 추가하기 전 clean이었다. 다음 담당자는 이 문서를 commit한 branch head부터 이어가되, candidate release identity는 코드 source commit `0f2fb7a...`로 유지한다.
