"""Preset library -- save/load/delete presets paired with data conditions.

Presets are stored per module in user_presets/<module>.json as a JSON array.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PRESETS_DIR = Path(__file__).resolve().parent.parent / "user_presets"


def _module_path(module: str) -> Path:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    return PRESETS_DIR / f"{module}.json"


def _read_all(module: str) -> list[dict[str, Any]]:
    path = _module_path(module)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _write_all(module: str, presets: list[dict[str, Any]]) -> None:
    path = _module_path(module)
    path.write_text(json.dumps(presets, indent=2))


def save_preset(
    module: str,
    name: str,
    params: dict[str, float],
    data_features: dict[str, float] | None = None,
) -> None:
    """Save a preset for the given module."""
    presets = _read_all(module)

    presets = [p for p in presets if p.get("name") != name]

    presets.append({
        "name": name,
        "module": module,
        "timestamp": datetime.now().isoformat(),
        "data_features": data_features or {},
        "params": params,
    })

    _write_all(module, presets)
    log.info("Saved preset '%s' for %s", name, module)


def load_presets(module: str) -> list[dict[str, Any]]:
    """Return all presets for a module, newest first."""
    presets = _read_all(module)
    presets.sort(key=lambda p: p.get("timestamp", ""), reverse=True)
    return presets


def delete_preset(module: str, name: str) -> bool:
    """Delete a preset by name. Returns True if found and removed."""
    presets = _read_all(module)
    before = len(presets)
    presets = [p for p in presets if p.get("name") != name]
    if len(presets) < before:
        _write_all(module, presets)
        log.info("Deleted preset '%s' from %s", name, module)
        return True
    return False


def get_preset(module: str, name: str) -> dict[str, Any] | None:
    """Return a single preset by name, or None."""
    for p in _read_all(module):
        if p.get("name") == name:
            return p
    return None
