"""A created attachment root must be usable by the process that created it.

Windows ignores os.mkdir's mode argument, but a restricted token can turn an explicit
0o700 into a DACL it cannot use, which made the storage root exist yet reject every
write. POSIX still gets 0o700.
"""
import hashlib
import os
import stat

import pytest

from talkabout.text_files import PrivateTextStorage

KEY = '0' * 32


def test_created_root_accepts_a_round_trip(tmp_path):
    storage = PrivateTextStorage(tmp_path / 'created-storage', create=True)
    content = b'synthetic attachment body'
    storage.write(KEY, content, hashlib.sha256(content).hexdigest())
    assert storage.read(KEY, hashlib.sha256(content).hexdigest(), len(content)) == content


def test_created_root_is_usable_more_than_once(tmp_path):
    storage = PrivateTextStorage(tmp_path / 'repeated-storage', create=True)
    for number in range(3):
        content = ('synthetic body %d' % number).encode('utf-8')
        key = ('%032x' % number)
        storage.write(key, content, hashlib.sha256(content).hexdigest())
        assert storage.read(key, hashlib.sha256(content).hexdigest(), len(content)) == content


@pytest.mark.skipif(os.name == 'nt', reason='Windows ignores the POSIX mode argument')
def test_posix_root_keeps_owner_only_mode(tmp_path):
    root = tmp_path / 'posix-storage'
    PrivateTextStorage(root, create=True)
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
