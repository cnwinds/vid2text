"""设置 KV 存储。"""

from __future__ import annotations

from web.db_common import _utc_now
from web.db_connection import get_conn


def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    now = _utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
        conn.commit()


def get_settings_map(keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            keys,
        )
        return {str(r["key"]): str(r["value"]) for r in cur.fetchall()}


def set_settings(pairs: dict[str, str]) -> None:
    if not pairs:
        return
    now = _utc_now()
    with get_conn() as conn:
        for key, value in pairs.items():
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
        conn.commit()


