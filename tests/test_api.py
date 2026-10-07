import httpx
import pytest
from fastapi.testclient import TestClient

from product_extractor.api import create_app
from product_extractor.errors import FetchFailed, FetchTimeout
from product_extractor.fetcher import Fetcher, FetchResult
from product_extractor.service import ExtractionService
from tests.conftest import fixture_html


class StubFetcher:
    def __init__(self, html=None, error=None):
        self.html, self.error, self.calls = html, error, []

    async def fetch(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return FetchResult(url=url, status=200, html=self.html, content_type="text/html")

    async def aclose(self):
        pass


def client_for(settings, fetcher):
    app = create_app(settings, ExtractionService(settings, fetcher=fetcher))
    return TestClient(app)


def lamp(settings):
    return client_for(settings, StubFetcher(fixture_html("norvane-arc-lamp.html")))


def test_health(settings):
    with lamp(settings) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": "0.1.0", "llm_fallback": False, "js_rendering": False,
                        "respects_robots_txt": True, "auth_required": False, "cached_urls": 0}


def test_health_never_reveals_secrets(settings):
    s = settings.model_copy(update={"api_key": "super-secret", "llm_api_key": "sk-secret"})
    with client_for(s, StubFetcher("<html></html>")) as c:
        body = c.get("/health").text
    assert "super-secret" not in body and "sk-secret" not in body


def test_extract_returns_the_documented_shape(settings):
    with lamp(settings) as c:
        r = c.post("/extract", json={"url": "https://shop.norvane.example/p/arc-desk-lamp"})
    assert r.status_code == 200 and r.headers["x-cache"] == "MISS"
    body = r.json()
    assert body["url"] == "https://shop.norvane.example/p/arc-desk-lamp"
    assert (body["name"], body["brand"], body["sku"], body["price"], body["currency"], body["availability"]) == (
        "Arc Desk Lamp", "Norvane", "NV-ARC-204", "89.00", "EUR", "in_stock")
    assert body["source_method"]["price"] == "jsonld" and body["confidence"]["price"] == "high"
    assert body["warnings"] == [] and set(body) == {
        "url", "name", "brand", "sku", "price", "currency", "availability", "description", "image_url", "dimensions",
        "colour", "finish", "material", "source_method", "confidence", "warnings"}


def test_missing_fields_are_null_in_the_json(settings):
    with client_for(settings, StubFetcher(fixture_html("orrery-halo-speaker.html"))) as c:
        body = c.post("/extract", json={"url": "https://orrery.example/halo"}).json()
    assert body["price"] is None and body["currency"] is None and body["source_method"]["price"] is None
    assert "No price was found on the page." in body["warnings"]


def test_the_second_request_for_a_url_is_served_from_the_cache(settings):
    fetcher = StubFetcher(fixture_html("norvane-arc-lamp.html"))
    with client_for(settings, fetcher) as c:
        a = c.post("/extract", json={"url": "https://shop.norvane.example/p/1"})
        b = c.post("/extract", json={"url": "https://SHOP.norvane.example/p/1#top"})
        assert c.get("/health").json()["cached_urls"] == 1
    assert (a.headers["x-cache"], b.headers["x-cache"]) == ("MISS", "HIT") and a.json() == b.json() and len(fetcher.calls) == 1


@pytest.mark.parametrize("body", [{}, {"url": ""}, {"url": 5}, {"link": "https://x.test"}, {"url": "x" * 3000}])
def test_bad_request_bodies_are_clean_422s(settings, body):
    with lamp(settings) as c:
        r = c.post("/extract", json=body)
    assert r.status_code == 422 and r.json()["error"] == "invalid_request" and r.json()["message"]


@pytest.mark.parametrize("url", ["ftp://x.test/f", "file:///etc/passwd", "not a url", "javascript:alert(1)", "http://user:pw@x.test/"])
def test_invalid_urls_are_422(settings, url):
    real = Fetcher(settings, transport=httpx.MockTransport(lambda r: pytest.fail("no request may be made")))
    with client_for(settings, real) as c:
        r = c.post("/extract", json={"url": url})
    assert r.status_code == 422 and r.json()["error"] == "invalid_url"


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost:8000/admin", "http://169.254.169.254/latest/meta-data/",
                                 "http://[::1]/", "http://10.0.0.1/", "http://192.168.0.1/x", "http://[::ffff:127.0.0.1]/"])
def test_private_addresses_are_blocked_with_400_and_nothing_is_fetched(settings, url):
    real = Fetcher(settings, transport=httpx.MockTransport(lambda r: pytest.fail("no request may be made")))
    with client_for(settings, real) as c:
        r = c.post("/extract", json={"url": url})
    assert r.status_code == 400 and r.json()["error"] == "blocked_address"


def test_robots_txt_blocks_return_403(settings, public_resolver):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        pytest.fail("page must not be fetched")

    real = Fetcher(settings, transport=httpx.MockTransport(handler), resolver=public_resolver)
    with client_for(settings, real) as c:
        r = c.post("/extract", json={"url": "https://shop.example.test/p/1"})
    assert r.status_code == 403 and r.json()["error"] == "blocked_by_robots"


def test_upstream_failures_map_to_502_and_504(settings):
    with client_for(settings, StubFetcher(error=FetchFailed("The site answered HTTP 500."))) as c:
        r = c.post("/extract", json={"url": "https://shop.example.test/p"})
    assert (r.status_code, r.json()) == (502, {"error": "fetch_failed", "message": "The site answered HTTP 500."})
    with client_for(settings, StubFetcher(error=FetchTimeout("The site took too long to respond."))) as c:
        assert c.post("/extract", json={"url": "https://shop.example.test/p"}).status_code == 504


def test_errors_are_not_cached(settings):
    fetcher = StubFetcher(error=FetchFailed("boom"))
    with client_for(settings, fetcher) as c:
        c.post("/extract", json={"url": "https://shop.example.test/p"})
        c.post("/extract", json={"url": "https://shop.example.test/p"})
    assert len(fetcher.calls) == 2


def test_api_key_is_required_only_when_configured(settings):
    s = settings.model_copy(update={"api_key": "s3cret"})
    with client_for(s, StubFetcher(fixture_html("norvane-arc-lamp.html"))) as c:
        url = {"url": "https://shop.norvane.example/p/1"}
        assert c.post("/extract", json=url).status_code == 401
        assert c.post("/extract", json=url, headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.post("/extract", json=url, headers={"X-API-Key": "s3cret"}).status_code == 200
        assert c.get("/health").status_code == 200 and c.get("/health").json()["auth_required"] is True
        assert c.post("/extract", json=url).json()["error"] == "unauthorized"


def test_interactive_docs_and_openapi_schema(settings):
    with lamp(settings) as c:
        assert c.get("/docs").status_code == 200
        assert c.get("/", follow_redirects=False).headers["location"] == "/docs"
        spec = c.get("/openapi.json").json()
    props = spec["components"]["schemas"]["ProductData"]["properties"]
    assert {"url", "name", "price", "source_method", "confidence", "warnings"} <= set(props)
    assert "/extract" in spec["paths"] and "/health" in spec["paths"]
