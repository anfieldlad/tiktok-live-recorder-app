"""Host checks for every URL that reaches the network layer.

Two different jobs live here:

- `validate_tiktok_url` guards URLs that come from the client and end up in a
  subprocess argv or in the vendor recorder's HTTP client. Without it, any URL
  the caller invents is fetched by the server.
- `ensure_public_http_url` guards URLs that come back from a third-party API
  before we fetch them, so a compromised or hostile upstream cannot point the
  server at its own private network or a cloud metadata endpoint.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


TIKTOK_HOSTS = ("tiktok.com",)


def validate_tiktok_url(url: str, label: str = "URL") -> str:
    normalized = (url or "").strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{label} must start with http or https")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not _matches_host(hostname, TIKTOK_HOSTS):
        raise ValueError(f"{label} must be a TikTok URL")
    return normalized


def ensure_public_http_url(url: str, label: str = "URL") -> str:
    """Reject anything that resolves to an address inside our own network."""
    normalized = (url or "").strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{label} must start with http or https")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        raise ValueError(f"{label} has no host")
    for address in _resolve_addresses(hostname, label):
        if not _is_public_address(address):
            raise ValueError(f"{label} points at a non-public address")
    return normalized


def _matches_host(hostname: str, allowed: tuple[str, ...]) -> bool:
    return any(hostname == host or hostname.endswith(f".{host}") for host in allowed)


def _resolve_addresses(hostname: str, label: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(hostname)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"{label} host could not be resolved") from exc
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not addresses:
        raise ValueError(f"{label} host could not be resolved")
    return addresses


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address.is_private or address.is_loopback or address.is_link_local:
        return False
    if address.is_reserved or address.is_multicast or address.is_unspecified:
        return False
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) reports as global unless unwrapped.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        return _is_public_address(mapped)
    return True
