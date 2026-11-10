"""Git precondition and observation for a work-profile directory (threat review F3-a).

A work-profile turn lets the agent edit the owner's real directory with its shell
tool on. The platform applies nothing, so the owner's own git history is the only
way back. This module makes that way back exist before the first turn and records,
after each turn, what changed against the commit that was current at the start.

What it does NOT do, on purpose:
- no branch, commit, stash, reset or checkout in the owner's repository;
- no claim that the agent stayed inside the directory or that nothing ran: the
  observation is made after the fact, from git, and ignored files are not visible;
- no prevention. It is evidence for a human, not an OS sandbox.

The agent may rewrite ``.git/config`` or hooks during a turn. A later ``git status``
run by the platform, outside the provider sandbox and with the owner's rights, would
execute them, and a changed hook never shows up in ``git diff``. So the config and hook
fingerprint taken at the start is compared *before* any git command runs, and a
mismatch stops the session instead of running git.
"""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from .adapters import AdapterError

SCHEMA = 'talkabout.work_repo_observation/1'
GIT_TIMEOUT_SECONDS = 10
MAX_OUTPUT_BYTES = 256 * 1024
MAX_LISTED = 100
_ENV_KEYS = ('PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'HOME', 'USERPROFILE')


def _git_environment():
    """A minimal environment: no platform credential, no inherited git configuration."""
    env = {key: value for key, value in os.environ.items() if key.upper() in _ENV_KEYS}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GIT_CONFIG_NOSYSTEM='1',
               GIT_CONFIG_GLOBAL=os.devnull, GIT_PAGER='cat')
    return env


def _git_executable():
    found = shutil.which('git')
    if not found:
        raise AdapterError('runtime_work_repo_git_unavailable')
    return found


def _run(cwd, *args):
    """Run one read-only git command; bounded time and output; never raises provider text."""
    command = [_git_executable(), '-c', 'core.fsmonitor=false', '--no-optional-locks', *args]
    try:
        completed = subprocess.run(command, cwd=str(cwd), env=_git_environment(), shell=False,
                                   capture_output=True, timeout=GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        raise AdapterError('runtime_work_repo_check_failed') from None
    if completed.returncode != 0:
        raise AdapterError('runtime_work_repo_check_failed')
    return completed.stdout[:MAX_OUTPUT_BYTES + 1], len(completed.stdout) > MAX_OUTPUT_BYTES


def _same_path(left, right):
    try:
        return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))
    except OSError:
        return False


def _text(raw):
    return raw.decode('utf-8', errors='replace')


def _split_nul(raw):
    return [item for item in _text(raw).split('\0') if item]


def _inspect(workspace):
    """One git call: the work tree root and the git directories whose contents decide what
    a later git command will execute (config, attributes, hooks)."""
    try:
        raw, _ = _run(workspace, 'rev-parse', '--show-toplevel', '--git-dir', '--git-common-dir')
    except AdapterError as error:
        if error.code == 'runtime_work_repo_git_unavailable':
            raise
        raise AdapterError('runtime_work_repo_not_repository') from None
    lines = _text(raw).splitlines()
    if len(lines) != 3:
        raise AdapterError('runtime_work_repo_check_failed')
    dirs = []
    for value in lines[1:]:
        path = Path(value)
        resolved = path if path.is_absolute() else Path(workspace) / path
        if not any(_same_path(resolved, seen) for seen in dirs):
            dirs.append(resolved)
    return lines[0], dirs


def _status(workspace):
    """One git call: current commit, whether anything is dirty, and the untracked paths."""
    raw, cut = _run(workspace, 'status', '--porcelain=v2', '--branch', '--untracked-files=all', '-z')
    tokens = _split_nul(raw)
    head, dirty, untracked, index = None, False, [], 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith('# '):
            if token.startswith('# branch.oid '):
                head = token[len('# branch.oid '):].strip()
        else:
            dirty = True
            if token.startswith('? '):
                untracked.append(token[2:])
            elif token.startswith('2 '):
                index += 1  # a rename or copy: the original path follows as its own token
        index += 1
    return head, dirty, untracked, cut


def _fingerprint(dirs):
    """Hash of everything that can make git run a program: config, attributes, hooks."""
    digest = hashlib.sha256()
    for base in dirs:
        for name in ('config', 'config.worktree', os.path.join('info', 'attributes')):
            target = base / name
            digest.update(b'F\0' + name.encode() + b'\0')
            try:
                digest.update(target.read_bytes() if target.is_file() else b'<absent>')
            except OSError:
                digest.update(b'<unreadable>')
        hooks = base / 'hooks'
        digest.update(b'H\0')
        try:
            entries = sorted(hooks.iterdir(), key=lambda item: item.name) if hooks.is_dir() else []
        except OSError:
            entries = []
        for entry in entries:
            digest.update(b'E\0' + entry.name.encode(errors='replace') + b'\0')
            try:
                digest.update(entry.read_bytes() if entry.is_file() and entry.stat().st_size <= 1_000_000 else b'<skipped>')
            except OSError:
                digest.update(b'<unreadable>')
    return digest.hexdigest()


def check_clean_repo(workspace):
    """Preconditions for a work-profile directory. Read-only. Returns the base commit.

    The directory must be the root of a git work tree, have a commit, and have no
    tracked change and no untracked file. Anything else leaves no way to tell the
    agent's edits from the owner's, and no way back.
    """
    return _precondition(workspace)[0]


def _precondition(workspace):
    """Two git calls. Returns (base commit, git directories)."""
    workspace = Path(workspace)
    top, dirs = _inspect(workspace)
    if not _same_path(top, workspace):
        raise AdapterError('runtime_work_repo_not_root')
    head, dirty, _, _ = _status(workspace)
    if not head or head == '(initial)':
        raise AdapterError('runtime_work_repo_no_commit')
    if dirty:
        raise AdapterError('runtime_work_repo_not_clean')
    return head, dirs


class WorkRepoGuard:
    """Per-runtime guard: precondition once, fingerprint check and observation per turn."""

    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.base_commit, self._dirs = _precondition(self.workspace)
        self._fingerprint = _fingerprint(self._dirs)
        self.tampered = False

    def before_turn(self):
        """Refuse the next turn if git's own configuration or hooks changed. Runs no git."""
        if self.tampered or _fingerprint(self._dirs) != self._fingerprint:
            self.tampered = True
            raise AdapterError('runtime_work_repo_tampered')

    def observe(self):
        """What differs from the starting commit now. Never raises; never runs git if tampered."""
        base = {'schema': SCHEMA, 'base_commit': self.base_commit, 'ignored_files': 'not_observed',
                'evidence': 'git_observation_after_turn_not_a_sandbox'}
        if self.tampered or _fingerprint(self._dirs) != self._fingerprint:
            self.tampered = True
            return {**base, 'state': 'unknown', 'reason': 'git_config_or_hooks_changed'}
        try:
            head, _, untracked, untracked_cut = _status(self.workspace)
            changed_raw, changed_cut = _run(self.workspace, 'diff', '--name-status', '--no-ext-diff', '--no-textconv',
                                            '--no-renames', '-z', self.base_commit)
        except AdapterError as error:
            return {**base, 'state': 'unknown', 'reason': error.code}
        tokens = _split_nul(changed_raw)
        changed = [{'status': tokens[i], 'path': tokens[i + 1]} for i in range(0, len(tokens) - 1, 2)]
        return {**base, 'state': 'observed', 'head_changed': head != self.base_commit,
                'changed_count': len(changed), 'changed': changed[:MAX_LISTED],
                'untracked_count': len(untracked), 'untracked': untracked[:MAX_LISTED],
                'truncated': bool(changed_cut or untracked_cut or len(changed) > MAX_LISTED or len(untracked) > MAX_LISTED)}
