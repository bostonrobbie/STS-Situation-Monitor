#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from sts_monitor.backup import restore_sqlite_database
from sts_monitor.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore sqlite database from a backup file")
    parser.add_argument("backup_file", help="Path to backup sqlite file")
    args = parser.parse_args()

    if not settings.database_url.startswith("sqlite:///"):
        raise SystemExit("db_restore.py currently supports sqlite URLs only")

    db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    restore_sqlite_database(backup_file=Path(args.backup_file), db_path=db_path)
    print(db_path)


if __name__ == "__main__":
    main()
