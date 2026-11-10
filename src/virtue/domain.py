"""Portable VM settings and explicit host resource validation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import re
import uuid


PRESETS = {
    "Light": {"memory_mib": 4096, "cpus": 2, "disk_gib": 64},
    "Balanced": {"memory_mib": 8192, "cpus": 4, "disk_gib": 128},
    "High Performance": {"memory_mib": 16384, "cpus": 8, "disk_gib": 256},
    "CAD / Engineering": {"memory_mib": 16384, "cpus": 8, "disk_gib": 256},
}


class ValidationError(ValueError):
    """An actionable invalid user setting."""


def automatic_resources(host_memory_mib: int, host_cpus: int, other_memory_mib: int = 0) -> dict[str, int]:
    """Choose a balanced boot allocation; validation rejects hosts below Windows minimums."""
    available = host_memory_mib - max(2048, host_memory_mib // 5) - other_memory_mib
    memory = max(4096, min(8192, available // 1024 * 1024)) if host_memory_mib else 8192
    cpus = min(4, max(2, host_cpus - 1)) if host_cpus else 4
    return {"memory_mib": memory, "cpus": cpus}


def valid_id(value: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{32}", value))


@dataclass
class VMConfig:
    id: str
    name: str
    windows_version: str = "11"
    memory_mib: int = 8192
    cpus: int = 4
    disk_gib: int = 128
    iso_path: str = ""
    drivers_iso_path: str = ""
    installed: bool = False
    graphics: str = "yttrium"
    secure_boot: bool = False
    graphics_consent: bool = True
    auto_setup: bool = False
    network: str = "user"
    created_at: str = ""
    storage_bus: str = "sata"
    virtio_network: bool = False
    machine_type: str = "q35"
    hyperv: bool = True
    schema_version: int = 1
    resource_mode: str = "manual"
    start_fullscreen: bool = False
    match_host_display: bool = True
    display_monitor: str = ""
    relative_mouse: bool = False
    grab_on_hover: bool = True
    solidworks_compatibility: bool = False

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VMConfig":
        if not isinstance(data, dict):
            raise ValidationError("VM configuration must be a JSON object.")
        allowed = {f.name for f in fields(cls)}
        extra = set(data) - allowed
        if extra:
            raise ValidationError("Unrecognized VM settings: " + ", ".join(sorted(extra)))
        try:
            value = cls(**data)
        except TypeError as exc:
            raise ValidationError(f"Incomplete VM settings: {exc}") from exc
        value.validate()
        return value

    @classmethod
    def new(cls, options: dict[str, Any]) -> "VMConfig":
        data = dict(options)
        data.setdefault("resource_mode", "manual" if {"memory_mib", "cpus", "disk_gib"} & data.keys() else "automatic")
        for key in ("id", "schema_version", "created_at", "graphics_consent"):
            data.pop(key, None)
        data["graphics"] = "yttrium"
        data["secure_boot"] = False
        data["graphics_consent"] = True
        data["auto_setup"] = True
        return cls.from_dict({"id": uuid.uuid4().hex, **data})

    def validate(self, *, host_memory_mib: int = 0, host_cpus: int = 0,
                 check_media: bool = False, other_memory_mib: int = 0) -> None:
        if not valid_id(self.id):
            raise ValidationError("Invalid VM identifier.")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 80:
            raise ValidationError("Choose a VM name between 1 and 80 characters.")
        if any(ord(c) < 32 for c in self.name):
            raise ValidationError("The VM name must not contain control characters.")
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise ValidationError("This VM uses an unsupported configuration version.")
        if self.windows_version not in ("11", "10"):
            raise ValidationError("Only Windows 11 and Windows 10 x64 guests are supported.")
        if self.resource_mode not in ("automatic", "manual"):
            raise ValidationError("Choose automatic or manual resources.")
        for key in ("memory_mib", "cpus", "disk_gib"):
            if type(getattr(self, key)) is not int:
                raise ValidationError(f"{key} must be a whole number.")
        if self.memory_mib < 4096 or self.memory_mib > 1048576:
            raise ValidationError("Allocate between 4 GiB and 1 TiB of memory.")
        if self.cpus < 2 or self.cpus > 512:
            raise ValidationError("Allocate between 2 and 512 virtual CPUs.")
        if self.disk_gib < 64 or self.disk_gib > 65536:
            raise ValidationError("Choose a disk capacity between 64 GiB and 64 TiB.")
        if host_memory_mib:
            reserve = max(2048, host_memory_mib // 5)
            available = host_memory_mib - reserve - other_memory_mib
            if self.memory_mib > available:
                raise ValidationError(f"Not enough host memory: keep {reserve // 1024} GiB for Linux "
                                      "and account for other running VMs. Choose a smaller allocation.")
        if host_cpus and self.cpus > max(2, host_cpus - 1):
            raise ValidationError("Leave at least one logical CPU for the host; reduce the virtual CPU count.")
        for key in ("installed", "secure_boot", "graphics_consent", "auto_setup", "virtio_network", "hyperv",
                    "start_fullscreen", "match_host_display", "relative_mouse", "grab_on_hover",
                    "solidworks_compatibility"):
            if type(getattr(self, key)) is not bool:
                raise ValidationError(f"{key} must be true or false.")
        if (not isinstance(self.display_monitor, str) or len(self.display_monitor) > 256
                or any(ord(c) < 32 for c in self.display_monitor)):
            raise ValidationError("Invalid display monitor name.")
        if self.graphics not in ("safe", "yttrium"):
            raise ValidationError("Unknown graphics backend.")
        if self.graphics == "yttrium" and not self.graphics_consent:
            raise ValidationError("Yttrium requires explicit experimental driver consent and a recovery checkpoint.")
        if self.network != "user" or self.storage_bus not in ("sata", "virtio"):
            raise ValidationError("Unsupported network or storage configuration.")
        if not isinstance(self.machine_type, str) or not re.fullmatch(r"(?:q35|pc-q35-[0-9]+\.[0-9]+)", self.machine_type):
            raise ValidationError("Only Q35 machine types are supported.")
        if not isinstance(self.created_at, str) or len(self.created_at) > 100:
            raise ValidationError("Invalid creation timestamp.")
        for key in ("iso_path", "drivers_iso_path"):
            value = getattr(self, key)
            if not isinstance(value, str) or "\x00" in value or "\n" in value:
                raise ValidationError("Invalid installation media path.")
            if check_media and value and not (key == "iso_path" and self.installed):
                path = Path(value)
                if not path.is_file() or path.suffix.lower() != ".iso":
                    raise ValidationError(f"Select an existing ISO file for {key}.")
        if check_media and not self.installed and not self.iso_path:
            raise ValidationError("Select your Windows installation ISO first.")

