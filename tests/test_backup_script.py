"""Offline integration tests for the POSIX backup script using a fake podman."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.skipif(os.name != "posix", reason="backup.sh requires POSIX tools and flock")
SCRIPT = Path(__file__).resolve().parents[1] / "deploy/podman/backup.sh"
STAMP = "20240102T030405Z"
DUMP = b"synthetic archive data\n" * 128


@pytest.fixture
def backup_env(tmp_path):
    if os.name == "posix":
        missing = [name for name in ("sh", "flock", "mktemp", "sha256sum") if not shutil.which(name)]
        if missing:
            pytest.skip("missing POSIX backup tools: " + ", ".join(missing))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    podman = bin_dir / "podman"
    podman.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_LOG\"\n"
        "case \"$*\" in\n"
        "  *pg_dump*)\n"
        "    i=0; while [ \"$i\" -lt 128 ]; do printf 'synthetic archive data\\n'; i=$((i + 1)); done\n"
        "    [ \"${FAKE_FAILURE:-}\" != dump ] ;;\n"
        "  *pg_restore*)\n"
        "    cat > \"$FAKE_RESTORE_INPUT\"\n"
        "    [ \"${FAKE_FAILURE:-}\" != restore ] || exit 1\n"
        "    printf 'synthetic table of contents\\n' ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    podman.chmod(0o755)
    date = bin_dir / "date"
    date.write_text(f"#!/bin/sh\nprintf '%s\\n' '{STAMP}'\n", encoding="utf-8")
    date.chmod(0o755)
    backup = tmp_path / "backups"
    backup.mkdir()
    log = tmp_path / "podman.log"
    restore_input = tmp_path / "restore-input"
    env = os.environ.copy()
    env.update({
        "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
        "TALKABOUT_BACKUP_DIR": str(backup),
        "TALKABOUT_BACKUP_KEEP": "1",
        "TALKABOUT_DB_CONTAINER": "fixture-container",
        "TALKABOUT_DB_USER": "fixture-user",
        "TALKABOUT_DB_NAME": "fixture-db",
        "FAKE_LOG": str(log),
        "FAKE_RESTORE_INPUT": str(restore_input),
    })
    env.pop("FAKE_FAILURE", None)
    return backup, log, restore_input, env


def run_backup(env):
    return subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)


def assert_no_workdirs(backup):
    assert not list(backup.glob(".talkabout-backup.*????????"))


def seed_backup(backup):
    old = backup / "talkabout-20000101T000000Z.dump"
    old.write_bytes(b"existing backup")
    os.utime(old, (946684800, 946684800))
    checksum = old.with_name(old.name + ".sha256")
    checksum.write_bytes(b"existing checksum\n")
    return old, checksum


def test_success_publishes_verified_backup_and_applies_retention(backup_env):
    backup, log, restore_input, env = backup_env
    old, old_checksum = seed_backup(backup)
    result = run_backup(env)
    assert result.returncode == 0, result.stderr
    target = backup / f"talkabout-{STAMP}.dump"
    assert target.read_bytes() == DUMP
    assert target.with_name(target.name + ".sha256").read_text() == f"{hashlib.sha256(DUMP).hexdigest()}  {target.name}\n"
    assert restore_input.read_bytes() == DUMP
    assert log.read_text().splitlines() == [
        "exec fixture-container pg_dump -U fixture-user -d fixture-db -Fc",
        "exec -i fixture-container pg_restore -l",
    ]
    assert "backup ok:" in result.stdout
    assert not old.exists() and not old_checksum.exists()
    assert_no_workdirs(backup)


@pytest.mark.parametrize("failure, message, expected_calls", [
    ("dump", "pg_dump failed", 1),
    ("restore", "archive is unreadable", 2),
])
def test_failures_remove_partial_output_and_preserve_existing_backup(backup_env, failure, message, expected_calls):
    backup, log, restore_input, env = backup_env
    old, old_checksum = seed_backup(backup)
    env["FAKE_FAILURE"] = failure
    result = run_backup(env)
    assert result.returncode != 0
    assert message in result.stderr
    assert old.read_bytes() == b"existing backup"
    assert old_checksum.read_bytes() == b"existing checksum\n"
    assert sorted(p.name for p in backup.glob("*.dump*")) == sorted([old.name, old_checksum.name])
    assert len(log.read_text().splitlines()) == expected_calls
    if failure == "restore":
        assert restore_input.read_bytes() == DUMP
    assert_no_workdirs(backup)


@pytest.mark.parametrize("existing_suffix", [".dump", ".dump.sha256"])
def test_same_timestamp_collision_never_clobbers(backup_env, existing_suffix):
    backup, log, _, env = backup_env
    old, old_checksum = seed_backup(backup)
    collision = backup / f"talkabout-{STAMP}{existing_suffix}"
    collision.write_bytes(b"do not overwrite")
    result = run_backup(env)
    assert result.returncode != 0
    assert "target already exists" in result.stderr
    assert collision.read_bytes() == b"do not overwrite"
    assert old.read_bytes() == b"existing backup"
    assert old_checksum.read_bytes() == b"existing checksum\n"
    assert not log.exists()
    assert len(list(backup.glob("*.dump*"))) == 3
    assert_no_workdirs(backup)


@pytest.mark.parametrize("keep", ["0", "00", "08", "09", "-1", "10001"])
def test_invalid_retention_count_never_touches_existing_backup(backup_env, keep):
    backup, log, _, env = backup_env
    old, old_checksum = seed_backup(backup)
    env["TALKABOUT_BACKUP_KEEP"] = keep
    result = run_backup(env)
    assert result.returncode != 0
    assert "invalid retention count" in result.stderr
    assert old.read_bytes() == b"existing backup"
    assert old_checksum.read_bytes() == b"existing checksum\n"
    assert not log.exists()


def test_retention_never_deletes_new_backup_if_old_mtime_is_future(backup_env):
    backup, _, _, env = backup_env
    old, old_checksum = seed_backup(backup)
    os.utime(old, (4102444800, 4102444800))
    result = run_backup(env)
    assert result.returncode == 0, result.stderr
    assert (backup / f"talkabout-{STAMP}.dump").read_bytes() == DUMP
    assert not old.exists() and not old_checksum.exists()


def test_lock_contention_preserves_existing_backup(backup_env):
    import fcntl

    backup, log, _, env = backup_env
    old, old_checksum = seed_backup(backup)
    with (backup / ".talkabout-backup.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_backup(env)
    assert result.returncode != 0
    assert "another backup is running" in result.stderr
    assert old.read_bytes() == b"existing backup"
    assert old_checksum.read_bytes() == b"existing checksum\n"
    assert not log.exists()
    assert sorted(p.name for p in backup.glob("*.dump*")) == sorted([old.name, old_checksum.name])
    assert_no_workdirs(backup)
