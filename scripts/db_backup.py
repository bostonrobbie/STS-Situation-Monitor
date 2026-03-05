#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from sts_monitor.backup import backup_sqlite_database
from sts_monitor.config import settings


def main() -> None:
    if not settings.database_url.startswith("sqlite:///"):
        raise SystemExit("db_backup.py currently supports sqlite URLs only")

    db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    backup_dir = Path("backups")
    backup_file = backup_sqlite_database(db_path=db_path, backup_dir=backup_dir)
    print(backup_file)


if __name__ == "__main__":
    main()
