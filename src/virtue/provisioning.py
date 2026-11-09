"""Prepare unattended installation media without altering the supplied ISO."""
from __future__ import annotations

import json
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from .domain import ValidationError
from .runtime import support_folder
from .setup import asset_specs, run_setup
from .store import atomic_json


def inspect_windows_iso(iso: Path, directory: Path) -> dict:
    extractor = shutil.which("7zz") or shutil.which("7z")
    if not extractor:
        raise ValidationError("The Windows media reader is missing. Use Repair setup in the app.")
    if shutil.disk_usage(directory).free < iso.stat().st_size + 2 * 1024 ** 3:
        raise ValidationError("Free more disk space to prepare the Windows installation automatically.")
    with tempfile.TemporaryDirectory(prefix=".windows-media-", dir=directory) as temporary:
        run_setup([extractor, "e", "-y", "-ssc-", "-o" + temporary, str(iso),
                   "sources/install.wim", "sources/install.esd"])
        image = next((path for path in Path(temporary).iterdir() if path.suffix.lower() in {".wim", ".esd"}), None)
        if image is None:
            raise ValidationError("This ISO does not contain a supported Windows installation image.")
        result = subprocess.run(["wimlib-imagex", "info", str(image), "--xml"],
                                capture_output=True, timeout=120, check=False)
        if result.returncode:
            raise ValidationError("The Windows image could not be read. Select an intact Windows 10/11 x64 ISO.")
        try:
            images = ET.fromstring(result.stdout).findall("IMAGE")
        except ET.ParseError as exc:
            raise ValidationError("The Windows ISO's image metadata is invalid.") from exc
    candidates = [image for image in images if image.findtext("WINDOWS/ARCH") == "9"]
    if not candidates:
        raise ValidationError("Choose a Windows 10/11 x64 ISO. ARM and 32-bit images are unsupported.")
    image = next((image for image in candidates if image.findtext("WINDOWS/EDITIONID") == "Professional"), candidates[0])
    build = int(image.findtext("WINDOWS/VERSION/BUILD", "0"))
    if image.findtext("WINDOWS/VERSION/MAJOR") != "10" or build < 10240:
        raise ValidationError("This ISO is not a supported Windows 10/11 image.")
    return {"image_index": int(image.attrib["INDEX"]), "windows_version": "11" if build >= 22000 else "10",
            "locale": image.findtext("WINDOWS/LANGUAGES/DEFAULT") or image.findtext("WINDOWS/LANGUAGES/LANGUAGE") or "en-US"}


def answer_file(config, image: dict, password: str) -> str:
    template = (support_folder("resources") / "windows-unattend.xml").read_text(encoding="utf-8")
    values = {"IMAGE_INDEX": str(image["image_index"]), "LOCALE": image["locale"],
              "WINDOWS_SIZE": str(config.disk_gib * 1024 - 260 - 16 - 1024 - 1),
              "COMPUTER_NAME": "VIRTUE-" + config.id[:8].upper(), "PASSWORD": password}
    for key, value in values.items():
        template = template.replace("__" + key + "__", escape(value))
    ET.fromstring(template)
    return template


def prepare_windows(config, directory: Path, assets: dict[str, Path], progress=lambda message: None) -> None:
    if not config.auto_setup or config.installed:
        return
    target = directory / "virtue-setup.iso"
    credentials = directory / "windows-credentials.json"
    if target.is_symlink() or credentials.is_symlink():
        raise ValidationError("Automatic installation files must not be symbolic links.")
    if target.is_file() and credentials.is_file():
        return
    progress("Reading your Windows ISO and choosing its installation defaults…")
    image = inspect_windows_iso(Path(config.iso_path), directory)
    config.windows_version = image["windows_version"]
    config.drivers_iso_path = str(assets["virtio"])
    if credentials.is_file():
        password = json.loads(credentials.read_text(encoding="utf-8"))["password"]
    else:
        password = "V!" + secrets.token_urlsafe(24)
        atomic_json(credentials, {"username": "Virtue", "password": password})
    with tempfile.TemporaryDirectory(prefix=".guest-setup-", dir=directory) as temporary:
        files = Path(temporary) / "files"
        files.mkdir()
        (files / "Autounattend.xml").write_text(answer_file(config, image, password), encoding="utf-8", newline="\n")
        shutil.copy2(credentials, files / "windows-account.json")
        for name in ("Install-VirtueGuest.ps1", "Collect-VirtueGpuReport.ps1"):
            shutil.copy2(support_folder("guest") / name, files / name)
        shutil.copy2(assets["guest"], files / "YttriumDrivers.zip")
        shutil.copy2(assets["vulkan"], files / "VulkanRT.exe")
        (files / "asset-checksums.json").write_text(json.dumps({key: spec["sha256"] for key, spec in asset_specs().items()}), encoding="utf-8")
        progress("Preparing automatic Windows and driver installation…")
        staged_iso = Path(temporary) / "setup.iso"
        run_setup(["xorriso", "-as", "mkisofs", "-quiet", "-J", "-r", "-V", "VIRTUE_SETUP", "-o", str(staged_iso), str(files)])
        staged_iso.chmod(0o600)
        staged_iso.replace(target)
    atomic_json(directory / "setup-progress.json", {"state": "installing", "message": "Installing Windows automatically…"})
