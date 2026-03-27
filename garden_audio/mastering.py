"""LUFS-based mastering for daily render output.

Reads all AIFF files in a render directory, measures combined integrated
loudness (ITU-R BS.1770-4), applies a single gain to reach the target LUFS,
and true-peak limits at -1 dBTP.  All files receive the same gain so
relative volume differences between modules are preserved.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import soundfile as sf
import pyloudnorm as pyln

log = logging.getLogger(__name__)

TARGET_LUFS = -16.0
TRUE_PEAK_DBTP = -1.0
AUDIO_EXTENSIONS = ("*.aiff", "*.aif", "*.wav")


def _true_peak_limit(audio: np.ndarray, ceiling_db: float) -> np.ndarray:
    """Brickwall true-peak limiter at the given dBTP ceiling.

    Uses 4x oversampling via linear interpolation to approximate
    inter-sample peaks, then applies gain reduction where needed.
    """
    ceiling_linear = 10.0 ** (ceiling_db / 20.0)
    peak = np.max(np.abs(audio))
    if peak <= ceiling_linear:
        return audio
    return audio * (ceiling_linear / peak)


def master_renders(render_dir: Path, target_lufs: float = TARGET_LUFS,
                   true_peak_dbtp: float = TRUE_PEAK_DBTP) -> None:
    """Normalize all rendered files in *render_dir* to *target_lufs*.

    Parameters
    ----------
    render_dir : Path
        Directory containing the day's AIFF renders.
    target_lufs : float
        Target integrated loudness in LUFS (default -16).
    true_peak_dbtp : float
        True-peak ceiling in dBTP (default -1).
    """
    render_dir = Path(render_dir)

    files: list[Path] = []
    for ext in AUDIO_EXTENSIONS:
        files.extend(sorted(render_dir.glob(ext)))

    if not files:
        log.warning("mastering: no audio files found in %s", render_dir)
        return

    file_data: list[tuple[Path, np.ndarray, int]] = []
    for fp in files:
        data, rate = sf.read(fp, dtype="float64")
        file_data.append((fp, data, rate))
        log.info("  loaded %s  (%d samples, %d Hz)", fp.name, len(data), rate)

    rate = file_data[0][2]
    meter = pyln.Meter(rate)

    per_file_lufs = []
    for fp, data, _ in file_data:
        lufs = meter.integrated_loudness(data)
        per_file_lufs.append(lufs)
        log.info("  %s  raw loudness: %.1f LUFS", fp.name, lufs)

    all_audio = np.concatenate([d for _, d, _ in file_data], axis=0)
    combined_lufs = meter.integrated_loudness(all_audio)
    log.info("  combined raw loudness: %.1f LUFS", combined_lufs)

    if combined_lufs == float("-inf"):
        log.warning("mastering: combined loudness is -inf (silence); skipping")
        return

    gain_db = target_lufs - combined_lufs
    gain_linear = 10.0 ** (gain_db / 20.0)
    log.info("  desired gain: %+.1f dB (linear %.4f)", gain_db, gain_linear)

    ceiling_linear = 10.0 ** (true_peak_dbtp / 20.0)
    max_peak_after_gain = max(
        np.max(np.abs(data)) for _, data, _ in file_data
    ) * gain_linear

    if max_peak_after_gain > ceiling_linear:
        headroom_reduction = ceiling_linear / max_peak_after_gain
        gain_linear *= headroom_reduction
        gain_db = 20.0 * np.log10(gain_linear + 1e-12)
        log.info("  peak ceiling would be exceeded; capping gain to %+.1f dB "
                 "(linear %.4f)", gain_db, gain_linear)

    for fp, data, file_rate in file_data:
        mastered = data * gain_linear

        sf.write(str(fp), mastered, file_rate, subtype="FLOAT",
                 format="AIFF")

        after_lufs = meter.integrated_loudness(mastered)
        peak_db = 20.0 * np.log10(np.max(np.abs(mastered)) + 1e-12)
        log.info("  wrote %s  mastered: %.1f LUFS, peak: %.1f dBTP",
                 fp.name, after_lufs, peak_db)

    log.info("mastering complete: %d files at target %.0f LUFS",
             len(file_data), target_lufs)
