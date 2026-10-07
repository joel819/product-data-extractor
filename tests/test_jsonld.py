import pytest

from tests.helpers import URL, D, confidences, jsonld, ld, page, product, values


def run(data, url=URL):
    return jsonld.extract(ld(data, url))


def test_reads_a_plain_product_node():
    r = run(product(name="Arc Lamp", sku="NV-1", brand={"@type": "Brand", "name": "Norvane"},
                    offers={"@type": "Offer", "price": "89.00", "priceCurrency": "EUR",
                            "availability": "https://schema.org/InStock"}))
    assert values(r) == {"name": "Arc Lamp", "sku": "NV-1", "brand": "Norvane", "price": D("89.00"),
                         "currency": "EUR", "availability": "in_stock"}
    assert set(confidences(r).values()) == {"high"} and r.warnings == []


def test_finds_the_product_inside_a_graph_next_to_other_nodes():
    r = run({"@context": "https://schema.org", "@graph": [
        {"@type": "Organization", "name": "Shop"}, {"@type": "WebPage", "name": "Page title"},
        {"@type": "Product", "name": "In The Graph"}]})
    assert values(r) == {"name": "In The Graph"}


def test_top_level_arrays_and_several_script_blocks():
    p = page(head='<script type="application/ld+json">[{"@type":"Organization","name":"O"},'
                  '{"@type":"Product","name":"From Array"}]</script>'
                  '<script type="application/ld+json">{"@type":"Product","name":"Second Block"}</script>')
    r = jsonld.extract(p)
    assert values(r)["name"] == "From Array" and "2 Product nodes" in r.warnings[0]
    assert confidences(r)["name"] == "medium"          # ambiguity lowers confidence


def test_page_url_match_beats_order_and_keeps_high_confidence():
    r = run([product(name="Other", url="https://shop.example.test/p/other"),
             product(name="This One", url=URL)])
    assert values(r)["name"] == "This One" and confidences(r)["name"] == "high" and r.warnings == []


def test_without_a_url_match_the_product_with_offers_is_preferred():
    r = run([product(name="No offers"), product(name="Has offers", offers={"price": "5", "priceCurrency": "EUR"})])
    assert values(r)["name"] == "Has offers"


@pytest.mark.parametrize("brand,expected", [
    ("Norvane", "Norvane"), ({"@type": "Brand", "name": "Norvane"}, "Norvane"),
    ([{"@type": "Brand", "name": "Norvane"}, {"name": "Other"}], "Norvane"), ({"@type": "Organization", "name": " Norvane  Ltd "}, "Norvane Ltd"),
])
def test_brand_as_string_object_or_list(brand, expected):
    assert values(run(product(name="x", brand=brand)))["brand"] == expected


def test_offer_list_with_one_price_is_not_ambiguous():
    r = run(product(offers=[{"price": "10.00", "priceCurrency": "EUR"}, {"price": 10, "priceCurrency": "EUR"}]))
    assert values(r)["price"] == D("10.00") and r.warnings == [] and confidences(r)["price"] == "high"


def test_offers_with_different_prices_use_the_first_and_say_so():
    r = run(product(offers=[{"price": "10.00", "priceCurrency": "EUR"}, {"price": "12.50", "priceCurrency": "EUR"}]))
    assert values(r)["price"] == D("10.00") and confidences(r)["price"] == "medium"
    assert "2 offers with different prices (10.00–12.50)" in r.warnings[0]


def test_aggregate_offer_range_uses_the_low_price_with_a_warning():
    r = run(product(offers={"@type": "AggregateOffer", "lowPrice": "49.90", "highPrice": "79.90", "priceCurrency": "GBP",
                            "offerCount": 3}))
    assert values(r)["price"] == D("49.90") and values(r)["currency"] == "GBP" and confidences(r)["price"] == "medium"
    assert "range (49.90–79.90)" in r.warnings[0]


def test_aggregate_offer_with_equal_low_and_high_is_a_plain_price():
    r = run(product(offers={"@type": "AggregateOffer", "lowPrice": "20", "highPrice": "20", "priceCurrency": "EUR"}))
    assert values(r)["price"] == D("20") and r.warnings == [] and confidences(r)["price"] == "high"


def test_aggregate_offer_with_nested_offers_inherits_the_currency():
    r = run(product(offers={"@type": "AggregateOffer", "priceCurrency": "EUR",
                            "offers": [{"@type": "Offer", "price": "30.00", "availability": "InStock"}]}))
    assert values(r)["price"] == D("30.00") and values(r)["currency"] == "EUR" and values(r)["availability"] == "in_stock"


def test_price_specification_skips_list_prices():
    r = run(product(offers={"@type": "Offer", "priceSpecification": [
        {"@type": "UnitPriceSpecification", "price": "120.00", "priceCurrency": "EUR", "priceType": "https://schema.org/ListPrice"},
        {"@type": "UnitPriceSpecification", "price": "99.00", "priceCurrency": "EUR"}]}))
    assert values(r)["price"] == D("99.00") and values(r)["currency"] == "EUR"


@pytest.mark.parametrize("raw,expected", [(89.9, "89.9"), ("89,90", "89.90"), ("1,299.00", "1299.00"), ("€ 89", "89")])
def test_price_formats(raw, expected):
    assert values(run(product(offers={"price": raw, "priceCurrency": "EUR"})))["price"] == D(expected)


def test_unparseable_price_stays_null_instead_of_guessing():
    r = run(product(name="x", offers={"price": "call for price", "priceCurrency": "EUR"}))
    assert "price" not in values(r)


def test_currency_is_validated_and_normalised():
    assert values(run(product(offers={"price": "5", "priceCurrency": "eur"})))["currency"] == "EUR"
    assert "currency" not in values(run(product(offers={"price": "5", "priceCurrency": "euros"})))


@pytest.mark.parametrize("raw,expected", [
    ("https://schema.org/InStock", "in_stock"), ("http://schema.org/OutOfStock", "out_of_stock"), ("InStock", "in_stock"),
    ("https://schema.org/PreOrder", "preorder"), ("https://schema.org/Discontinued", "discontinued"),
    ({"@id": "https://schema.org/BackOrder"}, "backorder"),
])
def test_availability_vocabulary(raw, expected):
    assert values(run(product(offers={"price": "5", "priceCurrency": "EUR", "availability": raw})))["availability"] == expected


def test_id_references_are_resolved():
    r = run({"@context": "https://schema.org", "@graph": [
        {"@type": "Product", "@id": "#p", "name": "Ref Lamp", "brand": {"@id": "#b"}, "offers": {"@id": "#o"}},
        {"@type": "Brand", "@id": "#b", "name": "Refbrand"},
        {"@type": "Offer", "@id": "#o", "price": "15.00", "priceCurrency": "EUR"}]})
    assert values(r) == {"name": "Ref Lamp", "brand": "Refbrand", "price": D("15.00"), "currency": "EUR"}


def test_self_referencing_graphs_terminate():
    r = run({"@graph": [{"@type": "Product", "@id": "#p", "name": "Loop", "offers": {"@id": "#o"}},
                        {"@type": "Offer", "@id": "#o", "price": "1", "priceCurrency": "EUR", "itemOffered": {"@id": "#p"}}]})
    assert values(r)["name"] == "Loop" and values(r)["price"] == D("1")


def test_images_string_list_and_image_object_become_absolute_urls():
    assert values(run(product(image="/a.jpg")))["image_url"] == "https://shop.example.test/a.jpg"
    assert values(run(product(image=["/a.jpg", "/b.jpg"])))["image_url"] == "https://shop.example.test/a.jpg"
    assert values(run(product(image={"@type": "ImageObject", "url": "https://cdn.example.test/x.png"})))["image_url"] == "https://cdn.example.test/x.png"
    assert "image_url" not in values(run(product(image="javascript:alert(1)")))


def test_description_is_stripped_of_markup_and_entities():
    assert values(run(product(description="<p>Solid&nbsp;oak &amp; <b>steel</b></p>")))["description"] == "Solid oak & steel"


def test_dimensions_from_width_height_depth_and_from_a_labelled_property():
    r = run(product(width={"@type": "QuantitativeValue", "value": 120, "unitCode": "CMT"},
                    height={"value": 75, "unitText": "cm"}, depth="60 cm"))
    assert values(r)["dimensions"] == "W 120 cm × H 75 cm × D 60 cm"
    only = run(product(height={"value": 75, "unitCode": "CMT"}))
    assert values(only)["dimensions"] == "H 75 cm"
    labelled = run(product(additionalProperty=[{"@type": "PropertyValue", "name": "Dimensions", "value": "120 x 60 cm"}]))
    assert values(labelled)["dimensions"] == "120 x 60 cm"


def test_finish_colour_and_material_including_additional_property_and_lists():
    r = run(product(color=["Red", "Blue"], material="Oak", additionalProperty=[{"name": "Finish", "value": "Matte"}]))
    assert (values(r)["colour"], values(r)["material"], values(r)["finish"]) == ("Red, Blue", "Oak", "Matte")
    r2 = run(product(additionalProperty=[{"name": "Colour", "value": "Sage"}, {"name": "Material", "value": "Steel"}]))
    assert (values(r2)["colour"], values(r2)["material"]) == ("Sage", "Steel")


def test_nothing_is_invented_for_absent_properties():
    r = run(product(name="Only a name"))
    assert values(r) == {"name": "Only a name"}


def test_related_products_and_variants_are_not_mistaken_for_the_product():
    r = run(product(name="Main", isRelatedTo=[product(name="Related", offers={"price": "1", "priceCurrency": "EUR"})],
                    hasVariant=[product(name="Variant", offers={"price": "2", "priceCurrency": "EUR"})]))
    assert values(r) == {"name": "Main"} and r.warnings == []


def test_product_group_without_offers_says_prices_were_not_extracted():
    r = run({"@type": "ProductGroup", "name": "Shirt", "hasVariant": [product(name="S", offers={"price": "9", "priceCurrency": "EUR"})]})
    assert values(r) == {"name": "Shirt"} and "variants" in r.warnings[0]


@pytest.mark.parametrize("type_value", [["Product", "Thing"], "schema:Product", "https://schema.org/Product", "product"])
def test_type_spellings(type_value):
    r = jsonld.extract(ld({"@type": type_value, "name": "Typed"}))
    assert values(r) == {"name": "Typed"}


def test_invalid_blocks_are_skipped_with_a_warning_and_valid_ones_still_work():
    p = page(head='<script type="application/ld+json">{ not json,, }</script>'
                  '<script type="application/ld+json">{"@type":"Product","name":"Survivor"}</script>')
    r = jsonld.extract(p)
    assert values(r) == {"name": "Survivor"} and "1 JSON-LD block" in r.warnings[0]


def test_comment_and_cdata_wrappers_are_tolerated():
    raw = '<!-- //<![CDATA[ -->{"@type":"Product","name":"Wrapped"}<!-- //]]> -->'
    assert values(jsonld.extract(ld(raw))) == {"name": "Wrapped"}


def test_other_script_types_and_non_product_json_ld_give_nothing():
    p = page(head='<script type="application/json">{"@type":"Product","name":"Not LD"}</script>'
                  '<script type="application/ld+json">{"@type":"Organization","name":"Org"}</script>')
    assert jsonld.extract(p).fields == {}
