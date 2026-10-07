"""Polite, SSRF-safe page fetcher: validated DNS, pinned connection, robots.txt, rate limit, size and time caps."""
import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from product_extractor.config import Settings
from product_extractor.errors import (
    BlockedByRobots, ExtractError, FetchFailed, FetchTimeout, NotHtml, PageTooLarge,
)
from product_extractor.ratelimit import DomainRateLimiter
from product_extractor.robots import RobotsChecker
from product_extractor.security import Resolver, Target, validate_url

_HTML_TYPES = ("text/html", "application/xhtml+xml")
_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I)
ROBOTS_MAX_BYTES = 500_000


@dataclass
class FetchResult:
    url: str            # final URL after redirects
    status: int
    html: str
    content_type: str


def decode_body(raw: bytes, content_type: str) -> str:
    """Decode using the HTTP charset, else a <meta charset>, else UTF-8. Never raises."""
    m = re.search(r"charset\s*=\s*([\w\-]+)", content_type or "", re.I)
    charset = m.group(1) if m else None
    if charset is None:
        sniff = _CHARSET.search(raw[:4096])
        charset = sniff.group(1).decode("ascii", "ignore") if sniff else "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None,
                 resolver: Resolver | None = None, limiter: DomainRateLimiter | None = None):
        self.settings = settings
        self._resolver = resolver
        self._limiter = limiter or DomainRateLimiter(settings.per_domain_min_interval_seconds)
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "en;q=0.9,*;q=0.5",
            },
            follow_redirects=False,
            trust_env=False,  # a proxy from the environment would bypass the pinned, validated address
        )
        self._robots = RobotsChecker(self._fetch_robots)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, url: str) -> FetchResult:
        """Fetch one page, following redirects manually so that every hop is validated again."""
        current = url
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds * 2):
                for _ in range(self.settings.max_redirects + 1):
                    target = await self._validate(current)
                    if self.settings.respect_robots_txt:
                        parts = urlsplit(target.url)
                        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
                        ok, why = await self._robots.allowed(target.scheme, target.host_header, path)
                        if not ok:
                            raise BlockedByRobots(why or "Blocked by robots.txt.")
                    status, headers, body = await self._get(target, self.settings.max_page_bytes)
                    if status in (301, 302, 303, 307, 308) and headers.get("location"):
                        current = urljoin(target.url, headers["location"])
                        continue
                    if status >= 400:
                        raise FetchFailed(f"The site answered HTTP {status}.")
                    ctype = headers.get("content-type", "")
                    if ctype and not any(t in ctype.lower() for t in _HTML_TYPES):
                        raise NotHtml(f"Expected an HTML page but got {ctype.split(';')[0]!r}.")
                    return FetchResult(url=target.url, status=status, html=decode_body(body, ctype), content_type=ctype)
                raise FetchFailed(f"Too many redirects (more than {self.settings.max_redirects}).")
        except TimeoutError:
            raise FetchTimeout("The site took too long to respond.") from None
        except ExtractError:
            raise
        except httpx.TimeoutException:
            raise FetchTimeout("The site took too long to respond.") from None
        except httpx.HTTPError as exc:
            raise FetchFailed(f"Could not fetch the page: {type(exc).__name__}.") from None

    async def _validate(self, url: str) -> Target:
        return await validate_url(url, allow_private=self.settings.allow_private_networks, resolver=self._resolver)

    async def _get(self, target: Target, max_bytes: int) -> tuple[int, httpx.Headers, bytes]:
        """One GET to the validated IP, with the original hostname in Host and (for https) in SNI."""
        await self._limiter.wait(target.host)
        pinned = httpx.URL(target.url).copy_with(host=target.ip)
        extensions = {"sni_hostname": target.host} if target.scheme == "https" else {}
        async with self._client.stream("GET", pinned, headers={"Host": target.host_header}, extensions=extensions) as r:
            declared = r.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise PageTooLarge(f"The page is larger than the {max_bytes:,} byte limit.")
            chunks, size = [], 0
            if r.status_code in (301, 302, 303, 307, 308) or r.status_code >= 400:
                return r.status_code, r.headers, b""
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise PageTooLarge(f"The page is larger than the {max_bytes:,} byte limit.")
                chunks.append(chunk)
            return r.status_code, r.headers, b"".join(chunks)

    async def _fetch_robots(self, robots_url: str) -> tuple[int, str]:
        target = await self._validate(robots_url)
        status, headers, body = await self._get(target, ROBOTS_MAX_BYTES)
        if status in (301, 302, 303, 307, 308):  # robots.txt redirects are not followed: treat as unavailable
            return 404, ""
        return status, decode_body(body, headers.get("content-type", ""))

