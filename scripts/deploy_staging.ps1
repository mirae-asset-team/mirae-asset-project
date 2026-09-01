[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ServerHost,
    [Parameter(Mandatory = $true)][string]$SshUser,
    [Parameter(Mandatory = $true)][string]$KeyPath,
    [string]$RemoteDirectory = "/srv/mirae/staging/app"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
    throw "SSH private key was not found: $KeyPath"
}
if ($ServerHost -notmatch '^[A-Za-z0-9.-]+$' -or $SshUser -notmatch '^[A-Za-z0-9._-]+$') {
    throw "ServerHost or SshUser contains unsupported characters."
}
if ($RemoteDirectory -notmatch '^/[A-Za-z0-9._/-]+$') {
    throw "RemoteDirectory must be an absolute safe path."
}

$repository = Split-Path -Parent $PSScriptRoot
$archive = Join-Path $env:TEMP ("mirae-staging-" + [guid]::NewGuid().ToString("N") + ".tar.gz")
$remoteArchive = "/tmp/mirae-staging-upload.tar.gz"
$target = "$SshUser@$ServerHost"

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}

try {
    Push-Location $repository
    & tar --exclude="eval/qa_results" -czf $archive Dockerfile pyproject.toml compose.yaml src config eval
    Assert-LastExitCode "package"
    Pop-Location

    & scp -i $KeyPath $archive "${target}:$remoteArchive"
    Assert-LastExitCode "upload"

    $remoteCommand = @(
        "mkdir -p '$RemoteDirectory'",
        "tar -xzf '$remoteArchive' -C '$RemoteDirectory'",
        "cd '$RemoteDirectory'",
        "docker compose build disclosure-agent-staging",
        "docker compose up -d --no-deps disclosure-agent-staging",
        "curl --fail --silent --show-error http://127.0.0.1:8001/health"
    ) -join " && "
    & ssh -i $KeyPath $target $remoteCommand
    Assert-LastExitCode "staging deployment or health check"
}
finally {
    if ((Get-Location).Path -ne $repository) { Pop-Location -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
}
