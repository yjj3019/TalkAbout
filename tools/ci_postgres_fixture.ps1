# Owned, disposable PostgreSQL fixture for local runs and Windows CI.
#
# Creates a brand-new cluster in $DataDir, listens on 127.0.0.1 only, writes the
# pytest `--postgres-config` JSON outside the repository, and refuses to reuse an
# existing data directory. `stop` shuts down only that owned cluster.
#
# No installation, no elevation, no existing service and no operating DSN is used.
param(
    [Parameter(Mandatory)][ValidateSet('create', 'stop')][string]$Action,
    [Parameter(Mandatory)][string]$BinDir,
    [Parameter(Mandatory)][string]$DataDir,
    [Parameter(Mandatory)][string]$ConfigOut,
    [int]$Port = 55432,
    [string]$User = 'fixture'
)

$ErrorActionPreference = 'Stop'
$pgCtl = Join-Path $BinDir 'pg_ctl.exe'
$psql = Join-Path $BinDir 'psql.exe'
$initdb = Join-Path $BinDir 'initdb.exe'
$log = Join-Path ([IO.Path]::GetDirectoryName($DataDir)) 'postgres-server.log'

function Start-Owned([string]$exe, [string]$argumentLine, [string]$tag, [switch]$NoWait) {
    # A child that inherits the caller's console/stdout handles keeps that pipe
    # open and stalls any harness waiting on it. Give the child files instead so
    # the long-lived server owns no handle back to the caller. `-Wait` also waits
    # for the whole process tree, which the server never exits, so `start` polls.
    $stdout = "$log.$tag.out"
    $stderr = "$log.$tag.err"
    $options = @{
        FilePath = $exe; ArgumentList = $argumentLine; PassThru = $true
        WindowStyle = 'Hidden'; RedirectStandardOutput = $stdout; RedirectStandardError = $stderr
    }
    if (-not $NoWait) { $options.Wait = $true }
    $process = Start-Process @options
    if ($NoWait) { return $process }
    if ($process.ExitCode -ne 0) {
        $detail = (Get-Content $stderr -ErrorAction SilentlyContinue | Select-Object -Last 3) -join ' '
        throw "$([IO.Path]::GetFileName($exe)) failed with exit code $($process.ExitCode): $detail"
    }
}

function Invoke-Sql([string]$sql, [string]$password) {
    $previous = $env:PGPASSWORD
    $env:PGPASSWORD = $password
    try {
        $value = & $psql -w -h 127.0.0.1 -p $Port -U $User -d postgres -tAc $sql
        if ($LASTEXITCODE -ne 0) { throw "psql failed with exit code $LASTEXITCODE" }
        return $value
    }
    finally { $env:PGPASSWORD = $previous }
}

if ($Action -eq 'create') {
    if (Test-Path $DataDir) { throw "refusing to reuse an existing data directory: $DataDir" }
    New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($DataDir)) | Out-Null

    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $password = [Convert]::ToBase64String($bytes).Replace('+', 'A').Replace('/', 'B').Replace('=', 'C')
    $passwordFile = Join-Path ([IO.Path]::GetTempPath()) ('talkabout-pg-' + [guid]::NewGuid().ToString('N') + '.txt')
    [IO.File]::WriteAllText($passwordFile, $password)
    try {
        Start-Owned $initdb "-D `"$DataDir`" -U `"$User`" --auth-host=scram-sha-256 --auth-local=trust --pwfile=`"$passwordFile`" -E UTF8 --no-locale" 'initdb'
    }
    finally { Remove-Item -LiteralPath $passwordFile -Force -ErrorAction SilentlyContinue }

    Start-Owned $pgCtl "-D `"$DataDir`" -o `"-p $Port -c listen_addresses=127.0.0.1`" -l `"$log`" -w start" 'start' -NoWait | Out-Null
    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try { $null = Invoke-Sql 'select 1' $password 2>$null; $ready = $true; break }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw "the owned server never accepted a loopback connection on port $Port" }

    $reported = (Invoke-Sql "select current_setting('data_directory') || '|' || current_setting('listen_addresses')" $password).Trim()
    $actualData, $actualListen = $reported -split '\|', 2
    $resolved = (Resolve-Path -LiteralPath $DataDir).Path
    if ([IO.Path]::GetFullPath($actualData) -ne [IO.Path]::GetFullPath($resolved)) {
        throw "server data_directory mismatch: $actualData"
    }
    if ($actualListen -ne '127.0.0.1') { throw "server listen_addresses must be 127.0.0.1, got $actualListen" }

    $config = [ordered]@{
        ephemeral_fixture = $true
        host = '127.0.0.1'
        port = $Port
        user = $User
        password = $password
        data_directory = $resolved
    } | ConvertTo-Json
    New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($ConfigOut)) | Out-Null
    [IO.File]::WriteAllText($ConfigOut, $config)
    Write-Output "fixture_ready port=$Port data_directory=$resolved config=$ConfigOut"
}
else {
    if (-not (Test-Path (Join-Path $DataDir 'postmaster.pid'))) {
        Write-Output "fixture_not_running data_directory=$DataDir"
        exit 0
    }
    Start-Owned $pgCtl "-D `"$DataDir`" -m fast -w stop" 'stop'
    Write-Output "fixture_stopped data_directory=$DataDir"
}
