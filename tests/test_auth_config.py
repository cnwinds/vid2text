"""ADMIN 配置启动校验。"""

from __future__ import annotations

import os
import unittest


class AdminConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("VID2TEXT_SKIP_ADMIN_CHECK", None)
        for key in ("ADMIN_PASSWORD", "ADMIN_API_TOKEN"):
            os.environ.pop(key, None)

    def test_require_admin_config_fails_when_unset(self) -> None:
        from web.auth import require_admin_config

        with self.assertRaises(RuntimeError):
            require_admin_config()

    def test_skip_flag_allows_startup(self) -> None:
        from web.auth import require_admin_config

        os.environ["VID2TEXT_SKIP_ADMIN_CHECK"] = "1"
        require_admin_config()

    def test_admin_password_allows_startup(self) -> None:
        from web.auth import require_admin_config

        os.environ["ADMIN_PASSWORD"] = "secret"
        require_admin_config()


if __name__ == "__main__":
    unittest.main()
