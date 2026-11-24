from app import worker


def test_delays_increase_and_cap():
    base = [30, 150, 750, 3750, 7200, 7200]  # 30*5^(n-1) capped at 7200
    for n, b in enumerate(base, start=1):
        d = worker.backoff_seconds(n)
        assert b <= d <= b * 1.2 + 0.001, (n, d)
    delays = [worker.backoff_seconds(n) for n in (1, 2, 3, 4)]
    assert delays == sorted(delays)


def test_retry_after_is_honoured():
    assert worker.backoff_seconds(1, retry_after=600) >= 600
