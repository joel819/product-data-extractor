import httpx
import pytest

from product_extractor.errors import (
    BlockedAddress,
    BlockedByRobots,
    FetchFailed,
    FetchTimeout,
    InvalidUrl,
    NotHtml,
    PageTooLarge,
)
from product_extractor.fetcher import Fetcher, decode_body
from product_extractor.ratelimit import DomainRateLimiter

HTML = "<html><body><h1>Hello</h1></body></html>"


def make(settings, public_resolver, routes, **kw):
    """A fetcher whose 'network' is a dict: {(host, path): (status, headers, body)}. Records every request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        host = request.headers["host"].split(":")[0]
        route = routes.get((host, request.url.path))
        if route is None:
            return httpx.Response(404, text="not found")
        if isinstance(route, Exception):
            raise route
        status, headers, body = route
        return httpx.Response(status, headers=headers, content=body)

    fetcher = Fetcher(settings.model_copy(update=kw), transport=httpx.MockTransport(handler), resolver=public_resolver)
    return fetcher, seen


def ok(body=HTML, ctype="text/html; charset=utf-8", **extra):
    return 200, {"content-type": ctype, **extra}, body.encode() if isinstance(body, str) else body


async def test_fetch_connects_to_the_validated_ip_with_the_real_host_and_a_clear_user_agent(settings, public_resolver):
    fetcher, seen = make(settings, public_resolver, {("shop.example.test", "/robots.txt"): (404, {}, b""),
                                                      ("shop.example.test", "/p/1"): ok()})
    res = await fetcher.fetch("https://shop.example.test/p/1")
    page = seen[-1]
    assert res.html == HTML and res.status == 200
    assert page.url.host == "93.184.216.34"                         # pinned to the address that was checked
    assert page.headers["host"] == "shop.example.test"             # ...while the site still sees its own name
    assert page.extensions.get("sni_hostname") == "shop.example.test"
    assert "product-data-extractor" in page.headers["user-agent"] and "github.com" in page.headers["user-agent"]


async def test_private_targets_never_reach_the_network(settings, public_resolver):
    fetcher, seen = make(settings, public_resolver, {})
    for url in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "file:///etc/passwd"):
        with pytest.raises((BlockedAddress, InvalidUrl)):
            await fetcher.fetch(url)
    assert seen == []


async def test_a_redirect_into_private_space_is_blocked(settings, public_resolver):
    fetcher, seen = make(settings, public_resolver, {
        ("shop.example.test", "/robots.txt"): (404, {}, b""),
        ("shop.example.test", "/go"): (302, {"location": "http://169.254.169.254/latest/meta-data/"}, b""),
    })
    with pytest.raises(BlockedAddress):
        await fetcher.fetch("https://shop.example.test/go")
    assert all(r.url.host != "169.254.169.254" for r in seen)


async def test_a_redirect_to_a_host_that_resolves_privately_is_blocked(settings, public_resolver):
    public_resolver.mapping["internal.example.test"] = ["10.0.0.9"]
    fetcher, _ = make(settings, public_resolver, {
        ("shop.example.test", "/robots.txt"): (404, {}, b""),
        ("shop.example.test", "/go"): (301, {"location": "https://internal.example.test/admin"}, b""),
    })
    with pytest.raises(BlockedAddress):
        await fetcher.fetch("https://shop.example.test/go")


async def test_public_redirects_are_followed_and_relative_locations_resolved(settings, public_resolver):
    fetcher, _ = make(settings, public_resolver, {
        ("shop.example.test", "/robots.txt"): (404, {}, b""),
        ("shop.example.test", "/old"): (301, {"location": "/new/path"}, b""),
        ("shop.example.test", "/new/path"): ok("<h1>moved</h1>"),
    })
    res = await fetcher.fetch("https://shop.example.test/old")
    assert res.url == "https://shop.example.test/new/path" and "moved" in res.html


async def test_redirect_loops_stop(settings, public_resolver):
    fetcher, _ = make(settings, public_resolver, {
        ("shop.example.test", "/robots.txt"): (404, {}, b""),
        ("shop.example.test", "/a"): (302, {"location": "/a"}, b""),
    }, max_redirects=3)
    with pytest.raises(FetchFailed, match="redirects"):
        await fetcher.fetch("https://shop.example.test/a")


@pytest.mark.parametrize("robots,path,allowed", [
    ("User-agent: *\nDisallow: /private/", "/private/p/1", False),
    ("User-agent: *\nDisallow: /private/", "/p/1", True),
    ("User-agent: product-data-extractor\nDisallow: /", "/p/1", False),
    ("User-agent: somebot\nDisallow: /", "/p/1", True),
    ("User-agent: *\nDisallow: /\nAllow: /p/", "/p/1", True),
    ("", "/p/1", True),
])
async def test_robots_txt_rules_are_obeyed(settings, public_resolver, robots, path, allowed):
    fetcher, _ = make(settings, public_resolver, {("shop.example.test", "/robots.txt"): ok(robots, "text/plain"),
                                                  ("shop.example.test", path): ok()})
    if allowed:
        assert (await fetcher.fetch(f"https://shop.example.test{path}")).status == 200
    else:
        with pytest.raises(BlockedByRobots):
            await fetcher.fetch(f"https://shop.example.test{path}")


async def test_robots_unavailable_means_allowed_server_error_means_blocked_and_a_connection_failure_means_fetch_failed(settings, public_resolver):
    f404, _ = make(settings, public_resolver, {("a.example.test", "/robots.txt"): (404, {}, b""), ("a.example.test", "/p"): ok()})
    assert (await f404.fetch("https://a.example.test/p")).status == 200
    f403, _ = make(settings, public_resolver, {("a.example.test", "/robots.txt"): (403, {}, b""), ("a.example.test", "/p"): ok()})
    assert (await f403.fetch("https://a.example.test/p")).status == 200
    f500, _ = make(settings, public_resolver, {("a.example.test", "/robots.txt"): (503, {}, b""), ("a.example.test", "/p"): ok()})
    with pytest.raises(BlockedByRobots, match="server error"):
        await f500.fetch("https://a.example.test/p")
    # a connection failure is not a robots decision: it is reported as a fetch failure (and the page is not requested)
    fnet, seen = make(settings, public_resolver, {("a.example.test", "/robots.txt"): httpx.ConnectError("boom"), ("a.example.test", "/p"): ok()})
    with pytest.raises(FetchFailed, match="ConnectError"):
        await fnet.fetch("https://a.example.test/p")
    assert [r.url.path for r in seen] == ["/robots.txt"]


async def test_robots_can_be_switched_off_and_is_then_not_even_requested(settings, public_resolver):
    fetcher, seen = make(settings, public_resolver, {("a.example.test", "/p"): ok()}, respect_robots_txt=False)
    await fetcher.fetch("https://a.example.test/p")
    assert [r.url.path for r in seen] == ["/p"]


async def test_robots_is_fetched_once_per_origin(settings, public_resolver):
    fetcher, seen = make(settings, public_resolver, {("a.example.test", "/robots.txt"): ok("", "text/plain"),
                                                     ("a.example.test", "/1"): ok(), ("a.example.test", "/2"): ok()})
    await fetcher.fetch("https://a.example.test/1")
    await fetcher.fetch("https://a.example.test/2")
    assert [r.url.path for r in seen].count("/robots.txt") == 1


async def test_oversized_pages_are_rejected_while_streaming(settings, public_resolver):
    fetcher, _ = make(settings, public_resolver, {("a.example.test", "/robots.txt"): (404, {}, b""),
                                                  ("a.example.test", "/big"): ok("x" * 5000)}, max_page_bytes=1000)
    with pytest.raises(PageTooLarge):
        await fetcher.fetch("https://a.example.test/big")


async def test_declared_content_length_is_checked_before_reading(settings, public_resolver):
    fetcher, _ = make(settings, public_resolver, {("a.example.test", "/robots.txt"): (404, {}, b""),
                                                  ("a.example.test", "/big"): (200, {"content-type": "text/html", "content-length": "999999"}, b"<p>tiny</p>")},
                      max_page_bytes=1000)
    with pytest.raises(PageTooLarge):
        await fetcher.fetch("https://a.example.test/big")


async def test_non_html_and_error_statuses_are_clear_errors(settings, public_resolver):
    fetcher, _ = make(settings, public_resolver, {("a.example.test", "/robots.txt"): (404, {}, b""),
                                                  ("a.example.test", "/img"): (200, {"content-type": "image/png"}, b"\x89PNG"),
                                                  ("a.example.test", "/boom"): (500, {}, b"oops")})
    with pytest.raises(NotHtml):
        await fetcher.fetch("https://a.example.test/img")
    with pytest.raises(FetchFailed, match="500"):
        await fetcher.fetch("https://a.example.test/boom")
    with pytest.raises(FetchFailed, match="404"):
        await fetcher.fetch("https://a.example.test/missing")


async def test_timeouts_and_connection_errors_are_mapped(settings, public_resolver):
    fetcher, _ = make(settings, public_resolver, {("a.example.test", "/robots.txt"): (404, {}, b""),
                                                  ("a.example.test", "/slow"): httpx.ReadTimeout("slow"),
                                                  ("a.example.test", "/down"): httpx.ConnectError("refused")})
    with pytest.raises(FetchTimeout):
        await fetcher.fetch("https://a.example.test/slow")
    with pytest.raises(FetchFailed, match="ConnectError"):
        await fetcher.fetch("https://a.example.test/down")


async def test_requests_to_one_host_are_spaced_out(settings, public_resolver):
    slept = []
    now = [0.0]

    async def sleep(s):
        slept.append(round(s, 2))
        now[0] += s

    limiter = DomainRateLimiter(2.0, clock=lambda: now[0], sleep=sleep)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(404 if request.url.path == "/robots.txt" else 200, headers={"content-type": "text/html"}, content=b"<p>x</p>")

    fetcher = Fetcher(settings, transport=httpx.MockTransport(handler), resolver=public_resolver, limiter=limiter)
    await fetcher.fetch("https://a.example.test/1")   # robots.txt, then (2s later) the page
    await fetcher.fetch("https://a.example.test/2")   # robots.txt is cached; the page waits 2s after the previous one
    await fetcher.fetch("https://b.example.test/1")   # b's own robots.txt is immediate; its page waits 2s after that
    assert slept == [2.0, 2.0, 2.0]
    assert [r.headers["host"] for r in seen].count("a.example.test") == 3


def test_decode_body_uses_http_charset_then_meta_then_utf8():
    assert decode_body("café".encode("latin-1"), "text/html; charset=iso-8859-1") == "café"
    assert decode_body(b'<meta charset="windows-1252"><p>\x80</p>', "text/html") .endswith("€</p>")
    assert decode_body("日本".encode(), "text/html") == "日本"
    assert decode_body(b"abc", "text/html; charset=nonsense") == "abc"


async def test_the_size_cap_applies_to_decompressed_bytes_so_a_zip_bomb_is_stopped(settings, public_resolver):
    import gzip
    bomb = gzip.compress(b"<p>" + b"A" * 5_000_000 + b"</p>")           # a few KB on the wire, 5 MB once decoded
    assert len(bomb) < 20_000
    fetcher, _ = make(settings, public_resolver, {
        ("a.example.test", "/robots.txt"): (404, {}, b""),
        ("a.example.test", "/bomb"): (200, {"content-type": "text/html", "content-encoding": "gzip"}, bomb)}, max_page_bytes=100_000)
    with pytest.raises(PageTooLarge):
        await fetcher.fetch("https://a.example.test/bomb")


async def test_normal_compressed_pages_still_work(settings, public_resolver):
    import gzip
    fetcher, _ = make(settings, public_resolver, {
        ("a.example.test", "/robots.txt"): (404, {}, b""),
        ("a.example.test", "/ok"): (200, {"content-type": "text/html", "content-encoding": "gzip"}, gzip.compress(HTML.encode()))})
    assert (await fetcher.fetch("https://a.example.test/ok")).html == HTML
