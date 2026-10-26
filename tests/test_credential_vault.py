"""All credentials here are SYNTHETIC; no network or external services."""
import base64
import json
import os
from pathlib import Path

import pytest

from talkabout.credential_vault import CredentialVault, MAX_FILE_BYTES, VaultError

BINDING = {"base_url": "http://127.0.0.1:8080", "allowed_origin": "http://127.0.0.1:8080"}
TOKEN = "SYNTHETIC-test-token-not-a-real-credential"


class FakeProtector:
    """Opaque in-memory handles, so portable tests never persist plaintext."""
    def __init__(self):
        self.values = {}

    def protect(self, value):
        key = b"fake-encrypted-" + str(len(self.values)).encode()
        self.values[key] = value
        return key

    def unprotect(self, value):
        return self.values[value]


def set_windows_dacl(path, *, everyone=False):
    """Set a protected inheritable test DACL without launching an external tool."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi.SetFileSecurityW.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        # TokenUser starts with SID_AND_ATTRIBUTES, whose first member is PSID.
        if advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size)):
            raise AssertionError("TokenUser size query unexpectedly succeeded")
        if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
            raise ctypes.WinError(ctypes.get_last_error())
        info = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, info, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(info, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            current_sid = text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)

    # P removes inherited permissions; OICI propagates full control to files/children.
    sddl = "D:P(A;OICI;FA;;;" + current_sid + ")"
    if everyone:
        sddl += "(A;OICI;FA;;;WD)"
    descriptor = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "vault"
    path.mkdir(mode=0o700)
    if os.name == "nt":
        set_windows_dacl(path)
    return path


@pytest.fixture
def vault(root):
    return CredentialVault(root, _protector=FakeProtector())


def assert_code(code, operation):
    with pytest.raises(VaultError) as caught:
        operation()
    assert caught.value.code == code
    assert str(caught.value) == code
    assert TOKEN not in str(caught.value)


def write_private(path, data):
    path.write_bytes(data)
    if os.name != "nt":
        path.chmod(0o600)


def test_roundtrip_metadata_delete_and_no_plaintext(vault, root, capsys):
    vault.save("work_1-a", TOKEN, BINDING)
    assert vault.load("work_1-a", BINDING) == TOKEN
    assert vault.list_profiles() == [{"profile": "work_1-a", "version": 1}]
    contents = (root / "work_1-a.json").read_bytes()
    assert TOKEN.encode() not in contents
    assert b"base_url" not in contents and b"127.0.0.1" not in contents
    assert set(json.loads(contents)) == {"profile", "version", "ciphertext"}
    vault.delete("work_1-a")
    assert vault.list_profiles() == []
    assert_code("not_found", lambda: vault.load("work_1-a", BINDING))
    assert_code("not_found", lambda: vault.delete("work_1-a"))
    assert capsys.readouterr() == ("", "")


def test_duplicate_does_not_overwrite(vault, root):
    vault.save("work", TOKEN, BINDING)
    original = (root / "work.json").read_bytes()
    assert_code("conflict", lambda: vault.save("work", "SYNTHETIC-other", BINDING))
    assert (root / "work.json").read_bytes() == original
    assert not list(root.glob("*.tmp"))


@pytest.mark.parametrize("profile", ["", "../escape", "a/b", "a\\b", ".", "a.json", "a:b", "a " , "é", "x" * 65, None, 1])
def test_profile_validation(vault, profile):
    for operation in (lambda: vault.save(profile, TOKEN, BINDING),
                      lambda: vault.load(profile, BINDING), lambda: vault.delete(profile)):
        assert_code("invalid_profile", operation)


@pytest.mark.parametrize("binding", [
    {}, {"base_url": "https://host", "allowed_origin": "https://other"},
    {"base_url": "http://example.org", "allowed_origin": "http://example.org"},
    {"base_url": "https://user:pass@host", "allowed_origin": "https://host"},
    {"base_url": "https://host?secret", "allowed_origin": "https://host"},
    {"base_url": "https://host#secret", "allowed_origin": "https://host"},
    {"base_url": "https://host", "allowed_origin": "https://host/path"},
    {"base_url": "https://host:99999", "allowed_origin": "https://host:99999"},
    {"base_url": "https://host\\evil", "allowed_origin": "https://host"},
    {"base_url": "https://host\n", "allowed_origin": "https://host"},
    {"base_url": "https://", "allowed_origin": "https://"},
    {"base_url": "https://host?", "allowed_origin": "https://host"},
])
def test_binding_validation(vault, binding):
    assert_code("invalid_binding", lambda: vault.save("work", TOKEN, binding))
    assert_code("invalid_binding", lambda: vault.load("work", binding))


def test_binding_canonicalization_and_mismatch(vault):
    vault.save("work", TOKEN, {"base_url": "https://EXAMPLE.org:443/api/", "allowed_origin": "https://example.org/"})
    assert vault.load("work", {"base_url": "https://example.org/api", "allowed_origin": "https://EXAMPLE.org:443"}) == TOKEN
    for binding in ({"base_url": "https://example.org/other", "allowed_origin": "https://example.org"}, BINDING):
        assert_code("binding_mismatch", lambda: vault.load("work", binding))


def test_binding_is_inside_encrypted_envelope(vault):
    vault.save("work", TOKEN, BINDING)
    plaintext = json.loads(next(iter(vault._protector.values.values())))
    assert plaintext["binding"] == BINDING
    assert plaintext["profile"] == "work"
    assert plaintext["token"] == TOKEN


@pytest.mark.parametrize("data", [b"not-json", b"{}", b"[]", b'{"version":1,"version":1}',
    b'{"profile":"work","version":1,"ciphertext":"!!!"}',
    b'{"profile":"other","version":1,"ciphertext":"YQ=="}',
    b'{"profile":"work","version":true,"ciphertext":"YQ=="}',
    pytest.param(b"x" * (MAX_FILE_BYTES + 1), id="oversize")])
def test_corruption_and_size(vault, root, data):
    write_private(root / "work.json", data)
    assert_code("invalid_data", lambda: vault.load("work", BINDING))
    assert_code("invalid_data", vault.list_profiles)
    # Corruption must not prevent explicit deletion.
    vault.delete("work")


def test_ciphertext_tampering_and_secret_free_errors(vault, root, capsys):
    vault.save("work", TOKEN, BINDING)
    data = {"profile": "work", "version": 1, "ciphertext": base64.b64encode(TOKEN.encode()).decode()}
    write_private(root / "work.json", json.dumps(data).encode())
    assert_code("protection_failed", lambda: vault.load("work", BINDING))
    assert capsys.readouterr() == ("", "")


def test_swapping_profiles_fails_closed(vault, root):
    vault.save("one", TOKEN, BINDING)
    data = json.loads((root / "one.json").read_bytes())
    data["profile"] = "two"
    write_private(root / "two.json", json.dumps(data).encode())
    assert_code("invalid_data", lambda: vault.load("two", BINDING))


def test_decrypted_corruption_and_protector_failure(vault):
    vault.save("work", TOKEN, BINDING)
    key = next(iter(vault._protector.values))
    for bad in (b"[", b"[]", b"{}", b"x" * (MAX_FILE_BYTES + 1)):
        vault._protector.values[key] = bad
        assert_code("invalid_data", lambda: vault.load("work", BINDING))

    class Broken:
        def protect(self, data):
            raise RuntimeError(TOKEN)

    vault._protector = Broken()
    assert_code("protection_failed", lambda: vault.save("other", TOKEN, BINDING))


def test_atomic_publication_failure_leaves_no_profile(vault, root, monkeypatch):
    def fail_link(*args, **kwargs):
        raise PermissionError(TOKEN)
    monkeypatch.setattr(os, "link", fail_link)
    assert_code("io_error", lambda: vault.save("work", TOKEN, BINDING))
    assert list(root.iterdir()) == []


def test_entry_replaced_by_directory_fails_closed(vault, root):
    (root / "work.json").mkdir()
    for operation in (lambda: vault.load("work", BINDING), lambda: vault.delete("work"),
                      lambda: vault.save("work", TOKEN, BINDING), vault.list_profiles):
        assert_code("unsafe_path", operation)


def test_exclusive_publish_race_preserves_existing(vault, root, monkeypatch):
    original_link = os.link
    def race(source, target, **kwargs):
        write_private(Path(target), b"existing")
        return original_link(source, target, **kwargs)
    monkeypatch.setattr(os, "link", race)
    assert_code("conflict", lambda: vault.save("work", TOKEN, BINDING))
    assert (root / "work.json").read_bytes() == b"existing"
    assert not list(root.glob("*.tmp"))


def test_list_sorted_and_does_not_decrypt(vault):
    vault.save("z", TOKEN, BINDING)
    vault.save("a", TOKEN, BINDING)
    vault._protector.values.clear()
    assert vault.list_profiles() == [{"profile": "a", "version": 1}, {"profile": "z", "version": 1}]


@pytest.mark.parametrize("token", ["", None, "has space", "has\nnewline", "x" * 16385])
def test_invalid_token(vault, token):
    assert_code("invalid_token", lambda: vault.save("work", token, BINDING))


def test_missing_root_is_not_created(tmp_path):
    path = tmp_path / "absent"
    assert_code("io_error", lambda: CredentialVault(path, _protector=FakeProtector()))
    assert not path.exists()


def test_symlink_root_ancestor_and_entries(root, tmp_path, vault):
    link = tmp_path / "link"
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not allow test symlinks")
    assert_code("unsafe_path", lambda: CredentialVault(link, _protector=FakeProtector()))
    child = root / "child"
    child.mkdir(mode=0o700)
    assert_code("unsafe_path", lambda: CredentialVault(link / "child", _protector=FakeProtector()))
    vault.save("real", TOKEN, BINDING)
    (root / "alias.json").symlink_to(root / "real.json")
    assert_code("unsafe_path", lambda: vault.load("alias", BINDING))
    assert_code("unsafe_path", lambda: vault.save("alias", TOKEN, BINDING))
    assert_code("unsafe_path", lambda: vault.delete("alias"))
    assert_code("unsafe_path", vault.list_profiles)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction/reparse point")
def test_windows_junction_rejected(root, tmp_path):
    junction = tmp_path / "junction"
    try:
        import _winapi
    except ImportError:
        pytest.skip("Windows junction OS API unavailable: _winapi")
    create_junction = getattr(_winapi, "CreateJunction", None)
    if create_junction is None:
        pytest.skip("Windows junction OS API unavailable: _winapi.CreateJunction")
    create_junction(str(root), str(junction))
    try:
        assert_code("unsafe_path", lambda: CredentialVault(junction, _protector=FakeProtector()))
        (root / "nested").mkdir()
        assert_code("unsafe_path", lambda: CredentialVault(junction / "nested", _protector=FakeProtector()))
    finally:
        junction.rmdir()


def test_reparse_attribute_is_rejected(root, monkeypatch):
    real_lstat = Path.lstat

    def reparse(path):
        info = real_lstat(path)
        if path == root:
            class Reparse:
                st_mode = info.st_mode
                st_file_attributes = 0x400
            return Reparse()
        return info

    monkeypatch.setattr(Path, "lstat", reparse)
    assert_code("unsafe_path", lambda: CredentialVault(root, _protector=FakeProtector()))


def test_root_revalidated_each_operation(vault, root, monkeypatch):
    root.rename(root.with_name("moved"))
    root.mkdir(mode=0o700)
    if os.name == "nt":
        set_windows_dacl(root, everyone=True)
    else:
        root.chmod(0o755)
    for operation in (lambda: vault.save("work", TOKEN, BINDING), lambda: vault.load("work", BINDING),
                      lambda: vault.delete("work"), vault.list_profiles):
        assert_code("unsafe_path", operation)


@pytest.mark.skipif(os.name != "nt", reason="Windows current-user DPAPI only")
def test_windows_real_dpapi_synthetic_roundtrip(root):
    vault = CredentialVault(root)
    vault.save("synthetic", TOKEN, BINDING)
    assert CredentialVault(root).load("synthetic", BINDING) == TOKEN
    assert TOKEN.encode() not in (root / "synthetic.json").read_bytes()
    assert_code("binding_mismatch", lambda: vault.load("synthetic", {"base_url": "https://example.org", "allowed_origin": "https://example.org"}))
    vault.delete("synthetic")


@pytest.mark.skipif(os.name == "nt", reason="POSIX unsupported default")
def test_posix_default_fails_closed(root):
    assert_code("unsupported_platform", lambda: CredentialVault(root))
