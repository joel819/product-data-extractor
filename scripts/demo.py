"""Offline demo: serve the six fictional fixture pages locally, extract each through the real fetcher
(robots.txt, rate limiter, size cap and all) and print a table. No internet, no API keys needed.

    python -m scripts.demo            # table + warnings
    python -m scripts.demo --json     # also print the full JSON for every page
"""
import argparse
import asyncio
import functools
import http.server
import json
import threading
from collections import Counter
from pathlib import Path

from product_extractor.config import get_settings
from product_extractor.schema import DATA_FIELDS, ProductData
from product_extractor.service import ExtractionService

PAGES = Path(__file__).resolve().parent.parent / "fixtures" / "pages"
DEMO_PAGES = (
    "norvane-arc-lamp", "tessello-weave-throw", "quillfern-ridge-pack",
    "brambleton-cask-side-table", "orrery-halo-speaker", "vantora-terrace-planter",
)
WHAT = {
    "norvane-arc-lamp": "full JSON-LD", "tessello-weave-throw": "Open Graph only", "quillfern-ridge-pack": "microdata",
    "brambleton-cask-side-table": "messy, needs the LLM", "orrery-halo-speaker": "no price on the page",
    "vantora-terrace-planter": "dimensions in a specs table",
}


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep the table readable
        pass


def serve_fixtures() -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(_Quiet, directory=str(PAGES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


async def collect() -> list[tuple[str, ProductData]]:
    """Extract every demo page through a real local HTTP server. The only place private networks are allowed."""
    server, base = serve_fixtures()
    settings = get_settings().model_copy(update={
        "allow_private_networks": True,      # the fixture server is on 127.0.0.1: demo and tests only
        "per_domain_min_interval_seconds": 0.0, "cache_ttl_seconds": 0,
        "render_js": False,
    })
    service = ExtractionService(settings)
    try:
        return [(name, (await service.extract(f"{base}/{name}.html"))[0]) for name in DEMO_PAGES]
    finally:
        await service.aclose()
        server.shutdown()


def _cell(value, width: int) -> str:
    text = "–" if value is None else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def render_table(results: list[tuple[str, ProductData]]) -> str:
    cols = [("page", 27), ("name", 28), ("brand", 14), ("price", 11), ("stock", 12), ("sku", 13),
            ("fields", 6), ("sources", 14), ("review", 6)]
    lines = ["  ".join(h.ljust(w) for h, w in cols), "  ".join("-" * w for _, w in cols)]
    for name, d in results:
        found = sum(getattr(d, f) is not None for f in DATA_FIELDS)
        sources = "+".join(sorted({m for m in d.source_method.values() if m})) or "–"
        low = sum(1 for c in d.confidence.values() if c == "low")
        price = f"{d.price} {d.currency or ''}".strip() if d.price is not None else None
        row = [name, d.name, d.brand, price, d.availability, d.sku, f"{found}/{len(DATA_FIELDS)}", sources, low or ""]
        lines.append("  ".join(_cell(v, w).ljust(w) for v, (_, w) in zip(row, cols, strict=True)))
    return "\n".join(lines)


def render_notes(results: list[tuple[str, ProductData]]) -> str:
    out = []
    for name, d in results:
        out.append(f"{name}  ({WHAT[name]})")
        out.extend(f"    ! {w}" for w in d.warnings) if d.warnings else out.append("    no warnings")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true", help="also print the full JSON for every page")
    args = parser.parse_args()
    results = asyncio.run(collect())
    llm = "on (LLM_API_KEY is set)" if get_settings().llm_enabled else "skipped (no LLM_API_KEY): missing fields stay null"
    print(f"\nproduct-data-extractor demo: {len(results)} fictional pages served from fixtures/pages/ · LLM step {llm}\n")
    print(render_table(results))
    print("\nWarnings per page\n" + render_notes(results))
    counts = Counter(m for _, d in results for m in d.source_method.values() if m)
    print("\nFields by source: " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
    print("Legend: fields = non-null fields out of 12 · review = fields with low confidence (a human should check them)")
    if args.json:
        for name, d in results:
            print(f"\n--- {name}\n{json.dumps(d.model_dump(mode='json'), indent=2, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
