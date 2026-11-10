"""Take display snapshots on the GUI thread before dispatching VM work."""

from PySide6.QtGui import QGuiApplication


def host_display(vm: dict, fallback=None) -> dict | None:
    if not vm.get("match_host_display", True):
        return None
    screens = QGuiApplication.screens()
    screen = next((item for item in screens if item.name() == vm.get("display_monitor")), None)
    screen = screen or fallback or QGuiApplication.primaryScreen()
    if screen is None:
        return None
    size = screen.size()
    scale = screen.devicePixelRatio()
    return {"width": round(size.width() * scale), "height": round(size.height() * scale),
            "refresh_hz": screen.refreshRate() or 60.0, "monitor": screen.name()}
