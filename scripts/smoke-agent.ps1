param(
  [string]$BaseUrl = 'http://127.0.0.1:8000'
)

$ErrorActionPreference = 'Stop'
$root = $BaseUrl.TrimEnd('/')
$health = $null
$deadline = [DateTime]::UtcNow.AddSeconds(60)
while ([DateTime]::UtcNow -lt $deadline) {
  try {
    $health = Invoke-RestMethod -Method Get -Uri "$root/health"
    if ($health.ready -eq $true) { break }
  } catch {
    $health = $null
  }
  Start-Sleep -Seconds 1
}
if ($null -eq $health -or $health.ready -ne $true) {
  throw 'Agent did not become ready within 60 seconds.'
}
if ([string]::IsNullOrWhiteSpace([string]$health.request_id)) {
  throw 'Health response is missing request_id.'
}

function Invoke-SmokeQuery([hashtable]$Payload) {
  $body = $Payload | ConvertTo-Json -Compress
  return Invoke-RestMethod -Method Post -Uri "$root/query" -ContentType 'application/json' -Body $body
}

$answerable = Invoke-SmokeQuery @{ question_id = 'smoke-answerable'; question = '고려아연의 2024-12-31 연결 XI. 당기순이익은 얼마인가?' }
if ([string]::IsNullOrWhiteSpace([string]$answerable.request_id)) { throw 'Answerable response is missing request_id.' }
if ($answerable.answerable -ne $true -or $answerable.verified -ne $true) { throw 'Answerable smoke response was not verified.' }
if ($null -eq $answerable.evidence -or @($answerable.evidence).Count -lt 1) { throw 'Answerable response has no evidence.' }

$abstention = Invoke-SmokeQuery @{ question_id = 'smoke-abstention'; question = '오늘 서울 날씨를 알려줘.' }
if ([string]::IsNullOrWhiteSpace([string]$abstention.request_id)) { throw 'Abstention response is missing request_id.' }
if ($abstention.answerable -ne $false -or $abstention.verified -ne $false) { throw 'Out-of-scope smoke response did not abstain.' }
if ($null -ne $abstention.evidence -and @($abstention.evidence).Count -ne 0) { throw 'Abstention response contains evidence.' }

Write-Output 'Agent smoke checks passed.'
