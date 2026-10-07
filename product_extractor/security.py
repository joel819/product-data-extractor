"""SSRF protection. A URL is only fetched if, after DNS resolution, every address it points at is public.

Checking the hostname text is not enough: 'localhost', '2130706433', '0x7f.1', '127.1' and any DNS name an
attacker controls can all resolve to internal addresses. So every host is resolved here, every resulting IP
is checked, and the fetcher then connects to the checked IP itself (so a second, different DNS answer
cannot be used between the check and the connection).
"""
import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from product_extractor.errors import BlockedAddress, InvalidUrl

MAX_URL_LENGTH = 2048
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")


async def system_resolver(host: str, port: int) -> list[str]:
    """Resolve with the operating system's resolver (the same one the connection would use)."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise InvalidUrl(f"Could not resolve host {host!r}: {exc.strerror or exc}") from None
    seen: dict[str, None] = {}
    for info in infos:
        seen[info[4][0].split("%", 1)[0]] = None
    return list(seen)


def is_public_ip(ip: IPAddress) -> bool:
    """True only for globally routable unicast addresses. Embedded IPv4 addresses are checked too."""
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if ip.teredo:
            embedded = ip.teredo[1]
        if ip in _NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if ip in _NAT64_LOCAL:
            return False
        if embedded is not None:
            return is_public_ip(embedded)
    if ip.is_multicast:
        return False
    return ip.is_global


@dataclass(frozen=True)
class Target:
    url: str          # normalised URL as requested
    scheme: str
    host: str
    port: int
    ip: str           # the validated address to connect to
    host_header: str  # what the Host header should say


def _split(url: str):
    if not isinstance(url, str) or not url.strip():
        raise InvalidUrl("URL must not be empty.")
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise InvalidUrl(f"URL is longer than {MAX_URL_LENGTH} characters.")
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise InvalidUrl("URL must not contain spaces or control characters.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise InvalidUrl("URL is malformed.") from None
    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidUrl(f"Only http and https URLs are supported (got {parts.scheme or 'none'!r}).")
    if parts.username is not None or parts.password is not None:
        raise InvalidUrl("URLs with embedded credentials are not allowed.")
    if not parts.hostname:
        raise InvalidUrl("URL has no host.")
    return url, parts, port


async def validate_url(url: str, *, allow_private: bool = False, resolver: Resolver | None = None) -> Target:
    """Raise InvalidUrl or BlockedAddress unless the URL is safe to fetch. Returns where to connect."""
    url, parts, port = _split(url)
    scheme = parts.scheme.lower()
    host = parts.hostname.rstrip(".").lower()
    port = port or (443 if scheme == "https" else 80)
    resolve = resolver or system_resolver
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        addresses = await resolve(host, port)
    if not addresses:
        raise InvalidUrl(f"Host {host!r} did not resolve to any address.")
    parsed = [ipaddress.ip_address(a) for a in addresses]
    if not allow_private:
        bad = [str(ip) for ip in parsed if not is_public_ip(ip)]
        if bad:
            raise BlockedAddress(
                f"Host {host!r} resolves to a non-public address ({bad[0]}); requests to private, loopback, "
                "link-local and reserved networks are blocked."
            )
    default_port = 443 if scheme == "https" else 80
    host_header = f"[{host}]" if ":" in host else host
    if port != default_port:
        host_header = f"{host_header}:{port}"
    return Target(url=url, scheme=scheme, host=host, port=port, ip=str(parsed[0]), host_header=host_header)
