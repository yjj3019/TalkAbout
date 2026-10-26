"""Opt-in, current-user Windows DPAPI bearer storage.

Nothing creates a directory or stores credentials implicitly. Callers must supply
an existing private directory. DPAPI is deliberately unavailable off Windows;
``_protector`` is a private testing seam, not an alternative storage backend.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
import re
import stat
import uuid
from urllib.parse import urlsplit, urlunsplit

MAX_FILE_BYTES = 64 * 1024
_PROFILE = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")
_CODES = frozenset({"unsupported_platform", "invalid_profile", "invalid_binding",
                    "invalid_token", "unsafe_path", "not_found", "conflict",
                    "invalid_data", "binding_mismatch", "protection_failed", "io_error"})


class VaultError(Exception):
    """A fixed, non-secret diagnostic; ``code`` is safe for CLI output."""

    def __init__(self, code: str):
        self.code = code if code in _CODES else "io_error"
        super().__init__(self.code)


def _profile(value):
    if not isinstance(value, str) or not _PROFILE.fullmatch(value):
        raise VaultError("invalid_profile")
    return value


def _binding(value):
    try:
        if not isinstance(value, dict) or set(value) != {"base_url", "allowed_origin"}:
            raise ValueError
        result = []
        for key in ("base_url", "allowed_origin"):
            raw = value[key]
            if not isinstance(raw, str) or not raw or any(ord(c) <= 32 or ord(c) == 127 for c in raw) or "\\" in raw:
                raise ValueError
            p = urlsplit(raw)
            if (not p.hostname or p.username is not None or p.password is not None
                    or "?" in raw or "#" in raw
                    or p.scheme not in {"http", "https"}
                    or (p.scheme == "http" and p.hostname.lower() not in {"localhost", "127.0.0.1", "::1"})):
                raise ValueError
            host = p.hostname.lower()
            host = "[" + host + "]" if ":" in host else host
            port = p.port  # also rejects malformed/out-of-range ports
            netloc = host + (":" + str(port) if port is not None and port != (443 if p.scheme == "https" else 80) else "")
            origin = p.scheme + "://" + netloc
            if key == "allowed_origin" and p.path not in {"", "/"}:
                raise ValueError
            result.append((origin, urlunsplit((p.scheme, netloc, p.path.rstrip("/"), "", ""))))
        if result[0][0] != result[1][0]:
            raise ValueError
        return {"base_url": result[0][1], "allowed_origin": result[1][0]}
    except (ValueError, TypeError, UnicodeError):
        raise VaultError("invalid_binding") from None


class _DPAPI:
    def __init__(self):
        if os.name != "nt":
            raise VaultError("unsupported_platform")
        from ctypes import wintypes as w

        class Blob(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        self.Blob = Blob
        self.crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        ptr = ctypes.POINTER(Blob)
        self.crypt.CryptProtectData.argtypes = [ptr, w.LPCWSTR, ptr, ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ptr]
        self.crypt.CryptUnprotectData.argtypes = [ptr, ctypes.c_void_p, ptr, ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ptr]
        self.crypt.CryptProtectData.restype = self.crypt.CryptUnprotectData.restype = w.BOOL
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p

    def _run(self, data, decrypt):
        buffer = ctypes.create_string_buffer(data)
        source = self.Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = self.Blob()
        function = self.crypt.CryptUnprotectData if decrypt else self.crypt.CryptProtectData
        # CRYPTPROTECT_UI_FORBIDDEN only. Never CRYPTPROTECT_LOCAL_MACHINE.
        try:
            if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
                raise VaultError("protection_failed")
            if target.size > MAX_FILE_BYTES:
                raise VaultError("invalid_data")
            return ctypes.string_at(target.data, target.size)
        finally:
            if target.data:
                self.kernel.LocalFree(target.data)

    def protect(self, data):
        return self._run(data, False)

    def unprotect(self, data):
        return self._run(data, True)


def _windows_private(path):
    """Allow this user, SYSTEM/admins, and OWNER RIGHTS for a trusted owner."""
    from ctypes import wintypes as w
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    adv.GetNamedSecurityInfoW.restype = w.DWORD
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = w.BOOL
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    adv.GetAce.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]

    def sid_string(sid):
        text = w.LPWSTR()
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise VaultError("unsafe_path")
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))

    token = w.HANDLE()
    descriptor = ctypes.c_void_p()
    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise VaultError("unsafe_path")
        needed = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise VaultError("unsafe_path")
        user = sid_string(ctypes.c_void_p.from_buffer(buffer).value)
        owner, dacl = ctypes.c_void_p(), ctypes.c_void_p()
        if adv.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor)) or not dacl:
            raise VaultError("unsafe_path")
        if sid_string(owner) not in {user, "S-1-5-18", "S-1-5-32-544"}:
            raise VaultError("unsafe_path")
        # ACL header: BYTE revision, BYTE reserved, WORD size, WORD ace count.
        count = ctypes.c_ushort.from_address(dacl.value + 4).value
        for index in range(count):
            ace = ctypes.c_void_p()
            if not adv.GetAce(dacl, index, ctypes.byref(ace)):
                raise VaultError("unsafe_path")
            kind = ctypes.c_ubyte.from_address(ace.value).value
            flags = ctypes.c_ubyte.from_address(ace.value + 1).value
            if flags & 8:  # INHERIT_ONLY_ACE does not grant access to this entry
                continue
            if kind == 0:  # ACCESS_ALLOWED_ACE
                if sid_string(ace.value + 8) not in {user, "S-1-5-18", "S-1-5-32-544", "S-1-3-4"}:
                    raise VaultError("unsafe_path")
            elif kind != 1:  # Reject unfamiliar allow/object/callback ACEs conservatively.
                raise VaultError("unsafe_path")
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if token:
            kernel.CloseHandle(token)


def _safe_stat(path, *, private=False, directory=False):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise VaultError("unsafe_path")
    if directory and not stat.S_ISDIR(info.st_mode):
        raise VaultError("unsafe_path")
    if not directory and not stat.S_ISREG(info.st_mode):
        raise VaultError("unsafe_path")
    if private:
        if os.name == "nt":
            _windows_private(path)
        elif info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise VaultError("unsafe_path")
    return info


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


class CredentialVault:
    """Explicit opt-in vault. Existing profiles are never overwritten.

    ``list_profiles`` exposes only ``profile`` and ``version``. Missing profiles
    raise ``VaultError('not_found')`` for both load and delete. Root privacy and
    symlink/reparse checks are repeated for each operation (not a hostile-admin
    or concurrently replaced directory security boundary).
    """

    def __init__(self, root: Path, *, _protector=None):
        try:
            supplied = Path(root)
            if ".." in supplied.parts:
                raise VaultError("unsafe_path")
            self.root = Path(os.path.abspath(supplied))
            self._check_root()
            self._protector = _protector if _protector is not None else _DPAPI()
        except VaultError:
            raise
        except Exception:
            raise VaultError("io_error") from None

    def _check_root(self):
        for ancestor in reversed(self.root.parents):
            _safe_stat(ancestor, directory=True)
        return _safe_stat(self.root, private=True, directory=True)

    def _read(self, profile):
        self._check_root()
        path = self.root / (profile + ".json")
        info = _safe_stat(path, private=True)
        if info.st_size > MAX_FILE_BYTES or info.st_nlink != 1:
            raise VaultError("invalid_data")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise VaultError("unsafe_path")
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise VaultError("invalid_data")
        envelope = _json(data)
        if (not isinstance(envelope, dict) or set(envelope) != {"profile", "version", "ciphertext"}
                or envelope["profile"] != profile or type(envelope["version"]) is not int or envelope["version"] != 1):
            raise VaultError("invalid_data")
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
        if not ciphertext:
            raise VaultError("invalid_data")
        return ciphertext

    def save(self, profile, token, binding: dict):
        profile = _profile(profile)
        binding = _binding(binding)
        if not isinstance(token, str) or not token or len(token) > 16384 or any(ord(c) < 33 or ord(c) == 127 for c in token):
            raise VaultError("invalid_token")
        temporary = None
        try:
            self._check_root()
            target = self.root / (profile + ".json")
            if os.path.lexists(target):
                _safe_stat(target, private=True)
                raise VaultError("conflict")
            plaintext = json.dumps({"version": 1, "profile": profile, "binding": binding, "token": token}, separators=(",", ":")).encode("utf-8")
            try:
                ciphertext = self._protector.protect(plaintext)
                if not isinstance(ciphertext, bytes) or not ciphertext:
                    raise ValueError
            except Exception:
                raise VaultError("protection_failed") from None
            data = json.dumps({"version": 1, "profile": profile, "ciphertext": base64.b64encode(ciphertext).decode("ascii")}, separators=(",", ":")).encode("ascii")
            if len(data) > MAX_FILE_BYTES:
                raise VaultError("invalid_data")
            temporary = self.root / (".vault-" + uuid.uuid4().hex + ".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self._check_root()
            _safe_stat(temporary, private=True)
            # link is an atomic, exclusive publication on Windows and POSIX.
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            raise VaultError("conflict") from None
        except VaultError:
            raise
        except Exception:
            raise VaultError("io_error") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def load(self, profile, binding: dict) -> str:
        profile = _profile(profile)
        binding = _binding(binding)
        try:
            ciphertext = self._read(profile)
            try:
                plaintext = self._protector.unprotect(ciphertext)
            except Exception:
                raise VaultError("protection_failed") from None
            if len(plaintext) > MAX_FILE_BYTES:
                raise VaultError("invalid_data")
            value = _json(plaintext)
            if (not isinstance(value, dict) or set(value) != {"version", "profile", "binding", "token"}
                    or type(value["version"]) is not int or value["version"] != 1 or value["profile"] != profile):
                raise VaultError("invalid_data")
            if _binding(value["binding"]) != binding:
                raise VaultError("binding_mismatch")
            token = value["token"]
            if not isinstance(token, str) or not token or len(token) > 16384 or any(ord(c) < 33 or ord(c) == 127 for c in token):
                raise VaultError("invalid_data")
            return token
        except FileNotFoundError:
            raise VaultError("not_found") from None
        except VaultError:
            raise
        except Exception:
            raise VaultError("invalid_data") from None

    def list_profiles(self) -> list[dict]:
        try:
            self._check_root()
            result = []
            for path in self.root.iterdir():
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise VaultError("unsafe_path")
                if path.suffix == ".json":
                    profile = _profile(path.stem)
                    self._read(profile)  # validate bounded envelope, never decrypt
                    result.append({"profile": profile, "version": 1})
            return sorted(result, key=lambda entry: entry["profile"])
        except VaultError:
            raise
        except Exception:
            raise VaultError("invalid_data") from None

    def delete(self, profile):
        profile = _profile(profile)
        try:
            self._check_root()
            path = self.root / (profile + ".json")
            _safe_stat(path, private=True)
            path.unlink()
        except FileNotFoundError:
            raise VaultError("not_found") from None
        except VaultError:
            raise
        except Exception:
            raise VaultError("io_error") from None
