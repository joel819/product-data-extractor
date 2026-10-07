import pytest

from product_extractor.errors import BlockedAddress, FetchFailed
from product_extractor.fetcher import FetchResult
from product_extractor.render import RenderUnavailable
from product_extractor.schema import DATA_FIELDS
from product_extractor.service import ExtractionService
from tests.conftest import fixture_html

URL = "https://shop.example.test/p/arc"


class FakeFetcher:
    def __init__(self, html=None, error=None, final_url=None):
        self.html, self.error, self.final_url, self.calls = html, error, final_url, []

    async def fetch(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return FetchResult(url=self.final_url or url, status=200, html=self.html, content_type="text/html")

    async def aclose(self):
        pass


class FakeRenderer:
    def __init__(self, html=None, error=None):
        self.html, self.error, self.calls = html, error, []

    async def render(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.html


async def test_extract_fetches_extracts_and_then_serves_the_cache(settings):
    fetcher = FakeFetcher(fixture_html("norvane-arc-lamp.html"))
    svc = ExtractionService(settings, fetcher=fetcher)
    first, cached1 = await svc.extract(URL)
    second, cached2 = await svc.extract("HTTPS://Shop.Example.test/p/arc#frag")
    assert (cached1, cached2) == (False, True) and len(fetcher.calls) == 1
    assert first.name == second.name == "Arc Desk Lamp" and first.url == URL


async def test_failures_propagate_and_are_not_cached(settings):
    fetcher = FakeFetcher(error=FetchFailed("The site answered HTTP 500."))
    svc = ExtractionService(settings, fetcher=fetcher)
    for _ in range(2):
        with pytest.raises(FetchFailed):
            await svc.extract(URL)
    assert len(fetcher.calls) == 2
    blocked = ExtractionService(settings, fetcher=FakeFetcher(error=BlockedAddress("no")))
    with pytest.raises(BlockedAddress):
        await blocked.extract("http://127.0.0.1/")


async def test_relative_links_resolve_against_the_final_url_after_redirects_but_url_stays_as_requested(settings):
    html = '<html><head><meta property="og:image" content="/m/x.jpg"></head><body></body></html>'
    svc = ExtractionService(settings, fetcher=FakeFetcher(html, final_url="https://cdn.example.test/final"))
    d, _ = await svc.extract(URL)
    assert d.url == URL and d.image_url == "https://cdn.example.test/m/x.jpg"


SPARSE = "<html><head><title>Loading</title></head><body><div id='app'></div></body></html>"


async def test_a_nearly_empty_page_suggests_rendering_when_it_is_switched_off(settings):
    d = await ExtractionService(settings).extract_html(SPARSE, URL)
    assert any("RENDER_JS=true" in w for w in d.warnings)


async def test_sparse_pages_are_rendered_when_the_flag_is_on_and_the_render_result_is_used(settings):
    s = settings.model_copy(update={"render_js": True})
    renderer = FakeRenderer(fixture_html("norvane-arc-lamp.html"))
    d = await ExtractionService(s, renderer=renderer).extract_html(SPARSE, URL)
    assert renderer.calls == [URL] and d.name == "Arc Desk Lamp" and d.url == URL
    assert any("rendered with Playwright" in w for w in d.warnings)


async def test_pages_with_enough_static_content_are_not_rendered(settings):
    s = settings.model_copy(update={"render_js": True})
    renderer = FakeRenderer(SPARSE)
    d = await ExtractionService(s, renderer=renderer).extract_html(fixture_html("vantora-terrace-planter.html"), URL)
    assert renderer.calls == [] and d.name


async def test_rendering_that_does_not_help_keeps_the_static_result(settings):
    s = settings.model_copy(update={"render_js": True})
    d = await ExtractionService(s, renderer=FakeRenderer(SPARSE)).extract_html(SPARSE, URL)
    assert all(getattr(d, f) is None for f in DATA_FIELDS) and any("did not reveal more fields" in w for w in d.warnings)


@pytest.mark.parametrize("error,expected", [
    (RenderUnavailable("Playwright is not installed"), "rendering is unavailable"),
])
async def test_missing_playwright_is_a_warning_not_an_error(settings, error, expected):
    s = settings.model_copy(update={"render_js": True})
    d = await ExtractionService(s, renderer=FakeRenderer(error=error)).extract_html(SPARSE, URL)
    assert any(expected in w for w in d.warnings)


async def test_render_failures_are_a_warning_not_an_error(settings):
    from product_extractor.errors import ExtractError
    s = settings.model_copy(update={"render_js": True})
    d = await ExtractionService(s, renderer=FakeRenderer(error=ExtractError("Rendering failed: TimeoutError"))).extract_html(SPARSE, URL)
    assert any("rendering failed" in w for w in d.warnings)
