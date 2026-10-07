"""Optional JavaScript rendering with Playwright (feature flag RENDER_JS). Imported lazily: the base install
does not need Playwright or a browser. Every request the page makes is checked with the same SSRF rules."""
from product_extractor.config import Settings
from product_extractor.errors import ExtractError
from product_extractor.security import Resolver, validate_url


class RenderUnavailable(Exception):
    """Playwright (or its browser) is not installed."""


class Renderer:
    def __init__(self, settings: Settings, resolver: Resolver | None = None):
        self.settings = settings
        self._resolver = resolver

    async def render(self, url: str) -> str:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RenderUnavailable("Playwright is not installed (pip install -r requirements-render.txt).") from None
        s = self.settings
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(executable_path=s.render_executable_path or None)
                try:
                    context = await browser.new_context(user_agent=s.user_agent, java_script_enabled=True)

                    async def guard(route):
                        # the browser resolves DNS itself, so each request is validated before it may go out
                        try:
                            await validate_url(route.request.url, allow_private=s.allow_private_networks, resolver=self._resolver)
                        except ExtractError:
                            await route.abort("blockedbyclient")
                        else:
                            await route.continue_()

                    await context.route("**/*", guard)
                    page = await context.new_page()
                    await page.goto(url, wait_until="networkidle", timeout=s.render_timeout_seconds * 1000)
                    return (await page.content())[: s.max_page_bytes]
                finally:
                    await browser.close()
        except RenderUnavailable:
            raise
        except Exception as exc:  # playwright raises its own error types; treat any as 'rendering did not work'
            if "Executable doesn't exist" in str(exc):
                raise RenderUnavailable("The Playwright browser is not installed (run: playwright install chromium).") from None
            raise ExtractError(f"Rendering failed: {type(exc).__name__}") from None

