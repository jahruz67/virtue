"""Process-local GTK styling for the native accelerated guest window."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


_THEME = "VirtueVM"
_SANDBOX_DATA = "/app/share:/usr/share:/usr/share/runtime/share:/run/host/user-share:/run/host/share"
_CSS = """
@import url("resource:///org/gtk/libgtk/theme/Adwaita/gtk-contained.css");
@define-color theme_selected_bg_color #245edb;
@define-color theme_selected_fg_color #ffffff;
window.background { background-color: #f5f6f8; color: #202631; }
headerbar, .titlebar {
  background-image: none; background-color: #f8f9fb; color: #202631;
  border-bottom: 1px solid #dce1e8; box-shadow: none;
}
menubar { background-color: #ffffff; color: #202631; padding: 2px 6px; }
menubar > menuitem { padding: 6px 12px; border-radius: 5px; }
menu { background-color: #ffffff; color: #202631; border: 1px solid #c6ced9; padding: 6px; }
menu menuitem { padding: 7px 12px; border-radius: 4px; }
menuitem:hover { background-color: #e8efff; color: #1949b2; }
button { background-image: none; background-color: #ffffff; color: #202631; border-color: #b8c2cf; border-radius: 6px; }
button:hover { background-color: #f0f3f8; border-color: #8d9bad; }
button:focus { outline: 2px solid #245edb; outline-offset: -2px; }
button:disabled, menuitem:disabled { color: #8b95a4; }
tooltip { background-color: #ffffff; color: #202631; border: 1px solid #b8c2cf; }
tooltip label { color: #202631; }
"""


def display_environment(runtime: Path, *, sandboxed: bool) -> dict[str, str]:
    """Return child-only overrides; cosmetic failures must not block a VM boot."""
    data = runtime / "display"
    if ":" in str(data):
        return {"GTK_THEME": "Adwaita:light"}
    temporary = None
    try:
        target = runtime
        for part in ("display", "themes", _THEME, "gtk-3.0"):
            target /= part
            if target.is_symlink():
                raise OSError("Display theme directories must not be symbolic links.")
            target.mkdir(mode=0o700, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(_CSS)
        temporary.replace(target / "gtk.css")
    except OSError:
        return {"GTK_THEME": "Adwaita:light"}
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Cosmetic cleanup must not prevent Windows from starting.
    defaults = _SANDBOX_DATA if sandboxed else (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share")
    return {"GTK_THEME": _THEME, "XDG_DATA_DIRS": f"{data}:{defaults}"}
