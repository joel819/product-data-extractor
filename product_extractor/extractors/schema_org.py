"""Read a schema.org Product that has been normalised to a JSON-LD-shaped dict. JSON-LD, microdata and
RDFa all end up here, so one set of rules decides how offers, brands, images and measurements are read."""
import re
from decimal import Decimal

from product_extractor.extractors import LayerResult, lower
from product_extractor.normalize import clean_text, currency_from, normalize_availability, parse_price
from product_extractor.page import Page
from product_extractor.schema import Confidence

PRODUCT_TYPES = frozenset({"product", "productgroup", "individualproduct", "productmodel"})
_UNIT_CODES = {"CMT": "cm", "MMT": "mm", "MTR": "m", "INH": "in", "FOT": "ft", "YRD": "yd"}
_LIST_PRICE = re.compile(r"list|strikethrough|msrp|rrp|retail", re.I)


def types_of(node: dict) -> set[str]:
    raw = node.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return {str(v).rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower() for v in values if v}


def is_product(node: dict) -> bool:
    return bool(types_of(node) & PRODUCT_TYPES)


def as_list(value) -> list:
    return value if isinstance(value, list) else [] if value is None else [value]


def scalar(value) -> str | None:
    """First usable text from a string, number, {'@value': ..} / {'name': ..} object, or a list of those."""
    for item in as_list(value):
        if isinstance(item, dict):
            item = item.get("@value", item.get("name", item.get("value")))
        if isinstance(item, bool) or item is None or isinstance(item, (dict, list)):
            continue
        text = clean_text(item)
        if text:
            return text
    return None


def joined(value) -> str | None:
    """All distinct texts of a list ('Oak, Steel'); used for colour and material."""
    seen: dict[str, None] = {}
    for item in as_list(value):
        text = scalar(item)
        if text:
            seen[text] = None
    return ", ".join(seen) or None


def _enumeration(value) -> str | None:
    """schema.org enumerations (availability) may be a string or a {'@id': 'https://schema.org/InStock'} reference."""
    for item in as_list(value):
        text = scalar(item.get("@id") if isinstance(item, dict) and "@id" in item else item)
        if text:
            return text
    return None


def _property_value(node: dict, pattern: str) -> str | None:
    """A PropertyValue in additionalProperty whose name matches, e.g. {name: 'Finish', value: 'Matte'}."""
    for prop in as_list(node.get("additionalProperty")):
        if isinstance(prop, dict) and re.search(pattern, str(scalar(prop.get("name")) or ""), re.I):
            found = scalar(prop.get("value"))
            if found:
                return found
    return None


def _measure(value) -> str | None:
    """'120 cm' from a QuantitativeValue ({value, unitText|unitCode}) or from plain text."""
    if isinstance(value, dict):
        number = scalar(value.get("value", value.get("@value")))
        unit = scalar(value.get("unitText")) or _UNIT_CODES.get(str(value.get("unitCode", "")).upper()) or scalar(value.get("unitCode"))
        return f"{number} {unit}".strip() if number else None
    return scalar(value)


def _dimensions(node: dict) -> str | None:
    labelled = _property_value(node, r"dimension|measurement")
    if labelled:
        return labelled
    parts = [(label, _measure(node.get(prop))) for label, prop in (("W", "width"), ("H", "height"), ("D", "depth"))]
    parts = [f"{label} {m}" for label, m in parts if m]
    return " × ".join(parts) or None


def _offers(node: dict) -> list[dict]:
    """Flatten Offer / AggregateOffer / lists of them into plain offer dicts (parent currency inherited)."""
    out: list[dict] = []

    def walk(offer, inherited_currency=None):
        if not isinstance(offer, dict):
            return
        currency = offer.get("priceCurrency") or inherited_currency
        nested = as_list(offer.get("offers"))
        if nested:
            for sub in nested:
                walk(sub, currency)
        if "aggregateoffer" in types_of(offer) or not nested:
            out.append({**offer, "priceCurrency": currency})

    for offer in as_list(node.get("offers")):
        walk(offer)
    return out


def _price_of(offer: dict) -> tuple[Decimal | None, str | None, bool]:
    """(price, currency, is_range_low) for one offer."""
    currency = scalar(offer.get("priceCurrency"))
    for key in ("price", "lowPrice"):
        price = parse_price(scalar(offer.get(key)))
        if price is not None:
            is_range = key == "lowPrice" and offer.get("highPrice") not in (None, offer.get("lowPrice"))
            return price, currency, is_range
    for spec in as_list(offer.get("priceSpecification")):
        if isinstance(spec, dict) and not _LIST_PRICE.search(str(spec.get("priceType", ""))):
            price = parse_price(scalar(spec.get("price")))
            if price is not None:
                return price, scalar(spec.get("priceCurrency")) or currency, False
    return None, currency, False


def read_product(node: dict, page: Page, method_result: LayerResult, base: Confidence = "high") -> None:
    """Fill `method_result` from one Product node. Never guesses: absent properties stay absent."""
    put = method_result.put
    put("name", scalar(node.get("name")), base)
    put("brand", scalar(node.get("brand")), base)
    put("sku", scalar(node.get("sku")), base)
    put("description", clean_text(scalar(node.get("description")), 2000), base)
    image = None
    for item in as_list(node.get("image")):
        image = page.abs(item if isinstance(item, str) else (item or {}).get("url") or (item or {}).get("contentUrl"))
        if image:
            break
    put("image_url", image, base)
    put("colour", joined(node.get("color", node.get("colour"))) or _property_value(node, r"^colou?r"), base)
    put("material", joined(node.get("material")) or _property_value(node, r"^material"), base)
    put("finish", _property_value(node, r"finish"), base)
    put("dimensions", _dimensions(node), base)

    offers = _offers(node)
    priced = [(o, *_price_of(o)) for o in offers]
    priced = [p for p in priced if p[1] is not None]
    if priced:
        distinct = {p[1] for p in priced}
        first, price, currency, is_range = priced[0]
        confidence = base
        if is_range:
            high = parse_price(scalar(first.get("highPrice")))
            method_result.warnings.append(f"Price is a range ({price}–{high}); the lowest price was used.")
            confidence = lower(base)
        elif len(distinct) > 1:
            lo, hi = min(distinct), max(distinct)
            method_result.warnings.append(
                f"{len(priced)} offers with different prices ({lo}–{hi}); the first offer was used.")
            confidence = lower(base)
        put("price", price, confidence)
        put("currency", currency_from(currency) or None, confidence)
    elif offers:
        c = next((currency_from(scalar(o.get("priceCurrency"))) for o in offers if o.get("priceCurrency")), None)
        put("currency", c, base)
    stock = [normalize_availability(_enumeration(o.get("availability"))) for o in offers if o.get("availability")]
    stock = [s for s in stock if s]
    if stock:
        conflict = len(set(stock)) > 1
        if conflict:
            method_result.warnings.append(f"Offers disagree on availability ({', '.join(sorted(set(stock)))}); the first was used.")
        put("availability", stock[0], lower(base) if conflict else base)
    if "productgroup" in types_of(node) and node.get("hasVariant") and not offers:
        method_result.warnings.append("This is a product group with variants; variant prices were not extracted.")
