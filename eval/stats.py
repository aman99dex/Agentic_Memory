"""Paired comparison of two systems on the same questions (ADR 0008). Stdlib + numpy."""

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Paired:
    n: int
    acc_a: float
    acc_b: float
    delta: float  # acc_b - acc_a
    ci_low: float  # 95% paired bootstrap interval of delta
    ci_high: float
    only_a: int  # questions A got right and B wrong
    only_b: int
    p_mcnemar: float


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for one accuracy; stays inside [0, 1] even for tiny n."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (centre - half, centre + half)


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact test: among disagreements, is a 50/50 split plausible?"""
    n = only_a + only_b
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(only_a, only_b) + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired(
    a: dict[str, bool], b: dict[str, bool], resamples: int = 10_000, seed: int = 0
) -> Paired:
    """Compare on the questions both systems answered (matched by question id)."""
    ids = sorted(a.keys() & b.keys())
    x = np.array([a[i] for i in ids], dtype=float)
    y = np.array([b[i] for i in ids], dtype=float)
    n = len(ids)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, n, size=(resamples, n))  # resample question ids, keep the pairing
    deltas = (y[picks] - x[picks]).mean(axis=1)
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    only_a = int(((x == 1) & (y == 0)).sum())
    only_b = int(((x == 0) & (y == 1)).sum())
    return Paired(
        n,
        float(x.mean()),
        float(y.mean()),
        float((y - x).mean()),
        float(lo),
        float(hi),
        only_a,
        only_b,
        mcnemar_exact(only_a, only_b),
    )


def kappa(a: list[bool], b: list[bool]) -> float:
    """Cohen's kappa for two yes/no raters: agreement beyond what chance alone would give."""
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if chance == 1 else (observed - chance) / (1 - chance)


def holm(pvalues: list[float]) -> list[float]:
    """Holm step-down adjustment for several comparisons, returned in the input order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted
