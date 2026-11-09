"""Virtue: a Linux-first desktop for Windows virtual machines."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib

_source_project = Path(__file__).resolve().parents[2] / "pyproject.toml"
if _source_project.is_file():
    __version__ = tomllib.loads(_source_project.read_text(encoding="utf-8"))["project"]["version"]
else:
    try:
        __version__ = version("virtue-desktop")
    except PackageNotFoundError:
        __version__ = "unknown"

