"""Parameter definitions for all 4 SuperCollider modules.

Each module is described by an ordered list of parameter dicts with:
  - name: Python/JSON config key
  - sc_arg: SuperCollider SynthDef argument name
  - min / max / step / default: slider range
  - label: human-readable label for the GUI
"""
from __future__ import annotations


MULTI_FX_ALGO_INFO: list[dict[str, str]] = [
    {"name": "Hall Reverb",
     "p1": "Room Size", "p2": "Damping", "p3": "Pre-delay"},
    {"name": "Ping-Pong Delay",
     "p1": "Delay Time", "p2": "Decay Time", "p3": "Stereo Cross"},
    {"name": "Tape Echo",
     "p1": "Echo Time", "p2": "Decay Time", "p3": "Wow/Flutter"},
    {"name": "Chorus",
     "p1": "Mod Rate", "p2": "Mod Depth", "p3": "Feedback"},
    {"name": "Overdrive",
     "p1": "Drive Gain", "p2": "Tone / LPF", "p3": "Asymmetry"},
    {"name": "Freezer",
     "p1": "Freeze Thr", "p2": "(reserved)", "p3": "Spectral Blur"},
]


def get_multifx_algo_info(algo: float) -> dict[str, str]:
    """Return algo name and p1/p2/p3 labels for the current algo value.

    When algo sits between two integers, blends the names (e.g. "Hall > Ping-Pong").
    """
    idx = max(0.0, min(5.0, algo))
    lo = int(idx)
    hi = min(lo + 1, 5)
    frac = idx - lo

    lo_info = MULTI_FX_ALGO_INFO[lo]
    hi_info = MULTI_FX_ALGO_INFO[hi]

    if frac < 0.15:
        name = lo_info["name"]
        p1, p2, p3 = lo_info["p1"], lo_info["p2"], lo_info["p3"]
    elif frac > 0.85:
        name = hi_info["name"]
        p1, p2, p3 = hi_info["p1"], hi_info["p2"], hi_info["p3"]
    else:
        name = f"{lo_info['name']} > {hi_info['name']}"
        p1 = f"{lo_info['p1']} > {hi_info['p1']}"
        p2 = f"{lo_info['p2']} > {hi_info['p2']}"
        p3 = f"{lo_info['p3']} > {hi_info['p3']}"

    return {"name": name, "p1": p1, "p2": p2, "p3": p3}


GRANULAR_ALGO_INFO: list[dict[str, str]] = [
    {"name": "Haze", "desc": "Grain cloud wash (Dust triggers, slow scan, pitch jitter)"},
    {"name": "Mosaic", "desc": "Multi-speed layers (0.5x + 1x + 2x overlapping)"},
    {"name": "Tunnel", "desc": "Tight loop drone (high density, minimal jitter)"},
    {"name": "Strum", "desc": "Rhythmic cascades (Impulse triggers, even chains)"},
    {"name": "Glide", "desc": "Pitch-shifting shimmer (LFO drift, fast scan)"},
]


def get_granular_algo_info(algo: float) -> dict[str, str]:
    """Return algo name and description for the current granular algo value."""
    idx = max(0.0, min(4.0, algo))
    lo = int(idx)
    hi = min(lo + 1, 4)
    frac = idx - lo

    lo_info = GRANULAR_ALGO_INFO[lo]
    hi_info = GRANULAR_ALGO_INFO[hi]

    if frac < 0.15:
        return {"name": lo_info["name"], "desc": lo_info["desc"]}
    elif frac > 0.85:
        return {"name": hi_info["name"], "desc": hi_info["desc"]}
    else:
        return {
            "name": f"{lo_info['name']} > {hi_info['name']}",
            "desc": f"{lo_info['desc']} / {hi_info['desc']}",
        }


MODULES: dict[str, dict] = {
    "granular_sampling": {
        "synthdef": "gardenMicrocosm",
        "label": "Granular",
        "params": [
            {"name": "algo", "sc_arg": "algo",
             "min": 0.0, "max": 4.0, "step": 0.01, "default": 0.0,
             "label": "Algorithm (0-4)"},
            {"name": "grain_density", "sc_arg": "dens",
             "min": 4, "max": 80, "step": 0.5, "default": 20,
             "label": "Grain Density (Hz)"},
            {"name": "grain_duration", "sc_arg": "dur",
             "min": 0.02, "max": 0.5, "step": 0.01, "default": 0.12,
             "label": "Grain Duration (s)"},
            {"name": "pos_rate", "sc_arg": "pRate",
             "min": 0.001, "max": 0.05, "step": 0.001, "default": 0.008,
             "label": "Scan Rate"},
            {"name": "pos_jitter", "sc_arg": "pJitter",
             "min": 0.0, "max": 0.3, "step": 0.01, "default": 0.05,
             "label": "Position Jitter"},
            {"name": "rate", "sc_arg": "rt",
             "min": 0.3, "max": 2.0, "step": 0.01, "default": 0.85,
             "label": "Pitch Rate"},
            {"name": "rate_jitter", "sc_arg": "rtJitter",
             "min": 0.0, "max": 0.5, "step": 0.01, "default": 0.1,
             "label": "Pitch Jitter"},
            {"name": "pan_width", "sc_arg": "pWidth",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.8,
             "label": "Pan Width"},
            {"name": "lpf", "sc_arg": "lpfFreq",
             "min": 500, "max": 18000, "step": 50, "default": 6000,
             "label": "LPF Cutoff (Hz)"},
            {"name": "delay_mix", "sc_arg": "dlMix",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.35,
             "label": "Delay Mix"},
            {"name": "delay_time", "sc_arg": "dlTime",
             "min": 0.01, "max": 1.0, "step": 0.01, "default": 0.33,
             "label": "Delay Time (s)"},
            {"name": "feedback", "sc_arg": "fb",
             "min": 0.0, "max": 0.7, "step": 0.01, "default": 0.4,
             "label": "Feedback"},
            {"name": "reverb_mix", "sc_arg": "vbMix",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.4,
             "label": "Reverb Mix"},
            {"name": "dry_mix", "sc_arg": "drMix",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.2,
             "label": "Dry Mix"},
        ],
    },
    "spectral_resynthesis": {
        "synthdef": "gardenPanharmonium",
        "label": "Spectral Resynth",
        "params": [
            {"name": "voices_threshold", "sc_arg": "voicesThr",
             "min": 0.001, "max": 0.2, "step": 0.001, "default": 0.04,
             "label": "Voice Threshold"},
            {"name": "blur", "sc_arg": "blurAmt",
             "min": 1, "max": 64, "step": 1, "default": 24,
             "label": "Blur (bins)"},
            {"name": "warp_stretch", "sc_arg": "wStretch",
             "min": 0.5, "max": 2.0, "step": 0.01, "default": 1.0,
             "label": "Warp Stretch"},
            {"name": "warp_shift", "sc_arg": "wShift",
             "min": -8, "max": 8, "step": 0.1, "default": 0.0,
             "label": "Warp Shift"},
            {"name": "rate", "sc_arg": "rt",
             "min": 0.2, "max": 2.0, "step": 0.01, "default": 0.65,
             "label": "Playback Rate"},
            {"name": "feedback", "sc_arg": "fb",
             "min": 0.0, "max": 0.5, "step": 0.01, "default": 0.2,
             "label": "Feedback"},
            {"name": "tilt_db", "sc_arg": "tiltDb",
             "min": -8, "max": 8, "step": 0.1, "default": 0.0,
             "label": "Tilt (dB)"},
            {"name": "glide_freq", "sc_arg": "glFreq",
             "min": 500, "max": 18000, "step": 50, "default": 5500,
             "label": "Glide Cutoff (Hz)"},
            {"name": "reverb_mix", "sc_arg": "vbMix",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.42,
             "label": "Reverb Mix"},
            {"name": "reverb_room", "sc_arg": "vbRoom",
             "min": 0.1, "max": 0.99, "step": 0.01, "default": 0.82,
             "label": "Reverb Room"},
            {"name": "dry_mix", "sc_arg": "drMix",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.22,
             "label": "Dry Mix"},
        ],
    },
    "spectral_resonators": {
        "synthdef": "gardenSMR",
        "label": "Resonators",
        "params": [
            {"name": "f0", "sc_arg": "f0",
             "min": 20, "max": 4000, "step": 1, "default": 55,
             "label": "Band 0 Freq (Hz)"},
            {"name": "f1", "sc_arg": "f1",
             "min": 20, "max": 4000, "step": 1, "default": 110,
             "label": "Band 1 Freq (Hz)"},
            {"name": "f2", "sc_arg": "f2",
             "min": 20, "max": 4000, "step": 1, "default": 165,
             "label": "Band 2 Freq (Hz)"},
            {"name": "f3", "sc_arg": "f3",
             "min": 20, "max": 4000, "step": 1, "default": 220,
             "label": "Band 3 Freq (Hz)"},
            {"name": "f4", "sc_arg": "f4",
             "min": 20, "max": 4000, "step": 1, "default": 275,
             "label": "Band 4 Freq (Hz)"},
            {"name": "f5", "sc_arg": "f5",
             "min": 20, "max": 4000, "step": 1, "default": 330,
             "label": "Band 5 Freq (Hz)"},
            {"name": "rq", "sc_arg": "rq",
             "min": 0.001, "max": 0.1, "step": 0.001, "default": 0.015,
             "label": "Reciprocal Q"},
            {"name": "noise_mix", "sc_arg": "noiseAmt",
             "min": 0.0, "max": 0.5, "step": 0.01, "default": 0.2,
             "label": "Noise Excitation"},
            {"name": "rate", "sc_arg": "rt",
             "min": 0.3, "max": 1.5, "step": 0.01, "default": 0.9,
             "label": "Playback Rate"},
            {"name": "excite_gain", "sc_arg": "excGain",
             "min": 0.1, "max": 4.0, "step": 0.1, "default": 1.2,
             "label": "Excitation Gain"},
            {"name": "morph_rate", "sc_arg": "mRate",
             "min": 0.01, "max": 0.5, "step": 0.01, "default": 0.1,
             "label": "Morph Rate (Hz)"},
            {"name": "reverb_mix", "sc_arg": "vbMix",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.35,
             "label": "Reverb Mix"},
            {"name": "reverb_room", "sc_arg": "vbRoom",
             "min": 0.1, "max": 0.99, "step": 0.01, "default": 0.65,
             "label": "Reverb Room"},
            {"name": "dry_mix", "sc_arg": "drMix",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.28,
             "label": "Dry Mix"},
        ],
    },
    "advanced_effects": {
        "synthdef": "gardenMultiFX",
        "label": "Multi FX",
        "params": [
            {"name": "algo", "sc_arg": "algo",
             "min": 0.0, "max": 5.0, "step": 0.01, "default": 0.0,
             "label": "Algorithm (0-5)"},
            {"name": "mix", "sc_arg": "mix",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.5,
             "label": "Dry/Wet Mix"},
            {"name": "param1", "sc_arg": "p1",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.5,
             "label": "Macro 1"},
            {"name": "param2", "sc_arg": "p2",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.5,
             "label": "Macro 2"},
            {"name": "param3", "sc_arg": "p3",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.5,
             "label": "Macro 3"},
            {"name": "stereo_width", "sc_arg": "width",
             "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.5,
             "label": "Stereo Width"},
            {"name": "level", "sc_arg": "level",
             "min": 0.0, "max": 1.5, "step": 0.01, "default": 0.8,
             "label": "Output Level"},
            {"name": "rate", "sc_arg": "rt",
             "min": 0.3, "max": 1.5, "step": 0.01, "default": 0.8,
             "label": "Playback Rate"},
        ],
    },
}


def get_module_names() -> list[str]:
    return list(MODULES.keys())


def get_param_defs(module: str) -> list[dict]:
    return MODULES[module]["params"]


def get_sc_arg(module: str, param_name: str) -> str:
    for p in MODULES[module]["params"]:
        if p["name"] == param_name:
            return p["sc_arg"]
    return param_name
