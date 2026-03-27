"""Aggregate daily per-event JSON files into a single dataset for SC config generation.

Walks incoming_data/YYYY-MM-DD/{cam01,cam02,cam03}/ and produces
daily_datasets/YYYY-MM-DD/dataset.json with per-camera summary statistics.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

CAMERA_IDS = ("cam01", "cam02", "cam03")

NEAR_CAM_FEATURES = ("brightness_mean", "ndvi_mean", "change_score")
TOF_FEATURES = ("depth_mean", "depth_std", "change_score")


def _load_events(camera_dir: Path) -> list[dict]:
    """Load and sort all JSON event files from a single camera directory."""
    events = []
    if not camera_dir.is_dir():
        log.warning("Camera directory missing: %s", camera_dir)
        return events

    for fpath in sorted(camera_dir.glob("*.json")):
        try:
            data = json.loads(fpath.read_text())
            events.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Skipping %s: %s", fpath, exc)
    return events


def _linear_slope(values: list[float]) -> float:
    """Compute linear trend (slope) over an equally-spaced series."""
    if len(values) < 2:
        return 0.0
    x = np.arange(len(values), dtype=np.float64)
    y = np.array(values, dtype=np.float64)
    coeffs = np.polyfit(x, y, 1)
    return float(coeffs[0])


def _summarise_series(values: list[float]) -> dict[str, float]:
    """Compute mean, std, min, max, and linear trend for a value series."""
    arr = np.array(values, dtype=np.float64)
    return {
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "trend": _linear_slope(values),
    }


def _time_of_day_thirds(events: list[dict], feature: str) -> dict[str, float]:
    """Split events into morning/noon/evening thirds and return mean per third."""
    n = len(events)
    if n == 0:
        return {"morning": 0.0, "noon": 0.0, "evening": 0.0}

    values = [e.get("features", {}).get(feature, 0.0) for e in events]
    third = max(n // 3, 1)

    morning = values[:third]
    noon = values[third : 2 * third]
    evening = values[2 * third :]

    return {
        "morning": float(np.mean(morning)) if morning else 0.0,
        "noon": float(np.mean(noon)) if noon else 0.0,
        "evening": float(np.mean(evening)) if evening else 0.0,
    }


def _aggregate_near_camera(events: list[dict]) -> dict[str, Any]:
    """Build summary for a near camera (cam01 or cam02)."""
    if not events:
        return {"event_count": 0}

    result: dict[str, Any] = {"event_count": len(events)}
    for feat in NEAR_CAM_FEATURES:
        values = [e.get("features", {}).get(feat, 0.0) for e in events]
        result[feat] = _summarise_series(values)

    result["brightness_by_time"] = _time_of_day_thirds(events, "brightness_mean")
    return result


def _aggregate_tof_camera(events: list[dict]) -> dict[str, Any]:
    """Build summary for the ToF camera."""
    if not events:
        return {"event_count": 0}

    result: dict[str, Any] = {"event_count": len(events)}
    for feat in TOF_FEATURES:
        values = [e.get("features", {}).get(feat, 0.0) for e in events]
        result[feat] = _summarise_series(values)
    return result


def _compute_inter_camera(cam01: dict, cam02: dict) -> dict[str, float]:
    """Derive inter-camera relationship metrics from cam01 and cam02 summaries."""
    correlation = 0.0
    if cam01.get("event_count", 0) > 0 and cam02.get("event_count", 0) > 0:
        b1 = cam01.get("brightness_mean", {})
        b2 = cam02.get("brightness_mean", {})
        mean_diff = abs(b1.get("mean", 0.0) - b2.get("mean", 0.0))
        max_val = max(b1.get("mean", 1.0), b2.get("mean", 1.0), 1e-6)
        correlation = 1.0 - min(mean_diff / max_val, 1.0)

    return {"brightness_correlation": correlation}


def _detect_anomalies(events: list[dict], feature: str, sigma: float = 2.0) -> dict:
    """Count events whose feature value exceeds mean +/- sigma*std."""
    values = [e.get("features", {}).get(feature, 0.0) for e in events]
    if len(values) < 3:
        return {"count": 0, "mean_spacing": 0.0}

    arr = np.array(values, dtype=np.float64)
    mu, sd = float(np.mean(arr)), float(np.std(arr))
    if sd < 1e-9:
        return {"count": 0, "mean_spacing": 0.0}

    outlier_indices = np.where(np.abs(arr - mu) > sigma * sd)[0]
    count = len(outlier_indices)
    if count < 2:
        return {"count": count, "mean_spacing": 0.0}

    spacings = np.diff(outlier_indices).astype(np.float64)
    return {"count": count, "mean_spacing": float(np.mean(spacings))}


def build_dataset(date_str: str, base_dir: str | Path) -> dict:
    """Build the full daily dataset from incoming event JSONs.

    Parameters
    ----------
    date_str : str
        Date in YYYY-MM-DD format.
    base_dir : str or Path
        Project root containing incoming_data/, daily_datasets/, etc.

    Returns
    -------
    dict
        The aggregated dataset, also written to disk.
    """
    base = Path(base_dir)
    day_dir = base / "incoming_data" / date_str

    log.info("Building dataset for %s from %s", date_str, day_dir)

    cam01_events = _load_events(day_dir / "cam01")
    cam02_events = _load_events(day_dir / "cam02")
    cam03_events = _load_events(day_dir / "cam03")

    cam01_summary = _aggregate_near_camera(cam01_events)
    cam02_summary = _aggregate_near_camera(cam02_events)
    cam03_summary = _aggregate_tof_camera(cam03_events)

    all_events = cam01_events + cam02_events
    global_brightness = [
        e.get("features", {}).get("brightness_mean", 0.0) for e in all_events
    ]
    global_variance = _summarise_series(global_brightness) if global_brightness else {}

    inter_camera = _compute_inter_camera(cam01_summary, cam02_summary)
    anomalies = _detect_anomalies(all_events, "change_score")

    dataset = {
        "date": date_str,
        "cam01": cam01_summary,
        "cam02": cam02_summary,
        "cam03": cam03_summary,
        "global": {
            "brightness_variance": global_variance,
            "inter_camera": inter_camera,
            "anomalies": anomalies,
            "total_events": len(all_events) + len(cam03_events),
        },
    }

    out_dir = base / "daily_datasets" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "dataset.json"
    out_path.write_text(json.dumps(dataset, indent=2))
    log.info("Wrote dataset to %s", out_path)

    return dataset
