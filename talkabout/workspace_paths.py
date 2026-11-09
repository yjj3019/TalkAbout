"""Local workspace path checks for explicitly enabled owner work profiles.

This module rejects symbolic-link and junction/reparse components in the
absolute path before resolving it. Pathlib normalizes dot components. This is a
path-shape check only. It cannot prevent a privileged concurrent actor from replacing
components after the check, and is not an OS sandbox.
"""
import errno
import stat
from pathlib import Path


_WINDOWS_REPARSE_POINT = 0x400


def _is_link_component(path):
    """Inspect one lexical component without following it; errors propagate."""
    info = path.lstat()
    attributes = getattr(info, 'st_file_attributes', 0)
    if stat.S_ISLNK(info.st_mode) or attributes & _WINDOWS_REPARSE_POINT:
        return True
    checker = getattr(path, 'is_junction', None)
    # lstat catches a junction as a reparse point on Windows. Retain the runtime's
    # Path.is_junction check for supported Python versions as a second signal.
    return bool(checker()) if checker is not None else False


def has_linked_component(path):
    """Whether an absolute path includes a symlink or junction component.

    A missing final component is left for strict resolution to report as a
    missing workspace; any missing ancestor or metadata error fails closed.
    Inspect before strict resolution so intermediate links in the path are
    not silently followed.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        return True
    parts = candidate.parts
    if len(parts) < 2:
        return False
    current = Path(parts[0])
    for index, component in enumerate(parts[1:], start=1):
        parent = current
        current = current / component
        try:
            if _is_link_component(current):
                return True
        except OSError as error:
            # Windows may report ENOENT for a child beneath a regular file,
            # whereas POSIX reports ENOTDIR. Check the parent explicitly so only
            # a genuinely absent final component is deferred to strict resolve.
            if error.errno == errno.ENOENT:
                try:
                    parent_info = parent.lstat()
                except OSError:
                    return True
                if not stat.S_ISDIR(parent_info.st_mode):
                    return True
                is_final = index == len(parts) - 1
                if is_final:
                    return False
            return True
    return False
