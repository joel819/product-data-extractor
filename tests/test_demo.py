"""The demo runs the real fetcher against a real local HTTP server (loopback only), so this also covers the
fetch path end to end: robots.txt, redirects-free GET, size cap, decoding."""
from decimal import Decimal as D

from scripts import demo


async def test_the_demo_extracts_all_six_fictional_pages_over_http():
    results = dict(await demo.collect())
    assert list(results) == list(demo.DEMO_PAGES) and len(results) == 6
    assert results["norvane-arc-lamp"].price == D("89.00")
    assert results["tessello-weave-throw"].source_method["name"] == "opengraph"
    assert results["quillfern-ridge-pack"].availability == "out_of_stock"
    assert results["vantora-terrace-planter"].dimensions.startswith("Large 45 × 45 × 50 cm")
    assert results["orrery-halo-speaker"].price is None
    brambleton = results["brambleton-cask-side-table"]
    assert brambleton.name is None and any("LLM fallback skipped" in w for w in brambleton.warnings)


async def test_the_table_and_notes_render():
    results = await demo.collect()
    table, notes = demo.render_table(results), demo.render_notes(results)
    assert table.count("\n") == 7 and "Arc Desk Lamp" in table and "89.00 EUR" in table and "0/12" in table
    assert "No price was found on the page." in notes and "(dimensions in a specs table)" in notes
