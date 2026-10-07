"""Run the layers in order and merge: the first layer to supply a field wins, and disagreements are reported."""
from decimal import Decimal

from product_extractor.config import Settings
from product_extractor.extractors import Candidate, LayerResult, jsonld, lower, microdata, opengraph
from product_extractor.extractors import html as html_layer
from product_extractor.extractors.llm import LLM_FIELDS, LLMExtractor
from product_extractor.page import Page
from product_extractor.schema import DATA_FIELDS, ProductData

STATIC_LAYERS = (jsonld, opengraph, microdata, html_layer)  # cheapest and most reliable first
_STRUCTURED = {"jsonld", "opengraph", "microdata"}  # layers trusted enough to flag a disagreement
_CORE = ("name", "price")  # a missing value here deserves an explicit warning


def _same(a: str | Decimal, b: str | Decimal) -> bool:
    return a == b if isinstance(a, Decimal) or isinstance(b, Decimal) else str(a).casefold() == str(b).casefold()


def merge(results: list[LayerResult]) -> tuple[dict[str, Candidate], list[str]]:
    chosen: dict[str, Candidate] = {}
    warnings: list[str] = [w for r in results for w in r.warnings]
    for name in DATA_FIELDS:
        for r in results:
            cand = r.fields.get(name)
            if cand is None:
                continue
            if name == "currency" and "price" in chosen and r.method != chosen["price"].method:
                # a currency may only be borrowed from a layer whose own price agrees with the chosen price
                other = r.fields.get("price")
                if other is None or not _same(other.value, chosen["price"].value):
                    continue
            chosen.setdefault(name, cand)
    for name in ("price", "currency", "sku"):
        if name not in chosen:
            continue
        first = chosen[name]
        clashes = [r.fields[name] for r in results
                   if name in r.fields and r.method in _STRUCTURED and r.method != first.method
                   and not _same(r.fields[name].value, first.value)]
        if clashes:
            other = clashes[0]
            warnings.append(f"{name} differs between sources: {first.method} says {first.value}, "
                            f"{other.method} says {other.value} ({first.method} was used).")
            chosen[name] = Candidate(first.value, first.method, lower(first.confidence) if first.confidence == "high" else first.confidence)
    return chosen, warnings


def build(url: str, chosen: dict[str, Candidate], warnings: list[str]) -> ProductData:
    data = ProductData(url=url, warnings=list(dict.fromkeys(warnings)))
    for name, cand in chosen.items():
        setattr(data, name, cand.value)
        data.source_method[name] = cand.method
        data.confidence[name] = cand.confidence
    return data


def missing_for_llm(chosen: dict[str, Candidate]) -> list[str]:
    return [f for f in LLM_FIELDS if f not in chosen]


async def run(page: Page, settings: Settings, llm: LLMExtractor | None) -> ProductData:
    results = [layer.extract(page) for layer in STATIC_LAYERS]
    chosen, warnings = merge(results)
    gaps = missing_for_llm(chosen)
    if gaps:
        if llm is not None and settings.llm_enabled:
            llm_result = await llm.extract(page, gaps)
            warnings.extend(llm_result.warnings)
            for name, cand in llm_result.fields.items():
                chosen.setdefault(name, cand)
        else:
            warnings.append(f"LLM fallback skipped (no LLM_API_KEY set); still missing: {', '.join(gaps)}.")
    if not chosen:
        warnings.insert(0, "No product data was found on this page.")
    for name in _CORE:
        if name not in chosen and chosen:
            warnings.append(f"No {name} was found on the page.")
    return build(page.url, chosen, warnings)
