import json
from decimal import Decimal

from product_extractor.extractors import jsonld, microdata, opengraph
from product_extractor.extractors import html as html_layer
from product_extractor.page import Page

URL = "https://shop.example.test/p/lamp"


def page(body: str = "", head: str = "", url: str = URL) -> Page:
    return Page(f"<!doctype html><html><head>{head}</head><body>{body}</body></html>", url)


def ld(data, url: str = URL) -> Page:
    """A page whose head holds the given JSON-LD (dict/list, or a raw string)."""
    raw = data if isinstance(data, str) else json.dumps(data)
    return page(head=f'<script type="application/ld+json">{raw}</script>', url=url)


def values(result) -> dict:
    return {k: v.value for k, v in result.fields.items()}


def confidences(result) -> dict:
    return {k: v.confidence for k, v in result.fields.items()}


def product(**extra) -> dict:
    return {"@context": "https://schema.org", "@type": "Product", **extra}


D = Decimal
__all__ = ["page", "ld", "values", "confidences", "product", "jsonld", "microdata", "opengraph", "html_layer", "D", "URL"]
