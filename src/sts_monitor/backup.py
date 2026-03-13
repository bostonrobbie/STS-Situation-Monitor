from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import shutil


def backup_sqlite_database(db_path: Path, backup_dir: Path) -> Path:
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"{db_path.stem}-{stamp}{db_path.suffix}"
    shutil.copy2(db_path, destination)
    return destination


def restore_sqlite_database(backup_file: Path, db_path: Path) -> None:
    if not backup_file.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_file}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_file, db_path)
