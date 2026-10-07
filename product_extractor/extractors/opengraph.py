"""Layer 2: Open Graph and product meta tags (og:*, product:*). Page-level metadata: medium confidence."""
from product_extractor.extractors import LayerResult
from product_extractor.normalize import clean_text, currency_from, normalize_availability, parse_price
from product_extractor.page import Page


def _meta(page: Page) -> dict[str, str]:
    """First value of every <meta property|name=...>, keys lower-cased."""
    found: dict[str, str] = {}
    for node in page.tree.css("meta"):
        attrs = node.attributes
        key = (attrs.get("property") or attrs.get("name") or "").strip().lower()
        value = (attrs.get("content") or "").strip()
        if key and value and key not in found:
            found[key] = value
    return found


def extract(page: Page) -> LayerResult:
    result = LayerResult("opengraph")
    meta = _meta(page)
    first = lambda *keys: next((meta[k] for k in keys if k in meta), None)  # noqa: E731
    result.put("name", clean_text(first("og:title")), "medium")
    result.put("description", clean_text(first("og:description"), 2000), "medium")
    result.put("image_url", page.abs(first("og:image:secure_url", "og:image", "og:image:url")), "medium")
    result.put("brand", clean_text(first("product:brand", "og:brand")), "medium")
    result.put("sku", clean_text(first("product:retailer_item_id")), "medium")
    result.put("colour", clean_text(first("product:color")), "medium")
    result.put("material", clean_text(first("product:material")), "medium")
    availability = first("product:availability", "og:availability")
    result.put("availability", normalize_availability(availability), "medium")
    price = parse_price(first("product:price:amount", "og:price:amount"))
    if price is not None:
        result.put("price", price, "medium")
        result.put("currency", currency_from(first("product:price:currency", "og:price:currency")), "medium")
    return result
