"""Резервное копирование SQLite-базы.

Запуск:
    python scripts/backup_db.py [--keep 14]

Создаёт согласованную копию через sqlite3.Connection.backup() в каталоге
``backups/`` и удаляет старые копии, оставляя ``--keep`` последних.
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE_DIR / "instance" / "utility_billing.db"
DEFAULT_BACKUPS = BASE_DIR / "backups"


def backup_database(db_path: Path, backups_dir: Path, keep: int) -> Path:
    if not db_path.exists():
        raise SystemExit(f"База данных не найдена: {db_path}")
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backups_dir / f"utility_billing-{stamp}.db"

    source = sqlite3.connect(db_path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    backups = sorted(backups_dir.glob("utility_billing-*.db"))
    for old in backups[:-keep] if keep > 0 else []:
        old.unlink()
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Резервная копия базы ЖКУ-Учёт")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--backups", type=Path, default=DEFAULT_BACKUPS)
    parser.add_argument("--keep", type=int, default=14)
    args = parser.parse_args()
    target = backup_database(args.db, args.backups, args.keep)
    print(f"Резервная копия создана: {target}")


if __name__ == "__main__":
    main()
