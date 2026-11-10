"""Git precondition and after-turn observation for work-profile directories (threat review F3-a)."""
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from conftest import init_clean_repo
from talkabout import work_repo
from talkabout.adapters import AdapterError
from talkabout.runtime import bounded_call_with_evidence
from talkabout.work_repo import MAX_LISTED, WorkRepoGuard, check_clean_repo
# Reuse the synthetic transport and event builders. These tests do NOT use the fake guard:
# every guard here is the real one, running real git.
from tests.test_work_evidence_runtime import CTX, structured  # noqa: F401  (pytest fixture)
from tests.test_work_profile_events import change, event, stream, workspace  # noqa: F401  (pytest fixture)

pytestmark = pytest.mark.skipif(not shutil.which('git'), reason='git is required')


def git(path, *args):
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', '-c', 'commit.gpgsign=false', *args],
                   cwd=str(path), check=True, capture_output=True, timeout=60)


def code(call, *args):
    with pytest.raises(AdapterError) as error:
        call(*args)
    return error.value.code


def test_clean_repository_root_returns_its_commit(tmp_path):
    init_clean_repo(tmp_path)
    commit = check_clean_repo(tmp_path)
    assert len(commit) == 40 and int(commit, 16) >= 0


# A plain directory is refused either way: with no enclosing repository it is "not a
# repository", and when the test runner's temp directory happens to sit inside a checkout
# git finds that parent, so it is "not the root". Both are refusals.
NOT_A_REPOSITORY_ROOT = {'runtime_work_repo_not_repository', 'runtime_work_repo_not_root'}


def test_plain_directory_is_refused(tmp_path):
    assert code(check_clean_repo, tmp_path) in NOT_A_REPOSITORY_ROOT


def test_subdirectory_of_a_repository_is_refused(tmp_path):
    init_clean_repo(tmp_path)
    inner = tmp_path / 'inner'
    inner.mkdir()
    assert code(check_clean_repo, inner) == 'runtime_work_repo_not_root'


def test_repository_without_a_commit_is_refused(tmp_path):
    git(tmp_path, 'init', '-q')
    assert code(check_clean_repo, tmp_path) == 'runtime_work_repo_no_commit'


def test_tracked_change_and_untracked_file_both_block(tmp_path):
    init_clean_repo(tmp_path)
    (tmp_path / 'a.txt').write_text('one')
    assert code(check_clean_repo, tmp_path) == 'runtime_work_repo_not_clean'
    git(tmp_path, 'add', 'a.txt')
    git(tmp_path, 'commit', '-q', '-m', 'add')
    assert check_clean_repo(tmp_path)
    (tmp_path / 'a.txt').write_text('two')
    assert code(check_clean_repo, tmp_path) == 'runtime_work_repo_not_clean'


def test_missing_git_is_a_distinct_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(work_repo.shutil, 'which', lambda name: None)
    assert code(check_clean_repo, tmp_path) == 'runtime_work_repo_git_unavailable'


def test_observation_is_against_the_starting_commit_and_sees_agent_commits(tmp_path):
    init_clean_repo(tmp_path)
    (tmp_path / 'keep.txt').write_text('base')
    git(tmp_path, 'add', 'keep.txt')
    git(tmp_path, 'commit', '-q', '-m', 'base')
    guard = WorkRepoGuard(tmp_path)
    assert guard.observe()['changed_count'] == 0
    (tmp_path / 'keep.txt').write_text('edited')
    (tmp_path / 'new.txt').write_text('new')
    # The agent can commit its own work: status alone would then look clean.
    git(tmp_path, 'add', '-A')
    git(tmp_path, 'commit', '-q', '-m', 'agent commit')
    observation = guard.observe()
    assert observation['state'] == 'observed' and observation['head_changed'] is True
    assert sorted((item['status'], item['path']) for item in observation['changed']) == [('A', 'new.txt'), ('M', 'keep.txt')]
    assert observation['base_commit'] == guard.base_commit


def test_ignored_files_are_declared_not_observed(tmp_path):
    init_clean_repo(tmp_path)
    # Bytes, not write_text: on Windows write_text writes CRLF, and the guard (which ignores the
    # user's autocrlf setting) would then see the file as modified.
    (tmp_path / '.gitignore').write_bytes(b'secret.env\n')
    git(tmp_path, 'add', '.gitignore')
    git(tmp_path, 'commit', '-q', '-m', 'ignore')
    guard = WorkRepoGuard(tmp_path)
    (tmp_path / 'secret.env').write_text('x')
    observation = guard.observe()
    assert observation['untracked'] == [] and observation['ignored_files'] == 'not_observed'


def test_listing_is_bounded_but_counts_stay_exact(tmp_path):
    init_clean_repo(tmp_path)
    guard = WorkRepoGuard(tmp_path)
    for index in range(MAX_LISTED + 5):
        (tmp_path / f'f{index}.txt').write_text('x')
    observation = guard.observe()
    assert observation['untracked_count'] == MAX_LISTED + 5
    assert len(observation['untracked']) == MAX_LISTED and observation['truncated'] is True


@pytest.mark.parametrize('mutation', ['config', 'hook', 'attributes'])
def test_changes_to_what_git_executes_refuse_the_next_turn(tmp_path, monkeypatch, mutation):
    init_clean_repo(tmp_path)
    guard = WorkRepoGuard(tmp_path)
    guard.before_turn()
    dot_git = tmp_path / '.git'
    if mutation == 'config':
        (dot_git / 'config').write_text((dot_git / 'config').read_text() + '\n[core]\n\tfsmonitor = x\n')
    elif mutation == 'hook':
        (dot_git / 'hooks').mkdir(exist_ok=True)
        (dot_git / 'hooks' / 'post-checkout').write_text('#!/bin/sh\n')
    else:
        (dot_git / 'info').mkdir(exist_ok=True)
        (dot_git / 'info' / 'attributes').write_text('* filter=x\n')
    monkeypatch.setattr(work_repo, '_run', lambda *args: pytest.fail('git must not run after a tamper'))
    observation = guard.observe()
    assert observation['state'] == 'unknown' and observation['reason'] == 'git_config_or_hooks_changed'
    assert code(guard.before_turn) == 'runtime_work_repo_tampered'


def test_a_tamper_is_permanent_even_if_the_file_is_restored(tmp_path):
    init_clean_repo(tmp_path)
    guard = WorkRepoGuard(tmp_path)
    config = tmp_path / '.git' / 'config'
    original = config.read_text()
    config.write_text(original + '\n[core]\n\tfsmonitor = x\n')
    assert code(guard.before_turn) == 'runtime_work_repo_tampered'
    config.write_text(original)
    assert code(guard.before_turn) == 'runtime_work_repo_tampered'


def test_runtime_refuses_a_work_directory_that_is_not_a_clean_repository(tmp_path):
    from talkabout.agent_runtime import StructuredRuntime
    plain = tmp_path / 'plain'
    plain.mkdir()
    with pytest.raises(AdapterError) as error:
        StructuredRuntime('codex', executable=None, work_profile=True, work_workspace=str(plain))
    # Under the safe runner the temp dir sits inside the platform checkout, which is refused earlier.
    assert error.value.code in NOT_A_REPOSITORY_ROOT | {'runtime_work_workspace_overlaps_platform'}


def test_work_turn_records_git_observation_after_confirmed_cleanup(structured, workspace):
    value, transport = structured(stream([event('completed', change())]))
    (Path(workspace) / 'agent-made.txt').write_text('x')
    bounded_call_with_evidence(value, CTX, threading.Event(), 5)
    observation = value.last_workspace_observation
    assert transport.closed and observation['state'] == 'observed'
    assert observation['untracked'] == ['agent-made.txt'] and observation['changed'] == []
    assert observation['evidence'] == 'git_observation_after_turn_not_a_sandbox'


def test_unconfirmed_cleanup_never_runs_git_and_is_recorded_unknown(structured, workspace, monkeypatch):
    value, _ = structured(stream([event('completed', change())]), close_ok=False)
    monkeypatch.setattr(work_repo, '_run', lambda *args: pytest.fail('git ran while the provider tree was not confirmed gone'))
    with pytest.raises(AdapterError, match='runtime_cleanup_unconfirmed'):
        bounded_call_with_evidence(value, CTX, threading.Event(), 5)
    assert value.last_workspace_observation['state'] == 'unknown'
    assert value.last_workspace_observation['reason'] == 'cleanup_unconfirmed'


def test_changed_git_config_refuses_the_turn_without_running_git(structured, workspace, monkeypatch):
    value, transport = structured(stream([event('completed', change())]))
    config = Path(workspace) / '.git' / 'config'
    config.write_text(config.read_text() + '\n[core]\n\tfsmonitor = calc.exe\n')
    monkeypatch.setattr(work_repo, '_run', lambda *args: pytest.fail('git ran after its configuration changed'))
    with pytest.raises(AdapterError, match='runtime_work_repo_tampered'):
        bounded_call_with_evidence(value, CTX, threading.Event(), 5)
    assert not transport.closed, 'no provider process may start after a tamper refusal'


def test_text_profile_has_no_repo_guard(structured):
    value, _ = structured(stream([]), work=False)
    assert value._work_repo is None and value.last_workspace_observation is None


def test_git_runs_with_a_minimal_environment(monkeypatch):
    monkeypatch.setenv('TALKABOUT_TOKEN', 'platform-token-value-0123456789abcdef')
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test-0123456789abcdef0123')
    env = work_repo._git_environment()
    assert 'TALKABOUT_TOKEN' not in env and 'OPENAI_API_KEY' not in env
    assert env['GIT_CONFIG_NOSYSTEM'] == '1' and env['GIT_TERMINAL_PROMPT'] == '0'
