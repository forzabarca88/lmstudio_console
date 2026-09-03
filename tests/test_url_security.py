"""Unit tests for backend.url_security (SSRF-safe URL validation).

Pure unit tests: IP literals need no network; hostname resolution is
mocked via unittest.mock.patch on socket.getaddrinfo.
"""

import os
import socket
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.url_security import (
    URLSecurityError,
    validate_proxy_url,
    validate_public_url,
)


def _gai(*ips):
    """Build a socket.getaddrinfo-style result list for the given IPs."""
    results = []
    for ip in ips:
        if ":" in ip:
            results.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0)))
        else:
            results.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)))
    return results


class TestValidateProxyUrl(unittest.TestCase):
    """validate_proxy_url: http(s) only; global or RFC1918-LAN IPs allowed."""

    def test_accepts_rfc1918_lan_ipv4(self):
        """ARRANGE: LAN IP literal (the live test endpoint)
        ACT: validate http://192.168.0.5:1234
        ASSERT: no exception raised"""
        validate_proxy_url("http://192.168.0.5:1234")

    def test_accepts_10_net_ipv4(self):
        """ARRANGE: 10/8 LAN IP literal
        ACT: validate https://10.0.0.1
        ASSERT: no exception raised"""
        validate_proxy_url("https://10.0.0.1")

    def test_accepts_global_ipv4(self):
        """ARRANGE: public IPv4 literal
        ACT: validate http://8.8.8.8
        ASSERT: no exception raised"""
        validate_proxy_url("http://8.8.8.8")

    @patch("backend.url_security.socket.getaddrinfo")
    def test_accepts_hostname_resolving_to_lan_ip(self, mock_gai):
        """ARRANGE: hostname that resolves to a 192.168/16 address
        ACT: validate the hostname URL
        ASSERT: no exception raised"""
        mock_gai.return_value = _gai("192.168.1.10")
        validate_proxy_url("http://lmstudio.lan:1234")

    def test_rejects_loopback_literal(self):
        """ARRANGE: loopback IP literal
        ACT: validate http://127.0.0.1:1234
        ASSERT: URLSecurityError raised"""
        with self.assertRaises(URLSecurityError):
            validate_proxy_url("http://127.0.0.1:1234")

    @patch("backend.url_security.socket.getaddrinfo")
    def test_rejects_hostname_resolving_to_loopback(self, mock_gai):
        """ARRANGE: hostname that resolves to 127.0.0.1
        ACT: validate http://localhost:1234
        ASSERT: URLSecurityError raised"""
        mock_gai.return_value = _gai("127.0.0.1")
        with self.assertRaises(URLSecurityError):
            validate_proxy_url("http://localhost:1234")

    def test_rejects_ipv6_loopback(self):
        """ARRANGE: IPv6 loopback literal
        ACT: validate http://[::1]:1234
        ASSERT: URLSecurityError raised"""
        with self.assertRaises(URLSecurityError):
            validate_proxy_url("http://[::1]:1234")

    def test_rejects_link_local_metadata_ip(self):
        """ARRANGE: cloud metadata link-local address
        ACT: validate http://169.254.169.254/latest/meta-data/
        ASSERT: URLSecurityError raised"""
        with self.assertRaises(URLSecurityError):
            validate_proxy_url("http://169.254.169.254/latest/meta-data/")

    @patch("backend.url_security.socket.getaddrinfo")
    def test_rejects_metadata_hostname_before_dns(self, mock_gai):
        """ARRANGE: bare metadata hostname
        ACT: validate http://metadata/latest/meta-data/
        ASSERT: URLSecurityError raised without any DNS lookup"""
        with self.assertRaises(URLSecurityError):
            validate_proxy_url("http://metadata/latest/meta-data/")
        mock_gai.assert_not_called()

    def test_rejects_non_http_scheme(self):
        """ARRANGE: non-http(s) scheme
        ACT: validate ftp://example.com
        ASSERT: URLSecurityError raised"""
        with self.assertRaises(URLSecurityError):
            validate_proxy_url("ftp://example.com")

    @patch("backend.url_security.socket.getaddrinfo",
           side_effect=socket.gaierror("Name or service not known"))
    def test_rejects_unresolvable_host(self, mock_gai):
        """ARRANGE: hostname that fails to resolve (gaierror)
        ACT: validate the hostname URL
        ASSERT: URLSecurityError raised"""
        with self.assertRaises(URLSecurityError):
            validate_proxy_url("http://no-such-host.invalid")

    def test_rejects_other_non_global_special_ranges(self):
        """ARRANGE: unspecified, reserved, multicast, CGNAT and other
                   private-range literals
        ACT: validate each URL
        ASSERT: URLSecurityError raised for every one"""
        for bad in (
            "http://0.0.0.0:1234/",     # unspecified
            "http://240.0.0.1:1234/",    # reserved (240.0.0.0/4)
            "http://224.0.0.1:1234/",    # multicast
            "http://100.64.0.1:1234/",   # CGNAT shared address space
            "http://198.19.1.1:1234/",   # benchmarking (private)
        ):
            with self.subTest(url=bad):
                with self.assertRaises(URLSecurityError):
                    validate_proxy_url(bad)


class TestValidatePublicUrl(unittest.TestCase):
    """validate_public_url: strict — only globally routable IPs."""

    def test_accepts_global_ipv4(self):
        """ARRANGE: public IPv4 literal
        ACT: validate http://8.8.8.8
        ASSERT: no exception raised"""
        validate_public_url("http://8.8.8.8")

    @patch("backend.url_security.socket.getaddrinfo")
    def test_accepts_hostname_resolving_to_global_ip(self, mock_gai):
        """ARRANGE: hostname that resolves to a public address
        ACT: validate the hostname URL
        ASSERT: no exception raised"""
        mock_gai.return_value = _gai("93.184.216.34")
        validate_public_url("https://example.com")

    def test_rejects_lan_ipv4(self):
        """ARRANGE: LAN IP literal
        ACT: validate http://192.168.0.5
        ASSERT: URLSecurityError raised (no LAN carve-out)"""
        with self.assertRaises(URLSecurityError):
            validate_public_url("http://192.168.0.5")

    def test_rejects_loopback(self):
        """ARRANGE: loopback IP literal
        ACT: validate http://127.0.0.1
        ASSERT: URLSecurityError raised"""
        with self.assertRaises(URLSecurityError):
            validate_public_url("http://127.0.0.1")


if __name__ == "__main__":
    unittest.main()
