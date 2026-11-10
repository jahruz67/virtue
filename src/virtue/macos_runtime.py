"""Read-only, checksummed native runtime supplied inside the Mac application."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re

from .domain import ValidationError
from .runtime import bundle_resources, support_folder


PROFILE = "windows-arm64-virt-triton-v1"
ROLES = ("qemu", "qemu_img", "swtpm", "firmware_code", "firmware_vars", "extractor",
         "wimlib", "xorriso", "guest_driver", "virtio_iso", "render_server", "dxmt_library")
EXECUTABLES = {"qemu", "qemu_img", "swtpm", "extractor", "wimlib", "xorriso", "render_server"}


def version_tuple(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+){1,2}", value):
        raise ValidationError("Invalid minimum macOS version in the runtime manifest.")
    parts = tuple(map(int, value.split(".")))
    return parts + (0,) * (3 - len(parts))


def load_runtime(root: Path | None = None, *, verify: bool = True) -> tuple[dict, dict[str, Path]]:
    if root is None:
        bundled = bundle_resources()
        root = (bundled.parent / "Frameworks" / "VirtueRuntime" if bundled is not None
                else support_folder("resources") / "macos-runtime")
    if root.is_symlink() or not root.is_dir():
        raise ValidationError("The native Mac VM runtime is missing. Install the complete Virtue Mac application.")
    root = root.resolve()
    manifest = root / "manifest.json"
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 2 * 1024 * 1024:
        raise ValidationError("The native Mac runtime manifest is missing or invalid.")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
            or data["schema_version"] != 1 or data.get("host_architecture") != "arm64"
            or data.get("execution_profile") != PROFILE):
        raise ValidationError("This application contains an incompatible Mac runtime profile.")
    version_tuple(data.get("minimum_macos", ""))
    if not isinstance(data.get("sources"), dict) or not data["sources"]:
        raise ValidationError("The Mac runtime must identify its component source revisions and licenses.")
    for component, record in data["sources"].items():
        if (not isinstance(record, dict) or any(not isinstance(record.get(key), str)
                or not record[key].strip() for key in ("repository", "revision", "license"))):
            raise ValidationError(f"Incomplete source revision/license record: {component}")
    files, roles = data.get("files"), data.get("paths")
    if not isinstance(files, dict) or not isinstance(roles, dict) or set(ROLES) - roles.keys():
        raise ValidationError("The Mac runtime inventory is incomplete.")
    inventory = {}
    for relative, digest in files.items():
        if not isinstance(relative, str):
            raise ValidationError("Runtime inventory paths must be strings.")
        path = PurePosixPath(relative)
        if (not isinstance(relative, str) or path.is_absolute() or ".." in path.parts
                or "\\" in relative or ":" in relative or not path.parts
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise ValidationError("The Mac runtime inventory contains an unsafe path or checksum.")
        target = root / relative
        if target.is_symlink() or not target.resolve().is_relative_to(root) or not target.is_file():
            raise ValidationError(f"The Mac runtime component is missing or unsafe: {relative}")
        if verify:
            with target.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != digest:
                raise ValidationError(f"The Mac runtime component failed verification: {relative}")
        inventory[relative] = target
    paths = {}
    for role in ROLES:
        relative = roles[role]
        if not isinstance(relative, str) or relative not in inventory:
            raise ValidationError(f"The Mac runtime does not inventory {role}.")
        paths[role] = inventory[relative]
        if role in EXECUTABLES and not os.access(paths[role], os.X_OK):
            raise ValidationError(f"The native Mac helper is not executable: {role}")
    if not any("license" in path.lower() or "notice" in path.lower() for path in inventory):
        raise ValidationError("The Mac runtime is missing its third-party notices.")
    actual_files = set()
    for target in root.rglob("*"):
        if target.is_symlink():
            raise ValidationError("The Mac runtime must not contain symbolic links.")
        if target.is_file() and target != manifest:
            actual_files.add(target.relative_to(root).as_posix())
    if actual_files != set(inventory):
        raise ValidationError("The Mac runtime contains files outside its checksummed inventory.")
    return data, paths


def bundled_tool(role: str) -> str:
    return str(load_runtime(verify=False)[1][role])


def guest_assets() -> dict:
    data, paths = load_runtime(verify=False)
    return {name: {"filename": paths[role].name, "sha256": data["files"][data["paths"][role]]}
            for name, role in (("guest", "guest_driver"), ("virtio", "virtio_iso"))}


def prepare_runtime(progress=lambda message: None, *, repair: bool = False) -> dict[str, Path]:
    if platform.system() != "Darwin" or platform.machine().lower() != "arm64":
        raise ValidationError("This Mac application requires native Apple Silicon execution.")
    progress("Verifying the bundled Mac virtualization and DirectX components…")
    data, paths = load_runtime()
    if version_tuple(platform.mac_ver()[0]) < version_tuple(data["minimum_macos"]):
        raise ValidationError(f"This runtime requires macOS {data['minimum_macos']} or later.")
    return {"guest": paths["guest_driver"], "triton": paths["guest_driver"], "virtio": paths["virtio_iso"]}
