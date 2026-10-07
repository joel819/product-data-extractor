from tests.helpers import D, confidences, microdata, page, values


def md(body: str):
    return microdata.extract(page(body=body))


PRODUCT = '''<div itemscope itemtype="https://schema.org/Product">
  <h1 itemprop="name">Ridge Pack</h1>
  <span itemprop="brand" itemscope itemtype="https://schema.org/Brand"><span itemprop="name">Quillfern</span></span>
  <span itemprop="sku">QF-1</span><img itemprop="image" src="/p.jpg">
  <span itemprop="color">Green</span>
  <div itemprop="offers" itemscope itemtype="https://schema.org/Offer">
    <meta itemprop="priceCurrency" content="USD"><span itemprop="price" content="119.00">US$119</span>
    <link itemprop="availability" href="https://schema.org/OutOfStock">
  </div></div>'''


def test_reads_nested_brand_and_offer_scopes():
    r = md(PRODUCT)
    assert values(r) == {"name": "Ridge Pack", "brand": "Quillfern", "sku": "QF-1", "colour": "Green",
                         "image_url": "https://shop.example.test/p.jpg", "price": D("119.00"), "currency": "USD",
                         "availability": "out_of_stock"}
    assert set(confidences(r).values()) == {"high"} and r.method == "microdata"


def test_the_content_attribute_beats_visible_text_and_text_is_used_otherwise():
    r = md('<div itemscope itemtype="https://schema.org/Product"><span itemprop="name">Plain Text</span>'
           '<div itemprop="offers" itemscope itemtype="https://schema.org/Offer"><span itemprop="price">€ 12,50</span>'
           '<span itemprop="priceCurrency">EUR</span></div></div>')
    assert values(r) == {"name": "Plain Text", "price": D("12.50"), "currency": "EUR"}


def test_properties_outside_the_product_scope_are_ignored():
    r = md('<span itemprop="name">Stray</span><div itemscope itemtype="https://schema.org/Product"><span itemprop="name">Mine</span></div>'
           '<div itemscope itemtype="https://schema.org/Organization"><span itemprop="name">Not a product</span></div>')
    assert values(r) == {"name": "Mine"}


def test_a_related_product_nested_as_a_property_is_not_taken_for_the_page_product():
    r = md('<div itemscope itemtype="https://schema.org/Product"><span itemprop="name">Main</span>'
           '<div itemprop="isRelatedTo" itemscope itemtype="https://schema.org/Product"><span itemprop="name">Related</span>'
           '<div itemprop="offers" itemscope itemtype="https://schema.org/Offer"><span itemprop="price">1</span></div></div></div>')
    assert values(r) == {"name": "Main"} and r.warnings == []


def test_several_top_level_products_use_the_first_and_lower_confidence():
    r = md('<div itemscope itemtype="https://schema.org/Product"><b itemprop="name">First</b></div>'
           '<div itemscope itemtype="https://schema.org/Product"><b itemprop="name">Second</b></div>')
    assert values(r) == {"name": "First"} and confidences(r)["name"] == "medium" and "2 Product items" in r.warnings[0]


def test_http_and_https_schema_urls_both_work():
    assert values(md('<div itemscope itemtype="http://schema.org/Product"><i itemprop="name">Old style</i></div>')) == {"name": "Old style"}


def test_rdfa_lite_is_read_with_the_same_rules():
    r = md('<div vocab="https://schema.org/" typeof="Product"><span property="name">RDFa Lamp</span>'
           '<span property="brand" typeof="Brand"><span property="name">Rdfabrand</span></span>'
           '<div property="offers" typeof="Offer"><span property="price" content="9.99">9,99</span>'
           '<meta property="priceCurrency" content="EUR"><link property="availability" href="https://schema.org/InStock"></div></div>')
    assert values(r) == {"name": "RDFa Lamp", "brand": "Rdfabrand", "price": D("9.99"), "currency": "EUR", "availability": "in_stock"}


def test_pages_without_item_markup_give_nothing():
    assert md("<h1>Just a heading</h1><p>text</p>").fields == {}
