from __future__ import annotations

import math
from pathlib import Path

import pytest

from options_bot.ml_model import SignalQualityModel, load, save


def test_score_matches_hand_computed_sigmoid() -> None:
    model = SignalQualityModel(
        feature_names=("a", "b"),
        means=(0.0, 0.0),
        stds=(1.0, 1.0),
        weights=(2.0, -1.0),
        bias=0.5,
        threshold=0.5,
        metadata={},
    )
    features = {"a": 1.0, "b": 1.0}
    expected = 1.0 / (1.0 + math.exp(-(0.5 + 2.0 * 1.0 + -1.0 * 1.0)))

    assert model.score(features) == pytest.approx(expected)


def test_decide_uses_threshold() -> None:
    model = SignalQualityModel(
        feature_names=("a",), means=(0.0,), stds=(1.0,), weights=(10.0,), bias=0.0, threshold=0.9, metadata={},
    )
    assert model.decide({"a": 1.0}) is True
    assert model.decide({"a": -1.0}) is False


def test_score_treats_zero_variance_feature_as_contributing_nothing() -> None:
    model = SignalQualityModel(
        feature_names=("a",), means=(5.0,), stds=(0.0,), weights=(100.0,), bias=0.0, threshold=0.5, metadata={},
    )
    # std=0 must never raise ZeroDivisionError -- the feature contributes 0.
    assert model.score({"a": 999.0}) == pytest.approx(0.5)


def test_score_raises_on_missing_feature() -> None:
    model = SignalQualityModel(
        feature_names=("a", "b"), means=(0.0, 0.0), stds=(1.0, 1.0), weights=(1.0, 1.0), bias=0.0, threshold=0.5, metadata={},
    )
    with pytest.raises(ValueError):
        model.score({"a": 1.0})


def test_constructor_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        SignalQualityModel(
            feature_names=("a", "b"), means=(0.0,), stds=(1.0, 1.0), weights=(1.0, 1.0), bias=0.0, threshold=0.5, metadata={},
        )


def test_save_then_load_round_trips_exactly(tmp_path: Path) -> None:
    model = SignalQualityModel(
        feature_names=("rsi", "atr_normalized"),
        means=(50.0, 0.02),
        stds=(10.0, 0.01),
        weights=(0.4, -0.2),
        bias=0.1,
        threshold=0.55,
        metadata={"candidate_name": "test-model", "training_row_count": 47},
    )
    target = tmp_path / "models" / "test-model.json"

    saved_path = save(model, target)
    loaded = load(saved_path)

    assert saved_path.exists()
    assert loaded == model


def test_load_rejects_a_file_with_mismatched_array_lengths(tmp_path: Path) -> None:
    import json

    target = tmp_path / "bad-model.json"
    target.write_text(
        json.dumps(
            {
                "feature_names": ["a", "b"],
                "means": [0.0],
                "stds": [1.0, 1.0],
                "weights": [1.0, 1.0],
                "bias": 0.0,
                "threshold": 0.5,
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load(target)
