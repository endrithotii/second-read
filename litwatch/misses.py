"""The counterfactual: what did the system miss?

You cannot list papers you never found, but you can estimate how many there
are. Two searches built from different terms each catch some of the relevant
papers. How much their catches overlap says how many neither caught
(capture-recapture, the Chapman form of the Lincoln-Petersen estimator).
A random sample of excluded papers, checked by a person, measures the other
kind of miss: papers found but wrongly screened out.
"""

from __future__ import annotations

import math
import random


def capture_recapture(in_primary: set[str], in_secondary: set[str]) -> dict:
    n1, n2 = len(in_primary), len(in_secondary)
    m = len(in_primary & in_secondary)
    found = len(in_primary | in_secondary)
    result = {"primary": n1, "secondary": n2, "both": m, "found": found,
              "only_secondary": sorted(in_secondary - in_primary),
              "only_primary": sorted(in_primary - in_secondary)}
    if n1 == 0 or n2 == 0:
        result.update(estimate=None, missed=None,
                      note="One search found no relevant papers, so no estimate is possible.")
        return result
    # Chapman's estimator is defined even when the overlap is zero and is less
    # biased than n1*n2/m for small counts.
    est = (n1 + 1) * (n2 + 1) / (m + 1) - 1
    var = (n1 + 1) * (n2 + 1) * (n1 - m) * (n2 - m) / ((m + 1) ** 2 * (m + 2))
    sd = math.sqrt(var)
    result.update(
        estimate=round(est, 1),
        low=max(found, round(est - 1.96 * sd)),
        high=round(est + 1.96 * sd),
        missed=max(0, round(est - found)),
        note=("Assumes the two searches find papers independently. They are built from "
              "different terms for that reason, but some dependence remains, which makes "
              "the estimate a lower bound." if m else
              "The two searches share no relevant papers, so the estimate is very uncertain."),
    )
    return result


def audit_sample(excluded_pmids: list[str], k: int, seed: str) -> list[str]:
    """A reproducible random sample of excluded papers for a person to check."""
    if not excluded_pmids:
        return []
    rng = random.Random(seed)
    return sorted(rng.sample(sorted(excluded_pmids), min(k, len(excluded_pmids))))
