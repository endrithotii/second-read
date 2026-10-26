"""Small, dependency-free statistics: Wilson intervals, Cohen's kappa, bootstrap."""

from __future__ import annotations

import math
import random
from collections import Counter


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None, float | None]:
    if n == 0:
        return None, None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def kappa(a: list, b: list) -> float | None:
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    n = len(pairs)
    if n == 0:
        return None
    po = sum(x == y for x, y in pairs) / n
    ca, cb = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def bootstrap(stat, rows: list, reps: int = 2000, seed: int = 7) -> tuple[float | None, float | None]:
    """95% percentile interval of stat(sample) over resamples of rows."""
    if not rows:
        return None, None
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        v = stat([rows[rng.randrange(len(rows))] for _ in rows])
        if v is not None:
            vals.append(v)
    if not vals:
        return None, None
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[min(len(vals) - 1, int(0.975 * len(vals)))]


def wss_at(ranked: list[tuple[float, bool, float]], target: float = 0.95) -> float | None:
    """Work saved over sampling at a target recall (Cohen et al., 2006).
    ranked: (score, relevant, weight). Documents are read from the highest score
    down; WSS = share of (weighted) documents left unread when the target recall
    is reached, minus (1 - target)."""
    total_rel = sum(w for _, r, w in ranked if r)
    total = sum(w for _, _, w in ranked)
    if total_rel == 0:
        return None
    order = sorted(ranked, key=lambda x: -x[0])
    seen_rel = read = 0.0
    i = 0
    while i < len(order):
        # Ties are read together: a threshold cannot split equal scores.
        j = i
        while j < len(order) and order[j][0] == order[i][0]:
            j += 1
        for s, r, w in order[i:j]:
            read += w
            if r:
                seen_rel += w
        i = j
        if seen_rel / total_rel >= target:
            break
    return (total - read) / total - (1 - target)
