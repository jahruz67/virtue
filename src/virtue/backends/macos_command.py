"""Apple Silicon/HVF Windows ARM profile, using Triton/Neptune and DXMT."""
from pathlib import Path
import json
import re
import uuid

from .backend import BackendError
from .command import option
from .display import validate_display_mode


def build_macos_command(config, directory: Path, qemu, firmware, qmp_socket, tpm_socket,
                        *, safe_graphics=False, audio_backend="coreaudio", hyperv=(),
                        fullscreen=False, accelerator="hvf", guest_agent_socket=None,
                        setup_iso=None, repair_setup=False, display_mode=None) -> list[str]:
    config.validate()
    if config.execution_profile != "windows-arm64-virt-triton-v1" or accelerator != "hvf":
        raise BackendError("The native Mac profile requires Windows 11 ARM and HVF; no guest conversion is performed.")
    if not re.fullmatch(r"virt(?:-\d+\.\d+)?", config.machine_type):
        raise BackendError("Invalid ARM machine profile.")
    if hyperv or audio_backend != "coreaudio":
        raise BackendError("Unsupported CPU or audio options for the native Mac profile.")
    mode = validate_display_mode(display_mode) if config.match_host_display else None
    command = [qemu] if isinstance(qemu, str) else list(qemu)
    command += ["-name", f"guest={option(config.name)}", "-uuid", str(uuid.UUID(config.id)),
                "-no-user-config", "-nodefaults", "-machine", config.machine_type,
                "-accel", "hvf,ipa-granule-size=0x1000", "-cpu", "host",
                "-smp", f"{config.cpus},sockets=1,cores={config.cpus},threads=1",
                "-m", str(config.memory_mib), "-rtc", "base=localtime,clock=host",
                "-boot", "menu=on,strict=on",
                "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={option(firmware)}",
                "-drive", f"if=pflash,format=raw,unit=1,file={option(directory / 'OVMF_VARS.fd')}",
                "-chardev", f"socket,id=chrtpm,path={option(tpm_socket)}",
                "-tpmdev", "emulator,id=tpm0,chardev=chrtpm", "-device", "tpm-tis-device,tpmdev=tpm0",
                "-qmp", f"unix:{option(qmp_socket)},server=on,wait=off",
                "-device", "qemu-xhci,id=xhci", "-device", "usb-kbd,bus=xhci.0",
                "-device", "usb-mouse,bus=xhci.0" if config.relative_mouse else "usb-tablet,bus=xhci.0",
                "-blockdev", json.dumps({"driver": "qcow2", "node-name": "osdisk", "discard": "unmap",
                    "file": {"driver": "file", "filename": str(directory / 'disk.qcow2'), "aio": "threads"}}, separators=(",", ":")),
                "-device", f"nvme,drive=osdisk,serial={config.id},bootindex=0",
                "-netdev", "user,id=network0", "-device", "virtio-net-pci,netdev=network0",
                "-audiodev", "coreaudio,id=audio0", "-device", "intel-hda",
                "-device", "hda-duplex,audiodev=audio0"]
    if config.solidworks_compatibility:
        command += ["-smbios", f"type=1,manufacturer=Virtue,product=Workstation,version=1.0,serial={config.id},uuid={uuid.UUID(config.id)},family=Workstation"]
    media = []
    if not config.installed and config.iso_path and not repair_setup:
        media.append(("installer", config.iso_path, ",bootindex=1"))
    if config.drivers_iso_path:
        media.append(("drivers", config.drivers_iso_path, ""))
    if setup_iso:
        media.append(("virtue_setup", setup_iso, ""))
    for name, path, boot in media:
        command += ["-drive", f"if=none,id={name},media=cdrom,readonly=on,file={option(path)}",
                    "-device", f"usb-storage,bus=xhci.0,drive={name},removable=on{boot}"]
    if setup_iso:
        command += ["-chardev", f"file,id=setup_status,path={option(directory / 'setup-events.jsonl')}",
                    "-serial", "chardev:setup_status"]
    if guest_agent_socket:
        command += ["-device", "virtio-serial-pci,id=virtio-serial0",
                    "-chardev", f"socket,id=qga,path={option(guest_agent_socket)},server=on,wait=off",
                    "-device", "virtserialport,chardev=qga,name=org.qemu.guest_agent.0"]
    if safe_graphics or config.graphics == "safe":
        command += ["-device", "ramfb", "-display", "cocoa,gl=off"]
    else:
        gpu = "virtio-ramfb-gl,hostmem=4G,blob=on,venus=off,neptune=on"
        if mode:
            gpu += f",edid=on,xres={mode['width']},yres={mode['height']}"
        command += ["-device", gpu, "-vga", "none", "-display", "cocoa,gl=es"]
    if fullscreen or config.start_fullscreen:
        command.append("-full-screen")
    return command
