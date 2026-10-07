from tests.helpers import D, confidences, opengraph, page, values


def og(head: str, **kw):
    return opengraph.extract(page(head=head, **kw))


def test_maps_open_graph_and_product_tags():
    r = og('''<meta property="og:title" content="Weave Throw"><meta property="og:description" content="Soft &amp; heavy">
             <meta property="og:image" content="/m/x.jpg"><meta property="product:price:amount" content="64.50">
             <meta property="product:price:currency" content="GBP"><meta property="product:availability" content="in stock">
             <meta property="product:brand" content="Tessello"><meta property="product:retailer_item_id" content="TS-1">
             <meta property="product:color" content="Oat">''')
    assert values(r) == {"name": "Weave Throw", "description": "Soft & heavy", "image_url": "https://shop.example.test/m/x.jpg",
                         "brand": "Tessello", "sku": "TS-1", "colour": "Oat", "availability": "in_stock",
                         "price": D("64.50"), "currency": "GBP"}
    assert set(confidences(r).values()) == {"medium"}


def test_legacy_og_price_tags_and_the_name_attribute_form():
    r = og('<meta property="og:price:amount" content="12,50"><meta property="og:price:currency" content="EUR">'
           '<meta name="og:title" content="Named">')
    assert values(r) == {"name": "Named", "price": D("12.50"), "currency": "EUR"}


def test_a_price_without_a_currency_does_not_get_one_invented():
    assert values(og('<meta property="product:price:amount" content="10">')) == {"price": D("10")}


def test_a_currency_alone_is_not_enough_to_report_a_price():
    assert values(og('<meta property="product:price:currency" content="EUR">')) == {}


def test_empty_or_unparseable_values_are_ignored():
    r = og('<meta property="og:title" content="  "><meta property="product:price:amount" content="TBA"><meta property="og:image" content="javascript:x">')
    assert r.fields == {}


def test_first_value_wins_when_a_tag_repeats():
    r = og('<meta property="og:image" content="/first.jpg"><meta property="og:image" content="/second.jpg">')
    assert values(r)["image_url"].endswith("/first.jpg")


def test_secure_image_is_preferred_and_base_href_is_respected():
    p = page(head='<base href="https://cdn.example.test/assets/"><meta property="og:image" content="a.jpg">'
                  '<meta property="og:image:secure_url" content="https://secure.example.test/b.jpg">')
    assert values(opengraph.extract(p))["image_url"] == "https://secure.example.test/b.jpg"
    p2 = page(head='<base href="https://cdn.example.test/assets/"><meta property="og:image" content="a.jpg">')
    assert values(opengraph.extract(p2))["image_url"] == "https://cdn.example.test/assets/a.jpg"


def test_pages_without_meta_tags_give_nothing():
    assert og("").fields == {}
