"""Map aggregated daily dataset features to SuperCollider module parameters.

Reads daily_datasets/YYYY-MM-DD/dataset.json and writes one JSON config per
module into module_configs/YYYY-MM-DD/.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

RENDER_DURATION = 120.0  # seconds per module render


def _lerp(value: float, in_lo: float, in_hi: float,
           out_lo: float, out_hi: float) -> float:
    """Linear interpolation from [in_lo, in_hi] to [out_lo, out_hi], clamped."""
    if in_hi - in_lo < 1e-12:
        return (out_lo + out_hi) / 2.0
    t = (value - in_lo) / (in_hi - in_lo)
    t = max(0.0, min(1.0, t))
    return out_lo + t * (out_hi - out_lo)


def _safe_get(d: dict, *keys, default: float = 0.0) -> float:
    """Nested dict lookup with fallback."""
    current: Any = d
    for k in keys:
        if isinstance(current, dict):
            current = current.get(k, default)
        else:
            return default
    return float(current) if current is not None else default


GRANULAR_PRESETS: list[tuple[float, str, dict[str, float]]] = [
    (0.00, "deep_haze", {
        "algo": 0.0,
        "grain_density": 12.0,
        "grain_duration": 0.25,
        "pos_rate": 0.003,
        "pos_jitter": 0.02,
        "rate": 0.7,
        "rate_jitter": 0.05,
        "pan_width": 0.9,
        "lpf": 3800.0,
        "delay_mix": 0.5,
        "delay_time": 0.45,
        "feedback": 0.5,
        "reverb_mix": 0.5,
        "dry_mix": 0.15,
        "amp_attack": 0.25,
        "amp_decay": 0.7,
    }),
    (0.25, "warm_mosaic", {
        "algo": 1.0,
        "grain_density": 18.0,
        "grain_duration": 0.2,
        "pos_rate": 0.006,
        "pos_jitter": 0.06,
        "rate": 0.85,
        "rate_jitter": 0.1,
        "pan_width": 0.85,
        "lpf": 5500.0,
        "delay_mix": 0.38,
        "delay_time": 0.35,
        "feedback": 0.42,
        "reverb_mix": 0.45,
        "dry_mix": 0.2,
        "amp_attack": 0.2,
        "amp_decay": 0.65,
    }),
    (0.50, "drone_tunnel", {
        "algo": 2.0,
        "grain_density": 30.0,
        "grain_duration": 0.18,
        "pos_rate": 0.004,
        "pos_jitter": 0.015,
        "rate": 0.8,
        "rate_jitter": 0.02,
        "pan_width": 0.5,
        "lpf": 4500.0,
        "delay_mix": 0.45,
        "delay_time": 0.4,
        "feedback": 0.48,
        "reverb_mix": 0.52,
        "dry_mix": 0.12,
        "amp_attack": 0.22,
        "amp_decay": 0.7,
    }),
    (0.75, "rhythmic_strum", {
        "algo": 3.0,
        "grain_density": 15.0,
        "grain_duration": 0.1,
        "pos_rate": 0.012,
        "pos_jitter": 0.04,
        "rate": 1.0,
        "rate_jitter": 0.08,
        "pan_width": 0.8,
        "lpf": 8000.0,
        "delay_mix": 0.3,
        "delay_time": 0.25,
        "feedback": 0.35,
        "reverb_mix": 0.35,
        "dry_mix": 0.3,
        "amp_attack": 0.12,
        "amp_decay": 0.55,
    }),
    (1.00, "shimmer_glide", {
        "algo": 4.0,
        "grain_density": 25.0,
        "grain_duration": 0.15,
        "pos_rate": 0.015,
        "pos_jitter": 0.07,
        "rate": 1.05,
        "rate_jitter": 0.15,
        "pan_width": 0.75,
        "lpf": 10000.0,
        "delay_mix": 0.28,
        "delay_time": 0.22,
        "feedback": 0.3,
        "reverb_mix": 0.32,
        "dry_mix": 0.32,
        "amp_attack": 0.08,
        "amp_decay": 0.5,
    }),
]


def _blend_presets(activity: float,
                   presets: list[tuple[float, str, dict[str, float]]],
                   ) -> tuple[dict[str, float], str, str, float]:
    """Interpolate between the two nearest presets on the activity axis.

    Returns (blended_params, preset_lo_name, preset_hi_name, local_t).
    """
    activity = max(0.0, min(1.0, activity))

    lo_pos, lo_name, lo_params = presets[0]
    hi_pos, hi_name, hi_params = presets[-1]
    for i in range(len(presets) - 1):
        if activity <= presets[i + 1][0]:
            lo_pos, lo_name, lo_params = presets[i]
            hi_pos, hi_name, hi_params = presets[i + 1]
            break

    span = hi_pos - lo_pos
    t = (activity - lo_pos) / span if span > 1e-12 else 0.0
    t = max(0.0, min(1.0, t))

    blended = {}
    for key in lo_params:
        blended[key] = lo_params[key] + t * (hi_params[key] - lo_params[key])

    return blended, lo_name, hi_name, t


def _granular_sampling_config(dataset: dict, date_str: str,
                               base_dir: Path) -> dict:
    """Build config for the multi-algorithm granular sampling module.

    Uses a preset-blending system: 5 curated presets (deep_haze, warm_mosaic,
    drone_tunnel, rhythmic_strum, shimmer_glide) placed on an activity
    continuum, each using a different grain algorithm.  A composite activity
    score derived from camera data selects the blend position, and the two
    nearest presets are smoothly interpolated.
    """
    cs_mean = max(
        _safe_get(dataset, "cam01", "change_score", "mean"),
        _safe_get(dataset, "cam02", "change_score", "mean"),
    )
    bright_mean = max(
        _safe_get(dataset, "cam01", "brightness_mean", "mean"),
        _safe_get(dataset, "cam02", "brightness_mean", "mean"),
    )
    ndvi_mean = max(
        _safe_get(dataset, "cam01", "ndvi_mean", "mean"),
        _safe_get(dataset, "cam02", "ndvi_mean", "mean"),
    )

    activity = 0.5 * cs_mean + 0.3 * bright_mean + 0.2 * ndvi_mean
    activity = max(0.0, min(1.0, activity))

    params, lo_name, hi_name, t = _blend_presets(activity, GRANULAR_PRESETS)

    log.info("  granular activity=%.3f  blending %s <-> %s  (t=%.2f)",
             activity, lo_name, hi_name, t)

    rounded = {}
    for key, val in params.items():
        if key == "lpf":
            rounded[key] = round(val, 1)
        elif key == "grain_density":
            rounded[key] = round(val, 2)
        else:
            rounded[key] = round(val, 4)

    return {
        "date": date_str,
        "module": "granular_sampling",
        "audio_batch_dir": str(base_dir / "audio_batches" / "granular_sampling"),
        "output_dir": str(base_dir / "renders" / date_str),
        "duration": RENDER_DURATION,
        "preset_blend": {
            "activity_score": round(activity, 4),
            "preset_lo": lo_name,
            "preset_hi": hi_name,
            "blend_t": round(t, 4),
        },
        "params": rounded,
    }


SPECTRAL_RESYNTH_PRESETS: list[tuple[float, str, dict[str, float]]] = [
    (0.00, "spectral_drone", {
        "voices_threshold": 0.08,
        "blur": 42.0,
        "warp_stretch": 1.0,
        "warp_shift": 0.0,
        "rate": 0.4,
        "feedback": 0.3,
        "tilt_db": -5.0,
        "glide_freq": 2500.0,
        "reverb_mix": 0.55,
        "reverb_room": 0.93,
        "dry_mix": 0.12,
        "amp_attack": 0.25,
        "amp_decay": 0.7,
    }),
    (0.33, "warm_partials", {
        "voices_threshold": 0.04,
        "blur": 24.0,
        "warp_stretch": 1.05,
        "warp_shift": 0.5,
        "rate": 0.65,
        "feedback": 0.2,
        "tilt_db": -1.5,
        "glide_freq": 5500.0,
        "reverb_mix": 0.42,
        "reverb_room": 0.82,
        "dry_mix": 0.22,
        "amp_attack": 0.18,
        "amp_decay": 0.6,
    }),
    (0.67, "bright_swarm", {
        "voices_threshold": 0.015,
        "blur": 10.0,
        "warp_stretch": 1.15,
        "warp_shift": 1.5,
        "rate": 0.95,
        "feedback": 0.12,
        "tilt_db": 2.5,
        "glide_freq": 10000.0,
        "reverb_mix": 0.32,
        "reverb_room": 0.7,
        "dry_mix": 0.3,
        "amp_attack": 0.12,
        "amp_decay": 0.5,
    }),
    (1.00, "crystalline", {
        "voices_threshold": 0.003,
        "blur": 3.0,
        "warp_stretch": 1.08,
        "warp_shift": 2.0,
        "rate": 1.2,
        "feedback": 0.05,
        "tilt_db": 5.0,
        "glide_freq": 14000.0,
        "reverb_mix": 0.25,
        "reverb_room": 0.58,
        "dry_mix": 0.38,
        "amp_attack": 0.06,
        "amp_decay": 0.45,
    }),
]


def _spectral_resynthesis_config(dataset: dict, date_str: str,
                                  base_dir: Path) -> dict:
    """Build config for the Panharmonium-style spectral resynthesis module.

    Uses a preset-blending system: 4 curated presets (spectral_drone,
    warm_partials, bright_swarm, crystalline) placed on a vegetation-richness
    continuum.  A composite activity score weighted toward NDVI selects the
    blend position, and the two nearest presets are smoothly interpolated.
    """
    ndvi_mean = max(
        _safe_get(dataset, "cam01", "ndvi_mean", "mean"),
        _safe_get(dataset, "cam02", "ndvi_mean", "mean"),
    )
    bright_mean = max(
        _safe_get(dataset, "cam01", "brightness_mean", "mean"),
        _safe_get(dataset, "cam02", "brightness_mean", "mean"),
    )
    cs_mean = max(
        _safe_get(dataset, "cam01", "change_score", "mean"),
        _safe_get(dataset, "cam02", "change_score", "mean"),
    )

    activity = 0.4 * ndvi_mean + 0.35 * bright_mean + 0.25 * cs_mean
    activity = max(0.0, min(1.0, activity))

    params, lo_name, hi_name, t = _blend_presets(
        activity, SPECTRAL_RESYNTH_PRESETS
    )

    log.info("  spectral_resynth activity=%.3f  blending %s <-> %s  (t=%.2f)",
             activity, lo_name, hi_name, t)

    rounded = {}
    for key, val in params.items():
        if key == "glide_freq":
            rounded[key] = round(val, 1)
        elif key in ("blur", "voices_threshold"):
            rounded[key] = round(val, 4)
        else:
            rounded[key] = round(val, 4)

    return {
        "date": date_str,
        "module": "spectral_resynthesis",
        "audio_batch_dir": str(base_dir / "audio_batches" / "spectral_resynthesis"),
        "output_dir": str(base_dir / "renders" / date_str),
        "duration": RENDER_DURATION,
        "preset_blend": {
            "activity_score": round(activity, 4),
            "preset_lo": lo_name,
            "preset_hi": hi_name,
            "blend_t": round(t, 4),
        },
        "params": rounded,
    }


HARMONIC_SCALE = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

SPECTRAL_RESONATOR_PRESETS: list[tuple[float, str, dict[str, float]]] = [
    (0.00, "deep_gong", {
        "root_freq": 55.0,
        "spread": 1.0,
        "rotate": 0.0,
        "rq": 0.003,
        "noise_mix": 0.3,
        "rate": 0.5,
        "excite_gain": 0.8,
        "morph_rate": 0.04,
        "reverb_mix": 0.55,
        "reverb_room": 0.92,
        "dry_mix": 0.1,
        "amp_attack": 0.25,
        "amp_decay": 0.7,
    }),
    (0.33, "warm_filter", {
        "root_freq": 110.0,
        "spread": 2.0,
        "rotate": 2.0,
        "rq": 0.06,
        "noise_mix": 0.12,
        "rate": 0.75,
        "excite_gain": 1.5,
        "morph_rate": 0.1,
        "reverb_mix": 0.42,
        "reverb_room": 0.78,
        "dry_mix": 0.22,
        "amp_attack": 0.18,
        "amp_decay": 0.6,
    }),
    (0.67, "spectral_chord", {
        "root_freq": 165.0,
        "spread": 3.0,
        "rotate": 4.0,
        "rq": 0.015,
        "noise_mix": 0.2,
        "rate": 0.9,
        "excite_gain": 1.2,
        "morph_rate": 0.2,
        "reverb_mix": 0.35,
        "reverb_room": 0.65,
        "dry_mix": 0.28,
        "amp_attack": 0.12,
        "amp_decay": 0.5,
    }),
    (1.00, "crystalline_ring", {
        "root_freq": 220.0,
        "spread": 2.0,
        "rotate": 3.0,
        "rq": 0.005,
        "noise_mix": 0.25,
        "rate": 1.1,
        "excite_gain": 1.0,
        "morph_rate": 0.3,
        "reverb_mix": 0.28,
        "reverb_room": 0.55,
        "dry_mix": 0.35,
        "amp_attack": 0.08,
        "amp_decay": 0.45,
    }),
]


def _resolve_band_freqs(root_freq: float, spread: float,
                        rotate: float) -> list[float]:
    """Resolve 6 band frequencies from harmonic scale + rotate + spread."""
    spread_int = max(1, round(spread))
    rotate_int = round(rotate) % len(HARMONIC_SCALE)
    freqs = []
    for i in range(6):
        idx = (rotate_int + i * spread_int) % len(HARMONIC_SCALE)
        freqs.append(round(root_freq * HARMONIC_SCALE[idx], 2))
    return freqs


def _spectral_resonators_config(dataset: dict, date_str: str,
                                 base_dir: Path) -> dict:
    """Build config for the SMR-style spectral resonators module.

    Uses a preset-blending system: 4 curated presets (deep_gong, warm_filter,
    spectral_chord, crystalline_ring) placed on a garden-activity continuum.
    A composite activity score from camera data selects the blend position,
    and the two nearest presets are smoothly interpolated.

    After blending, 6 band frequencies are resolved from the harmonic scale
    using root_freq, spread, and rotate.
    """
    cs_mean = max(
        _safe_get(dataset, "cam01", "change_score", "mean"),
        _safe_get(dataset, "cam02", "change_score", "mean"),
    )
    bright_mean = max(
        _safe_get(dataset, "cam01", "brightness_mean", "mean"),
        _safe_get(dataset, "cam02", "brightness_mean", "mean"),
    )
    ndvi_mean = max(
        _safe_get(dataset, "cam01", "ndvi_mean", "mean"),
        _safe_get(dataset, "cam02", "ndvi_mean", "mean"),
    )

    activity = 0.35 * cs_mean + 0.35 * bright_mean + 0.3 * ndvi_mean
    activity = max(0.0, min(1.0, activity))

    params, lo_name, hi_name, t = _blend_presets(
        activity, SPECTRAL_RESONATOR_PRESETS
    )

    log.info("  spectral_resonators activity=%.3f  blending %s <-> %s  (t=%.2f)",
             activity, lo_name, hi_name, t)

    band_freqs = _resolve_band_freqs(
        params["root_freq"], params["spread"], params["rotate"]
    )
    log.info("  band_freqs: %s", band_freqs)

    rounded = {}
    for key, val in params.items():
        if key in ("root_freq",):
            rounded[key] = round(val, 2)
        elif key in ("spread", "rotate"):
            rounded[key] = round(val, 2)
        else:
            rounded[key] = round(val, 4)

    return {
        "date": date_str,
        "module": "spectral_resonators",
        "audio_batch_dir": str(base_dir / "audio_batches" / "spectral_resonators"),
        "output_dir": str(base_dir / "renders" / date_str),
        "duration": RENDER_DURATION,
        "band_freqs": band_freqs,
        "preset_blend": {
            "activity_score": round(activity, 4),
            "preset_lo": lo_name,
            "preset_hi": hi_name,
            "blend_t": round(t, 4),
        },
        "params": rounded,
    }


MULTI_FX_PRESETS: list[tuple[float, str, dict[str, float]]] = [
    (0.00, "ambient_wash", {
        "algo": 0.0,
        "mix": 0.85,
        "param1": 0.9,
        "param2": 0.6,
        "param3": 0.4,
        "stereo_width": 0.8,
        "level": 0.7,
        "rate": 0.5,
        "amp_attack": 0.3,
        "amp_decay": 0.8,
    }),
    (0.33, "dub_echo", {
        "algo": 2.0,
        "mix": 0.6,
        "param1": 0.45,
        "param2": 0.55,
        "param3": 0.25,
        "stereo_width": 0.6,
        "level": 0.8,
        "rate": 0.7,
        "amp_attack": 0.2,
        "amp_decay": 0.65,
    }),
    (0.67, "wide_chorus", {
        "algo": 3.0,
        "mix": 0.5,
        "param1": 0.4,
        "param2": 0.55,
        "param3": 0.2,
        "stereo_width": 0.9,
        "level": 0.85,
        "rate": 0.85,
        "amp_attack": 0.15,
        "amp_decay": 0.55,
    }),
    (1.00, "frozen_drive", {
        "algo": 4.8,
        "mix": 0.4,
        "param1": 0.6,
        "param2": 0.5,
        "param3": 0.35,
        "stereo_width": 0.5,
        "level": 0.9,
        "rate": 1.0,
        "amp_attack": 0.1,
        "amp_decay": 0.5,
    }),
]


def _advanced_effects_config(dataset: dict, date_str: str,
                              base_dir: Path) -> dict:
    """Build config for the multi-algorithm FX module.

    Uses a preset-blending system: 4 curated presets (ambient_wash, dub_echo,
    wide_chorus, frozen_drive) placed on a garden-activity continuum.
    A composite activity score from camera data selects the blend position,
    and the two nearest presets are smoothly interpolated.
    """
    cs_mean = max(
        _safe_get(dataset, "cam01", "change_score", "mean"),
        _safe_get(dataset, "cam02", "change_score", "mean"),
    )
    bright_mean = max(
        _safe_get(dataset, "cam01", "brightness_mean", "mean"),
        _safe_get(dataset, "cam02", "brightness_mean", "mean"),
    )
    ndvi_mean = max(
        _safe_get(dataset, "cam01", "ndvi_mean", "mean"),
        _safe_get(dataset, "cam02", "ndvi_mean", "mean"),
    )

    activity = 0.35 * cs_mean + 0.35 * bright_mean + 0.3 * ndvi_mean
    activity = max(0.0, min(1.0, activity))

    params, lo_name, hi_name, t = _blend_presets(
        activity, MULTI_FX_PRESETS
    )

    log.info("  advanced_effects activity=%.3f  blending %s <-> %s  (t=%.2f)",
             activity, lo_name, hi_name, t)

    rounded = {}
    for key, val in params.items():
        rounded[key] = round(val, 4)

    return {
        "date": date_str,
        "module": "advanced_effects",
        "audio_batch_dir": str(base_dir / "audio_batches" / "advanced_effects"),
        "output_dir": str(base_dir / "renders" / date_str),
        "duration": RENDER_DURATION,
        "preset_blend": {
            "activity_score": round(activity, 4),
            "preset_lo": lo_name,
            "preset_hi": hi_name,
            "blend_t": round(t, 4),
        },
        "params": rounded,
    }


def _extract_data_features(dataset: dict) -> dict[str, float]:
    """Extract the standard feature vector from a daily dataset."""
    def _max_cam(key: str, sub: str) -> float:
        vals = []
        for cam in ("cam01", "cam02"):
            try:
                vals.append(float(dataset[cam][key][sub]))
            except (KeyError, TypeError):
                pass
        return max(vals) if vals else 0.0

    return {
        "brightness_mean": _max_cam("brightness_mean", "mean"),
        "ndvi_mean": _max_cam("ndvi_mean", "mean"),
        "change_score_mean": _max_cam("change_score", "mean"),
    }


def _try_user_preset(module: str, dataset: dict, date_str: str,
                     base_dir: Path, similarity_threshold: float = 0.15,
                     ) -> dict | None:
    """Check user_presets/ for a close match.  Returns a config dict or None."""
    presets_path = base_dir / "user_presets" / f"{module}.json"
    if not presets_path.exists():
        return None

    try:
        presets = json.loads(presets_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    if not presets:
        return None

    features = _extract_data_features(dataset)
    query = np.array([features.get(k, 0.0) for k in
                       ("brightness_mean", "ndvi_mean", "change_score_mean")])
    weights = np.array([0.35, 0.35, 0.30])

    best_preset = None
    best_dist = float("inf")
    for preset in presets:
        pf = preset.get("data_features", {})
        if not pf:
            continue
        vec = np.array([pf.get(k, 0.0) for k in
                         ("brightness_mean", "ndvi_mean", "change_score_mean")])
        dist = float(np.sqrt(np.sum(((query - vec) * weights) ** 2)))
        if dist < best_dist:
            best_dist = dist
            best_preset = preset

    if best_preset is None or best_dist > similarity_threshold:
        return None

    log.info("  %s: using user preset '%s' (dist=%.4f)",
             module, best_preset.get("name", "?"), best_dist)

    return {
        "date": date_str,
        "module": module,
        "audio_batch_dir": str(base_dir / "audio_batches" / module),
        "output_dir": str(base_dir / "renders" / date_str),
        "duration": RENDER_DURATION,
        "user_preset": {
            "name": best_preset.get("name", ""),
            "distance": round(best_dist, 4),
        },
        "params": best_preset.get("params", {}),
    }


MODULE_BUILDERS = {
    "granular_sampling": _granular_sampling_config,
    "spectral_resynthesis": _spectral_resynthesis_config,
    "spectral_resonators": _spectral_resonators_config,
    "advanced_effects": _advanced_effects_config,
}


def generate_configs(date_str: str, base_dir: str | Path) -> dict[str, Path]:
    """Generate all four module config JSONs for the given date.

    Parameters
    ----------
    date_str : str
        Date in YYYY-MM-DD format.
    base_dir : str or Path
        Project root.

    Returns
    -------
    dict mapping module name to the written config Path.
    """
    base = Path(base_dir)
    dataset_path = base / "daily_datasets" / date_str / "dataset.json"

    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    dataset = json.loads(dataset_path.read_text())

    out_dir = base / "module_configs" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}
    for name, builder in MODULE_BUILDERS.items():
        user_config = _try_user_preset(name, dataset, date_str, base)
        if user_config is not None:
            config = user_config
        else:
            config = builder(dataset, date_str, base)
        out_path = out_dir / f"{name}.json"
        out_path.write_text(json.dumps(config, indent=2))
        log.info("Wrote %s config to %s", name, out_path)
        written[name] = out_path

    return written
