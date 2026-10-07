import json
from decimal import Decimal as D

import httpx
import pytest

from product_extractor import pipeline
from product_extractor.extractors import LayerResult
from product_extractor.extractors.llm import LLMExtractor
from product_extractor.page import Page
from product_extractor.schema import DATA_FIELDS
from product_extractor.service import ExtractionService
from tests.conftest import fixture_html

URL = "https://shop.example.test/p/lamp"
LD = lambda **kw: '<script type="application/ld+json">' + json.dumps({"@context": "https://schema.org", "@type": "Product", **kw}) + "</script>"  # noqa: E731


def doc(head="", body=""):
    return f"<!doctype html><html><head>{head}</head><body>{body}</body></html>"


async def run(html, settings, llm=None):
    return await pipeline.run(Page(html, URL), settings, llm)


async def test_layer_precedence_is_jsonld_then_opengraph_then_microdata_then_html(settings):
    head = LD(name="From JSON-LD") + '<meta property="og:title" content="From OG">'
    body = '<div itemscope itemtype="https://schema.org/Product"><b itemprop="name">From microdata</b></div><h1>From HTML</h1>'
    assert (await run(doc(head, body), settings)).name == "From JSON-LD"
    assert (await run(doc('<meta property="og:title" content="From OG">', body), settings)).name == "From OG"
    assert (await run(doc("", body), settings)).name == "From microdata"
    assert (await run(doc("", "<h1>From HTML</h1>"), settings)).name == "From HTML"


async def test_each_field_comes_from_the_first_layer_that_has_it(settings):
    head = LD(name="LD name") + '<meta property="og:image" content="/og.jpg"><meta property="og:title" content="OG name">'
    d = await run(doc(head, '<table><tr><th>Colour</th><td>Sage</td></tr></table>'), settings)
    assert (d.name, d.image_url, d.colour) == ("LD name", "https://shop.example.test/og.jpg", "Sage")
    assert (d.source_method["name"], d.source_method["image_url"], d.source_method["colour"]) == ("jsonld", "opengraph", "html")


async def test_structured_sources_that_disagree_on_price_are_reported_and_lose_confidence(settings):
    head = LD(offers={"price": "89.00", "priceCurrency": "EUR"}) + '<meta property="product:price:amount" content="79.00"><meta property="product:price:currency" content="EUR">'
    d = await run(doc(head), settings)
    assert d.price == D("89.00") and d.source_method["price"] == "jsonld" and d.confidence["price"] == "medium"
    assert any("price differs between sources: jsonld says 89.00, opengraph says 79.00" in w for w in d.warnings)


async def test_agreement_between_sources_keeps_high_confidence_and_stays_quiet(settings):
    head = LD(offers={"price": "89.00", "priceCurrency": "EUR"}) + '<meta property="product:price:amount" content="89"><meta property="product:price:currency" content="EUR">'
    d = await run(doc(head), settings)
    assert d.confidence["price"] == "high" and not any("differs" in w for w in d.warnings)


async def test_the_weak_html_layer_never_triggers_a_disagreement_warning(settings):
    d = await run(doc(LD(offers={"price": "89.00", "priceCurrency": "EUR"}), '<span class="price">€12.00</span>'), settings)
    assert d.price == D("89.00") and d.confidence["price"] == "high" and not any("differs" in w for w in d.warnings)


async def test_a_currency_is_only_borrowed_from_a_layer_whose_price_agrees(settings):
    agree = doc(LD(offers={"price": "10.00"}) + '<meta property="product:price:amount" content="10.00"><meta property="product:price:currency" content="GBP">')
    d = await run(agree, settings)
    assert (d.price, d.currency, d.source_method["currency"]) == (D("10.00"), "GBP", "opengraph")
    differ = doc(LD(offers={"price": "10.00"}) + '<meta property="product:price:amount" content="12.00"><meta property="product:price:currency" content="GBP">')
    d2 = await run(differ, settings)
    assert d2.price == D("10.00") and d2.currency is None          # GBP belongs to a different price: not borrowed


async def test_missing_everything_is_reported_once_and_nothing_is_invented(settings):
    d = await run(doc(body="<p>An article about gardening.</p>"), settings)
    assert all(getattr(d, f) is None for f in DATA_FIELDS)
    assert d.warnings[0] == "No product data was found on this page." and len(d.warnings) == len(set(d.warnings))


@pytest.mark.parametrize("html", [
    "", "   \n", "<", "<<<>>>", "\x00\x01\x02", "<html>", "<script>", "plain text, not html", "<p>" * 5000,
    "<div>" * 3000 + "</div>" * 3000, "<table>" * 400,
    doc(LD(offers="oops", name=["a", {"x": 1}], brand=12, image=[None, 3], sku={"a": 1}, description=["x"], color={"k": 1})),
    doc('<script type="application/ld+json">{"@type": "Product", "offers": [null, 1, "x", {"price": {"a": 1}}]}</script>'),
    doc('<script type="application/ld+json">[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[["@type"]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]</script>'),
    doc('<meta property="product:price:amount" content="NaN"><meta property="og:image" content="http://[::1">'),
    doc("", '<div itemscope itemtype="https://schema.org/Product"><span itemprop="price">∞</span><span itemprop="offers" itemscope><span itemprop="price">1e999</span></span></div>'),
])
async def test_garbage_in_gives_null_fields_not_a_crash_and_not_a_guess(settings, html):
    d = await run(html, settings)
    assert d.url == URL
    dumped = d.model_dump(mode="json")
    for f in DATA_FIELDS:
        assert (dumped[f] is None) == (dumped["source_method"][f] is None) == (dumped["confidence"][f] is None)


# ---- the LLM step inside the pipeline -------------------------------------------------------------------------------

def llm_with(settings, answer):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    s = settings.model_copy(update={"llm_api_key": "k", "llm_base_url": "https://llm.example.test/v1"})
    return s, LLMExtractor(s, httpx.AsyncClient(transport=httpx.MockTransport(handler))), seen


async def test_the_llm_is_never_called_when_nothing_is_missing(settings):
    s, llm, seen = llm_with(settings, {})
    full = LD(name="x", brand="b", sku="s", description="d", color="c", material="m", image="/i.jpg",
              offers={"price": "1", "priceCurrency": "EUR", "availability": "InStock"},
              additionalProperty=[{"name": "Finish", "value": "f"}, {"name": "Dimensions", "value": "1 x 1 x 1 cm"}])
    d = await run(doc(full), s, llm)
    assert seen == [] and not any("LLM" in w for w in d.warnings)


async def test_the_llm_is_asked_only_for_what_is_still_missing(settings):
    s, llm, seen = llm_with(settings, {"colour": "Oat", "finish": "Matte oil"})
    page_html = doc(LD(name="Throw", brand="Tessello", sku="T1", description="A throw.", material="Cotton", image="/i.jpg",
                       offers={"price": "10", "priceCurrency": "GBP", "availability": "InStock"}),
                    "<p>Colour: Oat. Finish: Matte oil. Dimensions are not listed. This is a long enough page body text.</p>")
    d = await run(page_html, s, llm)
    asked = json.loads(json.dumps(seen[0]))["messages"][1]["content"]
    assert '"colour"' in asked and '"finish"' in asked and '"dimensions"' in asked
    assert '"name"' not in asked and '"price"' not in asked and '"brand"' not in asked
    assert (d.colour, d.finish) == ("Oat", "Matte oil") and d.source_method["colour"] == "llm" and d.confidence["colour"] == "low"
    assert d.name == "Throw" and d.source_method["name"] == "jsonld"      # earlier layers are never overwritten


async def test_llm_values_never_overwrite_values_from_earlier_layers(settings):
    s, llm, _ = llm_with(settings, {"name": "Hijacked", "price": 1, "colour": "Oat"})
    d = await run(doc(LD(name="Real Name"), "<p>Colour: Oat. A long enough page body text so the model is consulted.</p>"), s, llm)
    assert d.name == "Real Name"


async def test_without_a_key_the_llm_step_is_skipped_with_one_warning_and_fields_stay_null(settings):
    d = await run(fixture_html("brambleton-cask-side-table.html"), settings)
    assert d.price is None and d.name is None
    assert sum("LLM fallback skipped" in w for w in d.warnings) == 1


async def test_service_end_to_end_with_the_messy_page_and_a_working_llm(settings):
    answer = {"name": "Cask Oak Side Table", "brand": "Brambleton Home", "sku": "BH-2290-OAK", "price": 149, "currency": "GBP",
              "availability": "Only 3 left in stock", "dimensions": "45 cm wide, 45 cm deep and 52 cm high",
              "material": "solid oak", "finish": "natural oil"}
    s, llm, _ = llm_with(settings, answer)
    svc = ExtractionService(s, llm=llm)
    d = await svc.extract_html(fixture_html("brambleton-cask-side-table.html"), "https://demo.example.test/brambleton-cask-side-table.html")
    assert (d.name, d.brand, d.sku, d.price, d.currency, d.availability) == ("Cask Oak Side Table", "Brambleton Home", "BH-2290-OAK", D("149"), "GBP", "limited")
    assert {m for m in d.source_method.values() if m} == {"llm"} and {c for c in d.confidence.values() if c} == {"low"}
    assert d.image_url is None and d.colour is None                       # the page does not say: still null
    assert not any("skipped" in w for w in d.warnings)


async def test_a_hallucinating_llm_changes_nothing_but_the_warnings(settings):
    s, llm, _ = llm_with(settings, {"name": "Totally Different Product", "price": 5, "brand": "Nobody"})
    d = await ExtractionService(s, llm=llm).extract_html(fixture_html("brambleton-cask-side-table.html"), "https://demo.example.test/x")
    assert all(getattr(d, f) is None for f in DATA_FIELDS)
    assert sum("was rejected" in w for w in d.warnings) == 3


def test_merge_is_a_pure_function_of_layer_results():
    a, b = LayerResult("jsonld"), LayerResult("opengraph")
    a.put("name", "A", "high")
    b.put("name", "B", "medium")
    b.put("brand", "Bb", "medium")
    chosen, warnings = pipeline.merge([a, b])
    assert (chosen["name"].value, chosen["brand"].value, warnings) == ("A", "Bb", [])
