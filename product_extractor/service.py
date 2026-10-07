"""The extraction service: fetch (or accept) a page, run the layers, optionally render and ask the LLM, cache."""
import httpx

from product_extractor import pipeline
from product_extractor.cache import TTLCache
from product_extractor.config import Settings
from product_extractor.errors import ExtractError
from product_extractor.extractors.llm import LLMExtractor
from product_extractor.fetcher import Fetcher
from product_extractor.page import Page
from product_extractor.render import Renderer, RenderUnavailable
from product_extractor.schema import DATA_FIELDS, ProductData


def _filled(data: ProductData) -> int:
    return sum(getattr(data, f) is not None for f in DATA_FIELDS)


class ExtractionService:
    def __init__(self, settings: Settings, *, fetcher: Fetcher | None = None, llm: LLMExtractor | None = None,
                 renderer: Renderer | None = None, cache: TTLCache | None = None,
                 llm_client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.fetcher = fetcher or Fetcher(settings)
        self.llm = llm or (LLMExtractor(settings, llm_client) if settings.llm_enabled else None)
        self.renderer = renderer or (Renderer(settings) if settings.render_js else None)
        self.cache = cache or TTLCache(settings.cache_ttl_seconds, settings.cache_max_entries)

    async def aclose(self) -> None:
        await self.fetcher.aclose()
        if self.llm:
            await self.llm.aclose()

    async def extract(self, url: str) -> tuple[ProductData, bool]:
        """(data, served_from_cache). Raises ExtractError subclasses for bad URLs and fetch failures."""
        cached = self.cache.get(url)
        if cached is not None:
            return cached, True
        fetched = await self.fetcher.fetch(url)
        data = await self.extract_html(fetched.html, url, final_url=fetched.url)
        self.cache.put(url, data)
        return data, False

    async def extract_html(self, html: str, url: str, final_url: str | None = None) -> ProductData:
        """Extract from HTML that is already in hand (no network, except the optional LLM call)."""
        page = Page(html, final_url or url)
        data = await pipeline.run(page, self.settings, self.llm)
        data.url = url
        if self.settings.render_js and len(page.text) < self.settings.render_min_text_chars:
            data = await self._try_render(url, final_url or url, data)
        elif len(page.text) < self.settings.render_min_text_chars and _filled(data) == 0:
            data.warnings.append("The page has almost no static content; it may need JavaScript. Set RENDER_JS=true to render it.")
        return data

    async def _try_render(self, url: str, final_url: str, static: ProductData) -> ProductData:
        try:
            assert self.renderer is not None
            rendered_html = await self.renderer.render(final_url)
        except RenderUnavailable as exc:
            static.warnings.append(f"RENDER_JS is on but rendering is unavailable: {exc}")
            return static
        except ExtractError as exc:
            static.warnings.append(f"The page has little static content and rendering failed: {exc.message}")
            return static
        rendered = await pipeline.run(Page(rendered_html, final_url), self.settings, self.llm)
        rendered.url = url
        if _filled(rendered) > _filled(static):
            rendered.warnings.append("The page was rendered with Playwright because its static HTML had little content.")
            return rendered
        static.warnings.append("The page was rendered with Playwright but that did not reveal more fields; static result kept.")
        return static
