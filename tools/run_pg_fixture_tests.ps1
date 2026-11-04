# One-command disposable PostgreSQL verification.
#
# Creates a brand-new owned cluster (via ci_postgres_fixture.ps1), runs pytest with
# `--postgres-config`, then stops the cluster and removes the disposable data
# directory. The pytest JSON config is written outside the repository.
#
# Data directory placement: some harnesses only let child processes write inside the
# workspace, so the cluster defaults to <repo>/.tmp-pg-fixture. Pass -DataRoot to
# choose another path that those child processes can write to. Never point it at an
# existing cluster or an operating database.
param(
    [string]$BinDir,
    [string]$DataRoot,
    [int]$Port = 0,
    [string[]]$PytestArgs,
    [switch]$KeepFixture
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$fixtureScript = Join-Path $PSScriptRoot 'ci_postgres_fixture.ps1'

if (-not $BinDir) {
    $candidates = @(
        (Join-Path $repoRoot '.tmp-perf\pg\pgsql\bin'),
        (Join-Path $repoRoot '.tmp-pg\pgsql\bin')
    )
    $BinDir = $candidates | Where-Object { Test-Path (Join-Path $_ 'initdb.exe') } | Select-Object -First 1
}
if (-not $BinDir -or -not (Test-Path (Join-Path $BinDir 'initdb.exe'))) {
    throw "pass -BinDir pointing at a PostgreSQL bin directory (contains initdb.exe, pg_ctl.exe, psql.exe)"
}
$BinDir = (Resolve-Path $BinDir).Path

if (-not $DataRoot) { $DataRoot = Join-Path $repoRoot '.tmp-pg-fixture' }
if (Test-Path $DataRoot) {
    throw "refusing to reuse an existing data directory: $DataRoot (remove it first or pass -DataRoot)"
}

$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
if (-not $PytestArgs) {
    # Browser cases need their own fixture configuration; exclude them by default.
    $files = Get-ChildItem (Join-Path $repoRoot 'tests') -Filter 'test_*.py' |
        Where-Object { $_.Name -notlike '*browser*' } | ForEach-Object { $_.FullName }
    $PytestArgs = @('-q') + $files
}

New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
$dataDir = Join-Path $DataRoot 'data'
$configDir = Join-Path $env:TEMP ('talkabout-pg-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$configPath = Join-Path $configDir 'pg-fixture.json'

if ($Port -eq 0) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $Port = $listener.LocalEndpoint.Port
    $listener.Stop()
}

Write-Output "fixture: bin=$BinDir data=$dataDir port=$Port config=$configPath"
$code = 1
try {
    & $fixtureScript -Action create -BinDir $BinDir -DataDir $dataDir -ConfigOut $configPath -Port $Port
    if ($LASTEXITCODE -ne 0) { throw "fixture creation failed with exit code $LASTEXITCODE" }

    Write-Output "pytest: $python -m pytest $($PytestArgs -join ' ') --postgres-config <temp>/pg-fixture.json"
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $python -m pytest @PytestArgs --postgres-config $configPath -p no:cacheprovider
    $code = $LASTEXITCODE
}
finally {
    if (Test-Path (Join-Path $dataDir 'postmaster.pid')) {
        & $fixtureScript -Action stop -BinDir $BinDir -DataDir $dataDir -ConfigOut $configPath
    }
    if (-not $KeepFixture -and (Test-Path $DataRoot)) {
        $resolved = (Resolve-Path $DataRoot).Path
        $inside = $resolved.StartsWith($repoRoot + [IO.Path]::DirectorySeparatorChar)
        if (-not $inside -or -not (Split-Path $resolved -Leaf).StartsWith('.tmp-pg-')) {
            throw "refusing to remove a path that is not an owned workspace fixture: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
        Write-Output "fixture removed: $resolved"
    }
}
exit $code
