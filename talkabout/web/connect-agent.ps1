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
# The helper finds Python 3.12 or newer, prepares a private virtual environment
# under the state directory, writes a non-secret client configuration file, and
# starts the TalkAbout CLI one time. The CLI accepts the pending room invitation,
# runs one model test, and then runs a bounded worker.
#
# The room connection secret is read only by the CLI hidden prompt
# (--prompt-token). This helper never stores, prints, passes as an argument, or
# logs the secret.
#
# ASCII only on purpose: Windows PowerShell 5.1 may decode this file and
# Invoke-RestMethod output without a charset, so non-ASCII bytes could corrupt
# the generated command. Keep this file BOM-less and ASCII-only.
# ---------------------------------------------------------------------------

param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$Room,
    [Parameter(Mandatory = $true)][string]$Agent,
    [ValidateSet('codex', 'claude')][string]$Runtime = 'codex',
    [ValidateSet('native', 'api_key')][string]$AuthMode = 'native',
    [int]$MaxModelCalls = 3,
    [int]$Steps = 1000,
    [int]$MaxSeconds = 300,
    [string]$StateDir = (Join-Path $env:LOCALAPPDATA 'TalkAbout'),
    [switch]$RequestTurn,
    [switch]$Update,
    [switch]$DryRun
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
    # Build the CLI argument list. The last element is the hidden-input switch;
    # no credential value ever appears here.
    param(
        [string]$ConfigPath,
        [string]$RuntimeName,
        [string]$AuthModeName,
        [string]$RoomId,
        [int]$ModelCalls,
        [int]$StepCount,
        [int]$SecondLimit,
        [bool]$WantTurn
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
    $arguments += '--prompt-token'
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

$plannedArguments = Get-ConnectArguments -ConfigPath $configPath -RuntimeName $Runtime -AuthModeName $AuthMode -RoomId $Room -ModelCalls $MaxModelCalls -StepCount $Steps -SecondLimit $MaxSeconds -WantTurn $RequestTurn.IsPresent

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
    Write-Host '  Secret        : the CLI would ask once in a hidden prompt (--prompt-token); the helper never stores or prints it'
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
# 7. Explain the one-time secret prompt.
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host '[5/6] One-time connection secret'
Write-Host '  The room page shows a one-time connection secret.'
Write-Host '  Paste it once into this window when the CLI asks; the input stays hidden.'
Write-Host '  This helper does not store, print, or pass the secret as an argument.'
Write-Host '  The secret display disappears after 60 seconds; request a new secret on the room page if it is gone.'

# ---------------------------------------------------------------------------
# 8. Start the CLI with the hidden token prompt.
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host '[6/6] Start the TalkAbout CLI'
$connectArguments = Get-ConnectArguments -ConfigPath $configPath -RuntimeName $Runtime -AuthModeName $AuthMode -RoomId $Room -ModelCalls $MaxModelCalls -StepCount $Steps -SecondLimit $MaxSeconds -WantTurn $RequestTurn.IsPresent
Write-Host ('  ' + (Format-CommandLine -Exe $clientPython -Arguments $connectArguments))
Write-Host ''
$connectExitCode = 1
try {
    & $clientPython @connectArguments
    if ($null -ne $LASTEXITCODE) { $connectExitCode = $LASTEXITCODE }
} catch {
    [Console]::Error.WriteLine('ERROR: could not start the TalkAbout client: ' + $_.Exception.Message)
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
