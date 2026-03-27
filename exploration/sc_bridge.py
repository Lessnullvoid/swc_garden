"""OSC bridge to SuperCollider for the Explorer GUI.

Manages the sclang subprocess and sends real-time parameter updates via OSC.
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from pythonosc import udp_client

log = logging.getLogger(__name__)

SCLANG_PORT = 57120
SC_APP = "/Applications/SuperCollider.app/Contents/MacOS/sclang"
EXPLORER_SCRIPT = (
    Path(__file__).resolve().parent.parent / "supercollider" / "explorer_server.scd"
)


class SCBridge:
    """Manages sclang process and OSC communication."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._client: udp_client.SimpleUDPClient | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running and self._proc is not None and self._proc.poll() is None

    def boot(self) -> None:
        """Launch sclang with the explorer server script."""
        if self.is_running:
            log.warning("sclang already running")
            return

        subprocess.run(["killall", "scsynth"], capture_output=True)
        subprocess.run(["killall", "sclang"], capture_output=True)
        time.sleep(1)

        log.info("Booting sclang: %s %s", SC_APP, EXPLORER_SCRIPT)
        self._proc = subprocess.Popen(
            [SC_APP, str(EXPLORER_SCRIPT)],
            stdout=None,
            stderr=None,
        )

        self._client = udp_client.SimpleUDPClient("127.0.0.1", SCLANG_PORT)
        self._running = True

        log.info("Waiting for sclang + scsynth to boot (8s)...")
        time.sleep(8)
        log.info("sclang bridge ready")

    def load_buffer(self, filepath: str) -> None:
        """Load an audio file into buffer 0 on the server (mono, channel 0)."""
        if not self.is_running:
            return
        log.info("Loading buffer: %s", filepath)
        self._client.send_message("/explorer/load_buffer", [filepath])
        time.sleep(1.0)

    def start_module(self, module_name: str) -> None:
        """Start a synth for the given module."""
        if not self.is_running:
            return
        log.info("Starting module: %s", module_name)
        self._client.send_message("/explorer/start", [module_name])
        time.sleep(0.3)

    def set_param(self, sc_arg: str, value: float) -> None:
        """Update a parameter on the running synth."""
        if not self.is_running:
            return
        self._client.send_message("/explorer/set", [sc_arg, float(value)])

    def stop(self) -> None:
        """Stop the current synth."""
        if not self.is_running:
            return
        self._client.send_message("/explorer/stop", [])

    def shutdown(self) -> None:
        """Quit sclang and clean up."""
        if self._client is not None:
            try:
                self._client.send_message("/explorer/quit", [])
            except Exception:
                pass

        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()

        self._running = False
        self._proc = None
        self._client = None
        log.info("SC bridge shut down")
