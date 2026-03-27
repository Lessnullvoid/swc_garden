"""End-of-day pipeline: build dataset, generate configs, invoke SuperCollider.

Intended to be called at 07:00 via launchd to process the previous day's data:
    python -m garden_audio.pipeline --date 2026-03-24
"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from garden_audio.dataset_builder import build_dataset
from garden_audio.config_generator import generate_configs
from garden_audio.mastering import master_renders

log = logging.getLogger("garden_audio.pipeline")

BASE_DIR = Path(__file__).resolve().parent.parent

RUN_DAILY_SCD = BASE_DIR / "supercollider" / "run_daily.scd"

MODULE_NAMES = [
    "granular_sampling",
    "spectral_resynthesis",
    "spectral_resonators",
    "advanced_effects",
]

AUDIO_EXTENSIONS = ("*.wav", "*.aif", "*.aiff")


def _setup_logging(log_path: Path) -> None:
    """Configure logging to both file and stderr."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


def _find_sclang() -> str:
    """Return path to sclang, checking PATH and common macOS install locations."""
    from_path = shutil.which("sclang")
    if from_path:
        return from_path

    candidates = [
        "/opt/homebrew/bin/sclang",
        "/Applications/SuperCollider.app/Contents/MacOS/sclang",
        "/Applications/SuperCollider/SuperCollider.app/Contents/MacOS/sclang",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return "sclang"


def run(date_str: str) -> None:
    """Execute the full daily pipeline for the given date."""
    log_path = BASE_DIR / "daily_datasets" / date_str / "pipeline.log"
    _setup_logging(log_path)

    log.info("=== Pipeline start for %s ===", date_str)

    log.info("Step 1: Building dataset")
    dataset = build_dataset(date_str, BASE_DIR)
    event_count = dataset.get("global", {}).get("total_events", 0)
    log.info("Dataset built: %d total events", event_count)

    if event_count == 0:
        log.warning("No events found for %s -- skipping render", date_str)
        return

    log.info("Step 2: Generating module configs")
    config_paths = generate_configs(date_str, BASE_DIR)
    for name, path in config_paths.items():
        log.info("  %s -> %s", name, path)

    renders_dir = BASE_DIR / "renders" / date_str
    renders_dir.mkdir(parents=True, exist_ok=True)

    log.info("Step 3: Validating audio source folders")
    missing_audio = False
    for module_name in MODULE_NAMES:
        batch_dir = BASE_DIR / "audio_batches" / module_name
        audio_files = []
        for ext in AUDIO_EXTENSIONS:
            audio_files.extend(batch_dir.glob(ext))
        if audio_files:
            log.info("  %s: %d source files", module_name, len(audio_files))
        else:
            log.error("  No source audio in %s -- %s will be skipped by SC",
                       batch_dir, module_name)
            missing_audio = True

    if missing_audio:
        log.warning("One or more modules have no source audio; render may be incomplete")

    log.info("Step 4: Running SuperCollider render")
    sclang = _find_sclang()
    scd_path = str(RUN_DAILY_SCD)
    cmd = [sclang, scd_path, date_str, str(BASE_DIR)]
    log.info("Command: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        log.info("sclang stdout:\n%s", result.stdout)
        if result.stderr:
            log.warning("sclang stderr:\n%s", result.stderr)
        if result.returncode != 0:
            log.error("sclang exited with code %d", result.returncode)
        else:
            log.info("SuperCollider render completed successfully")
    except FileNotFoundError:
        log.error("sclang not found at '%s'. Install SuperCollider or set path.", sclang)
    except subprocess.TimeoutExpired:
        log.error("sclang timed out after 1800 seconds")

    log.info("Step 5: Mastering renders to %d LUFS", -16)
    try:
        master_renders(renders_dir)
    except Exception:
        log.exception("Mastering failed")

    log.info("=== Pipeline complete for %s ===", date_str)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Garden audio daily pipeline"
    )
    parser.add_argument(
        "--date",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Date to process (YYYY-MM-DD). Defaults to yesterday.",
    )
    args = parser.parse_args()
    run(args.date)


if __name__ == "__main__":
    main()
