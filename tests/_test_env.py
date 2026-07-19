"""测试环境：临时 DB + 跳过 ADMIN 启动校验。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def use_temp_db() -> Path:
    os.environ["VID2TEXT_SKIP_ADMIN_CHECK"] = "1"
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)

    import web.db as db_mod
    import web.db_connection as conn_mod

    db_mod.DB_PATH = path
    conn_mod.DB_PATH = path
    db_mod.init_db()
    return path


def restore_db(path: Path, orig_db: Path, orig_conn: Path) -> None:
    import web.db as db_mod
    import web.db_connection as conn_mod

    db_mod.DB_PATH = orig_db
    conn_mod.DB_PATH = orig_conn
    if path.exists():
        os.unlink(path)
