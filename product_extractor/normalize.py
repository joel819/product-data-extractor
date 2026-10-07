"""Turn raw scraped strings into clean values. Deterministic lookups only: unknown input stays unknown."""
import html
import re
from decimal import Decimal, InvalidOperation

_WS = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")

# ISO 4217 codes we recognise when they appear next to a number.
ISO_CODES = frozenset(
    "USD EUR GBP CAD AUD NZD CHF JPY CNY INR SEK NOK DKK PLN CZK HUF RON BGN TRY BRL MXN ZAR "
    "AED SAR ILS KRW SGD HKD THB IDR MYR PHP VND RUB UAH NGN KES EGP ARS CLP COP PEN".split()
)
# Symbols that map to exactly one currency. '$' and '¥' are deliberately absent: they are ambiguous
# (USD/CAD/AUD/... and JPY/CNY), so a bare '$' yields no currency rather than a guess.
UNAMBIGUOUS_SYMBOLS = {
    "€": "EUR", "£": "GBP", "₹": "INR", "₩": "KRW", "₽": "RUB", "₺": "TRY",
    "₦": "NGN", "₪": "ILS", "₫": "VND", "₴": "UAH", "zł": "PLN", "Kč": "CZK",
}
AMBIGUOUS_SYMBOLS = ("$", "¥")

_AVAILABILITY = {
    "instock": "in_stock", "onlineonly": "in_stock", "instoreonly": "in_stock",
    "outofstock": "out_of_stock", "soldout": "out_of_stock",
    "preorder": "preorder", "presale": "preorder",
    "backorder": "backorder",
    "limitedavailability": "limited",
    "discontinued": "discontinued",
}
_AVAILABILITY_TEXT = (
    (re.compile(r"\b(out of stock|sold out|unavailable|not available)\b", re.I), "out_of_stock"),
    (re.compile(r"\b(pre-?order|available (for|to) pre-?order)\b", re.I), "preorder"),
    (re.compile(r"\b(back-?order)\b", re.I), "backorder"),
    (re.compile(r"\b(low stock|only \d+ left|limited (stock|availability))\b", re.I), "limited"),
    (re.compile(r"\b(in stock|available now|ready to ship|in-stock)\b", re.I), "in_stock"),
)


def clean_text(value: object, limit: int | None = None) -> str | None:
    """Strip tags, decode entities, collapse whitespace. Empty becomes None."""
    if value is None or isinstance(value, (dict, list)):
        return None
    text = html.unescape(_TAG.sub(" ", str(value)))
    text = _WS.sub(" ", text).strip()
    if not text:
        return None
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return text


def parse_price(raw: object) -> Decimal | None:
    """Parse '1,299.00', '1.299,00', '€ 89', '89.9' ... into a Decimal.

    Rules, in order: if both '.' and ',' appear, the one that appears last is the decimal separator.
    With a single kind of separator repeated, it is a thousands separator. With one separator and one to
    two digits after it, it is a decimal point. One separator followed by exactly three digits ('1,299')
    is a thousands separator only for ',' ; for '.' ('1.299') the meaning differs by locale, so no price
    is returned rather than a guess.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float, Decimal)):
        try:
            d = Decimal(str(raw))
        except InvalidOperation:
            return None
        return d if d >= 0 else None
    m = re.search(r"\d[\d.,\u00a0\u202f' ]*\d|\d", str(raw))
    if not m:
        return None
    num = re.sub(r"[\u00a0\u202f' ]", "", m.group(0))
    if "." in num and "," in num:
        dec = "." if num.rfind(".") > num.rfind(",") else ","
        thou = "," if dec == "." else "."
        num = num.replace(thou, "").replace(dec, ".")
    else:
        sep = "," if "," in num else "." if "." in num else None
        if sep:
            head, _, tail = num.rpartition(sep)
            if num.count(sep) > 1:
                num = num.replace(sep, "")
            elif len(tail) <= 2:
                num = num.replace(sep, ".")
            elif len(tail) == 3 and sep == "," and 1 <= len(head) <= 3 and head != "0":
                num = num.replace(sep, "")
            else:
                return None
    try:
        d = Decimal(num)
    except InvalidOperation:
        return None
    return d if d >= 0 else None


def currency_from(raw: object) -> str | None:
    """An ISO code, or an unambiguous currency symbol, found in the text. Bare '$' / '¥' give None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if re.fullmatch(r"[A-Za-z]{3}", s) and s.upper() in ISO_CODES:
        return s.upper()
    for m in re.finditer(r"\b([A-Z]{3})\b", s):
        if m.group(1) in ISO_CODES:
            return m.group(1)
    for sym, code in UNAMBIGUOUS_SYMBOLS.items():
        if sym in s:
            return code
    return None


def has_ambiguous_symbol(raw: object) -> bool:
    return raw is not None and any(sym in str(raw) for sym in AMBIGUOUS_SYMBOLS) and currency_from(raw) is None


def normalize_availability(raw: object) -> str | None:
    """schema.org URLs and common storefront wording map to a small vocabulary; anything else is kept verbatim."""
    text = clean_text(raw, 120)
    if not text:
        return None
    key = re.sub(r"[^a-z]", "", text.rsplit("/", 1)[-1].lower())
    if key in _AVAILABILITY:
        return _AVAILABILITY[key]
    for pattern, value in _AVAILABILITY_TEXT:
        if pattern.search(text):
            return value
    return text
