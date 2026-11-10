"""Qt entry point and machine-readable diagnostics without a display dependency."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Virtue — Windows on Linux, powered by KVM")
    parser.add_argument("--data-dir", type=Path, help="Private VM storage directory")
    parser.add_argument("--diagnose", action="store_true", help="Print host diagnostics as JSON and exit")
    parser.add_argument("--version", action="version", version=f"Virtue {__version__}")
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.diagnose:
        from .host import detect_host
        print(json.dumps(detect_host().to_dict(), indent=2))
        return 0
    from PySide6.QtCore import QCoreApplication, QSettings
    from PySide6.QtWidgets import QApplication
    from .services import VMService
    from .store import VMStore
    from .ui import create_window
    from .ui.icons import app_icon
    QCoreApplication.setOrganizationName("Virtue")
    QCoreApplication.setApplicationName("virtue")
    application = QApplication(["virtue"])
    application.setApplicationDisplayName("Virtue")
    application.setDesktopFileName("virtue")
    application.setWindowIcon(app_icon())
    try:
        service = VMService(VMStore(args.data_dir))
        window = create_window(service, settings=QSettings())
        window.show()
        return application.exec()
    except Exception as exc:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Virtue could not open", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

