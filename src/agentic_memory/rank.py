"""Rerank weights (ADR 0016): score = fused score * (1 + w * importance) * recency decay.

Importance rises when a fact is recalled (`access_boost`, up to `importance_cap`) and decays in
the nightly job. Recency halves every `half_life_days` since the fact was last recalled, or since
it became true if it never was. A weight of 0 switches that term off.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Rank:
    importance_weight: float = 0.5
    half_life_days: float = 365.0
    access_boost: float = 0.1
    importance_cap: float = 1.0

    @classmethod
    def off(cls) -> "Rank":
        """Both terms disabled: the ablation baseline (ADR 0015)."""
        return cls(importance_weight=0.0, half_life_days=0.0)

    @classmethod
    def from_env(cls) -> "Rank":
        d = cls()
        return cls(
            float(os.environ.get("RANK_IMPORTANCE_WEIGHT", d.importance_weight)),
            float(os.environ.get("RANK_HALF_LIFE_DAYS", d.half_life_days)),
            float(os.environ.get("RANK_ACCESS_BOOST", d.access_boost)),
            float(os.environ.get("RANK_IMPORTANCE_CAP", d.importance_cap)),
        )

    def factor(self, importance: float, age_days: float) -> float:
        boost = 1 + self.importance_weight * importance
        if self.half_life_days <= 0:
            return boost
        return float(boost * 0.5 ** (max(age_days, 0.0) / self.half_life_days))
