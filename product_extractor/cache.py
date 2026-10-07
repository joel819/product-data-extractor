"""A small in-memory TTL cache keyed by normalised URL."""
import time
from collections import OrderedDict
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

from product_extractor.schema import ProductData


def cache_key(url: str) -> str:
    """Scheme and host are case-insensitive, default ports and fragments do not change the page."""
    p = urlsplit(url.strip())
    host = (p.hostname or "").lower()
    port = f":{p.port}" if p.port and p.port != (443 if p.scheme.lower() == "https" else 80) else ""
    return urlunsplit((p.scheme.lower(), host + port, p.path or "/", p.query, ""))


class TTLCache:
    def __init__(self, ttl_seconds: float, max_entries: int, clock: Callable[[], float] = time.monotonic):
        self.ttl, self.max_entries, self._clock = ttl_seconds, max_entries, clock
        self._data: OrderedDict[str, tuple[float, ProductData]] = OrderedDict()

    def get(self, url: str) -> ProductData | None:
        if self.ttl <= 0:
            return None
        key = cache_key(url)
        entry = self._data.get(key)
        if entry is None:
            return None
        if self._clock() - entry[0] > self.ttl:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return entry[1].model_copy(deep=True)

    def put(self, url: str, data: ProductData) -> None:
        if self.ttl <= 0 or self.max_entries <= 0:
            return
        key = cache_key(url)
        self._data[key] = (self._clock(), data.model_copy(deep=True))
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)
