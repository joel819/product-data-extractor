import pytest

from product_extractor.robots import RobotsRules


def allowed(text, path):
    return RobotsRules(text).allowed(path)


@pytest.mark.parametrize("text,path,expected", [
    ("User-agent: *\nDisallow: /private/", "/private/x", False),
    ("User-agent: *\nDisallow: /private/", "/public/x", True),
    ("User-agent: *\nDisallow: /", "/anything", False),
    ("User-agent: *\nDisallow:", "/anything", True),                       # empty Disallow allows all
    ("User-agent: *\nDisallow: /\nAllow: /p/", "/p/1", True),             # longest match wins, whatever the order
    ("User-agent: *\nAllow: /p/\nDisallow: /", "/p/1", True),
    ("User-agent: *\nDisallow: /p/\nAllow: /", "/p/1", False),
    ("User-agent: *\nDisallow: /a\nAllow: /a", "/a", True),               # equal length: Allow wins
    ("User-agent: *\nDisallow: /*.pdf$", "/files/a.pdf", False),
    ("User-agent: *\nDisallow: /*.pdf$", "/files/a.pdf?x=1", True),       # $ anchors the end
    ("User-agent: *\nDisallow: /*?session=", "/p/1?session=abc", False),
    ("User-agent: *\nDisallow: /cart", "/cart/checkout", False),          # plain rules are prefixes
    ("User-agent: *\nDisallow: /cart", "/Cart", True),                    # paths are case-sensitive
    ("User-agent: product-data-extractor\nDisallow: /\n\nUser-agent: *\nAllow: /", "/p/1", False),
    ("User-agent: PRODUCT-DATA-EXTRACTOR\nDisallow: /", "/p/1", False),   # agent names are case-insensitive
    ("User-agent: otherbot\nDisallow: /", "/p/1", True),                   # someone else's group
    ("User-agent: otherbot\nUser-agent: *\nDisallow: /secret", "/secret", False),  # several agents, one group
    ("# comment only\n", "/p/1", True),
    ("User-agent: *  # everyone\nDisallow: /x # no\n", "/x", False),
    ("garbage\n:::\nUser-agent: *\nDisallow: /x", "/x", False),
    ("", "/anything", True),
])
def test_rfc_9309_matching(text, path, expected):
    assert allowed(text, path) is expected


def test_specific_group_replaces_the_wildcard_group_entirely():
    text = "User-agent: *\nDisallow: /\n\nUser-agent: product-data-extractor\nDisallow: /admin/"
    assert allowed(text, "/p/1") is True and allowed(text, "/admin/x") is False
