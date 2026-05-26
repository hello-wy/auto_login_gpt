import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS cdks (
    cdk TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    valid_days INTEGER NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cdks_expires_at ON cdks (expires_at);
"""


@dataclass(frozen=True)
class CdkCreateRecord:
    cdk: str
    email: str
    valid_days: int
    created_at: datetime
    expires_at: datetime | None = None


class CdkRepository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as connection:
            migrate_schema(connection)

    def create_cdk(self, record: CdkCreateRecord) -> dict:
        try:
            with self._connect() as connection:
                connection.execute(
                    (
                        "INSERT INTO cdks (cdk, email, valid_days, expires_at, created_at) "
                        "VALUES (?, ?, ?, ?, ?)"
                    ),
                    (
                        record.cdk,
                        record.email,
                        record.valid_days,
                        optional_isoformat(record.expires_at),
                        record.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(f"CDK already exists: {record.cdk}") from error
        return self.get_cdk(record.cdk)

    def get_cdk(self, cdk: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM cdks WHERE cdk = ?", (cdk,)).fetchone()
        return row_to_dict(row)

    def activate_cdk(self, cdk: str, expires_at: datetime) -> dict:
        with self._connect() as connection:
            connection.execute(
                "UPDATE cdks SET expires_at = ? WHERE cdk = ? AND expires_at IS NULL",
                (expires_at.isoformat(), cdk),
            )
        return self.get_cdk(cdk)

    def get_cdks(self, cdks: Iterable[str]) -> dict[str, dict]:
        unique_cdks = tuple(dict.fromkeys(cdks))
        if not unique_cdks:
            return {}
        placeholders = ",".join("?" for _ in unique_cdks)
        query = f"SELECT * FROM cdks WHERE cdk IN ({placeholders})"
        with self._connect() as connection:
            rows = connection.execute(query, unique_cdks).fetchall()
        return {row["cdk"]: row_to_dict(row) for row in rows}

    def switch_email_to_oldest_inactive(self, cdk: str) -> dict | None:
        with self._connect() as connection:
            replacement = connection.execute(
                (
                    "SELECT * FROM cdks WHERE expires_at IS NULL AND cdk != ? "
                    "ORDER BY created_at ASC, cdk ASC LIMIT 1"
                ),
                (cdk,),
            ).fetchone()
            if replacement is None:
                return None
            cursor = connection.execute(
                "UPDATE cdks SET email = ? WHERE cdk = ?",
                (replacement["email"], cdk),
            )
            if cursor.rowcount == 0:
                return None
            connection.execute("DELETE FROM cdks WHERE cdk = ?", (replacement["cdk"],))
            updated = connection.execute("SELECT * FROM cdks WHERE cdk = ?", (cdk,)).fetchone()
        result = row_to_dict(updated)
        result["replacement_cdk"] = replacement["cdk"]
        result["replacement_email"] = replacement["email"]
        return result

    def list_cdks(self, page: int, page_size: int) -> dict:
        offset = (page - 1) * page_size
        with self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM cdks").fetchone()[0]
            rows = connection.execute(
                "SELECT * FROM cdks ORDER BY created_at DESC, cdk DESC LIMIT ? OFFSET ?",
                (page_size, offset),
            ).fetchall()
        return {
            "items": [row_to_dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def delete_cdk(self, cdk: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM cdks WHERE cdk = ?", (cdk,))
        return cursor.rowcount > 0

    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return {
        "cdk": row["cdk"],
        "email": row["email"],
        "valid_days": row["valid_days"],
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
    }


def migrate_schema(connection) -> None:
    if not table_exists(connection):
        connection.executescript(SCHEMA)
        return
    columns = table_columns(connection)
    if "valid_days" not in columns or columns["expires_at"]["notnull"]:
        rebuild_cdks_table(connection, columns)
    connection.executescript("CREATE INDEX IF NOT EXISTS idx_cdks_expires_at ON cdks (expires_at);")


def table_exists(connection) -> bool:
    row = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'cdks'").fetchone()
    return row is not None


def table_columns(connection) -> dict:
    rows = connection.execute("PRAGMA table_info(cdks)").fetchall()
    return {row["name"]: {"notnull": bool(row["notnull"])} for row in rows}


def rebuild_cdks_table(connection, old_columns: dict) -> None:
    connection.execute("ALTER TABLE cdks RENAME TO cdks_old")
    connection.executescript(SCHEMA)
    valid_days_expr = "valid_days" if "valid_days" in old_columns else "1"
    connection.execute(
        (
            "INSERT INTO cdks (cdk, email, valid_days, expires_at, created_at) "
            f"SELECT cdk, email, {valid_days_expr}, expires_at, created_at FROM cdks_old"
        )
    )
    connection.execute("DROP TABLE cdks_old")


def optional_isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
