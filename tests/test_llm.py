import json
from decimal import Decimal as D

import httpx
import pytest

from product_extractor.config import Settings
from product_extractor.extractors.llm import LLMExtractor, build_messages, verify
from product_extractor.page import Page, norm
from tests.conftest import fixture_html

URL = "https://demo.example.test/brambleton-cask-side-table.html"


@pytest.fixture
def messy():
    return Page(fixture_html("brambleton-cask-side-table.html"), URL)


def make(reply, status=200, **kw):
    """An LLMExtractor talking to a fake chat-completions API. `reply` is the assistant's message content."""
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        if isinstance(reply, Exception):
            raise reply
        body = {"choices": [{"message": {"role": "assistant", "content": reply if isinstance(reply, str) else json.dumps(reply)}}]}
        return httpx.Response(status, json=body)

    s = Settings(_env_file=None, llm_api_key="test-key", llm_base_url="https://llm.example.test/v1", llm_model="test-model", **kw)
    return LLMExtractor(s, httpx.AsyncClient(transport=httpx.MockTransport(handler))), seen


ALL = ["name", "brand", "sku", "price", "currency", "availability", "description", "dimensions", "colour", "finish", "material"]
GOOD = {"name": "Cask Oak Side Table", "brand": "Brambleton Home", "sku": "BH-2290-OAK", "price": "149", "currency": "GBP",
        "availability": "Only 3 left in stock", "dimensions": "45 cm wide, 45 cm deep and 52 cm high",
        "material": "solid oak", "finish": "natural oil", "colour": None, "description": None}


async def test_values_that_are_on_the_page_are_accepted_and_marked_low_confidence(messy):
    llm, _ = make(GOOD)
    r = await llm.extract(messy, ALL)
    got = {k: v.value for k, v in r.fields.items()}
    assert got == {"name": "Cask Oak Side Table", "brand": "Brambleton Home", "sku": "BH-2290-OAK", "price": D("149"),
                   "currency": "GBP", "availability": "limited", "dimensions": "45 cm wide, 45 cm deep and 52 cm high",
                   "material": "solid oak", "finish": "natural oil"}
    assert {c.method for c in r.fields.values()} == {"llm"} and {c.confidence for c in r.fields.values()} == {"low"}
    assert r.warnings == []


async def test_invented_values_are_rejected_and_reported(messy):
    llm, _ = make({"name": "Cask Oak Side Table (Limited Edition)", "brand": "Acme Corp", "sku": "ZZ-999", "price": 199,
                   "currency": "USD", "dimensions": "50 x 50 x 50 cm", "material": "solid oak"})
    r = await llm.extract(messy, ALL)
    assert set(r.fields) == {"material"}                      # the one value that really is on the page
    for field in ("name", "brand", "sku", "price", "currency", "dimensions"):
        assert any(w.startswith(f"LLM value for {field} was rejected") for w in r.warnings), field


async def test_a_paraphrase_is_not_good_enough(messy):
    llm, _ = make({"availability": "Almost sold out", "finish": "oiled"})
    r = await llm.extract(messy, ["availability", "finish"])
    assert r.fields == {} and len(r.warnings) == 2


@pytest.mark.parametrize("price", [149, 149.0, "149", "149.00", "£149"])
async def test_price_spellings_of_a_number_on_the_page(messy, price):
    llm, _ = make({"price": price})
    assert (await llm.extract(messy, ["price"])).fields["price"].value == D("149")


@pytest.mark.parametrize("price", [150, "14.9", "1490", "free", None, "189.50"])
async def test_prices_that_are_not_on_the_page_are_rejected(messy, price):
    llm, _ = make({"price": price})
    assert "price" not in (await llm.extract(messy, ["price"])).fields


async def test_the_old_price_on_the_page_is_a_valid_number_but_the_prompt_asks_for_the_current_one(messy):
    """£189 does occur in the page, so verification passes it: choosing the *right* number is the model's job,
    and why LLM values are marked low confidence for human review."""
    llm, seen = make({"price": 189})
    assert (await llm.extract(messy, ["price"])).fields["price"].confidence == "low"
    assert "current selling price" in seen[0].content.decode()


async def test_only_requested_fields_are_accepted(messy):
    llm, seen = make({"name": "Cask Oak Side Table", "brand": "Brambleton Home"})
    r = await llm.extract(messy, ["name"])
    assert set(r.fields) == {"name"}
    prompt = json.loads(seen[0].content)["messages"][1]["content"]
    assert '"name"' in prompt and '"brand"' not in prompt


async def test_request_shape_and_credentials(messy):
    llm, seen = make(GOOD, llm_max_input_chars=500)
    await llm.extract(messy, ["name"])
    req = seen[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://llm.example.test/v1/chat/completions" and req.headers["authorization"] == "Bearer test-key"
    assert body["model"] == "test-model" and body["temperature"] == 0 and body["response_format"] == {"type": "json_object"}
    system, user = body["messages"][0]["content"], body["messages"][1]["content"]
    assert "Never guess" in system and "untrusted data" in system
    assert "Cask Oak Side Table" in user and len(user) < 1200      # input is capped by LLM_MAX_INPUT_CHARS


@pytest.mark.parametrize("reply", ["not json at all", "[]", '"a string"', '{"price": {"nested": 1}}', "", '{"name": 5, "sku": [1]}'])
async def test_unusable_replies_are_ignored_with_a_warning(messy, reply):
    llm, _ = make(reply)
    r = await llm.extract(messy, ["name", "price"])
    assert r.fields == {} and any("not valid JSON" in w for w in r.warnings)


async def test_code_fenced_json_is_accepted(messy):
    llm, _ = make('```json\n{"name": "Cask Oak Side Table"}\n```')
    assert (await llm.extract(messy, ["name"])).fields["name"].value == "Cask Oak Side Table"


@pytest.mark.parametrize("status", [401, 429, 500, 503])
async def test_http_errors_never_raise(messy, status):
    llm, _ = make({"name": "x"}, status=status)
    r = await llm.extract(messy, ["name"])
    assert r.fields == {} and f"HTTP {status}" in r.warnings[0]


async def test_network_failures_never_raise(messy):
    llm, _ = make(httpx.ConnectError("boom"))
    r = await llm.extract(messy, ["name"])
    assert r.fields == {} and "ConnectError" in r.warnings[0]


async def test_a_page_with_almost_no_text_makes_no_request():
    llm, seen = make(GOOD)
    r = await llm.extract(Page("<html><body><p>Hi</p></body></html>", URL), ["name"])
    assert r.fields == {} and seen == []


def test_prompt_lists_only_the_wanted_fields():
    messages = build_messages(URL, "some page text", ["price", "colour"])
    assert '"price"' in messages[1]["content"] and '"colour"' in messages[1]["content"] and '"sku"' not in messages[1]["content"]


TEXT = "The Cask table\nPrice: £149 (was £189)\nColour: Oak\nSize M available. Item no. BH-2290-OAK\nIn stock"


@pytest.mark.parametrize("field,value,ok", [
    ("sku", "BH-2290-OAK", True), ("sku", "  bh-2290-oak ", True), ("sku", "bh 2290 oak", False),   # case and spacing only
    ("sku", "BH-2291-OAK", False),
    ("colour", "Oak", True), ("colour", "M", True), ("colour", "Red", False),
    ("currency", "GBP", True), ("currency", "gbp", True), ("currency", "USD", False), ("currency", "pounds", False),
    ("availability", "In stock", True), ("availability", "Out of stock", False),
    ("name", "the cask TABLE", True), ("name", "The Cask Table Deluxe", False),
])
def test_verify(field, value, ok):
    got, why = verify(field, value, TEXT, norm(TEXT))
    assert (got is not None) is ok and (why is None) is ok


def test_a_one_letter_value_must_be_a_whole_word():
    assert verify("colour", "Q", TEXT, norm(TEXT))[0] is None        # no standalone 'Q' on the page
    assert verify("colour", "M", TEXT, norm(TEXT))[0] == "M"         # 'Size M available' has it as a word


def test_a_dollar_sign_alone_does_not_justify_usd():
    text = "Lamp $49.99"
    assert verify("currency", "USD", text, norm(text))[0] is None
    assert verify("currency", "USD", "Lamp USD 49.99")[0] == "USD"


@pytest.mark.parametrize("text,price,ok", [
    ("Delivery in 3–5 working days", 5, False),                     # a bare number is not evidence of a price
    ("45 cm wide, 52 cm high", 52, False),
    ("Item 149 in stock", 149, False),
    ("just £149", 149, True), ("149 GBP", 149, True), ("EUR 1.299,00", 1299, True), ("US$ 49.99", 49.99, True),
    ("12,50 €", 12.5, True), ("Price: £ 149", 149, True),
])
def test_a_price_must_sit_next_to_a_currency_marker(text, price, ok):
    assert (verify("price", price, text)[0] is not None) is ok


def test_tokens_must_match_whole_not_as_part_of_a_longer_token():
    assert verify("sku", "2290", "Item BH-2290-OAK")[0] is None            # inside a longer code
    assert verify("sku", "BH-2290-OAK", "Item BH-2290-OAK.")[0] == "BH-2290-OAK"
    assert verify("sku", "5", "Pack of 5")[0] is None and verify("sku", "AB", "Code AB")[0] is None   # too short for a SKU
