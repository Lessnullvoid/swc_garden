"""Process incoming Pi camera images on the Mac.

Scans incoming_data/ for JPEG + .meta.json pairs that have not yet been
processed (no matching .json event file), applies dark/flat calibration,
extracts features, and writes the JSON event that dataset_builder.py reads.

Designed to run unattended via launchd every 15 minutes.

Usage:
    python process_incoming.py
    python process_incoming.py --date 2026-03-24
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("process_incoming")

PROJECT_ROOT = Path(__file__).resolve().parent
INCOMING_DIR = PROJECT_ROOT / "incoming_data"
CALIBRATION_DIR = PROJECT_ROOT / "calibration"
CAMERA_IDS = ("cam01", "cam02", "cam03")


def _load_npy(path: Path) -> np.ndarray | None:
    if path.exists():
        return np.load(str(path)).astype(np.float32)
    return None


def _calibrate(
    frame: np.ndarray,
    dark: np.ndarray | None,
    flat: np.ndarray | None,
) -> np.ndarray:
    """Apply dark subtraction and flat field correction.

    Returns a float32 image in [0, 255] range.
    """
    img = frame.astype(np.float32)

    if dark is not None:
        if dark.shape == img.shape:
            img = img - dark
            np.clip(img, 0, None, out=img)
        else:
            log.warning(
                "Dark frame shape %s != image shape %s, skipping",
                dark.shape,
                img.shape,
            )

    if flat is not None:
        if flat.shape == img.shape:
            safe_flat = np.where(flat > 0.01, flat, 1.0)
            img = img / safe_flat
        else:
            log.warning(
                "Flat field shape %s != image shape %s, skipping",
                flat.shape,
                img.shape,
            )

    np.clip(img, 0, 255, out=img)
    return img


def _compute_brightness(img: np.ndarray) -> float:
    return float(np.mean(img) / 255.0)


def _compute_ndvi_digital(img: np.ndarray) -> float:
    """Median-split proxy NDVI from a single broadband monochrome frame."""
    median = float(np.median(img))
    nir_mask = img >= median
    red_mask = ~nir_mask

    nir_mean = float(np.mean(img[nir_mask])) if np.any(nir_mask) else 128.0
    red_mean = float(np.mean(img[red_mask])) if np.any(red_mask) else 128.0

    denom = nir_mean + red_mean
    if denom < 1e-6:
        return 0.0
    return (nir_mean - red_mean) / denom


def _compute_change_score(
    img: np.ndarray, ref_path: Path
) -> float:
    """Mean absolute difference vs. stored reference frame, normalised [0,1]."""
    img_u8 = np.clip(img, 0, 255).astype(np.uint8)

    if not ref_path.exists():
        np.save(str(ref_path), img_u8)
        return 0.0

    ref = np.load(str(ref_path))
    if ref.shape != img_u8.shape:
        np.save(str(ref_path), img_u8)
        return 0.0

    diff = cv2.absdiff(img_u8, ref)
    score = float(np.mean(diff) / 255.0)

    np.save(str(ref_path), img_u8)
    return score


def _compute_depth_change(depth: np.ndarray, ref_path: Path) -> float:
    """Change score for depth data, normalized [0,1]."""
    if not ref_path.exists():
        np.save(str(ref_path), depth)
        return 0.0
    ref = np.load(str(ref_path))
    if ref.shape != depth.shape:
        np.save(str(ref_path), depth)
        return 0.0
    diff = np.abs(depth - ref)
    max_range = max(float(np.nanmax(depth)), 1.0)
    score = float(np.nanmean(diff) / max_range)
    np.save(str(ref_path), depth)
    return score


def _find_unprocessed(day_dir: Path) -> list[tuple[Path, Path, str]]:
    """Return (data_path, meta_path, frame_type) tuples with no event JSON.

    frame_type is 'alvium' for JPEG frames or 'tof' for .depth.npy frames.
    """
    items = []
    if not day_dir.is_dir():
        return items

    for cam_dir in sorted(day_dir.iterdir()):
        if not cam_dir.is_dir():
            continue

        seen_stems = set()

        for depth_npy in sorted(cam_dir.glob("*.depth.npy")):
            stem = depth_npy.name.replace(".depth.npy", "")
            meta = cam_dir / f"{stem}.meta.json"
            event = cam_dir / f"{stem}.json"
            if meta.exists() and not event.exists():
                items.append((depth_npy, meta, "tof"))
                seen_stems.add(stem)

        for jpg in sorted(cam_dir.glob("*.jpg")):
            stem = jpg.stem
            if stem in seen_stems:
                continue
            meta = cam_dir / f"{stem}.meta.json"
            event = cam_dir / f"{stem}.json"
            if meta.exists() and not event.exists():
                items.append((jpg, meta, "alvium"))

    return items


def process_day(date_str: str) -> int:
    """Process all unprocessed images for a given date. Returns count."""
    day_dir = INCOMING_DIR / date_str
    items = _find_unprocessed(day_dir)
    if not items:
        return 0

    dark_cache: dict[str, np.ndarray | None] = {}
    flat_cache: dict[str, np.ndarray | None] = {}

    processed = 0
    for data_path, meta_path, frame_type in items:
        cam_id = data_path.parent.name
        stem = meta_path.name.replace(".meta.json", "")

        with open(meta_path) as f:
            meta = json.load(f)

        ts = meta.get("timestamp", datetime.now(timezone.utc).isoformat())
        ref_path = CALIBRATION_DIR / f"{cam_id}_ref.npy"
        event_path = data_path.parent / f"{stem}.json"

        if frame_type == "tof":
            depth = np.load(str(data_path))
            depth_mean = round(float(np.nanmean(depth)), 4)
            depth_std = round(float(np.nanstd(depth)), 4)
            change = round(_compute_depth_change(depth, ref_path), 4)

            event = {
                "timestamp": ts,
                "camera_id": cam_id,
                "camera_type": "tof",
                "features": {
                    "depth_mean": depth_mean,
                    "depth_std": depth_std,
                    "change_score": change,
                },
            }
            event_path.write_text(json.dumps(event, indent=2))
            log.info("Processed ToF %s -> depth=%.3f std=%.3f change=%.4f",
                     stem, depth_mean, depth_std, change)

        else:
            if cam_id not in dark_cache:
                dark_p = CALIBRATION_DIR / f"{cam_id}_dark.npy"
                dark_cache[cam_id] = _load_npy(dark_p)
                if dark_cache[cam_id] is None:
                    log.warning("No dark frame for %s", cam_id)

            if cam_id not in flat_cache:
                flat_p = CALIBRATION_DIR / f"{cam_id}_flat.npy"
                flat_cache[cam_id] = _load_npy(flat_p)
                if flat_cache[cam_id] is None:
                    log.warning("No flat field for %s", cam_id)

            frame = cv2.imread(str(data_path), cv2.IMREAD_GRAYSCALE)
            if frame is None:
                log.error("Failed to read %s", data_path)
                continue

            calibrated = _calibrate(
                frame, dark_cache[cam_id], flat_cache[cam_id]
            )

            features = {
                "brightness_mean": round(_compute_brightness(calibrated), 4),
                "ndvi_mean": round(_compute_ndvi_digital(calibrated), 4),
                "change_score": round(
                    _compute_change_score(calibrated, ref_path), 4
                ),
            }

            event = {
                "timestamp": ts,
                "camera_id": cam_id,
                "features": features,
            }
            event_path.write_text(json.dumps(event, indent=2))
            log.info("Processed %s -> %s", data_path.name, features)

        processed += 1

    return processed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process incoming Pi images on the Mac"
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Process a specific date (YYYY-MM-DD). Default: today + yesterday.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)

    if args.date:
        dates = [args.date]
    else:
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        dates = [today.isoformat(), yesterday.isoformat()]

    total = 0
    for d in dates:
        n = process_day(d)
        if n > 0:
            log.info("Date %s: processed %d images", d, n)
        total += n

    if total == 0:
        log.info("No new images to process")
    else:
        log.info("Total processed: %d", total)


if __name__ == "__main__":
    main()
