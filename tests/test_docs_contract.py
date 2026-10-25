"""The documentation contract is enforced, not just documented.

`tools/docs_check.py` detects broken links, stale repo paths and orphan docs. These
tests keep that gate itself honest: they check the current tree, and they prove the
detector still flags a seeded violation.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

from docs_check import NON_PATH_TOKENS, check, check_links, slug  # noqa: E402


def test_current_docs_have_no_new_violations():
    new, known = check()
    assert new == [], 'docs contract violations: %r' % (new,)
    assert known == [], 'acknowledged docs debt must be fixed, not parked: %r' % (known,)


def test_broken_link_is_detected():
    violations = set()
    check_links(ROOT / 'README.md', '[gone](docs/DOES_NOT_EXIST.md)', violations)
    assert violations == {('link', 'README.md', 'docs/DOES_NOT_EXIST.md')}


def test_stale_repo_path_is_detected(tmp_path):
    path = ROOT / 'docs' / 'STATUS.md'
    violations = set()
    from docs_check import check_repo_paths
    check_repo_paths(path, 'see `talkabout/does_not_exist.py`', violations)
    assert ('path', 'docs/STATUS.md', 'talkabout/does_not_exist.py') in violations


def test_anchor_must_match_a_heading():
    violations = set()
    check_links(ROOT / 'docs' / 'RUNTIME_CONNECTIONS.md',
                '[flow](USER_FLOW.md#no-such-heading)', violations)
    assert ('anchor', 'docs/RUNTIME_CONNECTIONS.md', 'USER_FLOW.md#no-such-heading') in violations


def test_slug_follows_github_rules_for_mixed_korean_headings():
    # Two spaces (one from `/`, one its own) become two hyphens, not one.
    assert slug('참가자 PC 준비 / Client prerequisites') == '참가자-pc-준비--client-prerequisites'


def test_protocol_method_names_are_not_repo_paths():
    assert 'tools/list' in NON_PATH_TOKENS


def test_placeholder_paths_are_not_reported():
    from docs_check import check_repo_paths
    violations = set()
    check_repo_paths(ROOT / 'docs' / 'SESSION_LOG.md', '예: `talkabout/…`, `tests/…`', violations)
    assert violations == set()
