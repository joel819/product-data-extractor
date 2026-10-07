"""A parsed page: the DOM, the base URL for resolving relative links, and cleaned visible text."""
import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser

_NOISE_TAGS = ["script", "style", "noscript", "template", "svg", "iframe", "nav", "footer", "form", "select", "button"]
_WS = re.compile(r"[ \t\r\f\v ]+")


class Page:
    def __init__(self, html: str, url: str):
        self.html = html
        self.url = url
        self.tree = LexborHTMLParser(html or "")
        base = self.tree.css_first("base[href]")
        self.base_url = urljoin(url, base.attributes["href"]) if base and base.attributes.get("href") else url
        self._text: str | None = None

    def abs(self, href: str | None) -> str | None:
        """Resolve a possibly relative link. Only http(s) results are returned (no data:, javascript:)."""
        if not href or not href.strip():
            return None
        full = urljoin(self.base_url, href.strip())
        return full if urlsplit(full).scheme in ("http", "https") else None

    @property
    def text(self) -> str:
        """Visible text with page chrome removed: one line per block, whitespace collapsed."""
        if self._text is None:
            tree = LexborHTMLParser(self.html or "")
            tree.strip_tags(_NOISE_TAGS)
            body = tree.body
            raw = body.text(deep=True, separator="\n", strip=True) if body is not None else ""
            lines = (_WS.sub(" ", ln).strip() for ln in raw.splitlines())
            self._text = "\n".join(ln for ln in lines if ln)
        return self._text

    @property
    def title(self) -> str | None:
        node = self.tree.css_first("title")
        return node.text(strip=True) if node else None


def squash(text: str) -> str:
    """Case, width and spacing independent form used to check that a value occurs in the page text."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())
