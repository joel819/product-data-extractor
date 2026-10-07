"""Real browser rendering. Opt-in because it needs Playwright and a Chromium:

    pip install -r requirements-render.txt && playwright install chromium
    RUN_PLAYWRIGHT_TESTS=1 python -m pytest tests/test_render_playwright.py
    (PW_CHROMIUM_PATH=/path/to/chromium to use an existing browser)
"""
import http.server
import os
import threading

import pytest

from product_extractor.errors import ExtractError
from product_extractor.render import Renderer
from product_extractor.service import ExtractionService

pytestmark = pytest.mark.skipif(not os.environ.get("RUN_PLAYWRIGHT_TESTS"), reason="set RUN_PLAYWRIGHT_TESTS=1 to run browser tests")

JS_PAGE = """<!doctype html><html><head><title>Loading…</title></head><body><div id="app"></div>
<script>
  document.addEventListener('DOMContentLoaded', function () {
    var ld = document.createElement('script'); ld.type = 'application/ld+json';
    ld.textContent = JSON.stringify({"@context":"https://schema.org","@type":"Product","name":"Rendered Lamp","brand":"Jsco",
      "sku":"JS-1","offers":{"@type":"Offer","price":"42.00","priceCurrency":"EUR","availability":"https://schema.org/InStock"}});
    document.head.appendChild(ld);
    document.getElementById('app').innerHTML = '<h1>Rendered Lamp</h1><p>This content only exists after JavaScript runs, and it is long enough to count as a real page now.</p>';
  });
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            self.send_response(404); self.end_headers(); return
        body = JS_PAGE.encode()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def js_site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/p/lamp"
    server.shutdown()


@pytest.fixture
def render_settings(settings):
    return settings.model_copy(update={"render_js": True, "allow_private_networks": True,
                                       "render_executable_path": os.environ.get("PW_CHROMIUM_PATH", "")})


async def test_a_javascript_page_is_empty_statically_and_complete_after_rendering(js_site, render_settings):
    plain = render_settings.model_copy(update={"render_js": False})
    static, _ = await ExtractionService(plain).extract(js_site)
    assert static.name is None and static.price is None and any("RENDER_JS=true" in w for w in static.warnings)

    svc = ExtractionService(render_settings)
    rendered, _ = await svc.extract(js_site)
    assert (rendered.name, rendered.brand, rendered.sku, str(rendered.price), rendered.currency) == ("Rendered Lamp", "Jsco", "JS-1", "42.00", "EUR")
    assert rendered.source_method["name"] == "jsonld" and any("rendered with Playwright" in w for w in rendered.warnings)


async def test_the_browser_obeys_the_same_ssrf_rules(js_site, render_settings):
    strict = render_settings.model_copy(update={"allow_private_networks": False})
    with pytest.raises(ExtractError, match="Rendering failed"):
        await Renderer(strict).render(js_site)       # 127.0.0.1 is blocked for the browser too
