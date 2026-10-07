"""Layer 4: plain HTML heuristics. The weakest layer, so it is deliberately narrow: it only looks in places that
are explicitly about the product (a price element, a specs table, the main heading) and never scans free text for
numbers. Specs-table rows are labelled data (medium); everything else here is low confidence."""
import re

from selectolax.lexbor import LexborNode

from product_extractor.extractors import LayerResult
from product_extractor.normalize import (
    clean_text, currency_from, has_ambiguous_symbol, normalize_availability, parse_price,
)
from product_extractor.page import Page

_PRICE_SELECTOR = '[class*="price" i], [id*="price" i], [data-price], [data-product-price]'
_NOT_THE_PRICE = re.compile(r"old|was|compare|regular|original|list|strike|before|msrp|rrp|cross|savings?|save|discount|from-|per-", re.I)
_STOCK_SELECTOR = '[class*="stock" i], [class*="availab" i], [id*="stock" i], [id*="availab" i]'
_NOT_A_PRODUCT_IMAGE = re.compile(r"logo|icon|sprite|avatar|badge|banner|flag|payment|social|thumb-?nav", re.I)
_VOCAB = {"in_stock", "out_of_stock", "preorder", "backorder", "limited", "discontinued"}

# label (lower-case, trailing colon removed) -> field
_LABELS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"^(product |overall |item )?(dimensions?|measurements?)( \(.*\))?$"), "dimensions"),
    (re.compile(r"^colou?rs?$"), "colour"),
    (re.compile(r"^(surface )?finish$"), "finish"),
    (re.compile(r"^(main |frame |primary )?materials?$"), "material"),
    (re.compile(r"^(sku|item code|article (number|no\.?)|item (number|no\.?))$"), "sku"),
    (re.compile(r"^brand$"), "brand"),
)
_SIZE_WITH_UNITS = re.compile(r"\d+(\.\d+)?\s*(x|×|by)\s*\d+", re.I)
_EMPTY_VALUE = {"", "-", "—", "n/a", "na", "none", "tbc", "tba"}


def _classes(node: LexborNode) -> str:
    a = node.attributes
    return f"{a.get('class') or ''} {a.get('id') or ''}"


def _struck_through(node: LexborNode) -> bool:
    n: LexborNode | None = node
    while n is not None and n.tag not in ("body", "html", None):
        if n.tag in ("del", "s", "strike") or _NOT_THE_PRICE.search(_classes(n)):
            return True
        n = n.parent
    return False


def _price(page: Page, result: LayerResult) -> None:
    for node in page.tree.css(_PRICE_SELECTOR):
        if _struck_through(node):
            continue
        attrs = node.attributes
        text = clean_text(node.text(deep=True, separator=" ", strip=True)) or ""
        raw = attrs.get("data-price") or attrs.get("data-product-price") or text
        if len(text) > 60 or not re.search(r"\d", str(raw)):
            continue  # a container with prose, not a price
        price = parse_price(raw)
        if price is None:
            continue
        result.put("price", price, "low")
        currency = currency_from(text) or currency_from(attrs.get("data-currency"))
        result.put("currency", currency, "low")
        if currency is None and has_ambiguous_symbol(text):
            result.warnings.append(f"Found the price {text!r}, but its currency symbol is ambiguous; currency left empty.")
        return


def _stock(page: Page, result: LayerResult) -> None:
    for node in page.tree.css(_STOCK_SELECTOR):
        text = clean_text(node.text(deep=True, separator=" ", strip=True))
        if text and len(text) <= 80:
            value = normalize_availability(text)
            if value in _VOCAB:
                result.put("availability", value, "low")
                return


def _image(page: Page, result: LayerResult) -> None:
    link = page.tree.css_first('link[rel="image_src"]')
    if link and page.abs(link.attributes.get("href")):
        result.put("image_url", page.abs(link.attributes["href"]), "low")
        return
    for selector in ('img[class*="product" i]', '[class*="product" i] img', '[class*="gallery" i] img',
                     '[id*="product" i] img', "main img", "article img"):
        for img in page.tree.css(selector):
            a = img.attributes
            src = a.get("src") or a.get("data-src") or a.get("data-lazy-src")
            if not src or src.startswith("data:") or _NOT_A_PRODUCT_IMAGE.search(f"{src} {_classes(img)} {a.get('alt') or ''}"):
                continue
            if any(str(a.get(k, "")).isdigit() and int(a[k]) < 80 for k in ("width", "height")):
                continue
            url = page.abs(src)
            if url:
                result.put("image_url", url, "low")
                return


def _label_value(label: str, value: str, result: LayerResult) -> None:
    key = label.strip().rstrip(":").strip().lower()
    value = clean_text(value, 300) or ""
    if value.lower() in _EMPTY_VALUE:
        return
    for pattern, field in _LABELS:
        if pattern.match(key):
            result.put(field, value, "medium")
            return
    if key == "size" and _SIZE_WITH_UNITS.search(value):  # 'Size: M' is a clothing size, '45 x 45 x 50 cm' is a dimension
        result.put("dimensions", value, "medium")


def _specs(page: Page, result: LayerResult) -> None:
    for row in page.tree.css("tr"):
        head = row.css_first("th")
        data = row.css("td")
        if head and data:
            _label_value(head.text(deep=True, separator=" ", strip=True), data[0].text(deep=True, separator=" ", strip=True), result)
        elif len(data) >= 2:
            _label_value(data[0].text(deep=True, separator=" ", strip=True), data[1].text(deep=True, separator=" ", strip=True), result)
    for dl in page.tree.css("dl"):
        term = None
        child = dl.child
        while child is not None:
            if child.tag == "dt":
                term = child.text(deep=True, separator=" ", strip=True)
            elif child.tag == "dd" and term:
                _label_value(term, child.text(deep=True, separator=" ", strip=True), result)
                term = None
            child = child.next
    for item in page.tree.css("li"):
        text = clean_text(item.text(deep=True, separator=" ", strip=True)) or ""
        if ":" in text and len(text) < 160:
            label, _, value = text.partition(":")
            _label_value(label, value, result)


def extract(page: Page) -> LayerResult:
    result = LayerResult("html")
    heading = page.tree.css_first("main h1, [role=main] h1, article h1") or page.tree.css_first("h1")
    if heading:
        result.put("name", clean_text(heading.text(deep=True, separator=" ", strip=True), 300), "low")
    description = page.tree.css_first('meta[name="description" i]')
    if description:
        result.put("description", clean_text(description.attributes.get("content"), 2000), "low")
    _price(page, result)
    _stock(page, result)
    _image(page, result)
    _specs(page, result)
    return result
