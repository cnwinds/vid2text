"""client_scope 单元测试。"""

from __future__ import annotations

import unittest
from unittest import mock

from web.client_scope import (
    MONITOR_SCOPE,
    compute_client_scope,
    scope_filter_enabled,
    task_visible_to_scope,
)


class ClientScopeTests(unittest.TestCase):
    def test_compute_ip_scope(self) -> None:
        self.assertEqual(compute_client_scope(client_ip="1.2.3.4"), "ip:1.2.3.4")

    def test_compute_token_scope(self) -> None:
        s = compute_client_scope(api_token="secret-token")
        self.assertTrue(s.startswith("token:"))

    def test_monitor_hidden_when_filter_on(self) -> None:
        with unittest.mock.patch("web.client_scope.public_api_token", return_value="x"):
            task = {"client_scope": MONITOR_SCOPE, "client_ip": "monitor"}
            self.assertFalse(task_visible_to_scope(task, "ip:1.2.3.4"))

    def test_legacy_ip_match(self) -> None:
        with unittest.mock.patch("web.client_scope.public_api_token", return_value="x"):
            task = {"client_scope": "", "client_ip": "9.9.9.9"}
            self.assertTrue(task_visible_to_scope(task, "ip:9.9.9.9"))
            self.assertFalse(task_visible_to_scope(task, "ip:1.1.1.1"))

    def test_filter_off_allows_all(self) -> None:
        with unittest.mock.patch("web.client_scope.public_api_token", return_value=""):
            self.assertFalse(scope_filter_enabled())
            task = {"client_scope": MONITOR_SCOPE}
            self.assertTrue(task_visible_to_scope(task, "ip:1.2.3.4"))


if __name__ == "__main__":
    unittest.main()
