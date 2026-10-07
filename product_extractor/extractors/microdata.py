"""Layer 3: Microdata (itemscope / itemprop) and RDFa Lite (typeof / property) Product markup.
Both are converted to the JSON-LD shape and read by the same rules as layer 1; results are reported as 'microdata'."""
from selectolax.lexbor import LexborNode

from product_extractor.extractors import LayerResult, lower
from product_extractor.extractors.schema_org import PRODUCT_TYPES, read_product
from product_extractor.page import Page

_URL_ATTR = {"a": "href", "link": "href", "area": "href", "img": "src", "source": "src", "audio": "src",
             "video": "src", "embed": "src", "iframe": "src", "track": "src", "object": "data"}


class _Syntax:
    """The attribute names that differ between microdata and RDFa Lite."""

    def __init__(self, prop: str, scope: str, type_attr: str):
        self.prop, self.scope, self.type_attr = prop, scope, type_attr


MICRODATA = _Syntax("itemprop", "itemscope", "itemtype")
RDFA = _Syntax("property", "typeof", "typeof")


def _type_names(value: str | None) -> set[str]:
    return {t.rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower() for t in (value or "").split()}


def _first_type(value: str | None) -> str | None:
    types = (value or "").split()
    return types[0].rsplit("/", 1)[-1].rsplit(":", 1)[-1] if types else None


def _prop_value(node: LexborNode, page: Page):
    attrs = node.attributes
    if attrs.get("content") is not None:
        return attrs["content"]
    attr = _URL_ATTR.get(node.tag)
    if attr and attrs.get(attr):
        return page.abs(attrs[attr]) or attrs[attr]
    if node.tag in ("data", "meter") and attrs.get("value") is not None:
        return attrs["value"]
    if node.tag == "time" and attrs.get("datetime"):
        return attrs["datetime"]
    if attrs.get("resource") and attrs.get("typeof") is None:  # RDFa
        return attrs["resource"]
    return node.text(deep=True, separator=" ", strip=True)


def _read_scope(scope: LexborNode, syntax: _Syntax, page: Page) -> dict:
    """Properties of one item as a JSON-LD-shaped dict. Nested items become nested dicts."""
    props: dict[str, list] = {}

    def add(names: str, value) -> None:
        for name in names.split():
            props.setdefault(name.rsplit(":", 1)[-1], []).append(value)

    def walk(node: LexborNode) -> None:
        child = node.child
        while child is not None:
            attrs = child.attributes
            if child.tag not in ("-text", "-comment"):
                names = attrs.get(syntax.prop)
                is_scope = syntax.scope in attrs
                if names and is_scope:
                    nested = _read_scope(child, syntax, page)
                    nested["@type"] = _first_type(attrs.get(syntax.type_attr))
                    add(names, nested)
                elif names:
                    add(names, _prop_value(child, page))
                    walk(child)
                elif not is_scope:
                    walk(child)
                # an itemscope/typeof element with no itemprop/property is a separate item: not ours
            child = child.next

    walk(scope)
    out: dict = {k: (v[0] if len(v) == 1 else v) for k, v in props.items()}
    # JSON-LD property names are camelCase and case-sensitive; microdata authors are usually consistent, but be forgiving
    canonical = {"pricecurrency": "priceCurrency", "lowprice": "lowPrice", "highprice": "highPrice",
                 "additionalproperty": "additionalProperty", "unitcode": "unitCode", "unittext": "unitText",
                 "pricespecification": "priceSpecification", "pricetype": "priceType"}
    return {canonical.get(k.lower(), k): v for k, v in out.items()}


def _find_products(page: Page, syntax: _Syntax) -> list[LexborNode]:
    found = []
    for node in page.tree.css(f"[{syntax.scope}]"):
        names = _type_names(node.attributes.get(syntax.type_attr))
        # top-level items only: a Product nested as a property of something else (e.g. isRelatedTo) is skipped
        if names & PRODUCT_TYPES and not node.attributes.get(syntax.prop):
            found.append(node)
    return found


def extract(page: Page) -> LayerResult:
    result = LayerResult("microdata")
    for syntax in (MICRODATA, RDFA):
        products = _find_products(page, syntax)
        if not products:
            continue
        confidence = "high"
        if len(products) > 1:
            result.warnings.append(f"{len(products)} Product items found in {syntax.prop} markup; used the first one.")
            confidence = lower("high")
        data = _read_scope(products[0], syntax, page)
        data["@type"] = _first_type(products[0].attributes.get(syntax.type_attr))
        read_product(data, page, result, confidence)
        break
    return result
