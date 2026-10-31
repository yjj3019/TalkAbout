"""Owner opt-in work profile: local tools inside one declared directory.

The profile is a trust transfer to the owner's own machine directory, not a sandbox.
These tests pin the refusals, the command shape, and the prompt honesty. A real
provider turn in work mode is NOTRUN: it needs provider budget and consent.
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from talkabout.adapters import AdapterError
from talkabout.agent_runtime import StructuredRuntime, prompt_text, work_prompt_text


@pytest.fixture
def workspace():
    """A directory outside the platform tree, as an owner workspace must be.

    Some environments put the system temp directory inside the checkout; the platform
    refuses that on purpose, so fall back to a sibling of the checkout and skip when
    neither location is usable.
    """
    package = Path(__file__).resolve().parents[1]
    candidates = [Path(tempfile.gettempdir()), package.parent]
    for base in candidates:
        try:
            path = Path(tempfile.mkdtemp(prefix='talkabout-workspace-', dir=base))
        except OSError:
            continue
        if package not in path.resolve().parents:
            try:
                yield path
            finally:
                shutil.rmtree(path, ignore_errors=True)
            return
        shutil.rmtree(path, ignore_errors=True)
    pytest.skip('no writable directory outside the platform tree in this environment')


def runtime(**options):
    options.setdefault('executable', None)
    return StructuredRuntime('codex', **options)


@pytest.mark.parametrize('workspace_value', [None, '', '   '])
def test_work_profile_requires_a_workspace(workspace_value):
    with pytest.raises(AdapterError, match='^runtime_work_workspace_required$'):
        runtime(work_profile=True, work_workspace=workspace_value)


def test_missing_workspace_is_refused(tmp_path):
    with pytest.raises(AdapterError, match='^runtime_work_workspace_missing$'):
        runtime(work_profile=True, work_workspace=str(tmp_path / 'nope'))


def test_file_is_not_a_workspace(tmp_path):
    target = tmp_path / 'file.txt'
    target.write_text('x')
    with pytest.raises(AdapterError, match='^runtime_work_workspace_invalid$'):
        runtime(work_profile=True, work_workspace=str(target))


def test_home_directory_is_too_broad():
    with pytest.raises(AdapterError, match='^runtime_work_workspace_too_broad$'):
        runtime(work_profile=True, work_workspace=str(Path.home()))


def test_filesystem_root_is_too_broad():
    root = Path(Path.cwd().anchor)
    with pytest.raises(AdapterError, match='^runtime_work_workspace_too_broad$'):
        runtime(work_profile=True, work_workspace=str(root))


def test_platform_directory_is_refused():
    package = Path(__file__).resolve().parents[1]
    with pytest.raises(AdapterError, match='^runtime_work_workspace_overlaps_platform$'):
        runtime(work_profile=True, work_workspace=str(package))
    with pytest.raises(AdapterError, match='^runtime_work_workspace_overlaps_platform$'):
        runtime(work_profile=True, work_workspace=str(package.parent))


@pytest.mark.skipif(os.name == 'nt', reason='symlink creation needs a privilege on Windows')
def test_symlinked_workspace_is_refused(tmp_path):
    target = tmp_path / 'real'
    target.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(target)
    with pytest.raises(AdapterError, match='^runtime_work_workspace_invalid$'):
        runtime(work_profile=True, work_workspace=str(link))


def test_junction_workspace_is_refused(tmp_path, monkeypatch):
    """Final-component junction rejection is checked without creating a real junction.

    Creating a Windows junction needs an OS call this suite does not make (NOTRUN);
    the branch is exercised by reporting the candidate as a junction.
    """
    target = tmp_path / 'real'
    target.mkdir()
    candidate = tmp_path / 'junction'
    candidate.mkdir()
    monkeypatch.setattr(Path, 'is_junction', lambda self: self == candidate, raising=False)
    with pytest.raises(AdapterError, match='^runtime_work_workspace_invalid$'):
        runtime(work_profile=True, work_workspace=str(candidate))
    # A plain directory outside the platform tree is covered by
    # test_valid_workspace_is_accepted_and_resolved; this test must not assume the
    # pytest basetemp lives outside the repository (CI keeps it inside).


@pytest.mark.parametrize('kind, transport', [('claude', 'exec-json'), ('grok', 'exec-json'),
                                             ('codex', 'app-server')])
def test_unverified_providers_are_refused(kind, transport, workspace):
    with pytest.raises(AdapterError, match='^runtime_work_profile_unsupported$'):
        StructuredRuntime(kind, executable=None, codex_transport=transport,
                          work_profile=True, work_workspace=str(workspace))


def test_valid_workspace_is_accepted_and_resolved(workspace):
    adapter = runtime(work_profile=True, work_workspace=str(workspace))
    try:
        assert adapter.work_profile is True
        assert adapter.work_workspace == workspace.resolve()
    finally:
        adapter.shutdown()


def test_work_command_keeps_isolation_and_enables_tools(workspace):
    adapter = runtime(work_profile=True, work_workspace=str(workspace))
    try:
        command = adapter.command()
        assert command[1:4] == ['exec', '--sandbox', 'workspace-write']
        options = [command[index + 1] for index, item in enumerate(command) if item == '-c']
        assert 'web_search="disabled"' in options
        assert 'features.hooks=false' in options
        assert 'features.plugin_hooks=false' in options
        assert 'approval_policy="never"' in options
        # The working tools must not be disabled in work mode.
        assert not any('shell_tool=false' in option for option in options)
        assert not any('unified_exec=false' in option for option in options)
        assert not any('apply_patch_freeform=false' in option for option in options)
        assert command[-1] == '-'
    finally:
        adapter.shutdown()


def test_text_profile_command_is_unchanged():
    adapter = runtime()
    try:
        command = adapter.command()
        assert command[command.index('--sandbox') + 1] == 'read-only'
        options = [command[index + 1] for index, item in enumerate(command) if item == '-c']
        assert 'features.shell_tool=false' in options
        assert 'features.apply_patch_freeform=false' in options
    finally:
        adapter.shutdown()


def test_work_prompt_names_the_directory_and_claims_nothing(workspace):
    text = work_prompt_text(workspace)
    assert str(workspace) in text
    assert 'only' in text
    assert 'platform applies nothing' in text


def test_work_prompt_precedes_the_room_prompt(workspace):
    room = prompt_text({'room_id': 'room-one', 'deliverable': 'change_proposal'}, work_profile=True)
    combined = work_prompt_text(workspace) + room
    assert combined.startswith('Work profile')
    assert 'Deliverable: a change proposal' in combined


def test_existing_work_workspace_is_never_deleted(workspace):
    (workspace / 'keep.txt').write_text('keep')
    adapter = runtime(work_profile=True, work_workspace=str(workspace))
    from talkabout.agent_runtime import _FixedWorkspace
    try:
        fixed = _FixedWorkspace(adapter.work_workspace)
        fixed.cleanup()
        assert (workspace / 'keep.txt').read_text() == 'keep'
    finally:
        adapter.shutdown()
