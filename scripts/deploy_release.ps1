[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ServerHost,
    [Parameter(Mandatory = $true)][string]$SshUser,
    [Parameter(Mandatory = $true)][string]$ExpectedCommit,
    [Parameter(Mandatory = $true)][string]$ExpectedImageId,
    [Parameter(Mandatory = $true)][string]$ExpectedBaseDbSha256,
    [Parameter(Mandatory = $true)][string]$ExpectedOverlayDbSha256,
    [Parameter(Mandatory = $true)][string]$ExpectedSearchIndexSha256,
    [string]$ReleaseGatePath = "",
    [string]$KeyPath = "",
    [string]$RemoteDirectory = "/srv/mirae/releases",
    [string]$RollbackDirectory = "/srv/mirae/rollback"
)

$ErrorActionPreference = "Stop"
$repository = Split-Path -Parent $PSScriptRoot
if (-not $ReleaseGatePath) {
    $ReleaseGatePath = Join-Path $repository "data/derived/release_gate_summary.json"
}

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

function Assert-Sha256([string]$Value, [string]$Label) {
    if ($Value -notmatch '^[0-9a-fA-F]{64}$') {
        throw "$Label must be a 64-character SHA-256 value."
    }
}

function Assert-SafeAbsoluteUnixPath([string]$Value, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -notmatch '^/[A-Za-z0-9._/-]+$') {
        throw "$Label must be an absolute safe path."
    }
    $segments = @($Value -split '/')
    if ($segments -contains '.' -or $segments -contains '..' -or $Value.Contains('//') -or ($Value.Length -gt 1 -and $Value.EndsWith('/'))) {
        throw "$Label must use canonical path components without '.', '..', duplicate separators, or a trailing separator."
    }
}

function ConvertTo-ValidatedUtcTimestamp([object]$Value, [string]$Label) {
    if ($null -eq $Value) {
        throw "$Label is missing."
    }
    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).ToUniversalTime()
    }
    if ($Value -is [DateTime]) {
        if (([DateTime]$Value).Kind -eq [DateTimeKind]::Unspecified) {
            throw "$Label must include a UTC offset."
        }
        return ([DateTimeOffset]([DateTime]$Value)).ToUniversalTime()
    }
    if ($Value -isnot [string] -or $Value -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})$') {
        throw "$Label must be an ISO-8601 timestamp with a UTC offset."
    }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse($Value, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AllowWhiteSpaces, [ref]$parsed)) {
        throw "$Label is invalid."
    }
    return $parsed.ToUniversalTime()
}

function ConvertTo-FiniteMetric([object]$Value, [string]$Name) {
    if ($null -eq $Value -or $Value -is [bool] -or $Value -is [string]) {
        throw "Release gate metric '$Name' must be a JSON number."
    }
    $numericTypeCodes = @(
        [System.TypeCode]::Byte,
        [System.TypeCode]::SByte,
        [System.TypeCode]::Int16,
        [System.TypeCode]::UInt16,
        [System.TypeCode]::Int32,
        [System.TypeCode]::UInt32,
        [System.TypeCode]::Int64,
        [System.TypeCode]::UInt64,
        [System.TypeCode]::Single,
        [System.TypeCode]::Double,
        [System.TypeCode]::Decimal
    )
    if ($numericTypeCodes -notcontains [System.Type]::GetTypeCode($Value.GetType())) {
        throw "Release gate metric '$Name' must be a JSON number."
    }
    $number = [System.Convert]::ToDouble($Value, [System.Globalization.CultureInfo]::InvariantCulture)
    if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) {
        throw "Release gate metric '$Name' must be finite."
    }
    return $number
}

function Assert-RequiredReleaseMetrics([object]$Metrics) {
    if ($null -eq $Metrics -or $Metrics -isnot [pscustomobject]) {
        throw "Release gate metrics must be an object."
    }
    $rules = @(
        [PSCustomObject]@{ Name = "financial_total"; Kind = "equal"; Expected = 856.0 },
        [PSCustomObject]@{ Name = "financial_passed"; Kind = "equal"; Expected = 856.0 },
        [PSCustomObject]@{ Name = "financial_failed"; Kind = "equal"; Expected = 0.0 },
        [PSCustomObject]@{ Name = "judge_case_count"; Kind = "equal"; Expected = 600.0 },
        [PSCustomObject]@{ Name = "judge_evaluated_count"; Kind = "equal"; Expected = 600.0 },
        [PSCustomObject]@{ Name = "judge_pass_count"; Kind = "equal"; Expected = 600.0 },
        [PSCustomObject]@{ Name = "judge_failure_count"; Kind = "equal"; Expected = 0.0 },
        [PSCustomObject]@{ Name = "judge_numeric_exactness"; Kind = "equal"; Expected = 1.0 },
        [PSCustomObject]@{ Name = "judge_claim_citation_coverage"; Kind = "equal"; Expected = 1.0 },
        [PSCustomObject]@{ Name = "judge_answerability_agreement"; Kind = "minimum"; Expected = 0.95 },
        [PSCustomObject]@{ Name = "judge_metamorphic_consistency"; Kind = "minimum"; Expected = 0.98 },
        [PSCustomObject]@{ Name = "retrieval_recall_at_20"; Kind = "minimum"; Expected = 0.95 },
        [PSCustomObject]@{ Name = "concurrency_request_count"; Kind = "equal"; Expected = 20.0 },
        [PSCustomObject]@{ Name = "concurrency_error_count"; Kind = "equal"; Expected = 0.0 },
        [PSCustomObject]@{ Name = "concurrency_p95_ms"; Kind = "maximum"; Expected = 2000.0 },
        [PSCustomObject]@{ Name = "provider_eligible_root_count"; Kind = "equal"; Expected = 42.0 },
        [PSCustomObject]@{ Name = "provider_probe_observation_count"; Kind = "equal"; Expected = 120.0 },
        [PSCustomObject]@{ Name = "provider_call_count"; Kind = "equal"; Expected = 120.0 },
        [PSCustomObject]@{ Name = "provider_p95_ms"; Kind = "maximum"; Expected = 10000.0 },
        [PSCustomObject]@{ Name = "hallucinated_numeric_claim_count"; Kind = "equal"; Expected = 0.0 },
        [PSCustomObject]@{ Name = "unknown_citation_count"; Kind = "equal"; Expected = 0.0 },
        [PSCustomObject]@{ Name = "cross_filing_citation_count"; Kind = "equal"; Expected = 0.0 },
        [PSCustomObject]@{ Name = "policy_violation_count"; Kind = "equal"; Expected = 0.0 },
        [PSCustomObject]@{ Name = "secret_leak_count"; Kind = "equal"; Expected = 0.0 },
        [PSCustomObject]@{ Name = "evaluator_error_count"; Kind = "equal"; Expected = 0.0 },
        [PSCustomObject]@{ Name = "forbidden_provider_call_count"; Kind = "equal"; Expected = 0.0 },
        [PSCustomObject]@{ Name = "deterministic_provider_call_count"; Kind = "equal"; Expected = 0.0 }
    )
    $requiredNames = @($rules | ForEach-Object { $_.Name })
    $unexpectedNames = @($Metrics.PSObject.Properties.Name | Where-Object { $requiredNames -notcontains $_ })
    if ($unexpectedNames.Count -ne 0) {
        throw "Release gate metrics contain unsupported properties."
    }
    foreach ($rule in $rules) {
        if ($Metrics.PSObject.Properties.Name -notcontains $rule.Name) {
            throw "Release gate metric '$($rule.Name)' is missing."
        }
        $actual = ConvertTo-FiniteMetric $Metrics.($rule.Name) $rule.Name
        if ($rule.Kind -eq "equal" -and $actual -ne $rule.Expected) {
            throw "Release gate metric '$($rule.Name)' must equal $($rule.Expected)."
        }
        if ($rule.Kind -eq "minimum" -and $actual -lt $rule.Expected) {
            throw "Release gate metric '$($rule.Name)' must be at least $($rule.Expected)."
        }
        if ($rule.Kind -eq "maximum" -and $actual -gt $rule.Expected) {
            throw "Release gate metric '$($rule.Name)' must be at most $($rule.Expected)."
        }
    }
}

function Assert-ReleaseGateReport {
    param(
        [string]$Path,
        [string]$Commit,
        [string]$ImageId,
        [hashtable]$DataIdentities
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Release gate report is missing; deployment is refused: $Path"
    }
    $reportFile = Get-Item -LiteralPath $Path
    if ($reportFile.Length -le 0 -or $reportFile.Length -gt 1MB) {
        throw "Release gate report size is invalid."
    }
    try {
        $report = [System.IO.File]::ReadAllText($reportFile.FullName) | ConvertFrom-Json
    }
    catch {
        throw "Release gate report is not valid JSON."
    }

    $rootProperties = @("schema_version", "evaluated_at_utc", "release_state", "hard_gate_passed", "hard_gate_reasons", "metrics", "identity", "source_timestamps")
    foreach ($propertyName in $rootProperties) {
        if ($report.PSObject.Properties.Name -notcontains $propertyName) {
            throw "Release gate report is missing '$propertyName'."
        }
    }
    $unexpectedProperties = @($report.PSObject.Properties.Name | Where-Object { $rootProperties -notcontains $_ })
    if ($unexpectedProperties.Count -ne 0) {
        throw "Release gate report contains unsupported properties."
    }
    if ($report.schema_version -isnot [string] -or $report.schema_version -cne "release-gate-summary-v1") {
        throw "Unsupported release gate report schema."
    }
    if ($report.hard_gate_passed -isnot [bool] -or $report.hard_gate_passed -ne $true) {
        throw "Release gate did not pass; deployment is refused."
    }
    if ($report.release_state -isnot [string] -or $report.release_state -ne "PASS") {
        throw "Release state is not PASS; deployment is refused."
    }
    if ($report.hard_gate_reasons -isnot [System.Array] -or $report.hard_gate_reasons.Count -ne 0) {
        throw "PASS release gate report must contain an actual empty hard_gate_reasons array."
    }
    Assert-RequiredReleaseMetrics $report.metrics
    if ($null -eq $report.source_timestamps -or $report.source_timestamps -isnot [pscustomobject]) {
        throw "Release gate source_timestamps must be an object."
    }
    $evaluatedAt = ConvertTo-ValidatedUtcTimestamp $report.evaluated_at_utc "Release gate evaluated_at_utc"
    $reportAge = [DateTimeOffset]::UtcNow - $evaluatedAt
    if ($reportAge.TotalSeconds -gt 86400 -or $reportAge.TotalSeconds -lt -300) {
        throw "Release gate report is stale or from the future."
    }
    $sourceProperties = @("financial", "judge", "retrieval", "deployment")
    $unexpectedSources = @($report.source_timestamps.PSObject.Properties.Name | Where-Object { $sourceProperties -notcontains $_ })
    if ($unexpectedSources.Count -ne 0) {
        throw "Release gate source_timestamps contains unsupported properties."
    }
    foreach ($sourceName in $sourceProperties) {
        if ($report.source_timestamps.PSObject.Properties.Name -notcontains $sourceName) {
            throw "Release gate source timestamp is missing '$sourceName'."
        }
        $sourceTimestamp = ConvertTo-ValidatedUtcTimestamp $report.source_timestamps.$sourceName "Release gate source timestamp '$sourceName'"
        $sourceAge = [DateTimeOffset]::UtcNow - $sourceTimestamp
        if ($sourceAge.TotalSeconds -gt 86400 -or $sourceAge.TotalSeconds -lt -300) {
            throw "Release gate source timestamp '$sourceName' is stale or from the future."
        }
    }

    $identity = $report.identity
    foreach ($propertyName in @("commit", "image_id", "base_sha256", "overlay_sha256", "search_index_sha256")) {
        if ($null -eq $identity -or $identity.PSObject.Properties.Name -notcontains $propertyName) {
            throw "Release identity is missing '$propertyName'."
        }
    }
    if ($identity.commit -isnot [string] -or $identity.commit -notmatch '^[0-9a-fA-F]{40}$') {
        throw "Release commit identity is invalid."
    }
    if ($identity.image_id -isnot [string] -or $identity.image_id -notmatch '^sha256:[0-9a-fA-F]{64}$') {
        throw "Release image identity is invalid."
    }
    if (-not [string]::Equals($identity.commit, $Commit, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Release commit identity does not match the expected commit."
    }
    if (-not [string]::Equals($identity.image_id, $ImageId, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Release image identity does not match the expected image."
    }

    $actualData = $identity
    foreach ($propertyName in $DataIdentities.Keys) {
        if ($null -eq $actualData -or $actualData.PSObject.Properties.Name -notcontains $propertyName) {
            throw "Release data identity is missing '$propertyName'."
        }
        $actual = [string]$actualData.$propertyName
        Assert-Sha256 $actual "Release data identity '$propertyName'"
        if (-not [string]::Equals($actual, [string]$DataIdentities[$propertyName], [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Release data identity '$propertyName' does not match the expected value."
        }
    }

    return [PSCustomObject]@{
        Commit = ([string]$identity.commit).ToLowerInvariant()
        ImageId = ([string]$identity.image_id).ToLowerInvariant()
        DataIdentities = $actualData
    }
}

function Invoke-RemoteScript {
    param([string]$Target, [string]$ScriptText, [string]$Arguments, [string]$PrivateKeyPath, [string]$Step)
    if ($PrivateKeyPath) {
        $ScriptText | & ssh -i $PrivateKeyPath $Target $Arguments
    }
    else {
        $ScriptText | & ssh $Target $Arguments
    }
    Assert-LastExitCode $Step
}

function Invoke-Rollback {
    param(
        [string]$Target,
        [string]$CandidateDirectory,
        [string]$RollbackTag,
        [string]$PrivateKeyPath
    )
    $rollbackScript = @'
set -euo pipefail
candidate_dir="$1"
rollback_ref="$2"
cd "$candidate_dir"
if ! docker image inspect "$rollback_ref" >/dev/null 2>&1; then
    printf '%s\n' "rollback image is absent: $rollback_ref" >&2
    exit 41
fi
export DISCLOSURE_RELEASE_IMAGE="$rollback_ref"
export DISCLOSURE_EXPECTED_COMMIT="rollback"
docker compose -p mirae-release -f compose.release.yaml up -d --no-build --no-deps disclosure-agent
container="$(docker compose -p mirae-release -f compose.release.yaml ps -q disclosure-agent)"
test -n "$container"
rollback_image_id="$(docker image inspect --format '{{.Id}}' "$rollback_ref")"
test -n "$rollback_image_id"
active_image_id="$(docker inspect --format '{{.Image}}' "$container")"
test -n "$active_image_id"
test "$active_image_id" = "$rollback_image_id"
curl --fail --silent --show-error http://127.0.0.1:8000/health | python3 -c 'import json,sys; assert json.load(sys.stdin)["ready"]'
curl --fail --silent --show-error --output /dev/null http://127.0.0.1:8000/
'@
    $rollbackArguments = "bash -s -- '$CandidateDirectory' '$RollbackTag'"
    Invoke-RemoteScript -Target $Target -ScriptText $rollbackScript -Arguments $rollbackArguments -PrivateKeyPath $PrivateKeyPath -Step "production rollback"
}

if ($ServerHost -notmatch '^[A-Za-z0-9.-]+$' -or $SshUser -notmatch '^[A-Za-z0-9._-]+$') {
    throw "ServerHost or SshUser contains unsupported characters."
}
Assert-SafeAbsoluteUnixPath $RemoteDirectory "RemoteDirectory"
Assert-SafeAbsoluteUnixPath $RollbackDirectory "RollbackDirectory"
if ($ExpectedCommit -notmatch '^[0-9a-fA-F]{40}$') {
    throw "ExpectedCommit must be a full 40-character Git commit."
}
if ($ExpectedImageId -notmatch '^sha256:[0-9a-fA-F]{64}$') {
    throw "ExpectedImageId must be a Docker sha256 image ID."
}
$expectedData = @{
    base_sha256 = $ExpectedBaseDbSha256
    overlay_sha256 = $ExpectedOverlayDbSha256
    search_index_sha256 = $ExpectedSearchIndexSha256
}
foreach ($entry in $expectedData.GetEnumerator()) {
    Assert-Sha256 ([string]$entry.Value) "Expected $($entry.Key)"
}

$gate = Assert-ReleaseGateReport -Path $ReleaseGatePath -Commit $ExpectedCommit -ImageId $ExpectedImageId -DataIdentities $expectedData
# RELEASE_GATE_VALIDATED_BEFORE_MUTATION

$head = (& git -C $repository rev-parse HEAD).Trim()
Assert-LastExitCode "read Git commit"
if (-not [string]::Equals($head, $gate.Commit, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Working tree commit does not match the release gate."
}

$shortCommit = $gate.Commit.Substring(0, 12)
$shortImage = $gate.ImageId.Substring(7, 12)
$candidateTag = "mirae-disclosure-agent:candidate-$shortCommit-$shortImage"
$candidateDirectory = "$RemoteDirectory/candidate-$shortCommit-$shortImage"
$rollbackStamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$rollbackTag = "mirae-disclosure-agent:rollback-$rollbackStamp-$shortImage"
$rollbackArchive = "$RollbackDirectory/mirae-disclosure-agent-rollback-$rollbackStamp-$shortImage.tar"
$target = "$SshUser@$ServerHost"

$promotionScript = @'
set -euo pipefail
candidate_dir="$1"
candidate_ref="$2"
expected_image="$3"
expected_commit="$4"
expected_base="$5"
expected_overlay="$6"
expected_search="$7"
rollback_ref="$8"
rollback_archive="$9"
rollback_dir="${10}"

cd "$candidate_dir"
test -f candidate.identity
assert_candidate_line() {
    grep -Fx "$1=$2" candidate.identity >/dev/null
}
assert_candidate_line schema release-deployment.v1
assert_candidate_line commit "$expected_commit"
assert_candidate_line image_id "$expected_image"
assert_candidate_line image_ref "$candidate_ref"
assert_candidate_line base_sha256 "$expected_base"
assert_candidate_line overlay_sha256 "$expected_overlay"
assert_candidate_line search_index_sha256 "$expected_search"
recorded_image_archive_sha="$(awk -F= '$1 == "image_archive_sha256" {print $2}' candidate.identity)"
test -n "$recorded_image_archive_sha"
test "$recorded_image_archive_sha" = "$(sha256sum candidate-image.tar | awk '{print $1}')"

check_hash() {
    actual="$(sha256sum "$1" | awk '{print $1}')"
    test "$actual" = "$2"
}
check_hash /srv/mirae/data/base/disclosure.sqlite "$expected_base"
check_hash /srv/mirae/data/agent/agent_overlay.sqlite "$expected_overlay"
check_hash /srv/mirae/data/agent/agent_search.sqlite "$expected_search"

candidate_image_id="$(docker image inspect --format '{{.Id}}' "$candidate_ref")"
test "$candidate_image_id" = "$expected_image"
export DISCLOSURE_RELEASE_IMAGE="$candidate_ref"
export DISCLOSURE_EXPECTED_COMMIT="$expected_commit"

staging_container="$(docker compose -p mirae-release -f compose.release.yaml ps -q disclosure-agent-staging)"
dense_container="$(docker compose -p mirae-release -f compose.release.yaml ps -q dense-retriever)"
test -n "$staging_container"
test -n "$dense_container"
stagingImageId="$(docker inspect --format '{{.Image}}' "$staging_container")"
test "$stagingImageId" = "$expected_image"

assert_ro_mounts() {
    container="$1"
    shift
    mounts="$(docker inspect --format '{{range .Mounts}}{{printf "%s=%v\n" .Destination .RW}}{{end}}' "$container")"
    for destination in "$@"; do
        printf '%s\n' "$mounts" | grep -Fqx "$destination=false"
    done
}
assert_ro_mounts "$staging_container" /data/base/disclosure.sqlite /data/agent/agent_overlay.sqlite /data/agent/agent_search.sqlite /data/attestation.json
assert_ro_mounts "$dense_container" /data/dense /model

production_container="$(docker compose -p mirae-release -f compose.release.yaml ps -q disclosure-agent)"
test -n "$production_container"
previous_image_id="$(docker inspect --format '{{.Image}}' "$production_container")"
test -n "$previous_image_id"
if docker image inspect "$rollback_ref" >/dev/null 2>&1; then
    exit 1
fi
mkdir -p "$rollback_dir"
test ! -e "$rollback_archive"
test ! -e "$rollback_archive.sha256"
docker tag "$previous_image_id" "$rollback_ref"
docker save --output "$rollback_archive" "$rollback_ref"
sha256sum "$rollback_archive" > "$rollback_archive.sha256"
chmod 0444 "$rollback_archive" "$rollback_archive.sha256"

docker compose -p mirae-release -f compose.release.yaml up -d --no-build --no-deps disclosure-agent
production_container="$(docker compose -p mirae-release -f compose.release.yaml ps -q disclosure-agent)"
production_image_id="$(docker inspect --format '{{.Image}}' "$production_container")"
test "$production_image_id" = "$expected_image"
assert_ro_mounts "$production_container" /data/base/disclosure.sqlite /data/agent/agent_overlay.sqlite /data/agent/agent_search.sqlite /data/attestation.json
curl --fail --silent --show-error http://127.0.0.1:8000/health | python3 -c 'import json,sys; assert json.load(sys.stdin)["ready"]'
curl --fail --silent --show-error --output /dev/null http://127.0.0.1:8000/
'@

$promotionArguments = "bash -s -- '$candidateDirectory' '$candidateTag' '$($gate.ImageId)' '$($gate.Commit)' '$($ExpectedBaseDbSha256.ToLowerInvariant())' '$($ExpectedOverlayDbSha256.ToLowerInvariant())' '$($ExpectedSearchIndexSha256.ToLowerInvariant())' '$rollbackTag' '$rollbackArchive' '$RollbackDirectory'"
try {
    Invoke-RemoteScript -Target $target -ScriptText $promotionScript -Arguments $promotionArguments -PrivateKeyPath $KeyPath -Step "production promotion or smoke test"
}
catch {
    $promotionError = $_
    try {
        Invoke-Rollback -Target $target -CandidateDirectory $candidateDirectory -RollbackTag $rollbackTag -PrivateKeyPath $KeyPath
    }
    catch {
        Write-Error "Promotion failed and rollback verification also failed."
    }
    throw $promotionError
}

Write-Output ([PSCustomObject]@{
    release_state = "PROMOTED"
    commit = $gate.Commit
    image_id = $gate.ImageId
    staging_image_id = $gate.ImageId
    production_image_id = $gate.ImageId
    rollback_tag = $rollbackTag
    rollback_archive = $rollbackArchive
})
