from decimal import Decimal

from product_extractor.cache import TTLCache, cache_key
from product_extractor.schema import ProductData


def product(url="https://shop.example.test/p/1", **kw):
    return ProductData(url=url, **kw)


def test_keys_ignore_case_default_ports_fragments_and_trailing_host_slash():
    base = cache_key("https://shop.example.test/p/1")
    assert cache_key("HTTPS://Shop.Example.TEST/p/1") == base
    assert cache_key("https://shop.example.test:443/p/1") == base
    assert cache_key("https://shop.example.test/p/1#reviews") == base
    assert cache_key("https://shop.example.test") == cache_key("https://shop.example.test/")
    assert cache_key("https://shop.example.test/p/1?v=2") != base            # the query can change the page
    assert cache_key("https://shop.example.test:8443/p/1") != base
    assert cache_key("http://shop.example.test/p/1") != base


def test_entries_expire_after_the_ttl():
    now = [0.0]
    c = TTLCache(60, 10, clock=lambda: now[0])
    c.put("https://a.test/", product("https://a.test/", name="A"))
    now[0] = 59
    assert c.get("https://a.test/").name == "A"
    now[0] = 61
    assert c.get("https://a.test/") is None and len(c) == 0


def test_least_recently_used_entry_is_evicted():
    c = TTLCache(60, 2)
    for u in ("https://a.test/", "https://b.test/"):
        c.put(u, product(u))
    c.get("https://a.test/")                       # a is now the most recent
    c.put("https://c.test/", product("https://c.test/"))
    assert c.get("https://b.test/") is None and c.get("https://a.test/") and c.get("https://c.test/")


def test_a_zero_ttl_disables_the_cache():
    c = TTLCache(0, 10)
    c.put("https://a.test/", product("https://a.test/"))
    assert c.get("https://a.test/") is None and len(c) == 0


def test_callers_cannot_mutate_what_is_stored():
    c = TTLCache(60, 10)
    c.put("https://a.test/", product("https://a.test/", price=Decimal("1.00")))
    c.get("https://a.test/").warnings.append("tampered")
    assert c.get("https://a.test/").warnings == []
