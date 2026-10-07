"""Each extraction layer against the fictional fixture pages, end to end (no network, no LLM)."""
from decimal import Decimal as D

import pytest

from product_extractor.schema import DATA_FIELDS
from product_extractor.service import ExtractionService
from tests.conftest import fixture_html

BASE = "https://demo.example.test"


@pytest.fixture
def service(settings):
    return ExtractionService(settings)


async def extract(service, name):
    return await service.extract_html(fixture_html(name), f"{BASE}/{name}")


def summary(d):
    return {f: (getattr(d, f), d.source_method[f], d.confidence[f]) for f in DATA_FIELDS if getattr(d, f) is not None}


async def test_jsonld_page_uses_json_ld_for_every_field_and_ignores_the_open_graph_title(service):
    d = await extract(service, "norvane-arc-lamp.html")
    assert d.name == "Arc Desk Lamp" and d.brand == "Norvane" and d.sku == "NV-ARC-204"
    assert (d.price, d.currency, d.availability) == (D("89.00"), "EUR", "in_stock")
    assert d.image_url == f"{BASE}/img/arc-lamp-front.jpg"
    assert d.dimensions == "W 28 cm × H 46 cm × D 17 cm"
    assert (d.colour, d.finish, d.material) == ("Brushed brass", "Satin brass", "Steel, opal glass")
    assert d.description.startswith("A slim arc lamp") and "&nbsp;" not in d.description
    assert {m for m in d.source_method.values() if m} == {"jsonld"} and {c for c in d.confidence.values() if c} == {"high"}
    assert d.warnings == []


async def test_open_graph_only_page(service):
    d = await extract(service, "tessello-weave-throw.html")
    assert (d.name, d.brand, d.sku, d.colour) == ("Weave Throw Blanket", "Tessello", "TS-4471", "Oat")
    assert (d.price, d.currency, d.availability) == (D("64.50"), "GBP", "in_stock")
    assert d.image_url == f"{BASE}/media/weave-throw-oat.jpg"
    assert {m for m in d.source_method.values() if m} == {"opengraph"} and {c for c in d.confidence.values() if c} == {"medium"}
    assert d.dimensions is None and d.finish is None and d.material is None


async def test_the_decoy_price_in_an_unrelated_teaser_never_beats_the_open_graph_price(service):
    d = await extract(service, "tessello-weave-throw.html")
    assert d.price == D("64.50")


async def test_microdata_page(service):
    d = await extract(service, "quillfern-ridge-pack.html")
    assert (d.name, d.brand, d.sku) == ("Ridge Hiking Pack 28L", "Quillfern", "QF-RDG-28-MG")
    assert (d.price, d.currency, d.availability) == (D("119.00"), "USD", "out_of_stock")
    assert (d.colour, d.material) == ("Moss green", "Recycled nylon")
    assert {m for m in d.source_method.values() if m} == {"microdata"} and {c for c in d.confidence.values() if c} == {"high"}


async def test_messy_page_yields_nothing_without_an_llm_and_says_so(service):
    d = await extract(service, "brambleton-cask-side-table.html")
    assert all(getattr(d, f) is None for f in DATA_FIELDS)               # nothing is guessed from the prose
    assert not any(d.source_method.values()) and not any(d.confidence.values())
    assert d.warnings[0] == "No product data was found on this page."
    assert any("LLM fallback skipped (no LLM_API_KEY set)" in w for w in d.warnings)


async def test_page_with_no_price_leaves_price_and_currency_null_and_warns(service):
    d = await extract(service, "orrery-halo-speaker.html")
    assert d.name == "Halo Bookshelf Speaker" and d.brand == "Orrery Audio" and d.sku == "OA-HALO-BK"
    assert d.price is None and d.currency is None and d.availability is None
    assert d.source_method["price"] is None and d.confidence["price"] is None
    assert "No price was found on the page." in d.warnings


async def test_dimensions_buried_in_a_specs_table_are_found_by_the_html_layer(service):
    d = await extract(service, "vantora-terrace-planter.html")
    assert d.dimensions == "Large 45 × 45 × 50 cm; medium 38 × 38 × 42 cm; small 30 × 30 × 34 cm"
    assert (d.brand, d.sku, d.colour, d.material, d.finish) == ("Vantora", "VT-PLN-3S", "Sage", "Galvanised steel", "Powder-coated, matte")
    assert (d.price, d.currency, d.availability) == (D("72.00"), "GBP", "in_stock")   # not the struck-through £90
    assert d.source_method["dimensions"] == "html" and d.confidence["dimensions"] == "medium"
    assert d.confidence["price"] == "low"


async def test_every_output_is_complete_and_serialisable(service):
    for name in ("norvane-arc-lamp", "tessello-weave-throw", "quillfern-ridge-pack", "brambleton-cask-side-table",
                 "orrery-halo-speaker", "vantora-terrace-planter"):
        d = await extract(service, f"{name}.html")
        dumped = d.model_dump(mode="json")
        assert set(dumped["source_method"]) == set(DATA_FIELDS) == set(dumped["confidence"])
        for f in DATA_FIELDS:   # a field is null exactly when it has no source and no confidence
            assert (dumped[f] is None) == (dumped["source_method"][f] is None) == (dumped["confidence"][f] is None)
