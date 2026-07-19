"""SQLite schema 版本与增量迁移。"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 3


def _current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        )
        """
    )
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def run_migrations(conn: sqlite3.Connection) -> None:
    """在基础表创建后执行增量迁移。"""
    version = _current_version(conn)

    if version < 2:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "client_scope" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN client_scope TEXT NOT NULL DEFAULT ''"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_client_scope "
                "ON tasks(client_scope, id DESC)"
            )

    if version < SCHEMA_VERSION:
        _set_version(conn, SCHEMA_VERSION)
