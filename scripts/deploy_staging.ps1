[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ServerHost,
    [Parameter(Mandatory = $true)][string]$SshUser,
    [Parameter(Mandatory = $true)][string]$ExpectedCommit,
    [Parameter(Mandatory = $true)][string]$ExpectedBaseDbSha256,
    [Parameter(Mandatory = $true)][string]$ExpectedOverlayDbSha256,
    [Parameter(Mandatory = $true)][string]$ExpectedSearchIndexSha256,
    [Parameter(Mandatory = $true)][string]$ExpectedRetrievalReportSha256,
    [Parameter(Mandatory = $true)][string]$PrivateHoldoutPath,
    [string]$ExpectedImageId = "",
    [string]$FinancialGatePath = "",
    [string]$RetrievalGatePath = "",
    [string]$JudgeManifestPath = "",
    [string]$ReleaseContractPath = "",
    [string]$KeyPath = "",
    [string]$RemoteDirectory = "/srv/mirae/releases",
    [string]$ArtifactDirectory = "",
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$repository = Split-Path -Parent $PSScriptRoot
if (-not $FinancialGatePath) { $FinancialGatePath = Join-Path $repository "data/derived/financial_release_evaluation.json" }
if (-not $RetrievalGatePath) { $RetrievalGatePath = Join-Path $repository "data/derived/freeform_retrieval_summary.json" }
if (-not $JudgeManifestPath) { $JudgeManifestPath = Join-Path $repository "data/derived/judge_stress_v2_manifest.json" }
if (-not $ReleaseContractPath) { $ReleaseContractPath = Join-Path $repository "config/release_gate_contract.json" }
if (-not $ArtifactDirectory) { $ArtifactDirectory = Join-Path ([System.IO.Path]::GetTempPath()) "mirae-release-artifacts" }

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}

function Assert-Sha256([string]$Value, [string]$Label) {
    if ($Value -notmatch '^[0-9a-fA-F]{64}$') { throw "$Label must be a 64-character SHA-256 value." }
}

function Get-FileSha256([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $algorithm.ComputeHash($stream)
        return (-join ($bytes | ForEach-Object { $_.ToString("x2") }))
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Assert-SafeAbsoluteUnixPath([string]$Value, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -notmatch '^/[A-Za-z0-9._/-]+$') { throw "$Label must be an absolute safe path." }
    $segments = @($Value -split '/')
    if ($segments -contains '.' -or $segments -contains '..' -or $Value.Contains('//') -or ($Value.Length -gt 1 -and $Value.EndsWith('/'))) {
        throw "$Label must use canonical path components without '.', '..', duplicate separators, or a trailing separator."
    }
}

function Read-JsonObject([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label is missing: $Path" }
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -le 0 -or $item.Length -gt 32MB) { throw "$Label size is invalid." }
    try { $value = [System.IO.File]::ReadAllText($item.FullName) | ConvertFrom-Json }
    catch { throw "$Label is not valid JSON." }
    if ($null -eq $value -or $value -isnot [pscustomobject]) { throw "$Label must be a JSON object." }
    return $value
}

function ConvertTo-ValidatedUtcTimestamp([object]$Value, [string]$Label) {
    if ($null -eq $Value) { throw "$Label is missing." }
    if ($Value -is [DateTimeOffset]) { return ([DateTimeOffset]$Value).ToUniversalTime() }
    if ($Value -is [DateTime]) {
        if (([DateTime]$Value).Kind -eq [DateTimeKind]::Unspecified) { throw "$Label must include a UTC offset." }
        return ([DateTimeOffset]([DateTime]$Value)).ToUniversalTime()
    }
    if ($Value -isnot [string] -or $Value -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})$') { throw "$Label must be an ISO-8601 timestamp with a UTC offset." }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse($Value, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AllowWhiteSpaces, [ref]$parsed)) { throw "$Label is invalid." }
    return $parsed.ToUniversalTime()
}

function Assert-FreshTimestamp([object]$Value, [string]$Label) {
    $parsed = ConvertTo-ValidatedUtcTimestamp $Value $Label
    $age = [DateTimeOffset]::UtcNow - $parsed
    if ($age.TotalSeconds -gt 86400 -or $age.TotalSeconds -lt -300) { throw "$Label is stale or from the future." }
}

function ConvertTo-FiniteNumber([object]$Value, [string]$Label) {
    if ($null -eq $Value -or $Value -is [bool] -or $Value -is [string]) { throw "$Label must be a JSON number." }
    $numericTypes = @([System.TypeCode]::Byte, [System.TypeCode]::SByte, [System.TypeCode]::Int16, [System.TypeCode]::UInt16, [System.TypeCode]::Int32, [System.TypeCode]::UInt32, [System.TypeCode]::Int64, [System.TypeCode]::UInt64, [System.TypeCode]::Single, [System.TypeCode]::Double, [System.TypeCode]::Decimal)
    if ($numericTypes -notcontains [System.Type]::GetTypeCode($Value.GetType())) { throw "$Label must be a JSON number." }
    $number = [System.Convert]::ToDouble($Value, [System.Globalization.CultureInfo]::InvariantCulture)
    if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) { throw "$Label must be finite." }
    return $number
}

function Assert-ExactNumber([object]$Object, [string]$Name, [double]$Expected, [string]$Label) {
    if ($null -eq $Object -or $Object.PSObject.Properties.Name -notcontains $Name) { throw "$Label.$Name is missing." }
    $actual = ConvertTo-FiniteNumber $Object.$Name "$Label.$Name"
    if ($actual -ne $Expected) { throw "$Label.$Name must equal $Expected." }
}

function Assert-DataIdentity([object]$Object, [hashtable]$Expected, [string]$Label) {
    foreach ($name in $Expected.Keys) {
        if ($null -eq $Object -or $Object.PSObject.Properties.Name -notcontains $name) { throw "$Label.$name is missing." }
        $actual = [string]$Object.$name
        Assert-Sha256 $actual "$Label.$name"
        if (-not [string]::Equals($actual, [string]$Expected[$name], [System.StringComparison]::OrdinalIgnoreCase)) { throw "$Label.$name does not match the external trust anchor." }
    }
}

function Assert-FinancialPreStage([object]$Report, [hashtable]$Expected) {
    if ($Report.schema_version -isnot [string] -or $Report.schema_version -cne "financial-release-evaluation-v1") { throw "Unsupported financial pre-stage schema." }
    Assert-FreshTimestamp $Report.finished_at_utc "financial.finished_at_utc"
    if ($Report.hard_gate_passed -isnot [bool] -or $Report.hard_gate_passed -ne $true) { throw "Financial hard gate did not pass." }
    if ($Report.hard_gate_reasons -isnot [System.Array] -or $Report.hard_gate_reasons.Count -ne 0) { throw "Financial hard_gate_reasons must be an actual empty array." }
    Assert-DataIdentity $Report.inputs $Expected "financial.inputs"
    Assert-ExactNumber $Report.cases "total" 856 "financial.cases"
    Assert-ExactNumber $Report.cases "passed" 856 "financial.cases"
    Assert-ExactNumber $Report.cases "failed" 0 "financial.cases"
    Assert-ExactNumber $Report.cases "structured_accuracy" 1 "financial.cases"
    Assert-ExactNumber $Report.cases "false_numeric_claim_count" 0 "financial.cases"
    Assert-ExactNumber $Report.cases "ungrounded_verified_answer_count" 0 "financial.cases"
    if ($Report.cases.failures -isnot [System.Array] -or $Report.cases.failures.Count -ne 0) { throw "Financial failures must be an actual empty array." }
    Assert-ExactNumber $Report.concurrency "request_count" 20 "financial.concurrency"
    Assert-ExactNumber $Report.concurrency "error_count" 0 "financial.concurrency"
    $p95 = ConvertTo-FiniteNumber $Report.concurrency.p95_ms "financial.concurrency.p95_ms"
    if ($p95 -gt 2000) { throw "Financial concurrency p95 exceeds 2000 ms." }
}

function Assert-RetrievalPreStage([object]$Report, [hashtable]$Expected) {
    if ($Report.schema_version -isnot [string] -or $Report.schema_version -cne "freeform-retrieval-evaluation-v2") { throw "Unsupported retrieval pre-stage schema." }
    if ($Report.evaluation_scope -isnot [string] -or $Report.evaluation_scope -cne "independent_hidden") { throw "Retrieval evaluation scope is not independent hidden." }
    if ($Report.release_eligible -isnot [bool] -or $Report.release_eligible -ne $true) { throw "Retrieval report is not release-eligible." }
    if ($Report.status -isnot [string] -or $Report.status -cne "ok") { throw "Retrieval status is not ok." }
    Assert-FreshTimestamp $Report.generated_at "retrieval.generated_at"
    $retrievalIdentity = [PSCustomObject]@{
        base_sha256 = $Report.database_sha256
        overlay_sha256 = $Report.overlay_sha256
        search_index_sha256 = $Report.search_index_sha256
    }
    Assert-DataIdentity $retrievalIdentity $Expected "retrieval"
    Assert-ExactNumber $Report.metrics "case_count" 120 "retrieval.metrics"
    $queryCount = ConvertTo-FiniteNumber $Report.metrics.query_count "retrieval.metrics.query_count"
    if ($queryCount -ne [math]::Floor($queryCount) -or $queryCount -lt 120 -or $queryCount -gt 960) { throw "Retrieval query_count must be an integer between 120 and 960." }
    foreach ($name in @("wrong_issuer_count", "wrong_version_count", "hard_failure_count")) { Assert-ExactNumber $Report.metrics $name 0 "retrieval.metrics" }
    $recall = ConvertTo-FiniteNumber $Report.metrics.target_recall_at_20 "retrieval.metrics.target_recall_at_20"
    if ($recall -lt 0.95 -or $recall -gt 1) { throw "Retrieval Recall@20 is below the release threshold." }
}

function Assert-JudgeInputs([object]$Manifest, [string]$HoldoutPath) {
    if ($Manifest.schema_version -isnot [string] -or $Manifest.schema_version -cne "judge-stress-v2-manifest-v1") { throw "Unsupported Judge manifest schema." }
    Assert-ExactNumber $Manifest "case_count" 600 "judge_manifest"
    Assert-ExactNumber $Manifest.split_counts "development" 480 "judge_manifest.split_counts"
    Assert-ExactNumber $Manifest.split_counts "holdout" 120 "judge_manifest.split_counts"
    $holdoutRoot = [System.IO.Path]::GetFullPath((Join-Path $repository "eval/judge_stress_v2"))
    $holdout = [System.IO.Path]::GetFullPath($HoldoutPath)
    $prefix = $holdoutRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $holdout.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $holdout -PathType Leaf)) { throw "Private holdout must be an existing file under the git-ignored evaluator directory." }
    $item = Get-Item -LiteralPath $holdout
    if ($item.Length -le 0 -or $item.Length -gt 32MB) { throw "Private holdout size is invalid." }
    $declared = $Manifest.raw_artifacts.holdout
    Assert-ExactNumber $declared "case_count" 120 "judge_manifest.raw_artifacts.holdout"
    $declaredSha = [string]$declared.sha256
    Assert-Sha256 $declaredSha "judge_manifest.raw_artifacts.holdout.sha256"
    $actualSha = Get-FileSha256 $holdout
    if ($actualSha -ne $declaredSha.ToLowerInvariant()) { throw "Private holdout hash does not match the Judge manifest." }
    $lineCount = 0
    foreach ($line in [System.IO.File]::ReadLines($holdout)) { if (-not [string]::IsNullOrWhiteSpace($line)) { $lineCount++ } }
    if ($lineCount -ne 120) { throw "Private holdout must contain exactly 120 records." }
}

function Invoke-RemoteScript {
    param([string]$Target, [string]$ScriptText, [string]$Arguments, [string]$PrivateKeyPath, [string]$Step)
    if ($PrivateKeyPath) { $ScriptText | & ssh -i $PrivateKeyPath $Target $Arguments }
    else { $ScriptText | & ssh $Target $Arguments }
    Assert-LastExitCode $Step
}

if ($ServerHost -notmatch '^[A-Za-z0-9.-]+$' -or $SshUser -notmatch '^[A-Za-z0-9._-]+$') { throw "ServerHost or SshUser contains unsupported characters." }
Assert-SafeAbsoluteUnixPath $RemoteDirectory "RemoteDirectory"
if ($ExpectedCommit -notmatch '^[0-9a-fA-F]{40}$') { throw "ExpectedCommit must be a full 40-character Git commit." }
if ($ExpectedImageId -and $ExpectedImageId -notmatch '^sha256:[0-9a-fA-F]{64}$') { throw "ExpectedImageId must be a Docker sha256 image ID." }
$expectedData = @{ base_sha256 = $ExpectedBaseDbSha256; overlay_sha256 = $ExpectedOverlayDbSha256; search_index_sha256 = $ExpectedSearchIndexSha256 }
foreach ($entry in $expectedData.GetEnumerator()) { Assert-Sha256 ([string]$entry.Value) "Expected $($entry.Key)" }
Assert-Sha256 $ExpectedRetrievalReportSha256 "Expected retrieval report SHA-256"

$financial = Read-JsonObject $FinancialGatePath "Financial pre-stage report"
$retrieval = Read-JsonObject $RetrievalGatePath "Retrieval pre-stage report"
$manifest = Read-JsonObject $JudgeManifestPath "Judge manifest"
$retrievalReportSha256 = Get-FileSha256 $RetrievalGatePath
if ($retrievalReportSha256 -cne $ExpectedRetrievalReportSha256.ToLowerInvariant()) { throw "Retrieval report does not match the external SHA-256 trust anchor." }
Assert-FinancialPreStage $financial $expectedData
Assert-RetrievalPreStage $retrieval $expectedData
Assert-JudgeInputs $manifest $PrivateHoldoutPath
# PRE_STAGE_GATES_VALIDATED_BEFORE_MUTATION; this state is not final release PASS.

$head = (& git -C $repository rev-parse HEAD).Trim()
Assert-LastExitCode "read Git commit"
if (-not [string]::Equals($head, $ExpectedCommit, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Working tree commit does not match the external trust anchor." }
$dirty = @(& git -C $repository status --porcelain --untracked-files=no)
Assert-LastExitCode "check tracked working tree"
if ($dirty.Count -ne 0) { throw "Tracked working tree changes make the commit identity untrustworthy." }
& git -C $repository check-ignore -q -- $PrivateHoldoutPath
Assert-LastExitCode "verify private holdout is git-ignored"

$requiredInputs = @("Dockerfile", ".dockerignore", "pyproject.toml", "compose.release.yaml", "src", "config", "eval/qa_cases.jsonl", "scripts/evaluate_release_candidate.py")
foreach ($relativePath in $requiredInputs) { if (-not (Test-Path -LiteralPath (Join-Path $repository $relativePath))) { throw "Required deployment input is missing: $relativePath" } }

New-Item -ItemType Directory -Path $ArtifactDirectory -Force | Out-Null
$preStagePath = Join-Path $ArtifactDirectory "pre_stage_attestation.json"
$preStage = [ordered]@{
    schema_version = "pre-stage-attestation-v1"; evaluated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    stage_state = "READY_FOR_STAGING"; final_release_passed = $false
    identity = [ordered]@{ commit = $ExpectedCommit.ToLowerInvariant(); base_sha256 = $ExpectedBaseDbSha256.ToLowerInvariant(); overlay_sha256 = $ExpectedOverlayDbSha256.ToLowerInvariant(); search_index_sha256 = $ExpectedSearchIndexSha256.ToLowerInvariant(); retrieval_report_sha256 = $retrievalReportSha256 }
    non_staging_gates = [ordered]@{ financial = $true; retrieval = $true; judge_inputs = $true }
}
[System.IO.File]::WriteAllText($preStagePath, ($preStage | ConvertTo-Json -Depth 6), [System.Text.UTF8Encoding]::new($false))

$shortCommit = $ExpectedCommit.Substring(0, 12).ToLowerInvariant()
$buildTag = "mirae-disclosure-agent:build-$shortCommit"
$target = "$SshUser@$ServerHost"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mirae-release-" + [guid]::NewGuid().ToString("N"))
$buildRoot = Join-Path $tempRoot "build"
$packageRoot = Join-Path $tempRoot "package"
$iidFile = Join-Path $tempRoot "candidate.iid"
$sourceArchive = Join-Path $tempRoot "source-tree.tar"

try {
    New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
    & git -C $repository archive --format=tar --output=$sourceArchive $ExpectedCommit -- Dockerfile .dockerignore pyproject.toml compose.release.yaml src config eval
    Assert-LastExitCode "archive exact trusted Git tree"
    & tar -xf $sourceArchive -C $buildRoot
    Assert-LastExitCode "extract trusted Git build context"
    foreach ($relativePath in @("Dockerfile", ".dockerignore", "pyproject.toml", "compose.release.yaml", "src", "config", "eval")) {
        if (-not (Test-Path -LiteralPath (Join-Path $buildRoot $relativePath))) { throw "Trusted Git archive is missing build input: $relativePath" }
    }
    New-Item -ItemType Directory -Path (Join-Path $buildRoot "data/derived") -Force | Out-Null
    Copy-Item -LiteralPath $RetrievalGatePath -Destination (Join-Path $buildRoot "data/derived/freeform_retrieval_summary.json")
    if ((Get-FileSha256 (Join-Path $buildRoot "data/derived/freeform_retrieval_summary.json")) -cne $retrievalReportSha256) { throw "Trusted retrieval report changed while assembling the build context." }

    & docker build --file (Join-Path $buildRoot "Dockerfile") --tag $buildTag --iidfile $iidFile $buildRoot
    Assert-LastExitCode "build candidate image"
    $builtImageId = ([System.IO.File]::ReadAllText($iidFile)).Trim().ToLowerInvariant()
    $inspectedBuildId = (& docker image inspect --format '{{.Id}}' $buildTag).Trim().ToLowerInvariant()
    Assert-LastExitCode "inspect candidate image"
    if ($builtImageId -notmatch '^sha256:[0-9a-f]{64}$' -or $inspectedBuildId -ne $builtImageId) { throw "Built image identity is invalid or unstable." }
    if ($ExpectedImageId -and $builtImageId -ne $ExpectedImageId.ToLowerInvariant()) { throw "Built image does not match the optional expected image identity." }

    $shortImage = $builtImageId.Substring(7, 12)
    $candidateTag = "mirae-disclosure-agent:candidate-$shortCommit-$shortImage"
    & docker tag $buildTag $candidateTag
    Assert-LastExitCode "tag candidate image"
    $taggedImageId = (& docker image inspect --format '{{.Id}}' $candidateTag).Trim().ToLowerInvariant()
    Assert-LastExitCode "inspect tagged candidate image"
    if ($taggedImageId -ne $builtImageId) { throw "Candidate tag changed the tested image identity." }

    $imageArchive = Join-Path $packageRoot "candidate-image.tar"
    & docker save --output $imageArchive $candidateTag
    Assert-LastExitCode "save candidate image"
    $imageArchiveSha256 = Get-FileSha256 $imageArchive
    Copy-Item -LiteralPath (Join-Path $buildRoot "compose.release.yaml") -Destination $packageRoot
    New-Item -ItemType Directory -Path (Join-Path $packageRoot "data/derived") -Force | Out-Null
    Copy-Item -LiteralPath $RetrievalGatePath -Destination (Join-Path $packageRoot "data/derived/freeform_retrieval_summary.json")
    Copy-Item -LiteralPath $preStagePath -Destination (Join-Path $packageRoot "pre_stage_attestation.json")
    if ((Get-FileSha256 (Join-Path $packageRoot "data/derived/freeform_retrieval_summary.json")) -cne $retrievalReportSha256) { throw "Trusted retrieval report changed while packaging the candidate." }
    $identityLines = @("schema=release-deployment.v1", "commit=$($ExpectedCommit.ToLowerInvariant())", "image_id=$builtImageId", "image_ref=$candidateTag", "image_archive_sha256=$imageArchiveSha256", "base_sha256=$($ExpectedBaseDbSha256.ToLowerInvariant())", "overlay_sha256=$($ExpectedOverlayDbSha256.ToLowerInvariant())", "search_index_sha256=$($ExpectedSearchIndexSha256.ToLowerInvariant())", "retrieval_report_sha256=$retrievalReportSha256")
    [System.IO.File]::WriteAllLines((Join-Path $packageRoot "candidate.identity"), $identityLines, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText((Join-Path $packageRoot "candidate-image.tar.sha256"), "$imageArchiveSha256  candidate-image.tar`n", [System.Text.UTF8Encoding]::new($false))
    $deploymentArchive = Join-Path $ArtifactDirectory "mirae-release-$shortCommit-$shortImage.tar.gz"
    & tar -czf $deploymentArchive -C $packageRoot .
    Assert-LastExitCode "package release candidate"
    $deploymentArchiveSha256 = Get-FileSha256 $deploymentArchive
    [System.IO.File]::WriteAllText("$deploymentArchive.sha256", "$deploymentArchiveSha256  $([System.IO.Path]::GetFileName($deploymentArchive))`n", [System.Text.UTF8Encoding]::new($false))
    $candidateDirectory = "$RemoteDirectory/candidate-$shortCommit-$shortImage"
    $remoteArchive = "/tmp/mirae-release-$shortCommit-$shortImage.tar.gz"

    if ($KeyPath) { & scp -i $KeyPath $deploymentArchive "${target}:$remoteArchive" } else { & scp $deploymentArchive "${target}:$remoteArchive" }
    Assert-LastExitCode "upload release candidate"

    $remoteStageScript = @'
set -euo pipefail
archive="$1"; candidate_dir="$2"; image_ref="$3"; expected_image="$4"; archive_sha="$5"
expected_base="$6"; expected_overlay="$7"; expected_search="$8"; expected_commit="$9"
expected_retrieval="${10}"
mkdir -p "$candidate_dir"; tar -xzf "$archive" -C "$candidate_dir"; cd "$candidate_dir"
test "$(sha256sum candidate-image.tar | awk '{print $1}')" = "$archive_sha"
test "$(sha256sum data/derived/freeform_retrieval_summary.json | awk '{print $1}')" = "$expected_retrieval"
grep -Fx "commit=$expected_commit" candidate.identity >/dev/null
grep -Fx "image_id=$expected_image" candidate.identity >/dev/null
grep -Fx "image_ref=$image_ref" candidate.identity >/dev/null
grep -Fx "base_sha256=$expected_base" candidate.identity >/dev/null
grep -Fx "overlay_sha256=$expected_overlay" candidate.identity >/dev/null
grep -Fx "search_index_sha256=$expected_search" candidate.identity >/dev/null
grep -Fx "retrieval_report_sha256=$expected_retrieval" candidate.identity >/dev/null
test "$(sha256sum /srv/mirae/data/base/disclosure.sqlite | awk '{print $1}')" = "$expected_base"
test "$(sha256sum /srv/mirae/data/agent/agent_overlay.sqlite | awk '{print $1}')" = "$expected_overlay"
test "$(sha256sum /srv/mirae/data/agent/agent_search.sqlite | awk '{print $1}')" = "$expected_search"
docker load --input candidate-image.tar >/dev/null
test "$(docker image inspect --format '{{.Id}}' "$image_ref")" = "$expected_image"
export DISCLOSURE_RELEASE_IMAGE="$image_ref" DISCLOSURE_EXPECTED_COMMIT="$expected_commit"
docker compose -p mirae-release -f compose.release.yaml up -d --no-build dense-retriever disclosure-agent-staging
staging_container="$(docker compose -p mirae-release -f compose.release.yaml ps -q disclosure-agent-staging)"
dense_container="$(docker compose -p mirae-release -f compose.release.yaml ps -q dense-retriever)"
test -n "$staging_container"; test -n "$dense_container"
test "$(docker inspect --format '{{.Image}}' "$staging_container")" = "$expected_image"
assert_ro() { mounts="$(docker inspect --format '{{range .Mounts}}{{printf "%s=%v\n" .Destination .RW}}{{end}}' "$1")"; shift; for d in "$@"; do printf '%s\n' "$mounts" | grep -Fqx "$d=false"; done; }
assert_ro "$staging_container" /data/base/disclosure.sqlite /data/agent/agent_overlay.sqlite /data/agent/agent_search.sqlite /data/attestation.json
assert_ro "$dense_container" /data/dense /model
curl --fail --silent --show-error http://127.0.0.1:8001/health | python3 -c 'import json,sys; fields=("commit","image_id","base_sha256","overlay_sha256","search_index_sha256"); v=json.load(sys.stdin); actual=v.get("identity"); expected=dict(zip(fields,sys.argv[1:])); assert v.get("ready") is True and v.get("eval_enabled") is True; assert isinstance(actual,dict) and set(actual)==set(fields) and actual==expected' "$expected_commit" "$expected_image" "$expected_base" "$expected_overlay" "$expected_search"
rm -f "$archive"
'@
    $stageArguments = "bash -s -- '$remoteArchive' '$candidateDirectory' '$candidateTag' '$builtImageId' '$imageArchiveSha256' '$($ExpectedBaseDbSha256.ToLowerInvariant())' '$($ExpectedOverlayDbSha256.ToLowerInvariant())' '$($ExpectedSearchIndexSha256.ToLowerInvariant())' '$($ExpectedCommit.ToLowerInvariant())' '$retrievalReportSha256'"
    Invoke-RemoteScript -Target $target -ScriptText $remoteStageScript -Arguments $stageArguments -PrivateKeyPath $KeyPath -Step "staging 8001 deployment"

    $trustedIdentity = [ordered]@{ commit = $ExpectedCommit.ToLowerInvariant(); image_id = $builtImageId; base_sha256 = $ExpectedBaseDbSha256.ToLowerInvariant(); overlay_sha256 = $ExpectedOverlayDbSha256.ToLowerInvariant(); search_index_sha256 = $ExpectedSearchIndexSha256.ToLowerInvariant() }
    $trustedIdentityPath = Join-Path $tempRoot "trusted_identity.json"
    [System.IO.File]::WriteAllText($trustedIdentityPath, ($trustedIdentity | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
    $stagingEvaluationPath = Join-Path $ArtifactDirectory "staging_evaluation.json"
    $stagingEvaluatorPath = Join-Path $tempRoot "run_staging_evaluator.py"
    $stagingEvaluatorSource = @'
from __future__ import annotations
import argparse, hashlib, json, os, tempfile
from datetime import UTC, datetime
from pathlib import Path
from disclosure_db.judge_stress_execution import JudgeRunOptions
from disclosure_db.judge_stress_v2 import assemble_judge_suite, build_development_cases, load_audited_sources, load_contract, load_private_holdout
from disclosure_db.staging_evaluation import StagingApiClient, StagingJudgeRuntime, run_staging_suite, smoke_public_contracts
parser = argparse.ArgumentParser()
parser.add_argument("--repository-root", type=Path, required=True); parser.add_argument("--base-url", required=True)
parser.add_argument("--manifest", type=Path, required=True); parser.add_argument("--private-holdout", type=Path, required=True)
parser.add_argument("--identity", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args(); root = args.repository_root.resolve()
identity = json.loads(args.identity.read_text(encoding="utf-8"))

def validate_health_identity(health, trusted_identity):
    fields = ("commit", "image_id", "base_sha256", "overlay_sha256", "search_index_sha256")
    actual = health.get("identity") if isinstance(health, dict) else None
    if not isinstance(actual, dict) or set(actual) != set(fields):
        raise RuntimeError("staging /health identity is missing or is not allowlisted")
    if not isinstance(trusted_identity, dict) or set(trusted_identity) != set(fields):
        raise RuntimeError("external trusted identity is invalid")
    for name in fields:
        if not isinstance(actual[name], str) or not isinstance(trusted_identity[name], str):
            raise RuntimeError("staging identity values must be strings")
    if len(actual["commit"]) != 40 or any(char not in "0123456789abcdef" for char in actual["commit"]):
        raise RuntimeError("staging commit identity is invalid")
    if not actual["image_id"].startswith("sha256:") or len(actual["image_id"]) != 71 or any(char not in "0123456789abcdef" for char in actual["image_id"][7:]):
        raise RuntimeError("staging image identity is invalid")
    for name in fields[2:]:
        if len(actual[name]) != 64 or any(char not in "0123456789abcdef" for char in actual[name]):
            raise RuntimeError("staging data identity is invalid")
    if actual != trusted_identity:
        raise RuntimeError("staging /health identity does not match the external trust anchor")
    canonical = json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()
    return dict(actual), hashlib.sha256(canonical).hexdigest()

client = StagingApiClient(args.base_url, timeout_s=10, max_response_bytes=2_000_000)
health = client.health()
health_identity, health_identity_sha256 = validate_health_identity(health, identity)
if not all(health.get(name) is True for name in ("ready", "eval_enabled", "provider_configured", "function_calling_configured")): raise RuntimeError("staging runtime is not release-evaluable")
contract = load_contract(root / "config/judge_stress_v2_contract.json")
sources, _ = load_audited_sources(root, contract)
development = build_development_cases(sources, contract)
holdout = load_private_holdout(args.private_holdout, repository_root=root, contract=contract)
suite = assemble_judge_suite(development, holdout, contract); cases = suite["development"] + suite["holdout"]
if len(cases) != 600: raise RuntimeError("staging suite must contain exactly 600 cases")
manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
runtime = StagingJudgeRuntime(client)
summary = run_staging_suite(cases, runtime, JudgeRunOptions(private_holdout_available=True, provider_available=True, concurrency=20), manifest=manifest)
smoke = smoke_public_contracts(client)
if smoke.get("error_count") != 0: raise RuntimeError("staging public smoke failed")
post_health = client.health()
post_health_identity, post_health_identity_sha256 = validate_health_identity(post_health, identity)
if post_health_identity_sha256 != health_identity_sha256: raise RuntimeError("staging /health identity changed during evaluation")
payload = {"schema_version": "staging-evaluation-v1", "finished_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "identity": post_health_identity, "health_identity_sha256": health_identity_sha256, "smoke": smoke, **summary}
def assert_content_free(value):
    forbidden = {"question", "answer", "response", "prompt", "secret", "api_key"}
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in forbidden: raise RuntimeError("raw or secret material reached staging artifact")
            assert_content_free(child)
    elif isinstance(value, list):
        for child in value: assert_content_free(child)
assert_content_free(payload)
args.output.parent.mkdir(parents=True, exist_ok=True)
fd, name = tempfile.mkstemp(prefix=".staging-evaluation-", suffix=".tmp", dir=str(args.output.parent)); os.close(fd)
try:
    Path(name).write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"); os.replace(name, args.output)
finally: Path(name).unlink(missing_ok=True)
'@
    [System.IO.File]::WriteAllText($stagingEvaluatorPath, $stagingEvaluatorSource, [System.Text.UTF8Encoding]::new($false))
    $priorPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = Join-Path $repository "src"
        & $PythonExecutable $stagingEvaluatorPath --repository-root $repository --base-url "http://${ServerHost}:8001" --manifest $JudgeManifestPath --private-holdout $PrivateHoldoutPath --identity $trustedIdentityPath --output $stagingEvaluationPath
        Assert-LastExitCode "bounded staging evaluation"
    } finally { $env:PYTHONPATH = $priorPythonPath }

    $stagingEvaluation = Read-JsonObject $stagingEvaluationPath "Staging evaluation"
    Assert-DataIdentity $stagingEvaluation.identity $expectedData "staging.identity"
    if ($stagingEvaluation.identity.commit -cne $ExpectedCommit.ToLowerInvariant() -or $stagingEvaluation.identity.image_id -cne $builtImageId) { throw "Staging evaluation identity does not match the external trust anchor." }
    $healthIdentitySha256 = [string]$stagingEvaluation.health_identity_sha256
    Assert-Sha256 $healthIdentitySha256 "staging.health_identity_sha256"

    $remoteVerifyScript = @'
set -euo pipefail
candidate_dir="$1"; image_ref="$2"; expected_image="$3"; expected_base="$4"; expected_overlay="$5"; expected_search="$6"
expected_health_identity_sha256="$7"; expected_commit="$8"
cd "$candidate_dir"
test "$(docker image inspect --format '{{.Id}}' "$image_ref")" = "$expected_image"
container="$(docker compose -p mirae-release -f compose.release.yaml ps -q disclosure-agent-staging)"; test -n "$container"
test "$(docker inspect --format '{{.Image}}' "$container")" = "$expected_image"
test "$(sha256sum /srv/mirae/data/base/disclosure.sqlite | awk '{print $1}')" = "$expected_base"
test "$(sha256sum /srv/mirae/data/agent/agent_overlay.sqlite | awk '{print $1}')" = "$expected_overlay"
test "$(sha256sum /srv/mirae/data/agent/agent_search.sqlite | awk '{print $1}')" = "$expected_search"
actual_health_identity_sha256="$(curl --fail --silent --show-error http://127.0.0.1:8001/health | python3 -c 'import hashlib,json,sys; fields=("commit","image_id","base_sha256","overlay_sha256","search_index_sha256"); v=json.load(sys.stdin); actual=v.get("identity"); expected=dict(zip(fields,sys.argv[1:])); assert v.get("ready") is True and v.get("eval_enabled") is True; assert isinstance(actual,dict) and set(actual)==set(fields) and actual==expected; print(hashlib.sha256(json.dumps(actual,sort_keys=True,separators=(",", ":")).encode()).hexdigest())' "$expected_commit" "$expected_image" "$expected_base" "$expected_overlay" "$expected_search")"
test "$actual_health_identity_sha256" = "$expected_health_identity_sha256"
'@
    $verifyArguments = "bash -s -- '$candidateDirectory' '$candidateTag' '$builtImageId' '$($ExpectedBaseDbSha256.ToLowerInvariant())' '$($ExpectedOverlayDbSha256.ToLowerInvariant())' '$($ExpectedSearchIndexSha256.ToLowerInvariant())' '$healthIdentitySha256' '$($ExpectedCommit.ToLowerInvariant())'"
    Invoke-RemoteScript -Target $target -ScriptText $remoteVerifyScript -Arguments $verifyArguments -PrivateKeyPath $KeyPath -Step "post-evaluation staging identity verification"

    $financial | Add-Member -NotePropertyName identity -NotePropertyValue $trustedIdentity -Force
    $retrieval | Add-Member -NotePropertyName identity -NotePropertyValue $trustedIdentity -Force
    $boundFinancialPath = Join-Path $tempRoot "financial.bound.json"; $boundRetrievalPath = Join-Path $tempRoot "retrieval.bound.json"
    [System.IO.File]::WriteAllText($boundFinancialPath, ($financial | ConvertTo-Json -Depth 100), [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText($boundRetrievalPath, ($retrieval | ConvertTo-Json -Depth 100), [System.Text.UTF8Encoding]::new($false))
    $finalGatePath = Join-Path $ArtifactDirectory "release_gate_summary.json"; $finalGateHtmlPath = Join-Path $ArtifactDirectory "release_gate_summary.html"
    try {
        $env:PYTHONPATH = Join-Path $repository "src"
        & $PythonExecutable (Join-Path $repository "scripts/evaluate_release_candidate.py") --repository-root $repository --contract $ReleaseContractPath --financial $boundFinancialPath --judge $stagingEvaluationPath --retrieval $boundRetrievalPath --deployment $stagingEvaluationPath --expected-identity $trustedIdentityPath --json-summary $finalGatePath --html-summary $finalGateHtmlPath
        Assert-LastExitCode "final release gate evaluation"
    } finally { $env:PYTHONPATH = $priorPythonPath }

    $finalGate = Read-JsonObject $finalGatePath "Final release gate"
    if ($finalGate.schema_version -cne "release-gate-summary-v1" -or $finalGate.hard_gate_passed -ne $true -or $finalGate.release_state -cne "PASS") { throw "Final release gate did not authorize production." }
    if ($finalGate.hard_gate_reasons -isnot [System.Array] -or $finalGate.hard_gate_reasons.Count -ne 0) { throw "Final PASS reasons must be an actual empty array." }
    Assert-DataIdentity $finalGate.identity $expectedData "final.identity"
    if ($finalGate.identity.commit -cne $ExpectedCommit.ToLowerInvariant() -or $finalGate.identity.image_id -cne $builtImageId) { throw "Final gate identity does not match the staged candidate." }
    Write-Output ([PSCustomObject]@{ release_state = "PASS"; commit = $ExpectedCommit.ToLowerInvariant(); image_id = $builtImageId; staging_evaluation = $stagingEvaluationPath; final_release_gate = $finalGatePath; deployment_archive = $deploymentArchive; deployment_archive_sha256 = $deploymentArchiveSha256; remote_candidate_directory = $candidateDirectory; production_promoted = $false })
}
finally {
    $tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()); $resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
    if ($resolvedTemp.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolvedTemp) -like "mirae-release-*") { Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue }
}
