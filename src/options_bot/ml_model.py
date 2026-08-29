"""A tiny, dependency-free logistic-regression scorer for the ML signal filter.

Hand-rolled deliberately (no numpy/scikit-learn): the runtime target for live
paper trading is an Android tablet via Termux with real memory limits (the
README already excludes even Ruff there for this reason), so a pure-Python
dot-product-plus-sigmoid sidesteps that risk entirely rather than working
around it. Training (``research/train_signal_quality_model.py``) uses this
same ``score`` function, so there is exactly one implementation of "the
model" -- no gap between what training reports and what a backtest replay
actually filters.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SignalQualityModel:
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    stds: tuple[float, ...]
    weights: tuple[float, ...]
    bias: float
    threshold: float
    metadata: dict

    def __post_init__(self) -> None:
        lengths = {len(self.feature_names), len(self.means), len(self.stds), len(self.weights)}
        if len(lengths) != 1:
            raise ValueError("feature_names, means, stds, and weights must have the same length")

    def score(self, features: dict[str, float]) -> float:
        """Probability (0-1) that taking this signal is a good trade."""
        total = self.bias
        for name, mean, std, weight in zip(self.feature_names, self.means, self.stds, self.weights):
            if name not in features:
                raise ValueError(f"missing required feature: {name!r}")
            # A zero-variance training feature would divide by zero; treat it
            # as contributing nothing rather than crashing or producing inf.
            standardized = (features[name] - mean) / std if std else 0.0
            total += weight * standardized
        return 1.0 / (1.0 + math.exp(-total))

    def decide(self, features: dict[str, float]) -> bool:
        return self.score(features) >= self.threshold


def load(path: str | Path) -> SignalQualityModel:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return SignalQualityModel(
        feature_names=tuple(data["feature_names"]),
        means=tuple(data["means"]),
        stds=tuple(data["stds"]),
        weights=tuple(data["weights"]),
        bias=data["bias"],
        threshold=data["threshold"],
        metadata=data.get("metadata", {}),
    )


def save(model: SignalQualityModel, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "feature_names": list(model.feature_names),
        "means": list(model.means),
        "stds": list(model.stds),
        "weights": list(model.weights),
        "bias": model.bias,
        "threshold": model.threshold,
        "metadata": model.metadata,
    }
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination
