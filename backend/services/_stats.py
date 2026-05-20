"""Statistical helpers used by intelligence + correlation services."""
import math


def wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    """Wilson score interval lower bound.

    Returns a sample-size-aware estimate of the true success rate.  Unlike
    the naive ratio ``successes / total``, this stays well below 1.0 for
    small samples — 5/5 yields ~0.566, not 1.0.

    Args:
        successes: number of times the event of interest occurred.
        total: total trials.
        z: standard score; 1.96 gives a 95% confidence interval.

    Returns:
        A float in ``[0.0, 1.0]``.  Returns 0.0 when ``total`` is 0.
    """
    if total <= 0:
        return 0.0
    p = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = p + z2 / (2.0 * total)
    spread = z * math.sqrt(p * (1.0 - p) / total + z2 / (4.0 * total * total))
    return max(0.0, (center - spread) / denominator)
