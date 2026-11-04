# POSIX-only verification through WSL.
#
# The Windows test run skips 27 POSIX-gated cases (backup.sh/flock, POSIX
# containment, symlink/mode checks). This wrapper runs exactly those cases
# inside an installed WSL distribution, using its own Python 3 and pytest.
# It never installs packages, touches a real provider or reaches the network:
# it only executes the repository's POSIX tests against temporary directories.
#
# Prerequisites inside WSL (one-time, operator action):
#   sudo apt-get install -y python3-pytest python3-fastapi python3-httpx python3-psycopg python3-uvicorn
# Missing prerequisites make this wrapper fail with a clear message instead of
# silently skipping the cases.
param(
    [string]$Distro,
    [string]$Root
)

$ErrorActionPreference = 'Stop'
if (-not $Root) { $Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }
$Root = (Resolve-Path $Root).Path

$wsl = Get-Command wsl -ErrorAction SilentlyContinue
if (-not $wsl) { throw 'WSL is not available; POSIX-only cases cannot run on this host' }
$distro = if ($Distro) { @('-d', $Distro) } else { @() }

$probe = & wsl @distro -e bash -lc 'python3 -m pytest --version 2>/dev/null | head -1' 2>&1
if ($LASTEXITCODE -ne 0 -or -not $probe) {
    throw "WSL pytest is unavailable. Run inside WSL: sudo apt-get install -y python3-pytest python3-fastapi python3-httpx python3-psycopg python3-uvicorn"
}
Write-Output "wsl pytest: $probe"

$repo = (& wsl @distro -e bash -lc "wslpath -a '$($Root.Replace('\', '/'))'" 2>&1).Trim()
if ($LASTEXITCODE -ne 0 -or -not $repo) { throw "could not resolve the repository path inside WSL: $Root" }
Write-Output "wsl repo: $repo"

$selection = @(
    'tests/test_backup_script.py'
    'tests/test_posix_containment.py'
    'tests/test_agent_runtime.py::test_posix_real_launch_fails_closed'
    'tests/test_credential_vault.py::test_posix_default_fails_closed'
    'tests/test_credential_vault.py::test_symlink_root_ancestor_and_entries'
    'tests/test_work_profile.py::test_symlinked_workspace_is_refused'
    'tests/test_text_files_storage_root.py::test_posix_root_keeps_owner_only_mode'
)
$quoted = ($selection | ForEach-Object { "'" + ($_ -replace "'", "'\''") + "'" }) -join ' '

Write-Output "pytest (WSL): python3 -m pytest $($selection -join ' ') -q"
$env:PYTHONDONTWRITEBYTECODE = '1'
& wsl @distro -e bash -lc "cd '$repo' && python3 -B -m pytest $quoted -q -p no:cacheprovider"
exit $LASTEXITCODE
