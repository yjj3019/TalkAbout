# ---------------------------------------------------------------------------
# TalkAbout Windows connection helper.
#
# The room page generates a small .cmd file that calls this script:
#
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::
#   Create((Invoke-RestMethod -UseBasicParsing 'ORIGIN/web/connect-agent.ps1')))
#   -Server 'ORIGIN' -Room 'ROOM' -Agent 'AGENT' -Runtime 'codex'
#   -AuthMode 'native' -MaxModelCalls 4 [-RequestTurn] [-DryRun] [-Update]"
#
# Running this file with -File works the same way.
#
# The room page also offers a one-click path. It installs this helper once per
# Windows user with -InstallHelper, which registers a talkabout:// handler
# pinned to one server origin. Pressing the room-page button then copies a
# short-lived invite payload (server, room, agent, runtime, budget, credential)
# to the clipboard and opens that handler. The handler ignores the link text,
# reads the clipboard once, clears it, and passes the credential to the CLI
# through the child process environment only. The credential never appears in a
# URL, an argument, the generated launcher, or this helper's output.
#
# The helper finds Python 3.12 or newer, prepares a private virtual environment
# under the state directory, writes a non-secret client configuration file, and
# starts the TalkAbout CLI one time. The CLI accepts the pending room invitation,
# runs one model test, and then runs a bounded worker.
#
# The room connection secret is read only by the CLI hidden prompt
# (--prompt-token) in the download path. This helper never stores, prints, passes
# as an argument, or logs the secret in either path.
#
# ASCII only on purpose: Windows PowerShell 5.1 may decode this file and
# Invoke-RestMethod output without a charset, so non-ASCII bytes could corrupt
# the generated command. Keep this file BOM-less and ASCII-only.
# ---------------------------------------------------------------------------

param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$Room,
    [string]$Agent,
    [ValidateSet('codex', 'claude')][string]$Runtime = 'codex',
    [ValidateSet('native', 'api_key')][string]$AuthMode = 'native',
    [int]$MaxModelCalls = 3,
    [int]$Steps = 1000,
    [int]$MaxSeconds = 300,
    [string]$StateDir = (Join-Path $env:LOCALAPPDATA 'TalkAbout'),
    [switch]$RequestTurn,
    [switch]$Update,
    [switch]$DryRun,
    # One-click mode: the room page puts a short-lived JSON payload on the
    # clipboard and opens talkabout://connect. This helper reads it once, uses
    # the credential only through the child process environment, and clears the
    # clipboard. The payload never appears in an argument, a URL, or output.
    [switch]$FromClipboard,
    # Installs this helper for the current Windows user and registers the
    # talkabout:// handler pinned to one server origin. The handler never reads
    # a server origin from the URL it receives.
    [switch]$InstallHelper,
    # Diagnostics only: supply the clipboard payload without a clipboard. The
    # generated installer and the registered handler never use this parameter.
    [string]$ClipboardText = ''
)

$script:PackageArchiveUrl = 'https://github.com/yjj3019/TalkAbout/archive/refs/heads/main.zip'
$script:MinimumPython = [version]'3.12'

function Write-Fail {
    param([string]$Message)
    [Console]::Error.WriteLine('')
    [Console]::Error.WriteLine('ERROR: ' + $Message)
    exit 1
}

function Write-Notice {
    param([string]$Message)
    Write-Host ('WARNING: ' + $Message)
}

function Write-Banner {
    Write-Host ''
    Write-Host '=============================================================='
    Write-Host ' TalkAbout connection helper'
    Write-Host '=============================================================='
}

function Test-OriginValue {
    # Accept only an http(s) origin: no trailing slash, no path, no query, no
    # credentials. The server and its browser origin must match exactly.
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    if ($Value -notmatch '^https?://[^/?#@\s]+$') { return $false }
    try {
        $parsed = [Uri]$Value
    } catch {
        return $false
    }
    if (-not $parsed.IsAbsoluteUri) { return $false }
    if (@('http', 'https') -notcontains $parsed.Scheme) { return $false }
    if ([string]::IsNullOrEmpty($parsed.Host)) { return $false }
    if ($parsed.AbsolutePath -ne '/') { return $false }
    if ($parsed.Query -ne '') { return $false }
    if ($parsed.Fragment -ne '') { return $false }
    return $true
}

function Resolve-StatePath {
    param([string]$Value)
    try {
        return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Value)
    } catch {
        try {
            return [System.IO.Path]::GetFullPath($Value)
        } catch {
            return $null
        }
    }
}

function Read-ClipboardInvite {
    # Read the one-click payload exactly once. A marked clipboard value is
    # replaced immediately so a second activation cannot reuse it.
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) {
        try {
            $Text = Get-Clipboard -Raw -ErrorAction Stop
        } catch {
            Write-Fail 'Could not read the clipboard. Use the download-and-run helper or the manual command instead.'
        }
    }
    $text = [string]$Text
    if (-not [string]::IsNullOrWhiteSpace($text)) {
        try { Set-Clipboard -Value ' ' -ErrorAction Stop } catch { }
    }
    return $text
}

function ConvertFrom-InvitePayload {
    # Validate every field before using it. Unknown or extra keys are refused so
    # a payload cannot smuggle a different target or a different server.
    param([string]$Text, [string]$PinnedOrigin)
    if ([string]::IsNullOrWhiteSpace($Text)) {
        Write-Fail 'The clipboard does not contain a TalkAbout one-click invite. Copy the invite again from the room page.'
    }
    try {
        $payload = $Text.Trim() | ConvertFrom-Json
    } catch {
        Write-Fail 'The clipboard does not contain a valid TalkAbout one-click invite.'
    }
    if ($null -eq $payload -or $payload -is [array] -or $payload -isnot [pscustomobject]) {
        Write-Fail 'The clipboard does not contain a valid TalkAbout one-click invite.'
    }
    $allowed = @('v', 'origin', 'room', 'agent', 'runtime', 'auth_mode', 'max_model_calls', 'request_turn', 'token')
    foreach ($name in $payload.PSObject.Properties.Name) {
        if ($allowed -notcontains $name) {
            Write-Fail ('The invite payload contains an unsupported field: ' + $name)
        }
    }
    if ($payload.v -ne 1) {
        Write-Fail 'The invite payload version is not supported. Update the room page and try again.'
    }
    if (-not (Test-OriginValue -Value ([string]$payload.origin))) {
        Write-Fail 'The invite payload does not name a valid server origin.'
    }
    if ([string]$payload.origin -ne $PinnedOrigin) {
        Write-Fail ('The invite is for ' + [string]$payload.origin + ' and this helper is installed for ' + $PinnedOrigin + '. Install the helper for that server first.')
    }
    if (([string]$payload.room) -cnotmatch '^[A-Za-z0-9-]{1,128}$') {
        Write-Fail 'The invite payload does not name a valid room.'
    }
    if (([string]$payload.agent) -cnotmatch '^[A-Za-z0-9-]{1,64}$') {
        Write-Fail 'The invite payload does not name a valid agent.'
    }
    if (@('codex', 'claude') -notcontains [string]$payload.runtime) {
        Write-Fail 'The invite payload names an unsupported runtime.'
    }
    if (@('native', 'api_key') -notcontains [string]$payload.auth_mode) {
        Write-Fail 'The invite payload names an unsupported authentication mode.'
    }
    if ($payload.max_model_calls -isnot [int] -and $payload.max_model_calls -isnot [long]) {
        Write-Fail 'The invite payload does not name a valid model-call budget.'
    }
    if ($payload.max_model_calls -lt 1 -or $payload.max_model_calls -gt 10) {
        Write-Fail 'The invite payload names a model-call budget outside 1 to 10.'
    }
    if ($payload.request_turn -isnot [bool]) {
        Write-Fail 'The invite payload does not name a valid turn consent.'
    }
    $token = [string]$payload.token
    if ([string]::IsNullOrWhiteSpace($token) -or $token.Length -gt 512 -or $token -match '\s') {
        Write-Fail 'The invite payload does not contain a usable credential.'
    }
    return @{
        Origin = [string]$payload.origin
        Room = [string]$payload.room
        Agent = [string]$payload.agent
        Runtime = [string]$payload.runtime
        AuthMode = [string]$payload.auth_mode
        MaxModelCalls = [int]$payload.max_model_calls
        RequestTurn = [bool]$payload.request_turn
        Token = $token
    }
}

function Install-ConnectionHelper {
    # One-time per-user install: copy this script, write the pinned launcher and
    # register talkabout:// for the current user only. The handler ignores the
    # URL it receives, so a hostile talkabout:// link cannot change the server.
    param([string]$Origin)
    $helperDir = Join-Path $env:LOCALAPPDATA 'TalkAbout\helper'
    Write-Banner
    Write-Host 'TalkAbout one-click helper setup'
    Write-Host ('  Server        : ' + $Origin)
    Write-Host ('  Helper folder : ' + $helperDir)
    Write-Host '  Scope         : current Windows user only (HKCU), no administrator rights'
    try {
        New-Item -ItemType Directory -Path $helperDir -Force -ErrorAction Stop | Out-Null
    } catch {
        Write-Fail ('Could not create ' + $helperDir + ': ' + $_.Exception.Message)
    }
    $helperScript = Join-Path $helperDir 'connect-agent.ps1'
    $launcher = Join-Path $helperDir 'launch.cmd'
    try {
        $scriptText = Invoke-RestMethod -UseBasicParsing -Uri ($Origin + '/web/connect-agent.ps1')
    } catch {
        Write-Fail ('Could not download the helper from ' + $Origin + ': ' + $_.Exception.Message)
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($helperScript, [string]$scriptText, $encoding)
    } catch {
        Write-Fail ('Could not write ' + $helperScript + ': ' + $_.Exception.Message)
    }
    $helperArguments = '-Server ' + ("'" + $Origin + "'") + ' -FromClipboard'
    $launcherLine = 'powershell -NoProfile -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\TalkAbout\helper\connect-agent.ps1" ' + $helperArguments
    $launcherLines = @('@echo off', $launcherLine)
    try {
        [System.IO.File]::WriteAllText($launcher, ($launcherLines -join "`r`n") + "`r`n", [System.Text.Encoding]::ASCII)
    } catch {
        Write-Fail ('Could not write ' + $launcher + ': ' + $_.Exception.Message)
    }
    $command = '"' + $launcher + '" "%1"'
    try {
        New-Item -Path 'HKCU:\Software\Classes\talkabout' -Force -ErrorAction Stop | Out-Null
        New-ItemProperty -Path 'HKCU:\Software\Classes\talkabout' -Name '(default)' -Value 'URL:TalkAbout connection' -PropertyType String -Force | Out-Null
        New-ItemProperty -Path 'HKCU:\Software\Classes\talkabout' -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
        New-Item -Path 'HKCU:\Software\Classes\talkabout\shell\open\command' -Force -ErrorAction Stop | Out-Null
        New-ItemProperty -Path 'HKCU:\Software\Classes\talkabout\shell\open\command' -Name '(default)' -Value $command -PropertyType String -Force | Out-Null
    } catch {
        Write-Fail ('Could not register the talkabout:// handler: ' + $_.Exception.Message)
    }
    Write-Host ''
    Write-Host 'Installed. Return to the room page and press the one-click connect button again.'
    Write-Host ('The handler is pinned to ' + $Origin + '; it ignores the link text it receives.')
    if (Test-InteractiveConsole) {
        Write-Host ''
        try { [void](Read-Host 'Press Enter to close this window') } catch { }
    }
    return 0
}

function Confirm-StateDirectory {
    # The configuration file and the journal both live in the state directory.
    # Create it before any file is written, whichever Python mode is in use.
    # Verify the result: New-Item can fail without throwing on an unusable
    # parent path. Dry runs never call this function.
    param([string]$Path)
    if (Test-Path -LiteralPath $Path -PathType Container) { return }
    try {
        New-Item -ItemType Directory -Path $Path -Force -ErrorAction Stop | Out-Null
    } catch {
        Write-Fail ('Could not create the state directory ' + $Path + ': ' + $_.Exception.Message)
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        Write-Fail ('Could not create the state directory ' + $Path + '. Choose a writable StateDir path and try again.')
    }
}

function Get-PythonVersionText {
    # Ask the interpreter for its major.minor version without importing
    # anything from the project.
    param([string]$Exe, [string[]]$Prefix = @())
    # Single quotes inside the Python snippet only: Windows PowerShell 5.1
    # strips embedded double quotes from native command arguments.
    $probe = @($Prefix) + @('-c', 'import sys;print(''%d.%d'' % sys.version_info[:2])')
    try {
        $output = & $Exe @probe 2>$null
    } catch {
        return $null
    }
    if ($null -eq $LASTEXITCODE -or $LASTEXITCODE -ne 0) { return $null }
    foreach ($line in @($output)) {
        $text = ([string]$line).Trim()
        if ($text -match '^[0-9]+\.[0-9]+$') { return $text }
    }
    return $null
}

function Test-TalkaboutImport {
    param([string]$Exe)
    try {
        & $Exe -c 'import talkabout' 2>$null | Out-Null
    } catch {
        return $false
    }
    return ($null -ne $LASTEXITCODE -and $LASTEXITCODE -eq 0)
}

function Find-PythonRuntime {
    # Priority: TALKABOUT_PYTHON, TALKABOUT_SOURCE\.venv (only when it already
    # imports talkabout), py -3.12, py -3, python, python3.
    $candidates = New-Object System.Collections.ArrayList
    if (-not [string]::IsNullOrWhiteSpace($env:TALKABOUT_PYTHON)) {
        [void]$candidates.Add(@{ Kind = 'base'; Explicit = $true; Exe = $env:TALKABOUT_PYTHON; Prefix = @() })
    }
    if (-not [string]::IsNullOrWhiteSpace($env:TALKABOUT_SOURCE)) {
        $sourcePython = Join-Path $env:TALKABOUT_SOURCE '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $sourcePython -PathType Leaf) {
            [void]$candidates.Add(@{ Kind = 'source'; Explicit = $true; Exe = $sourcePython; Prefix = @() })
        }
    }
    $launcher = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        [void]$candidates.Add(@{ Kind = 'base'; Explicit = $false; Exe = $launcher.Source; Prefix = @('-3.12') })
        [void]$candidates.Add(@{ Kind = 'base'; Explicit = $false; Exe = $launcher.Source; Prefix = @('-3') })
    }
    foreach ($name in @('python', 'python3')) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $found) {
            [void]$candidates.Add(@{ Kind = 'base'; Explicit = $false; Exe = $found.Source; Prefix = @() })
        }
    }
    foreach ($candidate in $candidates) {
        $version = Get-PythonVersionText -Exe $candidate.Exe -Prefix $candidate.Prefix
        if ($null -eq $version) {
            if ($candidate.Explicit) {
                Write-Notice ($candidate.Exe + ' did not report a Python version; trying other interpreters.')
            }
            continue
        }
        if ([version]$version -lt $script:MinimumPython) {
            if ($candidate.Explicit) {
                Write-Notice ($candidate.Exe + ' reported Python ' + $version + '; Python 3.12 or newer is required.')
            }
            continue
        }
        if ($candidate.Kind -eq 'source') {
            if (Test-TalkaboutImport -Exe $candidate.Exe) {
                return @{ Path = $candidate.Exe; Mode = 'source'; Version = $version }
            }
            if ($candidate.Explicit) {
                Write-Notice ($candidate.Exe + ' reported Python ' + $version + ' but TalkAbout is not importable there; skipping it.')
            }
            continue
        }
        return @{ Path = $candidate.Exe; Mode = 'base'; Version = $version }
    }
    return $null
}

function Get-PackageTarget {
    # Prefer a local source checkout; otherwise install from the published
    # archive. The GUI has no way to choose a package source.
    if (-not [string]::IsNullOrWhiteSpace($env:TALKABOUT_SOURCE)) {
        $manifest = Join-Path $env:TALKABOUT_SOURCE 'pyproject.toml'
        if (Test-Path -LiteralPath $manifest -PathType Leaf) {
            return @{ Kind = 'source'; Value = $env:TALKABOUT_SOURCE }
        }
    }
    return @{ Kind = 'archive'; Value = $script:PackageArchiveUrl }
}

function Get-AgentConfigJson {
    # The configuration file intentionally contains no credential value. It
    # only names one environment variable that the CLI may use when the
    # interactive prompt is not used.
    param([string]$BaseUrl, [string]$JournalPath)
    $payload = [ordered]@{
        base_url = $BaseUrl
        allowed_origin = $BaseUrl
        journal_path = $JournalPath
        token_env = 'TALKABOUT_TOKEN'
    }
    return ($payload | ConvertTo-Json -Compress)
}

function Write-AgentConfig {
    # Write UTF-8 without a BOM so Windows PowerShell 5.1 and Python both read
    # the same bytes.
    param([string]$Path, [string]$BaseUrl, [string]$JournalPath)
    $json = Get-AgentConfigJson -BaseUrl $BaseUrl -JournalPath $JournalPath
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $encoding)
    return $json
}

function Get-ConnectArguments {
    # Build the CLI argument list. With -PromptToken the last element is the
    # hidden-input switch and the credential is read by the CLI itself; in
    # one-click mode the credential arrives through the child environment and
    # no argument mentions it either way.
    param(
        [string]$ConfigPath,
        [string]$RuntimeName,
        [string]$AuthModeName,
        [string]$RoomId,
        [int]$ModelCalls,
        [int]$StepCount,
        [int]$SecondLimit,
        [bool]$WantTurn,
        [bool]$PromptToken = $true
    )
    $arguments = @(
        '-m', 'talkabout.cli', 'connect',
        '--config', $ConfigPath,
        '--runtime', $RuntimeName,
        '--auth-mode', $AuthModeName,
        '--room', $RoomId,
        '--acknowledge-private',
        '--approve-external',
        '--max-model-calls', [string]$ModelCalls,
        '--steps', [string]$StepCount,
        '--max-seconds', [string]$SecondLimit
    )
    if ($WantTurn) {
        $arguments += '--request-turn'
    }
    if ($PromptToken) {
        $arguments += '--prompt-token'
    }
    return $arguments
}

function Format-CommandLine {
    param([string]$Exe, [object[]]$Arguments)
    $quoted = @()
    foreach ($item in $Arguments) {
        $text = [string]$item
        if ($text -match '[\s"]') {
            $quoted += ('"' + $text.Replace('"', '\"') + '"')
        } else {
            $quoted += $text
        }
    }
    return ('"' + $Exe + '" ' + ($quoted -join ' '))
}

function Test-InteractiveConsole {
    try {
        return (-not [Console]::IsInputRedirected)
    } catch {
        return [Environment]::UserInteractive
    }
}

# ---------------------------------------------------------------------------
# Validate parameters before doing anything else.
# ---------------------------------------------------------------------------

if (-not (Test-OriginValue -Value $Server)) {
    Write-Fail 'Server must be an http(s) origin such as http://127.0.0.1:8080, with no trailing slash, path, query, or credentials.'
}

if ($InstallHelper.IsPresent) {
    if ($FromClipboard.IsPresent) {
        Write-Fail 'InstallHelper runs alone; it does not connect.'
    }
    $helperDir = Join-Path $env:LOCALAPPDATA 'TalkAbout\helper'
    if ($DryRun.IsPresent) {
        $quotedOrigin = "'" + $Server + "'"
        $launcherLine = 'powershell -NoProfile -ExecutionPolicy Bypass -File <helper>\connect-agent.ps1 -Server ' + $quotedOrigin + ' -FromClipboard'
        Write-Banner
        Write-Host 'Dry run: one-click helper install plan.'
        Write-Host '  Nothing is downloaded, written or registered in a dry run.'
        Write-Host ('  Server        : ' + $Server)
        Write-Host ('  Helper folder : ' + $helperDir)
        Write-Host ('  Writes        : ' + (Join-Path $helperDir 'connect-agent.ps1') + ' and ' + (Join-Path $helperDir 'launch.cmd'))
        Write-Host ('  Launcher      : ' + $launcherLine)
        Write-Host '  Registry      : HKCU\Software\Classes\talkabout\shell\open\command -> "<helper>\launch.cmd" "%1"'
        Write-Host ('  Pinned origin : ' + $Server + ' (the handler ignores the link text it receives)')
        Write-Host '  Secret        : the installer writes no credential; it arrives later through the clipboard payload'
        exit 0
    }
    exit (Install-ConnectionHelper -Origin $Server)
}

$clipboardToken = ''
$promptToken = $true
if ($FromClipboard.IsPresent) {
    $payloadText = $ClipboardText
    if ([string]::IsNullOrWhiteSpace($payloadText)) {
        if ($DryRun.IsPresent) {
            # A dry run must not consume the clipboard for a real activation.
            Write-Banner
            Write-Host 'Dry run: one-click mode.'
            Write-Host ('  Server        : ' + $Server)
            Write-Host '  Clipboard     : would be read once for a short-lived invite payload; nothing is read or cleared in a dry run'
            Write-Host '  Credential    : would be taken from that payload and passed only through the child environment'
            Write-Host '  Next          : press the one-click connect button on the room page, then run the installed helper'
            exit 0
        }
        $payloadText = Read-ClipboardInvite -Text ''
    }
    $invite = ConvertFrom-InvitePayload -Text $payloadText -PinnedOrigin $Server
    if ([string]::IsNullOrWhiteSpace($Room)) { $Room = $invite.Room }
    if ([string]::IsNullOrWhiteSpace($Agent)) { $Agent = $invite.Agent }
    if ($Room -ne $invite.Room) { Write-Fail 'The -Room argument does not match the invite payload.' }
    if ($Agent -ne $invite.Agent) { Write-Fail 'The -Agent argument does not match the invite payload.' }
    $Runtime = $invite.Runtime
    $AuthMode = $invite.AuthMode
    $MaxModelCalls = $invite.MaxModelCalls
    $RequestTurn = [switch]$invite.RequestTurn
    $clipboardToken = $invite.Token
    $promptToken = $false
    $payloadText = ''
}

if ($Room -cnotmatch '^[A-Za-z0-9-]{1,128}$') {
    Write-Fail 'Room must match ^[A-Za-z0-9-]{1,128}$ (letters, digits, hyphen).'
}
if ($Agent -cnotmatch '^[A-Za-z0-9-]{1,64}$') {
    Write-Fail 'Agent must match ^[A-Za-z0-9-]{1,64}$ (letters, digits, hyphen).'
}
if ($MaxModelCalls -lt 1 -or $MaxModelCalls -gt 10) {
    Write-Fail 'MaxModelCalls must be between 1 and 10.'
}
if ($Steps -lt 1 -or $Steps -gt 1000) {
    Write-Fail 'Steps must be between 1 and 1000.'
}
if ($MaxSeconds -lt 1 -or $MaxSeconds -gt 300) {
    Write-Fail 'MaxSeconds must be between 1 and 300.'
}
if ([string]::IsNullOrWhiteSpace($StateDir)) {
    Write-Fail 'StateDir must not be empty.'
}
$statePath = Resolve-StatePath -Value $StateDir
if ([string]::IsNullOrWhiteSpace($statePath)) {
    Write-Fail ('StateDir could not be resolved to an absolute path: ' + $StateDir)
}

$configPath = Join-Path $statePath ('agent-' + $Agent + '.local.json')
$journalPath = Join-Path $statePath ('agent-' + $Agent + '.sqlite3')
$venvPath = Join-Path $statePath 'venv'
$venvPython = Join-Path (Join-Path $venvPath 'Scripts') 'python.exe'
$providerName = 'codex'
if ($Runtime -eq 'claude') { $providerName = 'claude' }
$package = Get-PackageTarget
$requestTurnText = 'no'
if ($RequestTurn.IsPresent) { $requestTurnText = 'yes' }
$updateText = 'no'
if ($Update.IsPresent) { $updateText = 'yes' }

# ---------------------------------------------------------------------------
# 1. Banner and plan.
# ---------------------------------------------------------------------------

Write-Banner
Write-Host 'Connection plan'
Write-Host ('  Server        : ' + $Server)
Write-Host ('  Room          : ' + $Room)
Write-Host ('  Agent         : ' + $Agent)
Write-Host ('  Runtime       : ' + $Runtime + ' (auth mode: ' + $AuthMode + ')')
Write-Host ('  Budget        : ' + $MaxModelCalls + ' model call(s), ' + $Steps + ' step(s), max ' + $MaxSeconds + ' second(s)')
Write-Host ('  Request turn  : ' + $requestTurnText)
Write-Host ('  Update        : ' + $updateText)
Write-Host ('  State dir     : ' + $statePath)
Write-Host ('  Config file   : ' + $configPath)
Write-Host ('  Journal file  : ' + $journalPath)

$plannedArguments = Get-ConnectArguments -ConfigPath $configPath -RuntimeName $Runtime -AuthModeName $AuthMode -RoomId $Room -ModelCalls $MaxModelCalls -StepCount $Steps -SecondLimit $MaxSeconds -WantTurn $RequestTurn.IsPresent -PromptToken $promptToken

# ---------------------------------------------------------------------------
# 5. Dry run: report the plan, create nothing, execute nothing.
# ---------------------------------------------------------------------------

if ($DryRun.IsPresent) {
    Write-Host ''
    Write-Host 'Dry run: evaluating the plan only. Nothing is created, installed, written, or executed.'
    Write-Host ''
    $dryPython = Find-PythonRuntime
    if ($null -eq $dryPython) {
        Write-Host '  Python        : not found. A real run prints the python.org install guide and stops with exit code 1.'
        Write-Host ('  Command       : <python> ' + ($plannedArguments -join ' '))
    } else {
        Write-Host ('  Python        : ' + $dryPython.Path + ' (Python ' + $dryPython.Version + ')')
        if ($dryPython.Mode -eq 'source') {
            Write-Host ('  Environment   : use the TalkAbout source environment directly (' + $dryPython.Path + ')')
            if ($Update.IsPresent) {
                Write-Host ('  Package       : update TalkAbout in that environment from ' + $package.Value)
            } else {
                Write-Host '  Package       : TalkAbout is importable there; no install is planned'
            }
        } else {
            Write-Host ('  Environment   : create or reuse the virtual environment at ' + $venvPath)
            Write-Host ('  Package       : check "import talkabout"; install ' + $package.Value + ' only when missing or -Update is given')
        }
        Write-Host ('  Command       : ' + (Format-CommandLine -Exe $dryPython.Path -Arguments $plannedArguments))
    }
    Write-Host ('  Configuration : would write ' + $configPath + ' as UTF-8 without BOM:')
    Write-Host ('    ' + (Get-AgentConfigJson -BaseUrl $Server -JournalPath $journalPath))
    Write-Host ('  Provider CLI  : would check "' + $providerName + '" on PATH; a missing CLI only warns')
    if ($promptToken) {
        Write-Host '  Secret        : the CLI would ask once in a hidden prompt (--prompt-token); the helper never stores or prints it'
    } else {
        Write-Host '  Secret        : comes from the one-click invite payload through the child environment only; the helper does not print or store it'
    }
    Write-Host ('  Next          : the CLI accepts the invitation, runs one model test, then runs the bounded worker (max ' + $MaxSeconds + ' second(s))')
    Write-Host ''
    Write-Host 'Dry run complete. Re-run this helper in an interactive window to connect.'
    exit 0
}

# ---------------------------------------------------------------------------
# 2. Find Python 3.12 or newer.
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host '[1/6] Find Python 3.12 or newer'
$python = Find-PythonRuntime
if ($null -eq $python) {
    Write-Host ''
    Write-Host 'Python 3.12 or newer was not found on this machine.'
    Write-Host 'Install Python 3.12 or newer from https://www.python.org/downloads/windows/'
    Write-Host 'Select "Add python.exe to PATH" during the installation, then run this helper again.'
    exit 1
}
Write-Host ('  Using ' + $python.Path + ' (Python ' + $python.Version + ')')

# ---------------------------------------------------------------------------
# 3. Prepare the private environment and install the client when needed.
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host '[2/6] Prepare the TalkAbout client environment'
if ($python.Mode -eq 'source') {
    $clientPython = $python.Path
    Write-Host ('  Using the source environment directly: ' + $clientPython)
    if ($Update.IsPresent) {
        Write-Host ('  Updating TalkAbout from ' + $package.Value)
        & $clientPython -m pip install --upgrade --disable-pip-version-check $package.Value
        if ($null -eq $LASTEXITCODE -or $LASTEXITCODE -ne 0) {
            Write-Fail 'Could not update the TalkAbout client. Check the network connection and try again.'
        }
    } else {
        Write-Host '  TalkAbout is already importable in that environment; no install is needed.'
    }
} else {
    Confirm-StateDirectory -Path $statePath
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        Write-Host ('  Reusing the virtual environment at ' + $venvPath)
    } else {
        Write-Host ('  Creating a private virtual environment at ' + $venvPath)
        $venvArguments = @($python.Prefix) + @('-m', 'venv', $venvPath)
        & $python.Path @venvArguments
        if ($null -eq $LASTEXITCODE -or $LASTEXITCODE -ne 0) {
            Write-Fail ('Could not create the virtual environment at ' + $venvPath + '. Remove that directory and try again.')
        }
    }
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        Write-Fail ('The virtual environment at ' + $venvPath + ' is incomplete. Remove that directory and try again.')
    }
    $clientPython = $venvPython
    $venvVersion = Get-PythonVersionText -Exe $clientPython
    if ($null -eq $venvVersion -or [version]$venvVersion -lt $script:MinimumPython) {
        Write-Fail ('The virtual environment at ' + $venvPath + ' does not use Python 3.12 or newer. Remove that directory and try again.')
    }
    $hasTalkabout = Test-TalkaboutImport -Exe $clientPython
    if (-not $hasTalkabout -or $Update.IsPresent) {
        Write-Host ('  Installing TalkAbout from ' + $package.Value)
        & $clientPython -m pip install --upgrade --disable-pip-version-check $package.Value
        if ($null -eq $LASTEXITCODE -or $LASTEXITCODE -ne 0) {
            Write-Fail 'Could not install the TalkAbout client. Check the network connection and try again.'
        }
    } else {
        Write-Host '  TalkAbout is already installed in the virtual environment.'
    }
}

# ---------------------------------------------------------------------------
# 4. Write the non-secret configuration file (UTF-8, no BOM).
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host '[3/6] Write the client configuration (no secret)'
Confirm-StateDirectory -Path $statePath
try {
    [void](Write-AgentConfig -Path $configPath -BaseUrl $Server -JournalPath $journalPath)
} catch {
    Write-Fail ('Could not write the configuration file ' + $configPath + ': ' + $_.Exception.Message)
}
Write-Host ('  ' + $configPath)

# ---------------------------------------------------------------------------
# 6. Check the provider CLI on PATH; warn only.
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host '[4/6] Check the provider CLI'
$providerCommand = Get-Command $providerName -ErrorAction SilentlyContinue
if ($null -eq $providerCommand) {
    Write-Notice ('the provider CLI "' + $providerName + '" was not found on PATH. Install and sign in to it; the helper continues.')
} else {
    Write-Host ('  Found ' + $providerName + ' at ' + $providerCommand.Source)
}

# ---------------------------------------------------------------------------
# 7. Explain where the credential comes from.
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host '[5/6] Connection credential'
if ($promptToken) {
    Write-Host '  The room page shows a one-time connection secret.'
    Write-Host '  Paste it once into this window when the CLI asks; the input stays hidden.'
    Write-Host '  This helper does not store, print, or pass the secret as an argument.'
    Write-Host '  The secret display disappears after 60 seconds; request a new secret on the room page if it is gone.'
} else {
    Write-Host '  The one-click invite arrived through the clipboard and was cleared already.'
    Write-Host '  The credential is passed to the client through this process environment only.'
    Write-Host '  This helper does not print or store it; it stays valid for one hour.'
}

# ---------------------------------------------------------------------------
# 8. Start the CLI. The one-click credential is exported for the child process
#    only and removed from this process before the result is reported.
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host '[6/6] Start the TalkAbout CLI'
$connectArguments = Get-ConnectArguments -ConfigPath $configPath -RuntimeName $Runtime -AuthModeName $AuthMode -RoomId $Room -ModelCalls $MaxModelCalls -StepCount $Steps -SecondLimit $MaxSeconds -WantTurn $RequestTurn.IsPresent -PromptToken $promptToken
Write-Host ('  ' + (Format-CommandLine -Exe $clientPython -Arguments $connectArguments))
Write-Host ''
$connectExitCode = 1
if (-not $promptToken) {
    $env:TALKABOUT_TOKEN = $clipboardToken
    $clipboardToken = ''
}
try {
    & $clientPython @connectArguments
    if ($null -ne $LASTEXITCODE) { $connectExitCode = $LASTEXITCODE }
} catch {
    [Console]::Error.WriteLine('ERROR: could not start the TalkAbout client: ' + $_.Exception.Message)
} finally {
    if (-not $promptToken) { Remove-Item Env:TALKABOUT_TOKEN -ErrorAction SilentlyContinue }
}

# ---------------------------------------------------------------------------
# 9. Report the result and return the CLI exit code.
# ---------------------------------------------------------------------------

Write-Host ''
if ($connectExitCode -eq 0) {
    Write-Host 'The connection helper finished successfully.'
    Write-Host 'Return to the browser and refresh the connection status.'
} else {
    Write-Host ('The connection helper stopped with exit code ' + $connectExitCode + '.')
    Write-Host 'Read the messages above, make sure the room is still open, and run this helper again.'
    Write-Host 'A room secret stays valid for one hour; request a new one on the room page when it expired.'
}

if (Test-InteractiveConsole) {
    Write-Host ''
    try {
        [void](Read-Host 'Press Enter to close this window')
    } catch {
    }
}

exit $connectExitCode
