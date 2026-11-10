"""Validated host display hints; GTK retains native rendering and input ownership."""

from __future__ import annotations

import json
import math
from pathlib import Path
import time

from .backend import BackendError


def validate_display_mode(mode: dict | None) -> dict | None:
    if mode is None:
        return None
    if not isinstance(mode, dict) or set(mode) != {"width", "height", "refresh_hz", "monitor"}:
        raise BackendError("Invalid host display settings.")
    for key in ("width", "height"):
        if type(mode[key]) is not int or not 320 <= mode[key] <= 16384:
            raise BackendError("The guest display must be between 320 and 16384 pixels on each axis.")
    rate = mode["refresh_hz"]
    if type(rate) not in (int, float) or not math.isfinite(rate) or not 1 <= rate <= 1000:
        raise BackendError("Invalid host display refresh rate.")
    monitor = mode["monitor"]
    if not isinstance(monitor, str) or len(monitor) > 256 or any(ord(c) < 32 for c in monitor):
        raise BackendError("Invalid host display monitor.")
    return dict(mode)


def apply_guest_display(runtime: Path, mode: dict, request_id: str, active, save) -> None:
    """Run in a daemon worker; never hold up boot or the Qt event loop."""
    from .guest_agent import GuestAgent

    request = {**mode, "request_id": request_id}
    deadline = time.monotonic() + 180
    launch_after = 0
    install_attempted = False
    last_error = "Windows display integration has not responded."
    while time.monotonic() < deadline and active():
        try:
            with GuestAgent(runtime / "qga.sock") as agent:
                if not active():
                    return
                if time.monotonic() >= launch_after:
                    agent.write_text("C:\\ProgramData\\Virtue\\display-request.json", json.dumps(request))
                    try:
                        agent.start_display_task()
                    except ValueError:
                        if install_attempted:
                            raise
                        install_attempted = True
                        agent.install_display_task()
                        agent.start_display_task()
                    launch_after = time.monotonic() + 15
                text = agent.read_text("C:\\ProgramData\\Virtue\\display-status.json", 16 * 1024)
            result = json.loads(text)
            if (isinstance(result, dict) and result.get("request_id") == request_id
                    and result.get("state") in {"applied", "unsupported", "error"}):
                if result["state"] == "applied":
                    validate_display_mode({key: result.get(key) for key in mode})
                if active():
                    save({**result, "requested": mode})
                return
        except (OSError, ValueError, KeyError, TypeError, BackendError) as exc:
            last_error = str(exc)
        time.sleep(min(3, max(0, deadline - time.monotonic())))
    if active():
        save({"state": "error", "request_id": request_id, "requested": mode,
              "message": "Could not match the Windows display. " + last_error})
