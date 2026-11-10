"""Owner work-profile workspace path shape checks; no providers are run."""
import errno
import os
from pathlib import Path

import pytest

from talkabout import workspace_paths
from talkabout.adapters import AdapterError
from talkabout.agent_runtime import _validated_work_workspace


def test_relative_workspace_path_fails_closed():
    assert workspace_paths.has_linked_component('relative/workspace')
    with pytest.raises(AdapterError, match='^runtime_work_workspace_invalid$'):
        _validated_work_workspace('codex', 'exec-json', 'relative/workspace')


@pytest.mark.skipif(os.name == 'nt', reason='symlink creation needs a privilege on Windows')
def test_intermediate_symlink_component_is_detected(tmp_path):
    base = tmp_path.resolve(strict=True)
    target = base / 'real'
    target.mkdir()
    alias = base / 'alias'
    alias.symlink_to(target, target_is_directory=True)

    assert workspace_paths.has_linked_component(alias / 'workspace')


@pytest.mark.parametrize('junction_component', ['final', 'ancestor'])
def test_final_and_ancestor_junction_components_are_detected(tmp_path, monkeypatch, junction_component):
    base = tmp_path.resolve(strict=True)
    parent = base / 'parent'
    parent.mkdir()
    candidate = parent / 'workspace'
    candidate.mkdir()
    junction = candidate if junction_component == 'final' else parent
    original = Path.lstat

    class JunctionStat:
        st_mode = 0o40700
        st_file_attributes = workspace_paths._WINDOWS_REPARSE_POINT

    def report_junction(self):
        return JunctionStat() if self == junction else original(self)

    monkeypatch.setattr(Path, 'lstat', report_junction)
    monkeypatch.setattr(Path, 'is_junction', lambda self: self == junction, raising=False)

    assert workspace_paths.has_linked_component(candidate)


def test_plain_absolute_directory_has_no_linked_component(tmp_path, monkeypatch):
    # CI may place tmp_path inside the checkout, which the platform-overlap rule rightly refuses;
    # point the platform root elsewhere so this test only exercises the link-shape check.
    from talkabout import agent_runtime
    elsewhere = Path(os.path.abspath(os.sep)) / 'nonexistent-platform' / 'talkabout' / 'agent_runtime.py'
    monkeypatch.setattr(agent_runtime, '__file__', str(elsewhere))
    candidate = tmp_path.resolve(strict=True) / 'workspace'
    candidate.mkdir()

    base = tmp_path.resolve(strict=True)
    assert not workspace_paths.has_linked_component(candidate)
    assert _validated_work_workspace('codex', 'exec-json', str(candidate)) == candidate.resolve()
    assert not workspace_paths.has_linked_component(base.anchor)


def test_component_metadata_error_fails_closed(tmp_path, monkeypatch):
    base = tmp_path.resolve(strict=True)
    ancestor = base / 'parent'
    ancestor.mkdir()
    candidate = ancestor / 'workspace'
    candidate.mkdir()
    original = Path.lstat
    failed = []

    def fail_on_candidate(self):
        if self == ancestor:
            failed.append(self)
            raise OSError(errno.EACCES, 'inspection denied')
        return original(self)

    monkeypatch.setattr(Path, 'lstat', fail_on_candidate)
    assert workspace_paths.has_linked_component(candidate)
    assert failed == [ancestor]


def test_missing_final_component_is_not_misclassified_as_link(tmp_path):
    candidate = tmp_path.resolve(strict=True) / 'missing'
    assert not workspace_paths.has_linked_component(candidate)
    with pytest.raises(AdapterError, match='^runtime_work_workspace_missing$'):
        _validated_work_workspace('codex', 'exec-json', str(candidate))


def test_missing_intermediate_component_fails_closed(tmp_path):
    candidate = tmp_path.resolve(strict=True) / 'missing' / 'workspace'
    assert workspace_paths.has_linked_component(candidate)
    with pytest.raises(AdapterError, match='^runtime_work_workspace_invalid$'):
        _validated_work_workspace('codex', 'exec-json', str(candidate))


def test_existing_file_used_as_intermediate_component_fails_closed(tmp_path):
    base = tmp_path.resolve(strict=True)
    parent = base / 'not-a-directory'
    parent.write_text('x')
    candidate = parent / 'workspace'
    assert workspace_paths.has_linked_component(candidate)
    with pytest.raises(AdapterError, match='^runtime_work_workspace_invalid$'):
        _validated_work_workspace('codex', 'exec-json', str(candidate))


@pytest.mark.parametrize('parent_is_directory, expected', [(True, False), (False, True)])
def test_enoent_is_only_deferred_for_final_child_of_directory(tmp_path, monkeypatch,
                                                              parent_is_directory, expected):
    base = tmp_path.resolve(strict=True)
    parent = base / 'parent'
    if parent_is_directory:
        parent.mkdir()
    else:
        parent.write_text('x')
    candidate = parent / 'missing'
    original = Path.lstat

    def report_missing(path):
        if path == candidate:
            raise FileNotFoundError(errno.ENOENT, 'missing')
        return original(path)

    monkeypatch.setattr(Path, 'lstat', report_missing)
    assert workspace_paths.has_linked_component(candidate) is expected


def test_enotdir_fails_closed(tmp_path, monkeypatch):
    base = tmp_path.resolve(strict=True)
    candidate = base / 'parent' / 'missing'
    original = Path.lstat

    def report_not_directory(path):
        if path == candidate:
            raise NotADirectoryError(errno.ENOTDIR, 'parent is not directory')
        return original(path)

    monkeypatch.setattr(Path, 'lstat', report_not_directory)
    assert workspace_paths.has_linked_component(candidate)


def test_final_nonjunction_reparse_attribute_is_refused(tmp_path, monkeypatch):
    candidate = tmp_path.resolve(strict=True) / 'workspace'
    candidate.mkdir()
    original = Path.lstat
    inspected = []

    class ReparseStat:
        st_mode = 0o40700
        st_file_attributes = 0x400

    def report_reparse(self):
        inspected.append(self)
        return ReparseStat() if self == candidate else original(self)

    monkeypatch.setattr(Path, 'lstat', report_reparse)
    assert workspace_paths.has_linked_component(candidate)
    assert candidate in inspected
