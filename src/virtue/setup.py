"""Automatic host-runtime downloads. All interaction belongs to the Qt UI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from . import __version__
from .domain import ValidationError
from .host import find_firmware
from .runtime import cache_root, support_folder
from .store import atomic_json


def run_setup(args: list[str], *, timeout: int = 1800) -> str:
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationError(f"Automatic setup could not run {Path(args[0]).name}: {exc}") from exc
    if result.returncode:
        raise ValidationError(f"Automatic setup failed: {(result.stderr or result.stdout)[-3000:]}")
    return result.stdout


def asset_specs() -> dict:
    if sys.platform == "darwin":
        from .macos_runtime import guest_assets
        return guest_assets()
    resources = support_folder("resources")
    stack = json.loads((resources / "yttrium-stack.json").read_text(encoding="utf-8"))
    tools = json.loads((resources / "setup-assets.json").read_text(encoding="utf-8"))
    return {"host": {**stack["host"], "filename": "org.qemu.yttrium.flatpak"},
            "guest": {**stack["guest"], "filename": "YttriumDrivers.zip"},
            "virtio": tools["virtio"], "vulkan": tools["vulkan"]}


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def authorize_host():
    agent = None
    agent_path = next((path for path in (
        Path("/usr/libexec/polkit-mate-authentication-agent-1"),
        Path("/usr/lib/policykit-1-mate/polkit-mate-authentication-agent-1"),
    ) if path.is_file()), None)
    if agent_path:
        environment = dict(os.environ)
        for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "LD_LIBRARY_PATH"):
            environment.pop(key, None)
        # An already registered desktop agent keeps handling authentication;
        # this fallback exits if that session already has one.
        agent = subprocess.Popen([str(agent_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env=environment, start_new_session=True)
        time.sleep(1)
    try:
        run_setup(["pkexec", "--disable-internal-agent", "/usr/libexec/virtue-setup-host"], timeout=2400)
    finally:
        if agent is not None and agent.poll() is None:
            agent.terminate()
            try:
                agent.wait(timeout=3)
            except subprocess.TimeoutExpired:
                agent.kill()
                agent.wait(timeout=3)


def download_asset(spec: dict, cache: Path, progress=lambda message: None) -> Path:
    url, digest, name = spec["url"], spec["sha256"], spec["filename"]
    if (urlsplit(url).scheme != "https" or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or Path(name).name != name or "/" in name or "\\" in name):
        raise ValidationError("The application's download manifest is invalid.")
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = cache / (digest + "-" + name)
    if target.is_file() and not target.is_symlink() and file_digest(target) == digest:
        return target
    descriptor, temporary = tempfile.mkstemp(prefix=".download-", dir=cache)
    try:
        progress(f"Downloading {name}…")
        request = Request(url, headers={"User-Agent": f"Virtue/{__version__}"})
        with os.fdopen(descriptor, "wb") as output, urlopen(request, timeout=60) as response:
            if urlsplit(response.url).scheme != "https":
                raise ValidationError("The download redirected to an insecure address.")
            total = int(response.headers.get("Content-Length", "0"))
            received, previous = 0, -1
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                received += len(chunk)
                amount = received * 100 // total if total else received // (1024 * 1024)
                if amount != previous:
                    progress(f"Downloading {name}: {amount}{'%' if total else ' MiB'}")
                    previous = amount
            output.flush()
            os.fsync(output.fileno())
        if total and received != total:
            raise ValidationError(f"The {name} download was interrupted. Retry setup in the app.")
        if file_digest(Path(temporary)) != digest:
            raise ValidationError(f"The {name} download failed verification. Retry setup in the app.")
        os.replace(temporary, target)
        return target
    except OSError as exc:
        raise ValidationError(f"Could not download {name}: {exc}. Check the connection and retry in the app.") from exc
    finally:
        Path(temporary).unlink(missing_ok=True)


def _missing_host_components() -> list[str]:
    commands = ("flatpak", "xorriso", "wimlib-imagex", "vulkaninfo",
                "qemu-system-x86_64", "qemu-img", "swtpm")
    missing = [name for name in commands if not shutil.which(name)]
    if not (shutil.which("7zz") or shutil.which("7z")):
        missing.append("7zip")
    firmware = find_firmware()
    if not (firmware.get("code_plain") and firmware.get("vars_plain")):
        missing.append("unenrolled UEFI firmware")
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        missing.append("KVM access")
    return missing


def prepare_runtime(progress=lambda message: None, *, repair: bool = False) -> dict[str, Path]:
    if sys.platform != "linux":
        raise ValidationError("Automatic Windows setup requires an x86-64 Linux desktop.")
    missing = _missing_host_components()
    if repair or missing:
        progress("Requesting administrator access to prepare virtualization…")
        authorize_host()
        missing = _missing_host_components()
        if missing:
            raise ValidationError("Host preparation is incomplete: " + ", ".join(missing) + ". Use Retry / repair setup.")
    specs = asset_specs()
    paths = {key: download_asset(spec, cache_root(), progress) for key, spec in specs.items()}
    stamp = cache_root() / "host-runtime.json"
    installed = {}
    if stamp.is_file():
        try:
            value = json.loads(stamp.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                installed = value
        except (OSError, ValueError):
            pass
    info = subprocess.run(["flatpak", "info", "--user", "org.qemu.yttrium"],
                          capture_output=True, timeout=30, check=False)
    if repair or installed.get("sha256") != specs["host"]["sha256"] or info.returncode:
        progress("Installing the Yttrium runtime and graphics components…")
        run_setup(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
                   "https://flathub.org/repo/flathub.flatpakrepo"])
        runtime = "org.freedesktop.Platform/x86_64/" + specs["host"]["freedesktop_runtime"]
        run_setup(["flatpak", "install", "--user", "--noninteractive", "--assumeyes", "flathub", runtime])
        run_setup(["flatpak", "install", "--user", "--noninteractive", "--assumeyes", "--reinstall", str(paths["host"])])
        atomic_json(stamp, {"sha256": specs["host"]["sha256"]})
    progress("Required components are ready.")
    return paths
