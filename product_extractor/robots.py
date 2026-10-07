"""robots.txt handling following RFC 9309: the most specific user-agent group wins, and within it the
longest matching path rule wins (Allow beats Disallow on a tie). Python's urllib.robotparser takes the first
matching rule instead, which wrongly blocks pages on sites that write 'Disallow: /' before 'Allow: /p/'."""
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

# A fetch function: (robots_url) -> (status_code, text) ; raises on network failure
RobotsFetch = Callable[[str], Awaitable[tuple[int, str]]]
ROBOTS_TTL_SECONDS = 3600
PRODUCT_TOKEN = "product-data-extractor"


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow?, path pattern)


class RobotsRules:
    def __init__(self, text: str):
        groups: list[_Group] = []
        current: _Group | None = None
        collecting_agents = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            name, _, value = line.partition(":")
            name, value = name.strip().lower(), value.strip()
            if name == "user-agent":
                if current is None or not collecting_agents:
                    current = _Group()
                    groups.append(current)
                current.agents.append(value.lower())
                collecting_agents = True
            elif name in ("allow", "disallow") and current is not None:
                collecting_agents = False
                if value:  # 'Disallow:' with no value allows everything
                    current.rules.append((name == "allow", value))
            else:
                collecting_agents = False
        specific = [g for g in groups if PRODUCT_TOKEN in g.agents]
        chosen = specific or [g for g in groups if "*" in g.agents]
        self._rules = [rule for g in chosen for rule in g.rules]

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        regex = ".*".join(re.escape(part) for part in body.split("*"))
        return re.match(regex + ("$" if anchored else ""), path) is not None

    def allowed(self, path_and_query: str) -> bool:
        best_len, best_allow = -1, True
        for allow, pattern in self._rules:
            if self._matches(pattern, path_and_query):
                if len(pattern) > best_len or (len(pattern) == best_len and allow):
                    best_len, best_allow = len(pattern), allow
        return best_allow


class RobotsChecker:
    """Decides whether we may fetch a URL. Rules for a robots.txt that cannot be read:
    4xx means 'no rules' (allowed); a 5xx or a network failure means 'assume disallowed'."""

    def __init__(self, fetch: RobotsFetch, clock: Callable[[], float] = time.monotonic):
        self._fetch, self._clock = fetch, clock
        self._cache: dict[str, tuple[float, RobotsRules | bool]] = {}

    async def allowed(self, scheme: str, host_header: str, path_and_query: str) -> tuple[bool, str | None]:
        origin = f"{scheme}://{host_header}"
        entry = self._cache.get(origin)
        if entry is None or self._clock() - entry[0] > ROBOTS_TTL_SECONDS:
            entry = (self._clock(), await self._load(origin))
            self._cache[origin] = entry
        rules = entry[1]
        if rules is True:
            return True, None
        if rules is False:
            return False, "robots.txt could not be fetched (server error or network failure), so the page is not fetched"
        if rules.allowed(path_and_query):
            return True, None
        return False, "robots.txt disallows this URL for this crawler"

    async def _load(self, origin: str) -> RobotsRules | bool:
        try:
            status, text = await self._fetch(f"{origin}/robots.txt")
        except Exception:
            return False
        if status == 200:
            return RobotsRules(text)
        if 400 <= status < 500:
            return True
        return False
