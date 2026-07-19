"""E2E：Playwright 打开首页与 API 文档。"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests._test_env import restore_db, use_temp_db


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class E2EUiSmokeTests(unittest.TestCase):
    _server_thread: threading.Thread | None = None
    _port = 0
    _db_path: Path | None = None
    _orig_db: Path | None = None
    _orig_conn: Path | None = None

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright 未安装")

        import web.db as db_mod
        import web.db_connection as conn_mod

        cls._orig_db = db_mod.DB_PATH
        cls._orig_conn = conn_mod.DB_PATH
        cls._db_path = use_temp_db()
        cls._port = _free_port()

        def run() -> None:
            import uvicorn

            uvicorn.run(
                "web.app:app",
                host="127.0.0.1",
                port=cls._port,
                log_level="error",
            )

        cls._server_thread = threading.Thread(target=run, daemon=True)
        cls._server_thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", cls._port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("E2E 测试服务器启动超时")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._db_path and cls._orig_db and cls._orig_conn:
            restore_db(cls._db_path, cls._orig_db, cls._orig_conn)

    def _with_page(self):
        from playwright.sync_api import sync_playwright

        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:
            raise unittest.SkipTest(f"Chromium 不可用: {exc}") from exc
        page = browser.new_page()
        return pw, browser, page

    def test_home_page_renders(self) -> None:
        base = f"http://127.0.0.1:{self._port}"
        pw, browser, page = self._with_page()
        try:
            page.goto(base, wait_until="domcontentloaded")
            self.assertIn("vid2text", page.locator("h1").inner_text())
            self.assertTrue(page.locator("#url-input").is_visible())
        finally:
            browser.close()
            pw.stop()

    def test_api_docs_page_renders(self) -> None:
        base = f"http://127.0.0.1:{self._port}"
        pw, browser, page = self._with_page()
        try:
            page.goto(f"{base}/api-docs", wait_until="domcontentloaded")
            self.assertIn("API", page.title())
        finally:
            browser.close()
            pw.stop()


if __name__ == "__main__":
    unittest.main()
