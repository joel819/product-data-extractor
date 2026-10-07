"""Per-domain politeness: at least `min_interval` seconds between requests to the same host."""
import asyncio
import time
from collections.abc import Awaitable, Callable


class DomainRateLimiter:
    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.min_interval = max(0.0, min_interval)
        self._clock, self._sleep = clock, sleep
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_allowed: dict[str, float] = {}

    async def wait(self, host: str) -> None:
        if self.min_interval == 0:
            return
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:  # requests to one host queue up behind each other
            delay = self._next_allowed.get(host, 0.0) - self._clock()
            if delay > 0:
                await self._sleep(delay)
            self._next_allowed[host] = self._clock() + self.min_interval
