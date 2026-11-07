"""Static contract for the one-click connection helper.

The helper is Windows PowerShell 5.1 code that reaches the network and the
provider CLI, so this suite reads its text instead of executing it. Behaviour of
the parser and the dry-run plan was checked by hand (see docs/SESSION_LOG.md);
the checks here keep the safety properties from silently regressing.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
from contextlib import nullcontext

import pytest

try:
    from ci_guard import synthetic_powershell_launch
except ImportError:
    synthetic_powershell_launch = nullcontext

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'talkabout' / 'web' / 'connect-agent.ps1'
TEXT = HELPER.read_text(encoding='ascii')
LINES = TEXT.splitlines()


def test_helper_is_ascii_and_bom_free():
    raw = HELPER.read_bytes()
    assert not raw.startswith(b'\xef\xbb\xbf')
    assert raw.decode('ascii')


def test_helper_offers_both_one_click_entry_points():
    assert '[switch]$FromClipboard' in TEXT
    assert '[switch]$InstallHelper' in TEXT
    assert 'function Read-ClipboardInvite' in TEXT
    assert 'function ConvertFrom-InvitePayload' in TEXT
    assert 'function Install-ConnectionHelper' in TEXT


def test_helper_reads_the_clipboard_once_and_clears_it():
    block = TEXT.split('function Read-ClipboardInvite', 1)[1].split('function ConvertFrom-InvitePayload', 1)[0]
    assert 'Get-Clipboard -Raw' in block
    assert 'Set-Clipboard -Value' in block


def test_payload_validation_is_closed_over_known_fields():
    block = TEXT.split('function ConvertFrom-InvitePayload', 1)[1].split('function Install-ConnectionHelper', 1)[0]
    assert "'v', 'origin', 'room', 'agent', 'runtime', 'auth_mode', 'max_model_calls', 'request_turn', 'token'" in block
    for guard in ('unsupported field', 'PinnedOrigin', 'valid room', 'valid agent', 'unsupported runtime',
                  'valid model-call budget', 'usable credential'):
        assert guard in block, guard
    assert 'ConvertFrom-Json' in block


def test_payload_origin_must_match_the_pinned_origin():
    block = TEXT.split('function ConvertFrom-InvitePayload', 1)[1].split('function Install-ConnectionHelper', 1)[0]
    assert '[string]$payload.origin -ne $PinnedOrigin' in block


def test_installer_pins_one_origin_and_ignores_the_link_text():
    block = TEXT.split('function Install-ConnectionHelper', 1)[1].split('function Confirm-StateDirectory', 1)[0]
    assert 'HKCU:\\Software\\Classes\\talkabout' in block
    assert '-FromClipboard' in block
    assert '-Server ' in block
    assert '"%1"' in block
    # The handler command is built from the install-time origin; the URL text is
    # never parsed for a server address and no payload is read here.
    assert ('$command = \'"\' + $launcher + \'" "%1"\'') in block
    assert 'ConvertFrom-Json' not in block
    assert '$args' not in TEXT


def test_credential_never_goes_through_arguments_or_output():
    # The prompt switch is conditional; one-click mode omits it.
    assert 'if ($PromptToken) {' in TEXT
    assert "if (-not $promptToken) {\n    $env:TALKABOUT_TOKEN = $clipboardToken" in TEXT
    assert 'Remove-Item Env:TALKABOUT_TOKEN' in TEXT
    secret_variables = ('$clipboardToken', '$payload.token', '$invite.Token', '$token', '$env:TALKABOUT_TOKEN')
    for line in LINES:
        stripped = line.strip()
        if stripped.startswith('Write-Host') or stripped.startswith('[Console]::Error'):
            for variable in secret_variables:
                assert variable not in stripped, stripped


@pytest.mark.parametrize('needle', ['--prompt-token', '$clipboardToken = $invite.Token'])
def test_expected_literals_exist(needle):
    assert needle in TEXT


def test_clipboard_text_is_a_dry_run_diagnostic_only():
    # A real run must never take a credential from the command line.
    assert '-ClipboardText is a dry-run diagnostic only' in TEXT
    assert 'if (-not [string]::IsNullOrWhiteSpace($ClipboardText) -and -not $DryRun.IsPresent)' in TEXT
    assert 'InstallHelper does not accept -ClipboardText' in TEXT


@pytest.mark.parametrize('clipboard', [False, True])
@pytest.mark.parametrize('want_turn', [False, True])
def test_dry_run_binds_optional_turn_to_selected_room(clipboard, want_turn, tmp_path):
    pwsh = shutil.which('pwsh') or shutil.which('powershell')
    if not pwsh:
        pytest.skip('PowerShell unavailable')
    room = 'selected-room-42'
    command = [pwsh, '-NoProfile', '-File', str(HELPER), '-Server', 'http://127.0.0.1:18081',
               '-StateDir', str(tmp_path), '-DryRun']
    if clipboard:
        payload = {'v': 1, 'origin': 'http://127.0.0.1:18081', 'room': room, 'agent': 'agent',
                   'runtime': 'codex', 'auth_mode': 'native', 'max_model_calls': 3,
                   'request_turn': want_turn, 'token': 'synthetic-token'}
        command += ['-FromClipboard', '-ClipboardText', json.dumps(payload, separators=(',', ':'))]
    else:
        command += ['-Room', room, '-Agent', 'agent']
        if want_turn:
            command += ['-RequestTurn']
    with synthetic_powershell_launch():
        result = subprocess.run(command, capture_output=True, text=True,
                                env={**os.environ, 'TALKABOUT_CI_GUARDED_CHILD': '1',
                                     'LOCALAPPDATA': str(tmp_path)})
    assert result.returncode == 0, result.stderr
    line = next(line for line in result.stdout.splitlines() if 'Command       :' in line)
    assert '--room ' + room in line
    if want_turn:
        assert line.count('--request-turn ') == 1
        assert '--request-turn --request-turn-room ' + room in line
    else:
        assert '--request-turn' not in line
    assert 'synthetic-token' not in result.stdout


@pytest.mark.parametrize('room', ['bad;calc', 'bad room', 'bad\n', 'x' * 129])
def test_dry_run_rejects_unsafe_room(room, tmp_path):
    pwsh = shutil.which('pwsh') or shutil.which('powershell')
    if not pwsh:
        pytest.skip('PowerShell unavailable')
    with synthetic_powershell_launch():
        result = subprocess.run([pwsh, '-NoProfile', '-File', str(HELPER), '-Server', 'http://127.0.0.1:18081',
                                 '-Room', room, '-Agent', 'agent', '-RequestTurn', '-DryRun', '-StateDir', str(tmp_path)],
                                capture_output=True, text=True, env={**os.environ, 'LOCALAPPDATA': str(tmp_path)})
    assert result.returncode != 0
    assert 'Room must match' in result.stderr
    assert 'Command       :' not in result.stdout


def test_clipboard_rejects_different_room_case(tmp_path):
    pwsh = shutil.which('pwsh') or shutil.which('powershell')
    if not pwsh:
        pytest.skip('PowerShell unavailable')
    payload = {'v': 1, 'origin': 'http://127.0.0.1:18081', 'room': 'Room', 'agent': 'agent',
               'runtime': 'codex', 'auth_mode': 'native', 'max_model_calls': 3,
               'request_turn': True, 'token': 'synthetic-token'}
    with synthetic_powershell_launch():
        result = subprocess.run([pwsh, '-NoProfile', '-File', str(HELPER), '-Server', payload['origin'],
                                 '-Room', 'room', '-FromClipboard', '-ClipboardText', json.dumps(payload),
                                 '-DryRun', '-StateDir', str(tmp_path)], capture_output=True, text=True,
                                env={**os.environ, 'LOCALAPPDATA': str(tmp_path)})
    assert result.returncode != 0
    assert 'does not match the invite payload' in result.stderr


def test_static_asset_is_served_by_the_web_allowlist():
    api = (ROOT / 'talkabout' / 'api.py').read_text(encoding='utf-8')
    assert '"connect-agent.ps1"' in api
