import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from product_extractor.schema import DATA_FIELDS, ProductData


def test_everything_defaults_to_null():
    p = ProductData(url="https://shop.example.test/p/1")
    assert all(getattr(p, f) is None for f in DATA_FIELDS)
    assert set(p.source_method) == set(DATA_FIELDS) and not any(p.source_method.values())
    assert set(p.confidence) == set(DATA_FIELDS) and not any(p.confidence.values())
    assert p.warnings == []


def test_price_is_a_decimal_and_serialises_as_a_string():
    p = ProductData(url="https://x.test/", price=Decimal("89.00"), currency="EUR")
    assert json.loads(p.model_dump_json())["price"] == "89.00"
    assert ProductData.model_validate_json(p.model_dump_json()).price == Decimal("89.00")


def test_unknown_fields_and_bad_methods_are_rejected():
    with pytest.raises(ValidationError):
        ProductData(url="https://x.test/", colour_family="red")
    with pytest.raises(ValidationError):
        ProductData(url="https://x.test/", source_method={"name": "guess"})
    with pytest.raises(ValidationError):
        ProductData(url="https://x.test/", confidence={"name": "certain"})


def test_instances_do_not_share_their_per_field_maps():
    a, b = ProductData(url="https://a.test/"), ProductData(url="https://b.test/")
    a.source_method["name"] = "jsonld"
    assert b.source_method["name"] is None
