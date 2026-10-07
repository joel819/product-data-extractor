from decimal import Decimal

import pytest

from product_extractor.normalize import (
    clean_text, currency_from, has_ambiguous_symbol, normalize_availability, parse_price,
)


@pytest.mark.parametrize("raw,expected", [
    ("89", "89"), ("89.90", "89.90"), ("€ 89,90", "89.90"), ("1,299.00", "1299.00"),
    ("1.299,00", "1299.00"), ("£1,299", "1299"), ("1 299,50 kr", "1299.50"), ("12,5", "12.5"),
    ("1.299.000", "1299000"), ("Now only $49.99!", "49.99"), (49.9, "49.9"), (0, "0"),
])
def test_parse_price(raw, expected):
    assert parse_price(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [None, "", "free", "call us", True, "1.299", "12,5000", -5, "abc"])
def test_parse_price_refuses_to_guess(raw):
    assert parse_price(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("EUR", "EUR"), ("eur", "EUR"), ("€89", "EUR"), ("£ 12", "GBP"), ("USD 12.00", "USD"),
    ("12 zł", "PLN"), ("$12", None), ("¥500", None), ("abc", None), (None, None),
])
def test_currency_from(raw, expected):
    assert currency_from(raw) == expected


def test_ambiguous_symbols_are_flagged_not_guessed():
    assert has_ambiguous_symbol("$12.00") and not has_ambiguous_symbol("USD $12.00")
    assert not has_ambiguous_symbol("€12")


@pytest.mark.parametrize("raw,expected", [
    ("https://schema.org/InStock", "in_stock"), ("http://schema.org/OutOfStock", "out_of_stock"),
    ("https://schema.org/PreOrder", "preorder"), ("SoldOut", "out_of_stock"), ("BackOrder", "backorder"),
    ("https://schema.org/LimitedAvailability", "limited"), ("Currently unavailable", "out_of_stock"),
    ("In stock", "in_stock"), ("Only 3 left", "limited"), ("Ships in 2 weeks", "Ships in 2 weeks"), (None, None), ("", None),
])
def test_normalize_availability(raw, expected):
    assert normalize_availability(raw) == expected


def test_clean_text_strips_tags_entities_and_whitespace():
    assert clean_text("  <p>Solid&nbsp;oak &amp; steel</p>\n\n<br/>frame ") == "Solid oak & steel frame"
    assert clean_text("   ") is None and clean_text(None) is None and clean_text({"a": 1}) is None
    assert clean_text("one two three four", limit=9).endswith("…")
