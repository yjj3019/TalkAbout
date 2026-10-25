"""Documentation contract checks. Exit 1 on any new violation.

Docs drift silently: a link to a renamed file, a backticked module that no longer
exists, or a doc nobody links from anywhere. These checks are cheap, deterministic
and run from the test suite (tests/test_docs_contract.py), so CI keeps them honest.

The known-violations list may only shrink. It exists to acknowledge today's debt
instead of hiding it behind a green gate; each entry needs a reason.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_FILES = ['README.md', 'docs']
SKIP_DIRS = {'validation'}
LINK = re.compile(r'!?\[[^\]]*\]\(([^)\s]+)\)')
FENCE = re.compile(r'```.*?```', re.DOTALL)
INLINE_CODE = re.compile(r'`([^`\n]+)`')
HEADING = re.compile(r'^#{1,6}\s+(.*)$', re.MULTILINE)
# A token is only a candidate when it could be a path at all. Ellipses, placeholders and
# prose fragments are excluded here rather than by an ever-growing character blacklist.
PATHISH = re.compile(r'^[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*$')
# Only tokens whose first segment is a real top-level entry are treated as repo paths,
# so placeholder text and user-home files (config.toml, agent.local.json) are not checked.
REPO_ROOTS = {'talkabout', 'tests', 'tools', 'docs', 'deploy', 'prompts', '.github'}

# Acknowledged debt: (kind, where, detail). Must only shrink; each needs a reason.
KNOWN: set[tuple[str, str, str]] = set()

# Not repository paths: these are MCP/JSON-RPC method names that happen to start with a
# repo root. Keep the list explicit rather than widening the path rule.
NON_PATH_TOKENS = {'tools/list', 'tools/call', 'prompts/list', 'prompts/get'}


def doc_paths():
    paths = []
    readme = ROOT / 'README.md'
    if readme.is_file():
        paths.append(readme)
    docs = ROOT / 'docs'
    if docs.is_dir():
        for path in sorted(docs.rglob('*.md')):
            if not SKIP_DIRS & set(path.relative_to(docs).parts):
                paths.append(path)
    return paths


def strip_code(text):
    """Ignore fenced and inline code for link checks; heading slugs keep their text."""
    return FENCE.sub('', text)


def slug(heading):
    """GitHub-style slug: lowercase, drop punctuation, then one hyphen per space."""
    value = heading.strip().lower()
    value = re.sub(r'[`*_~]', '', value)
    value = re.sub(r'[^\w\s-]', '', value)
    return value.replace(' ', '-').strip('-')


def anchors(path):
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return set()
    return {slug(value) for value in HEADING.findall(text)}


def check_links(path, text, violations):
    near = path.relative_to(ROOT).as_posix()
    for target in LINK.findall(strip_code(text)):
        if re.match(r'^[a-z][a-z0-9+.-]*:', target, re.IGNORECASE) or target.startswith('/'):
            continue
        file_part, _, anchor = target.partition('#')
        file_part = file_part.strip()
        resolved = (path.parent / file_part).resolve() if file_part else path
        if not resolved.exists():
            violations.add(('link', near, target))
            continue
        if anchor and resolved.suffix == '.md' and resolved.is_file():
            if anchor not in anchors(resolved):
                violations.add(('anchor', near, target))


def check_repo_paths(path, text, violations):
    near = path.relative_to(ROOT).as_posix()
    for token in INLINE_CODE.findall(FENCE.sub('', text)):
        candidate = token.strip().split('#', 1)[0].strip()
        if not candidate or candidate in NON_PATH_TOKENS:
            continue
        if not PATHISH.match(candidate) or '..' in candidate:
            continue
        first = candidate.split('/', 1)[0]
        if first not in REPO_ROOTS and '/' in candidate:
            continue
        if '/' not in candidate and not (ROOT / candidate).exists():
            continue
        if first not in REPO_ROOTS:
            continue
        resolved = ROOT / candidate
        if not resolved.exists():
            violations.add(('path', near, candidate))


def check_orphans(violations):
    linked = set()
    for path in doc_paths():
        near = path.relative_to(ROOT).as_posix()
        try:
            text = strip_code(path.read_text(encoding='utf-8'))
        except OSError:
            continue
        for target in LINK.findall(text):
            file_part = target.partition('#')[0].strip()
            if not file_part or re.match(r'^[a-z][a-z0-9+.-]*:', file_part, re.IGNORECASE):
                continue
            resolved = (path.parent / file_part).resolve()
            if resolved.suffix == '.md':
                linked.add(resolved)
    for path in doc_paths():
        if path.resolve() in linked:
            continue
        if path.name.upper() in {'README.MD'}:
            continue
        violations.add(('orphan', path.relative_to(ROOT).as_posix(), 'not linked from README.md or docs/'))


def check():
    violations = set()
    for path in doc_paths():
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            continue
        check_links(path, text, violations)
        if path.relative_to(ROOT).as_posix() != 'README.md':
            check_repo_paths(path, text, violations)
    check_orphans(violations)
    new = sorted(violations - KNOWN)
    known = sorted(violations & KNOWN)
    return new, known


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='machine-readable output')
    options = parser.parse_args()
    new, known = check()
    if options.json:
        print(json.dumps({'new': [list(item) for item in new],
                          'known': [list(item) for item in known]}, indent=2))
    else:
        for kind, where, detail in new:
            print('docs_violation %s %s %s' % (kind, where, detail))
        for kind, where, detail in known:
            print('docs_known %s %s %s' % (kind, where, detail))
        print('docs_check %d new, %d known' % (len(new), len(known)))
    return 1 if new else 0


if __name__ == '__main__':
    raise SystemExit(main())
