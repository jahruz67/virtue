"""Locations shared by the installed app and its setup worker."""
from pathlib import Path
import os
import sys


def bundle_resources() -> Path | None:
    """Locate read-only assets when launched from a native macOS app bundle."""
    executable = Path(sys.executable).resolve()
    if (sys.platform == "darwin" and executable.parent.name == "MacOS"
            and executable.parent.parent.name == "Contents"):
        return executable.parent.parent / "Resources"
    return None


def support_folder(name: str) -> Path:
    roots = [Path(__file__).resolve().parents[2], Path(sys.prefix) / "share/virtue", Path("/opt/virtue/share")]
    bundled = bundle_resources()
    if bundled is not None:
        roots.insert(0, bundled)
    if os.environ.get("VIRTUE_DATA_DIR"):
        roots.insert(0, Path(os.environ["VIRTUE_DATA_DIR"]))
    for root in roots:
        if (root / name).is_dir():
            return root / name
    raise FileNotFoundError(f"Virtue's {name} files are missing. Reinstall the complete application.")


def cache_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "Virtue"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    if not base.is_absolute():
        base = Path.home() / ".cache"
    return base / "virtue"
