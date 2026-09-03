"""SSRF-safe URL validation for client-supplied proxy targets.

The console is a trusted-LAN, unauthenticated dashboard, but the
``X-LM-Studio-URL`` request header lets any client choose the proxy
target. These validators keep the SPEC's "any OpenAI-compatible endpoint
on the LAN" capability while blocking SSRF against cloud metadata,
loopback, link-local, reserved and other non-routable addresses:

- ``validate_proxy_url`` — for the ``X-LM-Studio-URL`` header: http/https
  only, no metadata hostnames, and every resolved IP must be globally
  routable or inside an RFC1918 LAN range (10/8, 172.16/12, 192.168/16)
  so LAN endpoints such as ``http://192.168.0.5:1234`` keep working.
- ``validate_public_url`` — strict variant for client-facing fetches
  (e.g. the ``open_web_page`` tool): only globally routable IPs.

IPv6 note: IPv6 targets are allowed only if globally routable. Unlike
IPv4, there is no LAN carve-out for IPv6 — link-local (fe80::/10) and ULA
(fc00::/7) addresses are rejected even though IPv4 RFC1918 LAN ranges are
allowed (stricter is safer). An operator on an IPv6-only LAN should use
the env-configured ``LM_STUDIO_URL``, which is operator-trusted and
intentionally not validated.

Documented residual limitation: validation resolves the hostname once and
checks the resolved IPs. A full transport would pin (bind) the outgoing
connection to one of the validated IPs, so a TOCTOU DNS re-resolution
between validation and connect cannot redirect the request to a
different address.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Cloud metadata hostnames that must never be reachable via the proxy.
# Checked before any DNS lookup: on a cloud VM these resolve to
# link-local metadata services, so even resolving them is the hazard.
_METADATA_HOSTNAMES = frozenset({"metadata", "metadata.google.internal"})

# RFC1918 private LAN ranges the proxy may reach. The console targets LAN
# endpoints (per SPEC), so these are allowed even though they are not
# globally routable. All other private/special ranges (loopback,
# link-local, CGNAT, benchmarking, ...) remain blocked.
_LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


class URLSecurityError(ValueError):
    """Raised when a URL fails security validation."""


def _parse(url: str) -> str:
    """Parse *url* and enforce the scheme/hostname/metadata rules.

    Returns the lowercased hostname. Raises :class:`URLSecurityError` for
    anything that is not a usable http(s) URL (bad scheme, missing
    hostname, metadata hostname).
    """
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise URLSecurityError(f"Invalid URL: {e}") from e

    if parsed.scheme not in ("http", "https"):
        raise URLSecurityError(f"Unsupported URL scheme: {parsed.scheme!r}")

    hostname = parsed.hostname
    if not hostname:
        raise URLSecurityError("URL has no hostname")

    # parsed.hostname is already lowercased and stripped of IPv6 brackets.
    host = hostname.rstrip(".")
    if host in _METADATA_HOSTNAMES:
        raise URLSecurityError(f"Blocked metadata hostname: {host}")

    return host


def _resolve_ips(host: str) -> list[ipaddress._BaseAddress]:
    """Resolve *host* to a list of IP addresses.

    IP literals pass through unchanged (bracketed IPv6 literals have their
    brackets stripped). Hostnames are resolved with ``socket.getaddrinfo``;
    an unresolvable host raises :class:`URLSecurityError` so the caller can
    fall back to a configured target instead of guessing.
    """
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        literal = ipaddress.ip_address(candidate)
    except ValueError:
        literal = None
    if literal is not None:
        return [literal]

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise URLSecurityError(f"Could not resolve host {host!r}: {e}") from e

    ips: list[ipaddress._BaseAddress] = []
    for _family, _type, _proto, _canonname, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        # Collapse IPv4-mapped IPv6 addresses (e.g. ::ffff:192.168.0.5)
        # so they are judged by the IPv4 rules.
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        ips.append(ip)

    if not ips:
        raise URLSecurityError(f"Host {host!r} resolved to no usable addresses")
    return ips


def _ip_allowed(ip: ipaddress._BaseAddress, allow_lan: bool) -> bool:
    """Return True if *ip* may be used as a connection target."""
    # Explicit multicast rejection: CPython 3.12's IPv4 is_global does not
    # flag multicast (224.0.0.0/4) as non-global, so do not rely on it.
    if ip.is_multicast:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        if allow_lan and any(ip in net for net in _LAN_NETWORKS):
            return True
        return ip.is_global
    # IPv6: global only (no LAN carve-out).
    return ip.is_global


def _validate(url: str, allow_lan: bool) -> None:
    """Raise :class:`URLSecurityError` unless every resolved IP is allowed."""
    host = _parse(url)
    for ip in _resolve_ips(host):
        if not _ip_allowed(ip, allow_lan):
            raise URLSecurityError(
                f"Blocked target address for host {host!r}: {ip}"
            )


def validate_proxy_url(url: str) -> None:
    """Validate *url* as a proxy target (``X-LM-Studio-URL`` header).

    Raises :class:`URLSecurityError` unless the URL is http/https, is not a
    metadata hostname, and every resolved IP is globally routable or inside
    an RFC1918 LAN range (10/8, 172.16/12, 192.168/16).
    """
    _validate(url, allow_lan=True)


def validate_public_url(url: str) -> None:
    """Strictly validate *url* for client-facing fetches.

    Raises :class:`URLSecurityError` unless the URL is http/https, is not a
    metadata hostname, and every resolved IP is globally routable. No LAN
    carve-out.
    """
    _validate(url, allow_lan=False)
