"""Agent output must not carry secret values the platform knows about (threat review F3-c).

Only values the process already holds can be masked. A secret that merely sits in a file
the agent read is out of scope and is documented as such in WORK_PROFILE.md.
"""
from conftest import LocalClient
from talkabout.content_framing import REDACTED_PLACEHOLDER
from talkabout.worker import MAX_CONTRIBUTION_CHARS, Journal, Worker, output_secrets, redact_output

PLATFORM_TOKEN = 'platform-token-value-0123456789abcdef'
PROVIDER_KEY = 'sk-test-' + 'A1b2C3d4' * 4
SHORT = 'short-1234'


class Leaky:
    blocked_values = {PLATFORM_TOKEN, SHORT}

    def run(self, context, cancel):
        return f'token={PLATFORM_TOKEN} key={PROVIDER_KEY} short={SHORT}'


def test_known_secrets_are_masked_before_the_journal_and_the_room(fixture, tmp_path, monkeypatch):
    store, core, rid = fixture
    monkeypatch.setenv('OPENAI_API_KEY', PROVIDER_KEY)
    path = tmp_path / 'journal.sqlite'
    journal = Journal(path)
    client = LocalClient(store)
    try:
        assert Worker(client, journal, Leaky()).step() == 'completed'
    finally:
        client.http.close()
    journal.db.close()
    stored = path.read_bytes()
    assert PLATFORM_TOKEN.encode() not in stored and PROVIDER_KEY.encode() not in stored
    texts = [message['text'] for message in core.read('fixture-human', rid)['messages']]
    assert texts, 'the contribution was not published'
    published = '\n'.join(texts)
    assert PLATFORM_TOKEN not in published and PROVIDER_KEY not in published
    assert published.count(REDACTED_PLACEHOLDER) == 2
    # Below the minimum length a value is left alone: masking it would shred ordinary text.
    assert SHORT in published


def test_output_secret_set_uses_runtime_values_and_environment(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'x' * 20)
    monkeypatch.setenv('XAI_API_KEY', 'tiny')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    secrets = output_secrets(Leaky())
    assert PLATFORM_TOKEN in secrets and 'x' * 20 in secrets
    assert SHORT not in secrets and 'tiny' not in secrets


def test_untouched_text_is_returned_unchanged_and_not_truncated(monkeypatch):
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY', 'XAI_API_KEY', 'TALKABOUT_TOKEN', 'TALKABOUT_DATABASE_URL'):
        monkeypatch.delenv(name, raising=False)
    text = 'plain ' * 5000
    assert redact_output(object(), text) is text or redact_output(object(), text) == text


def test_a_redaction_that_grows_the_text_stays_within_the_message_limit():
    secret = 'S' * 16

    class Runtime:
        blocked_values = {secret}

    redacted = redact_output(Runtime(), secret * (MAX_CONTRIBUTION_CHARS // 16))
    assert len(redacted) <= MAX_CONTRIBUTION_CHARS
    assert secret not in redacted


def test_non_text_output_fails_closed():
    import pytest
    with pytest.raises(ValueError):
        redact_output(Leaky(), {'text': PLATFORM_TOKEN})
