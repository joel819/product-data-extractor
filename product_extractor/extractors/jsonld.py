"""Layer 1: schema.org Product in JSON-LD. Handles @graph, top-level arrays, nested nodes, @id references,
Offer / AggregateOffer, nested brand objects, and pages with several JSON-LD blocks."""
import json

from product_extractor.extractors import LayerResult, lower
from product_extractor.extractors.schema_org import is_product, read_product
from product_extractor.page import Page

# Properties that point at *other* products (related items, variants, reviews): never descend into them.
_SKIP_KEYS = frozenset({"isrelatedto", "issimilarto", "isaccessoryorsparepartfor", "isconsumablefor", "hasvariant",
                        "review", "reviews", "aggregaterating", "isvariantof"})
_NO_RESOLVE = _SKIP_KEYS | {"itemoffered", "mainentityofpage"}  # back-links; following them only loops
MAX_NODES = 5000


def _load(text: str):
    text = text.strip()
    for wrapper in ("<!--", "-->", "//<![CDATA[", "//]]>", "<![CDATA[", "]]>"):
        text = text.replace(wrapper, "")
    return json.loads(text)


def _walk(data, out: list[dict], by_id: dict[str, dict], depth: int = 0) -> None:
    """Collect every dict node. Products reached only through 'related'/'variant' keys are not collected."""
    if depth > 30 or len(out) > MAX_NODES:
        return
    if isinstance(data, list):
        for item in data:
            _walk(item, out, by_id, depth + 1)
    elif isinstance(data, dict):
        out.append(data)
        node_id = data.get("@id")
        if isinstance(node_id, str) and len(data) > 1:
            by_id.setdefault(node_id, data)
        for key, value in data.items():
            if key.lower() not in _SKIP_KEYS and isinstance(value, (dict, list)):
                _walk(value, out, by_id, depth + 1)


def _resolve(node, by_id, seen: frozenset = frozenset()):
    """Replace {'@id': 'x'} references with the node they point at. Each id is expanded once per path."""
    if isinstance(node, dict):
        ref = node.get("@id")
        if len(node) == 1 and isinstance(ref, str) and ref in by_id and ref not in seen:
            return _resolve(by_id[ref], by_id, seen | {ref})
        return {k: v if k.lower() in _NO_RESOLVE else _resolve(v, by_id, seen) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(i, by_id, seen) for i in node]
    return node


def _same_page(candidate: str | None, page_url: str) -> bool:
    norm = lambda u: (u or "").split("#")[0].rstrip("/").lower()  # noqa: E731
    return bool(candidate) and norm(candidate) == norm(page_url)


def extract(page: Page) -> LayerResult:
    result = LayerResult("jsonld")
    nodes: list[dict] = []
    by_id: dict[str, dict] = {}
    invalid = 0
    for script in page.tree.css("script"):
        if "ld+json" not in (script.attributes.get("type") or "").lower():
            continue
        try:
            _walk(_load(script.text(deep=False) or ""), nodes, by_id)
        except (ValueError, RecursionError):
            invalid += 1
    if invalid:
        result.warnings.append(f"{invalid} JSON-LD block(s) could not be parsed and were skipped.")
    products = [n for n in nodes if is_product(n)]
    if not products:
        return result
    chosen = next((p for p in products if _same_page(p.get("url") or p.get("@id"), page.url)), None)
    confidence = "high"
    if chosen is None:
        with_offers = [p for p in products if p.get("offers")]
        chosen = (with_offers or products)[0]
        if len(products) > 1:
            result.warnings.append(
                f"{len(products)} Product nodes found in JSON-LD; used the {'first one with offers' if with_offers else 'first one'}.")
            confidence = lower("high")
    read_product(_resolve(chosen, by_id), page, result, confidence)
    return result
