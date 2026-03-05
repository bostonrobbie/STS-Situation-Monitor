from pathlib import Path

import pytest

from sts_monitor.backup import backup_sqlite_database, restore_sqlite_database

pytestmark = pytest.mark.unit


def test_backup_and_restore_sqlite_database(tmp_path: Path) -> None:
    db_file = tmp_path / "sts_monitor.db"
    db_file.write_text("seed-data")

    backup_dir = tmp_path / "backups"
    backup_file = backup_sqlite_database(db_path=db_file, backup_dir=backup_dir)
    assert backup_file.exists()

    db_file.write_text("changed-data")
    restore_sqlite_database(backup_file=backup_file, db_path=db_file)
    assert db_file.read_text() == "seed-data"


def test_backup_raises_when_missing_db(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        backup_sqlite_database(db_path=tmp_path / "missing.db", backup_dir=tmp_path / "backups")
