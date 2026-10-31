"""Static contract for the one-click connection helper.

The helper is Windows PowerShell 5.1 code that reaches the network and the
provider CLI, so this suite reads its text instead of executing it. Behaviour of
the parser and the dry-run plan was checked by hand (see docs/SESSION_LOG.md);
the checks here keep the safety properties from silently regressing.
"""
from pathlib import Path

import pytest

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


def test_static_asset_is_served_by_the_web_allowlist():
    api = (ROOT / 'talkabout' / 'api.py').read_text(encoding='utf-8')
    assert '"connect-agent.ps1"' in api
