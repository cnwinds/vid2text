"""url_safety SSRF 防护单元测试（无真实外网解析）。"""

from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from douyin_to_text.url_safety import UnsafeUrlError, assert_safe_http_url


def _fake_public_addrinfo(host: str, *args, **kwargs):
    if host in ("www.youtube.com", "youtube.com"):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.80.46", 0))]
    raise socket.gaierror(f"unknown host {host}")


class UrlSafetyTests(unittest.TestCase):
    def test_rejects_localhost_hostname(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            assert_safe_http_url("http://localhost/video")

    def test_rejects_loopback_literal_ip(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            assert_safe_http_url("http://127.0.0.1/secret")

    def test_rejects_private_10_network(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            assert_safe_http_url("http://10.0.0.5/api")

    def test_rejects_private_172_network(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            assert_safe_http_url("http://172.16.0.1/metadata")

    def test_rejects_link_local_metadata_ip(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            assert_safe_http_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_non_http_scheme(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            assert_safe_http_url("file:///etc/passwd")

    @patch("douyin_to_text.url_safety.socket.getaddrinfo", side_effect=_fake_public_addrinfo)
    def test_allows_public_youtube_url(self, _mock_getaddrinfo) -> None:
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(assert_safe_http_url(url), url)

    def test_strips_whitespace(self) -> None:
        with patch(
            "douyin_to_text.url_safety.socket.getaddrinfo",
            side_effect=_fake_public_addrinfo,
        ):
            url = "  https://www.youtube.com/watch?v=abc  "
            self.assertEqual(
                assert_safe_http_url(url),
                "https://www.youtube.com/watch?v=abc",
            )


if __name__ == "__main__":
    unittest.main()
