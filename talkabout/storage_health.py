"""Read-only self-check of the linked SQLite's WAL-reset exposure for the local journal.

Reporting only: this module never changes a PRAGMA, a connection or a file, and never
raises into a caller. The server database is PostgreSQL, so the exposure is limited to
the worker-local journal and the text file store.

SQLite's WAL-reset bug (https://sqlite.org/wal.html#walresetbug) affects 3.7.0 through
3.51.2, was fixed in 3.51.3, and is backported in 3.50.7 and 3.44.6 -- those two minor
lines are safe even though their version numbers sit inside the affected range.
"""
from __future__ import annotations

import sqlite3
import sys
import threading

# The affected window, its fix, and the backported minor lines (start inclusive, end exclusive).
_FIRST_AFFECTED = (3, 7, 0)
_FIXED_IN = (3, 51, 3)
_BACKPORT_WINDOWS = ((3, 50, 7, 3, 51, 0), (3, 44, 6, 3, 45, 0))

# journal_policy vocabulary. 'avoid_wal' = do not enable WAL on a file that has not yet
# enabled it; 'wal' = proven fixed or backported; 'wal_unverified' = no claim possible.
POLICY_AVOID_WAL = 'avoid_wal'
POLICY_WAL = 'wal'
POLICY_UNVERIFIED = 'wal_unverified'

# reason vocabulary -- fixed strings only: no path, environment or credential value.
REASON_AFFECTED = 'wal_reset_bug_affected_range'
REASON_SAFE = 'wal_reset_bug_fixed_or_backported'
REASON_UNKNOWN = 'sqlite_version_unavailable'

# The one warning token. Its only variable part is the numeric version.
WARNING_EVENT = 'journal_sqlite_wal_reset_exposed'
_UNKNOWN_VERSION = 'unknown'

_lock = threading.Lock()
_warned = False


def _normalize(version_info):
    """A comparable version tuple, or None when the input cannot be read as one.

    Accepts a short tuple (``(3, 50)`` -> ``(3, 50, 0)``). Bools are rejected because
    ``True`` would otherwise pass as ``1``.
    """
    if not isinstance(version_info, (tuple, list)) or not version_info:
        return None
    parts = []
    for part in version_info:
        if isinstance(part, bool) or not isinstance(part, int) or part < 0:
            return None
        parts.append(part)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def sqlite_version():
    """``(major, minor, patch)`` of the SQLite linked into this interpreter, or None."""
    try:
        version_info = sqlite3.sqlite_version_info
    except AttributeError:  # a build without the attribute cannot be verified
        return None
    normalized = _normalize(version_info)
    return None if normalized is None else normalized[:3]


def wal_reset_vulnerable(version_info=None):
    """True when this SQLite carries the WAL-reset bug.

    Fail-closed: an unreadable version returns True, because "not proven safe" is not
    "safe". :func:`describe` reports that case as ``wal_unverified`` instead of claiming
    the bug is present.
    """
    info = sqlite_version() if version_info is None else _normalize(version_info)
    if info is None:
        return True
    if info < _FIRST_AFFECTED or info >= _FIXED_IN:
        return False
    for start_major, start_minor, start_patch, end_major, end_minor, end_patch in _BACKPORT_WINDOWS:
        if (start_major, start_minor, start_patch) <= info < (end_major, end_minor, end_patch):
            return False
    return True


def describe():
    """Fixed-vocabulary report: version, exposure verdict, journal policy and reason."""
    info = sqlite_version()
    if info is None:
        return {
            'sqlite_version': _UNKNOWN_VERSION,
            'wal_reset_vulnerable': True,
            'journal_policy': POLICY_UNVERIFIED,
            'reason': REASON_UNKNOWN,
        }
    vulnerable = wal_reset_vulnerable(info)
    return {
        'sqlite_version': '.'.join(str(part) for part in info),
        'wal_reset_vulnerable': vulnerable,
        'journal_policy': POLICY_AVOID_WAL if vulnerable else POLICY_WAL,
        'reason': REASON_AFFECTED if vulnerable else REASON_SAFE,
    }


def warn_if_journal_is_exposed():
    """Emit one fixed-vocabulary stderr line per process when the journal's WAL is exposed.

    Never raises and never repeats. An unverifiable version stays silent here by design:
    the report already says ``wal_unverified``, and a warning that fires on every startup
    of an unknown build is noise rather than evidence.
    """
    global _warned
    try:
        with _lock:
            if _warned:
                return
            report = describe()
            if report['journal_policy'] != POLICY_AVOID_WAL:
                return
            _warned = True
        sys.stderr.write(WARNING_EVENT + ' ' + report['sqlite_version'] + '\n')
        sys.stderr.flush()
    except Exception:  # diagnostics must never break journal startup
        return


def _reset_warning_state_for_tests():
    """Test seam: forget the once-per-process warning."""
    global _warned
    with _lock:
        _warned = False
