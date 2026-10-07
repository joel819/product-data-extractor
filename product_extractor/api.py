"""HTTP API. POST /extract {"url": ...} -> ProductData. Interactive docs at /docs."""
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from product_extractor import __version__
from product_extractor.config import Settings, get_settings
from product_extractor.errors import ExtractError, Unauthorized
from product_extractor.schema import DATA_FIELDS, ProductData
from product_extractor.service import ExtractionService

log = logging.getLogger("uvicorn.error")

DESCRIPTION = """
Paste a product page URL, get clean structured product data back. Every field that is not found is `null`:
values are never invented. `source_method` and `confidence` say, per field, where each value came from and how
much to trust it. Extraction order: schema.org JSON-LD, Open Graph, microdata/RDFa, HTML heuristics, then an
optional LLM fallback for fields still missing (its values must appear in the page text).
"""


class ExtractRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"url": "https://shop.example.com/products/arc-desk-lamp"}]})

    url: str = Field(min_length=1, max_length=2048, description="Absolute http(s) URL of a product page")


class ErrorBody(BaseModel):
    error: str = Field(description="Stable machine-readable code, e.g. invalid_url, blocked_address, blocked_by_robots")
    message: str


class Health(BaseModel):
    status: str
    version: str
    llm_fallback: bool
    js_rendering: bool
    respects_robots_txt: bool
    auth_required: bool
    cached_urls: int


def create_app(settings: Settings | None = None, service: ExtractionService | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = service or ExtractionService(settings)
        yield
        await app.state.service.aclose()

    app = FastAPI(title="product-data-extractor", version=__version__, description=DESCRIPTION, lifespan=lifespan,
                  license_info={"name": "MIT"})

    def require_key(x_api_key: str | None = Header(default=None, description="Required only if the server sets API_KEY")) -> None:
        if settings.api_key and not (x_api_key and secrets.compare_digest(x_api_key, settings.api_key)):
            raise Unauthorized("Missing or wrong X-API-Key header.")

    @app.exception_handler(ExtractError)
    async def extract_error(_: Request, exc: ExtractError):
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def bad_request(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return JSONResponse({"error": "invalid_request", "message": f"{where}: {first.get('msg', 'invalid request')}".strip(": ")},
                            status_code=422)

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/docs")

    @app.get("/health", response_model=Health, tags=["meta"])
    def health(request: Request) -> Health:
        svc: ExtractionService = request.app.state.service
        return Health(status="ok", version=__version__, llm_fallback=svc.llm is not None, js_rendering=settings.render_js,
                      respects_robots_txt=settings.respect_robots_txt, auth_required=bool(settings.api_key),
                      cached_urls=len(svc.cache))

    @app.post("/extract", response_model=ProductData, tags=["extract"], dependencies=[Depends(require_key)],
              responses={400: {"model": ErrorBody, "description": "URL points at a private or non-public address"},
                         403: {"model": ErrorBody, "description": "Disallowed by robots.txt"},
                         422: {"model": ErrorBody, "description": "Invalid URL, not an HTML page, or page too large"},
                         502: {"model": ErrorBody, "description": "The site could not be fetched"},
                         504: {"model": ErrorBody, "description": "The site timed out"}})
    async def extract(body: ExtractRequest, request: Request, response: Response) -> ProductData:
        """Fetch the page politely, extract the product, and return it. Results are cached by URL."""
        svc: ExtractionService = request.app.state.service
        data, cached = await svc.extract(body.url)
        response.headers["X-Cache"] = "HIT" if cached else "MISS"
        found = sum(getattr(data, f) is not None for f in DATA_FIELDS)
        log.info("extract host=%s cache=%s fields=%d/%d", data.url.split("/")[2] if "//" in data.url else "?",
                 "hit" if cached else "miss", found, len(DATA_FIELDS))
        return data

    return app


app = create_app()
