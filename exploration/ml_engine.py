"""k-NN similarity proposal engine for the Explorer GUI.

Given the current camera data features, proposes the closest user presets
using weighted Euclidean distance on normalised feature vectors.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from exploration import preset_library

log = logging.getLogger(__name__)

FEATURE_KEYS = ["brightness_mean", "ndvi_mean", "change_score_mean"]

FEATURE_WEIGHTS = np.array([0.35, 0.35, 0.30])


def _extract_vector(features: dict[str, float]) -> np.ndarray:
    return np.array([features.get(k, 0.0) for k in FEATURE_KEYS])


def propose(
    module: str,
    data_features: dict[str, float],
    top_n: int = 3,
) -> list[tuple[dict[str, Any], float]]:
    """Return up to *top_n* presets ranked by similarity to *data_features*.

    Each result is a ``(preset_dict, distance)`` tuple.  Lower distance is
    a closer match.
    """
    presets = preset_library.load_presets(module)
    if not presets:
        return []

    query = _extract_vector(data_features)

    scored: list[tuple[dict[str, Any], float]] = []
    for preset in presets:
        pf = preset.get("data_features", {})
        if not pf:
            scored.append((preset, float("inf")))
            continue
        vec = _extract_vector(pf)
        diff = (query - vec) * FEATURE_WEIGHTS
        dist = float(np.sqrt(np.sum(diff ** 2)))
        scored.append((preset, dist))

    scored.sort(key=lambda x: x[1])
    return scored[:top_n]
