"""url_safety 单元测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from douyin_to_text.url_safety import UnsafeUrlError, assert_safe_http_url


class UrlSafetyTests(unittest.TestCase):
    def test_rejects_localhost(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            assert_safe_http_url("http://127.0.0.1/video")

    def test_rejects_private_ip_literal(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            assert_safe_http_url("http://10.0.0.1/internal")

    def test_rejects_metadata_ip(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            assert_safe_http_url("http://169.254.169.254/latest/meta-data")

    def test_rejects_non_http_scheme(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            assert_safe_http_url("file:///etc/passwd")

    @patch(
        "douyin_to_text.url_safety.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("142.250.185.78", 0))],
    )
    def test_allows_public_youtube_url(self, _mock) -> None:
        url = assert_safe_http_url("https://www.youtube.com/watch?v=abc")
        self.assertIn("youtube.com", url)


if __name__ == "__main__":
    unittest.main()
