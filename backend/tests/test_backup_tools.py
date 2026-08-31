"""Backup / restore tool behaviour.

The docker-touching paths need a daemon, so the tests here pin down the pure
logic that keeps them safe: the backup directory sandbox (a caller-supplied
name must never escape it) and dry-run previews.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docker_tools import server as dt
from app.tools.classification import Tier, classify


def test_backup_tools_are_classified():
    assert classify("docker", "list_backups") is Tier.READ
    assert classify("docker", "backup_volume") is Tier.MUTATING
    assert classify("docker", "pg_dump_database") is Tier.MUTATING
    # Restores overwrite live data — destructive, always typed-confirm.
    assert classify("docker", "restore_volume") is Tier.DESTRUCTIVE
    assert classify("docker", "pg_restore_database") is Tier.DESTRUCTIVE


# --- the sandbox -------------------------------------------------------------

@pytest.mark.parametrize(
    "evil",
    ["../escape.tar.gz", "../../etc/passwd", "sub/../../out.tar.gz", "/abs/path.tar.gz"],
)
def test_resolve_backup_rejects_escapes(evil, tmp_path, monkeypatch):
    monkeypatch.setattr(dt, "_backup_dir", lambda: tmp_path)
    with pytest.raises(ValueError):
        dt._resolve_backup(evil)


def test_resolve_backup_accepts_plain_name(tmp_path, monkeypatch):
    monkeypatch.setattr(dt, "_backup_dir", lambda: tmp_path)
    got = dt._resolve_backup("pgdata-20260831.tar.gz")
    assert got == tmp_path / "pgdata-20260831.tar.gz"
    assert got.is_relative_to(tmp_path)


def test_backup_name_is_timestamped_and_safe():
    name = dt._backup_name("my-vol")
    assert name.startswith("my-vol-")
    assert name.endswith(".tar.gz")
    assert "/" not in name


def test_backup_name_sanitises_slashes():
    assert "/" not in dt._backup_name("a/b/../c")


# --- dry runs never touch docker --------------------------------------------

def test_backup_volume_dry_run(monkeypatch):
    monkeypatch.setattr(dt, "_docker", lambda: pytest.fail("docker must not be touched on dry_run"))
    res = dt.backup_volume("pgdata", dry_run=True)
    assert res["dry_run"] is True
    assert res["volume"] == "pgdata"


def test_restore_volume_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(dt, "_backup_dir", lambda: tmp_path)
    (tmp_path / "pgdata-1.tar.gz").write_bytes(b"x")
    monkeypatch.setattr(dt, "_docker", lambda: pytest.fail("docker must not be touched on dry_run"))
    res = dt.restore_volume("pgdata", "pgdata-1.tar.gz", dry_run=True)
    assert res["dry_run"] is True


def test_restore_volume_missing_archive_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(dt, "_backup_dir", lambda: tmp_path)
    res = dt.restore_volume("pgdata", "nope.tar.gz", dry_run=True)
    assert res["error"] is True
    assert res["type"] == "BackupNotFound"


def test_restore_volume_rejects_path_escape(monkeypatch, tmp_path):
    monkeypatch.setattr(dt, "_backup_dir", lambda: tmp_path)
    res = dt.restore_volume("pgdata", "../../etc/passwd", dry_run=True)
    assert res["error"] is True
    assert res["type"] == "UnsafePath"


def test_pg_dump_dry_run(monkeypatch):
    monkeypatch.setattr(dt, "_docker", lambda: pytest.fail("docker must not be touched on dry_run"))
    res = dt.pg_dump_database("pg", "app", "app", dry_run=True)
    assert res["dry_run"] is True


def test_list_backups_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(dt, "_backup_dir", lambda: tmp_path)
    assert dt.list_backups()["backups"] == []


def test_list_backups_reports_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(dt, "_backup_dir", lambda: tmp_path)
    (tmp_path / "a.tar.gz").write_bytes(b"hello")
    out = dt.list_backups()["backups"]
    assert len(out) == 1
    assert out[0]["name"] == "a.tar.gz"
    assert out[0]["size_bytes"] == 5


def test_pg_dump_password_is_not_echoed(monkeypatch, tmp_path):
    """A dry-run preview must not contain the password the caller passed."""
    res = dt.pg_dump_database("pg", "app", "app", password="hunter2", dry_run=True)
    assert "hunter2" not in repr(res)
