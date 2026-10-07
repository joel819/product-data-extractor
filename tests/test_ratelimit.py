from product_extractor.ratelimit import DomainRateLimiter


class FakeTime:
    def __init__(self):
        self.now, self.slept = 100.0, []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


async def test_second_request_to_the_same_host_waits():
    t = FakeTime()
    limiter = DomainRateLimiter(1.0, clock=t.clock, sleep=t.sleep)
    await limiter.wait("a.example.test")
    await limiter.wait("a.example.test")
    await limiter.wait("a.example.test")
    assert t.slept == [1.0, 1.0]


async def test_other_hosts_and_elapsed_time_do_not_wait():
    t = FakeTime()
    limiter = DomainRateLimiter(1.0, clock=t.clock, sleep=t.sleep)
    await limiter.wait("a.example.test")
    await limiter.wait("b.example.test")
    t.now += 5
    await limiter.wait("a.example.test")
    assert t.slept == []


async def test_zero_interval_disables_waiting():
    t = FakeTime()
    limiter = DomainRateLimiter(0, clock=t.clock, sleep=t.sleep)
    for _ in range(3):
        await limiter.wait("a.example.test")
    assert t.slept == []
