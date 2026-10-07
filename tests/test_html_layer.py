import pytest

from tests.helpers import D, confidences, html_layer, page, values


def h(body: str, head: str = "", **kw):
    return html_layer.extract(page(body=body, head=head, **kw))


def test_heading_price_stock_image_and_description():
    r = h('<main><h1> Terrace  Planter </h1><img class="product-image" src="/i/p.jpg" alt="x">'
          '<span class="price">£72.00</span><span class="stock-status">In stock</span></main>',
          head='<meta name="description" content="Three planters.">')
    assert values(r) == {"name": "Terrace Planter", "price": D("72.00"), "currency": "GBP", "availability": "in_stock",
                         "image_url": "https://shop.example.test/i/p.jpg", "description": "Three planters."}
    assert set(confidences(r).values()) == {"low"}


def test_old_struck_through_and_compare_prices_are_skipped():
    r = h('<span class="old-price">£90.00</span><del class="price">£95.00</del><s><span class="price">£99</span></s>'
          '<div class="was-price"><span class="price">£80</span></div><span class="price">£72.00</span>')
    assert values(r)["price"] == D("72.00")


def test_a_data_price_attribute_is_used():
    r = h('<div data-price="49.90" data-currency="EUR">Buy now</div>')
    assert values(r) == {"price": D("49.90"), "currency": "EUR"}


def test_an_ambiguous_dollar_sign_gives_a_price_but_no_currency_and_a_warning():
    r = h('<span class="price">$49.99</span>')
    assert values(r) == {"price": D("49.99")} and "ambiguous" in r.warnings[0]


def test_prose_in_a_price_class_is_not_treated_as_a_price():
    assert "price" not in values(h('<div class="price-info">Our prices include VAT and we match any price you find elsewhere, 100% guaranteed today.</div>'))


def test_numbers_in_free_text_are_never_scanned_for_prices():
    r = h("<p>Only £12.00 today! Was £20.</p><div class='deal'>just £149</div>")
    assert "price" not in values(r)


def test_title_is_not_used_as_the_name_but_the_main_heading_is_preferred():
    r = h("<h1>Site name</h1><main><h1>Real Product</h1></main>", head="<title>Brand | Online Store</title>")
    assert values(r)["name"] == "Real Product"
    assert "name" not in values(h("<p>no heading</p>", head="<title>Brand | Online Store</title>"))


@pytest.mark.parametrize("label,field", [
    ("Colour", "colour"), ("Color:", "colour"), ("Material", "material"), ("Main material", "material"),
    ("Finish", "finish"), ("Surface finish", "finish"), ("Dimensions", "dimensions"), ("Product dimensions", "dimensions"),
    ("Dimensions (W x D x H)", "dimensions"), ("Measurements", "dimensions"), ("SKU", "sku"), ("Article number", "sku"), ("Brand", "brand"),
])
def test_specs_table_labels(label, field):
    r = h(f"<table><tr><th>{label}</th><td>Some value</td></tr></table>")
    assert values(r) == {field: "Some value"} and confidences(r)[field] == "medium"


def test_specs_in_two_column_td_rows_definition_lists_and_list_items():
    r = h('<table><tr><td>Colour</td><td>Sage</td></tr></table>'
          '<dl><dt>Material</dt><dd>Steel</dd><dt>Finish</dt><dd>Matte</dd></dl>'
          '<ul><li>Brand: Vantora</li><li>Dimensions: 45 x 45 x 50 cm</li><li>Delivery: 3 days</li></ul>')
    assert values(r) == {"colour": "Sage", "material": "Steel", "finish": "Matte", "brand": "Vantora", "dimensions": "45 x 45 x 50 cm"}


def test_size_is_only_a_dimension_when_it_looks_like_one():
    assert "dimensions" not in values(h("<table><tr><th>Size</th><td>M</td></tr></table>"))
    assert values(h("<table><tr><th>Size</th><td>45 x 45 x 50 cm</td></tr></table>"))["dimensions"] == "45 x 45 x 50 cm"


def test_placeholder_values_are_not_reported():
    r = h("<table><tr><th>Colour</th><td>-</td></tr><tr><th>Material</th><td>N/A</td></tr><tr><th>Finish</th><td></td></tr></table>")
    assert r.fields == {}


def test_images_skip_logos_data_uris_and_tiny_icons():
    r = h('<main><img src="/logo.svg" class="logo"><img src="data:image/gif;base64,AAA"><img src="/tiny.png" width="16" height="16">'
          '<img src="/real.jpg"></main>')
    assert values(r)["image_url"] == "https://shop.example.test/real.jpg"


def test_images_without_a_product_context_are_not_guessed():
    assert "image_url" not in values(h('<div><img src="/random.jpg"></div>'))


def test_link_rel_image_src_and_base_href():
    r = h("", head='<base href="https://img.example.test/x/"><link rel="image_src" href="pic.jpg">')
    assert values(r)["image_url"] == "https://img.example.test/x/pic.jpg"


@pytest.mark.parametrize("text,value", [("In stock", "in_stock"), ("Out of stock", "out_of_stock"), ("Sold out", "out_of_stock"),
                                        ("Pre-order now", "preorder")])
def test_stock_wording(text, value):
    assert values(h(f'<span class="availability">{text}</span>'))["availability"] == value


def test_unrecognised_stock_wording_is_ignored_rather_than_copied():
    assert "availability" not in values(h('<span class="stock">Ships when the boat comes in</span>'))


def test_an_empty_page_gives_nothing():
    assert html_layer.extract(page()).fields == {}
