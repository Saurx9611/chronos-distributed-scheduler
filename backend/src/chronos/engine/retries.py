"""Full jitter with a bounded per-job exponential retry policy."""

import random


def retry_delay(attempt: int, base: float, cap: float) -> float:
    if not 1 <= attempt <= 10 or not 0 < base <= 60 or not base <= cap <= 3600:
        raise ValueError("invalid bounded retry policy")
    return random.uniform(0, min(cap, base * 2 ** (attempt - 1)))
