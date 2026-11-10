"""Pure argument construction. No shell commands or user-provided QEMU flags."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Sequence

from .backend import BackendError
from .display import validate_display_mode


def option(value: str | Path) -> str:
    """Escape QEMU's comma-separated key/value syntax (independent of shells)."""
    value = str(value)
    if "\x00" in value or "\n" in value or "\r" in value:
        raise BackendError("Paths and names must not contain control characters.")
    return value.replace(",", ",,")


def build_qemu_command(
    config: Any,
    directory: Path,
    qemu: str | Sequence[str],
    ovmf_code: str | Path,
    qmp_socket: str | Path,
    tpm_socket: str | Path,
    *,
    safe_graphics: bool = False,
    audio_backend: str | None = "pa",
    hyperv: Sequence[str] = (),
    fullscreen: bool = False,
    accelerator: str = "kvm",
    guest_agent_socket: str | Path | None = None,
    setup_iso: str | Path | None = None,
    repair_setup: bool = False,
    display_mode: dict | None = None,
) -> list[str]:
    graphics = "safe" if safe_graphics else config.graphics
    if graphics == "yttrium" and (config.secure_boot or not config.graphics_consent):
        raise BackendError("Yttrium requires explicit guest security consent and guest Secure Boot disabled.")
    if graphics not in ("safe", "yttrium"):
        raise BackendError("Unknown graphics configuration.")
    if int(config.memory_mib) < 4096 or int(config.cpus) < 2:
        raise BackendError("Windows requires at least 4 GiB RAM and two virtual CPUs.")
    display_mode = validate_display_mode(display_mode)
    if not getattr(config, "match_host_display", True):
        display_mode = None
    command = [qemu] if isinstance(qemu, str) else list(qemu)
    if accelerator not in {"kvm", "kvm,honor-guest-pat=on"}:
        raise BackendError("Only the Linux KVM accelerator is supported.")
    machine_type = getattr(config, "machine_type", "q35")
    if not re.fullmatch(r"(?:q35|pc-q35-[0-9]+\.[0-9]+)", machine_type):
        raise BackendError("Only supported Q35 machine types may be used.")
    machine = machine_type + ",smm=on,vmport=off,dump-guest-core=off"
    if graphics == "yttrium":
        machine += ",memory-backend=ram"
    solidworks = getattr(config, "solidworks_compatibility", False)
    cpu = "host"
    for flag in hyperv:
        if flag not in {"hv-relaxed", "hv-vapic", "hv-time", "hv-vpindex", "hv-synic", "hv-stimer", "hv-tlbflush", "hv-ipi"}:
            raise BackendError("Unsupported CPU optimization option.")
        if not solidworks:
            cpu += f",{flag}=on"
    if solidworks:
        # Hyper-V leaves and nested virtualization can expose a hypervisor even
        # when the standard CPUID hypervisor bit is cleared. Keep KVM acceleration.
        cpu += ",kvm=off,hypervisor=off,vmx=off,svm=off"
    command += [
        "-name", f"guest={option(config.name)}", "-uuid", str(uuid.UUID(config.id)),
        "-no-user-config", "-nodefaults", "-machine", machine, "-accel", accelerator,
        "-cpu", cpu, "-smp", f"{config.cpus},sockets=1,cores={config.cpus},threads=1",
        "-m", str(config.memory_mib),
        "-rtc", "base=localtime,clock=host", "-boot", "menu=on,strict=on",
        "-global", "driver=cfi.pflash01,property=secure,value=on",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={option(ovmf_code)}",
        "-drive", f"if=pflash,format=raw,unit=1,file={option(directory / 'OVMF_VARS.fd')}",
        "-chardev", f"socket,id=chrtpm,path={option(tpm_socket)}",
        "-tpmdev", "emulator,id=tpm0,chardev=chrtpm", "-device", "tpm-crb,tpmdev=tpm0",
        "-qmp", f"unix:{option(qmp_socket)},server=on,wait=off",
        "-device", "qemu-xhci,id=xhci",
        "-device", "usb-mouse,bus=xhci.0" if getattr(config, "relative_mouse", False) else "usb-tablet,bus=xhci.0",
        "-device", "usb-kbd,bus=xhci.0",
    ]
    if solidworks:
        # Replace QEMU's default SMBIOS branding without borrowing a real
        # machine's identity. Keep the existing VM UUID stable across boots.
        command += [
            "-smbios", "type=0,vendor=Virtue,version=1.0,uefi=on",
            "-smbios", f"type=1,manufacturer=Virtue,product=Workstation,version=1.0,serial={config.id},uuid={uuid.UUID(config.id)},family=Workstation",
            "-smbios", f"type=2,manufacturer=Virtue,product=Mainboard,version=1.0,serial={config.id}",
            "-smbios", f"type=3,manufacturer=Virtue,version=1.0,serial={config.id}",
        ]
    # JSON blockdev keeps filenames opaque, including commas and colons.
    command += ["-blockdev", json.dumps({
        "driver": "qcow2", "node-name": "osdisk", "discard": "unmap",
        "file": {"driver": "file", "filename": str(directory / "disk.qcow2"), "aio": "threads"},
    }, separators=(",", ":"))]
    # A blank disk falls through to the installer; subsequent boots prefer Windows.
    disk_boot = 0
    # Keeping a built-in Windows storage driver avoids boot failures before the
    # optional VirtIO driver ISO has been installed.
    if getattr(config, "storage_bus", "sata") == "virtio":
        command += ["-device", f"virtio-blk-pci,drive=osdisk,bootindex={disk_boot}"]
    else:
        disk_model = ",model=Virtue SSD" if solidworks else ""
        command += ["-device", "ich9-ahci,id=sata", "-device", f"ide-hd,bus=sata.0,drive=osdisk,bootindex={disk_boot}{disk_model}"]
    cd_index = 0
    if not config.installed and config.iso_path and not repair_setup:
        command += ["-drive", f"if=none,id=installer,media=cdrom,readonly=on,file={option(config.iso_path)}",
                    "-device", "ide-cd,bus=ide.0,drive=installer,bootindex=1"]
        cd_index += 1
    if config.drivers_iso_path:
        command += ["-drive", f"if=none,id=drivers,media=cdrom,readonly=on,file={option(config.drivers_iso_path)}",
                    "-device", f"ide-cd,bus=ide.{cd_index},drive=drivers"]
        cd_index += 1
    if setup_iso:
        command += ["-drive", f"if=none,id=virtue_setup,media=cdrom,readonly=on,file={option(setup_iso)}",
                    "-device", f"ide-cd,bus=ide.{cd_index},drive=virtue_setup"]
        command += ["-chardev", f"file,id=setup_status,path={option(directory / 'setup-events.jsonl')}",
                    "-device", "isa-serial,chardev=setup_status"]
    network = "virtio-net-pci" if getattr(config, "virtio_network", False) else "e1000e"
    if network not in ("e1000e", "virtio-net-pci"):
        raise BackendError("Unknown network device.")
    command += ["-netdev", "user,id=network0", "-device", f"{network},netdev=network0"]
    if guest_agent_socket:
        command += ["-device", "virtio-serial-pci,id=virtio-serial0",
                    "-chardev", f"socket,id=qga,path={option(guest_agent_socket)},server=on,wait=off",
                    "-device", "virtserialport,chardev=qga,name=org.qemu.guest_agent.0"]
    if graphics == "yttrium":
        gpu = "virtio-vga-gl,hostmem=4G,blob=on,venus=on"
        if display_mode:
            gpu += f",edid=on,xres={display_mode['width']},yres={display_mode['height']}"
        command += ["-object", f"memory-backend-memfd,id=ram,size={config.memory_mib}M,share=on",
                    "-device", gpu, "-vga", "none"]
    else:
        command += ["-device", "VGA,vgamem_mb=64"]
    # Let GTK own keyboard grabs and focus-loss key releases. Fullscreen launches
    # capture input even when automatic capture is disabled for windowed use.
    fullscreen = fullscreen or getattr(config, "start_fullscreen", False)
    capture_keyboard = fullscreen or getattr(config, "grab_on_hover", True)
    display = f"gtk,gl={'on' if graphics == 'yttrium' else 'off'},show-menubar=off,show-tabs=off,zoom-to-fit=on"
    display += f",grab-on-hover={'on' if capture_keyboard else 'off'}"
    command += ["-display", display]
    if fullscreen:
        command += ["-full-screen"]
    if audio_backend:
        if audio_backend not in {"pa", "pipewire", "alsa", "sdl"}:
            raise BackendError("Unknown audio backend.")
        command += ["-audiodev", f"{audio_backend},id=audio0", "-device", "ich9-intel-hda",
                    "-device", "hda-duplex,audiodev=audio0"]
    return command
