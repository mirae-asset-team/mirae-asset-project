param(
  [ValidateSet('Local','Docker')][string]$Mode = 'Local',
  [string]$DataRoot = 'D:\mirae-asset-project'
)

$baseDb = Join-Path $DataRoot 'db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite'
$agentDir = Join-Path $DataRoot 'db\agent'
$attestation = Join-Path $PSScriptRoot '..\data\derived\database_distribution_manifest_semantic_v1.json'
$required = @(
  $baseDb,
  (Join-Path $agentDir 'agent_overlay.sqlite'),
  (Join-Path $agentDir 'agent_search.sqlite'),
  $attestation
)
foreach ($path in $required) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "Required runtime file missing: $path"
  }
}

$env:DISCLOSURE_BASE_DB = $baseDb
$env:DISCLOSURE_OVERLAY_DB = Join-Path $agentDir 'agent_overlay.sqlite'
$env:DISCLOSURE_SEARCH_DB = Join-Path $agentDir 'agent_search.sqlite'
$env:DISCLOSURE_ATTESTATION = $attestation

if ($Mode -eq 'Docker') {
  $env:DISCLOSURE_BASE_DB_HOST = $baseDb
  $env:DISCLOSURE_AGENT_DB_DIR_HOST = $agentDir
  $env:DISCLOSURE_ATTESTATION_HOST = $attestation
  $env:DISCLOSURE_RUNTIME_DIR_HOST = Join-Path $DataRoot 'runs'
  docker compose up -d --build
  exit $LASTEXITCODE
}

$agentCommand = Get-Command disclosure-agent -ErrorAction SilentlyContinue
if ($null -ne $agentCommand) {
  & $agentCommand.Source serve
} else {
  $workspacePython = Join-Path $PSScriptRoot '..\..\.venv\Scripts\python.exe'
  if (Test-Path -LiteralPath $workspacePython -PathType Leaf) {
    & $workspacePython -m disclosure_db.cli serve
  } else {
    & python -m disclosure_db.cli serve
  }
}
exit $LASTEXITCODE
