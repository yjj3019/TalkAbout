# One-command disposable browser E2E verification.
#
# Starts the explicit synthetic MemoryStore fixture (tests/invite_browser_fixture_app.py)
# on a free loopback port, writes a temporary `--invite-browser-config` JSON, runs the
# real-Chrome Playwright suites, then stops the fixture and removes the temp directory.
# This never points at an operating deployment, a real provider or a real database.
#
# Chrome defaults to TALKABOUT_TEST_CHROMIUM, then the standard Windows install path.
# Pass -Chrome to override. -PytestArgs replaces the default suite selection (which is
# every tests/test_browser_*.py file).
param(
    [string]$Python,
    [string]$Chrome,
    [string]$Root,
    [string[]]$PytestArgs,
    [switch]$KeepFixture
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if (-not $Python) { $Python = Join-Path $repoRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path $Python)) {
    throw "Python interpreter not found: $Python (pass -Python or create .venv)"
}
$Python = (Resolve-Path $Python).Path

if (-not $Chrome) {
    if ($env:TALKABOUT_TEST_CHROMIUM) { $Chrome = $env:TALKABOUT_TEST_CHROMIUM }
    else { $Chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe' }
}
if (-not (Test-Path $Chrome)) {
    throw "Chrome executable not found: $Chrome (pass -Chrome or set TALKABOUT_TEST_CHROMIUM)"
}
$Chrome = (Resolve-Path $Chrome).Path

if (-not $Root) { $Root = Join-Path $repoRoot '.tmp-browser-e2e' }
if (Test-Path $Root) {
    throw "refusing to reuse an existing fixture directory: $Root (remove it first or pass -Root)"
}

if (-not $PytestArgs) {
    $files = Get-ChildItem (Join-Path $repoRoot 'tests') -Filter 'test_browser_*.py' |
        ForEach-Object { $_.FullName }
    if (-not $files) { throw 'no tests/test_browser_*.py suites found' }
    $PytestArgs = @('-q') + $files
}

New-Item -ItemType Directory -Force -Path $Root | Out-Null
$screenshots = Join-Path $Root 'shots'
New-Item -ItemType Directory -Force -Path $screenshots | Out-Null
$configPath = Join-Path $Root 'browser-fixture.json'
$logPath = Join-Path $Root 'fixture.log'
$errPath = Join-Path $Root 'fixture.err.log'
$fixtureId = [guid]::NewGuid().ToString()

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$listener.Stop()
$base = 'http://127.0.0.1:' + $port

$config = @{
    synthetic_memory_fixture = $true
    fixture_id               = $fixtureId
    base_url                 = $base
    chrome                   = $Chrome
    screenshots              = $screenshots
}
$config | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $configPath -Encoding utf8

Write-Output "fixture: base=$base id=$fixtureId config=$configPath chrome=$Chrome"
$server = $null
$code = 1
$previousPort = $env:TALKABOUT_FIXTURE_PORT
$previousId = $env:TALKABOUT_INVITE_FIXTURE_ID
try {
    $env:TALKABOUT_FIXTURE_PORT = [string]$port
    $env:TALKABOUT_INVITE_FIXTURE_ID = $fixtureId
    $server = Start-Process -FilePath $Python `
        -ArgumentList @('-u', (Join-Path $repoRoot 'tests\invite_browser_fixture_app.py')) `
        -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $logPath -RedirectStandardError $errPath

    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt += 1) {
        if ($server.HasExited) {
            throw "fixture exited early with code $($server.ExitCode); see $errPath"
        }
        try {
            $identity = Invoke-RestMethod -Uri ($base + '/__invite_fixture__') -TimeoutSec 1 -NoProxy
            if ($identity.synthetic_memory_fixture -eq $true -and $identity.fixture_id -eq $fixtureId) {
                $ready = $true
                break
            }
        }
        catch { Start-Sleep -Milliseconds 250 }
    }
    if (-not $ready) { throw "fixture did not become ready at $base; see $errPath" }
    Write-Output 'fixture_ready'

    Write-Output "pytest: $Python -m pytest $($PytestArgs -join ' ') --invite-browser-config <fixture>/browser-fixture.json"
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $Python -m pytest @PytestArgs --invite-browser-config $configPath -p no:cacheprovider
    $code = $LASTEXITCODE
}
finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        $server.WaitForExit(5000) | Out-Null
    }
    if ($null -eq $previousPort) { Remove-Item Env:TALKABOUT_FIXTURE_PORT -ErrorAction SilentlyContinue }
    else { $env:TALKABOUT_FIXTURE_PORT = $previousPort }
    if ($null -eq $previousId) { Remove-Item Env:TALKABOUT_INVITE_FIXTURE_ID -ErrorAction SilentlyContinue }
    else { $env:TALKABOUT_INVITE_FIXTURE_ID = $previousId }

    if (-not $KeepFixture -and (Test-Path $Root)) {
        $resolved = (Resolve-Path $Root).Path
        $inside = $resolved.StartsWith($repoRoot + [IO.Path]::DirectorySeparatorChar)
        if (-not $inside -or -not (Split-Path $resolved -Leaf).StartsWith('.tmp-browser-')) {
            throw "refusing to remove a path that is not an owned workspace fixture: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
        Write-Output "fixture removed: $resolved"
    }
}
exit $code
