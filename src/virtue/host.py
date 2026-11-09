"""Read-only, bounded probes of the real Linux virtualization/graphics stack.

``yttrium_ready`` means the prerequisites passed, never that guest rendering works.
No probe changes permissions, loads modules, installs software, or starts a VM.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import ctypes
import glob
import json
import os
from pathlib import Path
import platform as host_platform
import re
import shutil
import subprocess


@dataclass
class Check:
    name: str
    ok: bool
    required: bool
    detail: str


@dataclass
class HostReport:
    platform: str = field(default_factory=host_platform.system)
    architecture: str = field(default_factory=host_platform.machine)
    memory_mib: int = 0
    cpus: int = 1
    checks: list[Check] = field(default_factory=list)
    qemu: str = ""
    qemu_img: str = ""
    swtpm: str = ""
    ovmf_code: str = ""
    ovmf_vars: str = ""
    ovmf_code_plain: str = ""
    ovmf_vars_plain: str = ""
    ovmf_secure: bool = False
    qemu_prefix: list[str] = field(default_factory=list)
    yttrium_prefix: list[str] = field(default_factory=list)
    audio_backends: list[str] = field(default_factory=list)
    yttrium_accel: str = "kvm"
    yttrium_available: bool = False
    yttrium_ready: bool = False
    gpus: list[dict] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)
    ready: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _run(args: list[str], timeout: float = 8) -> tuple[bool, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, errors="replace",
                                timeout=timeout, check=False, shell=False)
        # Driver tools can be verbose. Keep diagnostics bounded.
        return result.returncode == 0, (result.stdout + result.stderr)[:4_000_000]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def memory_mib() -> int:
    if host_platform.system() == "Linux":
        try:
            text = Path("/proc/meminfo").read_text()
            return int(re.search(r"MemTotal:\s+(\d+)", text).group(1)) // 1024
        except (OSError, AttributeError, ValueError):
            pass
    if host_platform.system() == "Windows":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                        ("page_total", ctypes.c_ulonglong), ("page_avail", ctypes.c_ulonglong),
                        ("virtual_total", ctypes.c_ulonglong), ("virtual_avail", ctypes.c_ulonglong),
                        ("extended", ctypes.c_ulonglong)]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total // (1024 * 1024)
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
    except (AttributeError, OSError, ValueError):
        return 0


def check_kvm(path: str = "/dev/kvm") -> tuple[bool, str]:
    """KVM_GET_API_VERSION=12 and actual KVM_CREATE_VM, not just a file check."""
    try:
        import fcntl  # Linux only; the module still imports on other platforms.
        fd = os.open(path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
        try:
            version = fcntl.ioctl(fd, 0xAE00, 0)
            if version != 12:
                return False, f"Unsupported KVM API version {version}; expected 12."
            vmfd = fcntl.ioctl(fd, 0xAE01, 0)
            os.close(vmfd)
            return True, "KVM API 12; an empty hardware VM was created and closed successfully."
        finally:
            os.close(fd)
    except (ImportError, OSError) as exc:
        return False, (f"Cannot use {path}: {exc}. Enable Intel VT-x/AMD-V in firmware, "
                       "install the KVM kernel modules and grant this account access to /dev/kvm "
                       "(usually the kvm group), then sign out and back in.")


def find_firmware() -> dict[str, str]:
    """Prefer distro firmware descriptors, then known matching image pairs."""
    result: dict[str, str] = {}
    for descriptor in sorted(glob.glob("/usr/share/qemu/firmware/*.json")):
        try:
            data = json.loads(Path(descriptor).read_text())
            if not any(t.get("architecture") == "x86_64" for t in data.get("targets", [])):
                continue
            mapping = data.get("mapping", {})
            if mapping.get("device") != "flash":
                continue
            code = mapping["executable"]["filename"]
            var = mapping["nvram-template"]["filename"]
            if not Path(code).is_file() or not Path(var).is_file():
                continue
            features = data.get("features", [])
            if "secure-boot" in features and "enrolled-keys" in features:
                result.setdefault("code", code)
                result.setdefault("vars", var)
            elif "enrolled-keys" not in features:
                result.setdefault("code_plain", code)
                result.setdefault("vars_plain", var)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    pairs = [
        ("/usr/share/OVMF/OVMF_CODE_4M.secboot.fd", "/usr/share/OVMF/OVMF_VARS_4M.ms.fd", True),
        ("/usr/share/OVMF/OVMF_CODE.secboot.fd", "/usr/share/OVMF/OVMF_VARS.ms.fd", True),
        ("/usr/share/edk2/ovmf/OVMF_CODE.secboot.fd", "/usr/share/edk2/ovmf/OVMF_VARS.secboot.fd", True),
        ("/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd", "/usr/share/edk2/x64/OVMF_VARS.4m.fd", False),
        ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd", False),
        ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd", False),
        ("/usr/share/edk2/ovmf/OVMF_CODE.fd", "/usr/share/edk2/ovmf/OVMF_VARS.fd", False),
    ]
    for code, var, secure in pairs:
        if Path(code).is_file() and Path(var).is_file():
            suffix = "" if secure else "_plain"
            result.setdefault("code" + suffix, code)
            result.setdefault("vars" + suffix, var)
    return result


def parse_vulkaninfo(text: str) -> list[dict]:
    """Parse full vulkaninfo output without mistaking CPU Vulkan for a GPU.

    Extension names belong to each physical-device block; an extension from one
    GPU cannot qualify a different device on hybrid laptops.
    """
    blocks = re.split(r"(?m)^\s*GPU\d+:\s*$", text)
    gpus = []
    for block in blocks[1:]:
        def value(key: str) -> str:
            match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*(.+?)\s*$", block)
            return match.group(1) if match else ""
        name = value("deviceName")
        if not name:
            continue
        device_type = value("deviceType")
        api = value("apiVersion")
        extensions = sorted(set(re.findall(r"\bVK_[A-Za-z0-9_]+\b", block)))
        software = bool(re.search(r"llvmpipe|lavapipe|swiftshader|software|CPU", name + " " + device_type, re.I))
        version = re.search(r"(\d+)\.(\d+)", api)
        api_ok = bool(version and tuple(map(int, version.groups())) >= (1, 1))
        required = "VK_KHR_external_memory_fd" in extensions
        gpus.append({"name": name, "type": device_type, "api_version": api,
                     "vendor_id": value("vendorID"), "device_id": value("deviceID"),
                     "driver": value("driverName"), "driver_info": value("driverInfo"),
                     "driver_version": value("driverVersion"), "extensions": extensions,
                     "software": software, "venus_candidate": not software and api_ok and required,
                     "status": "Host prerequisites only; guest rendering has not been verified."})
    return gpus


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
    return tuple(int(p or 0) for p in match.groups()) if match else (0, 0, 0)


def venus_kernel_requirements(gpus: list[dict], kernel: str, cpuinfo: str) -> tuple[bool, bool, str]:
    """Conservative current Mesa requirements; passing is not compatibility proof."""
    need_pat = False
    reasons = []
    eligible = []
    intel_cpu = "GenuineIntel" in cpuinfo
    for gpu in gpus:
        if not gpu.get("venus_candidate"):
            continue
        name = (gpu.get("name", "") + " " + gpu.get("driver", "") + " " + gpu.get("driver_info", "")).lower()
        minimum = (0, 0, 0)
        pat = False
        if "radv" in name or "radeon" in name or "amd" in name:
            minimum = (6, 13, 0)
            # Determine integrated/discrete from Vulkan rather than vendor.
            pat = intel_cpu and "DISCRETE" in gpu.get("type", "")
        # Iris Xe hardware can use i915; the documented constraint concerns the
        # xe *kernel driver*, not every GPU whose marketing name contains Xe.
        if "meteor" in name or gpu.get("kernel_driver") == "xe":
            minimum = (6, 16, 0)
            pat = True
        if "nvidia" in name:
            pat = intel_cpu
            version = gpu.get("driver_info", "") or gpu.get("driver_version", "")
            if _version_tuple(version) < (570, 86, 0):
                reasons.append(f"{gpu['name']}: NVIDIA 570.86+ is required by the current Venus guide.")
                continue
        if pat:
            minimum = max(minimum, (6, 16, 0))
        if _version_tuple(kernel) < minimum:
            reasons.append(f"{gpu['name']}: requires Linux {minimum[0]}.{minimum[1]}+ for current Venus.")
            continue
        eligible.append(gpu)
        need_pat = need_pat or pat
    return bool(eligible), need_pat, "; ".join(reasons) or "Kernel/driver minimum checks passed for at least one candidate GPU."


def detect_host() -> HostReport:
    report = HostReport(memory_mib=memory_mib(), cpus=max(1, os.cpu_count() or 1))
    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        report.checks.append(Check(name, bool(ok), required, detail))
    supported = report.platform == "Linux" and report.architecture.lower() in {"x86_64", "amd64"}
    add("Linux x86-64 host", supported, f"Detected {report.platform} / {report.architecture}. Linux x86-64 is the implemented VM target.")
    add("Host memory", report.memory_mib >= 6144, f"{report.memory_mib} MiB physical RAM; at least 6 GiB is needed for a 4 GiB guest plus host reserve.")
    if not supported:
        report.errors = [check.detail for check in report.checks if check.required and not check.ok]
        return report
    kvm_ok, detail = check_kvm()
    add("KVM hardware virtualization", kvm_ok, detail)
    for field_name, command in [("qemu", "qemu-system-x86_64"), ("qemu_img", "qemu-img"), ("swtpm", "swtpm")]:
        executable = shutil.which(command) or ""
        setattr(report, field_name, executable)
        ok, output = _run([executable, "--version"]) if executable else (False, "Executable not found in PATH.")
        report.versions[field_name] = output.splitlines()[0] if output else ""
        add(command, ok, report.versions[field_name] or f"Install {command} from your distribution.")
        if field_name == "qemu" and ok:
            add("QEMU supported version", _version_tuple(output) >= (8, 0, 0),
                "QEMU 8.0 or later is required for the supported native launch profile; detected " + report.versions[field_name])
    if report.qemu:
        report.qemu_prefix = [report.qemu]
        ok, display = _run([report.qemu, "-display", "help"])
        add("QEMU desktop display", ok and bool(re.search(r"\bgtk\b", display)), "GTK display backend available." if "gtk" in display else "Install the QEMU GTK/UI package (qemu-system-gui on Ubuntu/Debian).")
        ok, audio = _run([report.qemu, "-audiodev", "help"])
        report.audio_backends = [backend for backend in ("pa", "pipewire", "sdl", "alsa") if re.search(rf"(?m)^\s*{backend}\s*$", audio)]
        add("QEMU sound", bool(report.audio_backends), "Available backends: " + ", ".join(report.audio_backends) if report.audio_backends else "Sound components need repair. Use Retry / repair setup.")
    firmware = find_firmware()
    report.ovmf_code = firmware.get("code", "")
    report.ovmf_vars = firmware.get("vars", "")
    report.ovmf_code_plain = firmware.get("code_plain", "")
    report.ovmf_vars_plain = firmware.get("vars_plain", "")
    report.ovmf_secure = bool(report.ovmf_code and report.ovmf_vars)
    report.ovmf_code = report.ovmf_code or report.ovmf_code_plain
    report.ovmf_vars = report.ovmf_vars or report.ovmf_vars_plain
    plain_firmware = bool(report.ovmf_code_plain and report.ovmf_vars_plain)
    add("UEFI firmware", plain_firmware,
        f"Code: {report.ovmf_code_plain}; variables template: {report.ovmf_vars_plain}" if plain_firmware else "Install ovmf (Ubuntu/Debian) or edk2-ovmf (Fedora), including an unenrolled variables template for Yttrium graphics.")
    add("Desktop session", bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")), "A Wayland or X11 session is required for the Windows window.")
    report.ready = all(check.ok for check in report.checks if check.required)
    report.errors = [check.detail for check in report.checks if check.required and not check.ok]

    flatpak = shutil.which("flatpak")
    prefix = [flatpak, "run", "--user", "org.qemu.yttrium"] if flatpak else []
    ok, version = _run(prefix + ["--version"]) if prefix else (False, "Flatpak is not installed.")
    report.yttrium_available = ok
    report.versions["yttrium"] = version.splitlines()[0] if version else ""
    add("Yttrium host bundle", ok, report.versions["yttrium"] if ok else "Automatic Yttrium setup has not finished. Retry setup in the app.")
    props_ok = False
    if ok:
        report.yttrium_prefix = prefix
        _, props = _run(prefix + ["-device", "virtio-vga-gl,help"])
        props_ok = all(re.search(rf"(?m)^\s*{prop}\s*=", props) for prop in ("blob", "venus", "hostmem"))
        add("Yttrium GPU device", props_ok, "VirtIO GPU exposes blob, venus and hostmem." if props_ok else "The installed QEMU lacks one or more required VirtIO GPU properties.")
    vulkaninfo = shutil.which("vulkaninfo")
    vk_ok, vk_text = _run([vulkaninfo], timeout=15) if vulkaninfo else (False, "Install vulkan-tools to inspect Vulkan capabilities.")
    report.gpus = parse_vulkaninfo(vk_text) if vk_ok else []
    for card in glob.glob("/sys/class/drm/card[0-9]*/device"):
        try:
            vendor = int((Path(card) / "vendor").read_text().strip(), 16)
            device = int((Path(card) / "device").read_text().strip(), 16)
            kernel_driver = (Path(card) / "driver").resolve(strict=True).name
            for gpu in report.gpus:
                if int(gpu.get("vendor_id", "0"), 0) == vendor and int(gpu.get("device_id", "0"), 0) == device:
                    gpu["kernel_driver"] = kernel_driver
        except (OSError, ValueError):
            continue
    candidates = [gpu for gpu in report.gpus if gpu.get("venus_candidate")]
    add("Vulkan hardware candidate", bool(candidates), ", ".join(gpu["name"] for gpu in candidates) + "; Vulkan 1.1+ and VK_KHR_external_memory_fd present. Actual shared-memory/rendering compatibility remains unverified." if candidates else "No compatible hardware Vulkan device is available. Open Repair setup to check the graphics driver.")
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
    except OSError:
        cpuinfo = ""
    kernel_ok, need_pat, kernel_detail = venus_kernel_requirements(report.gpus, host_platform.release(), cpuinfo)
    if need_pat:
        report.yttrium_accel = "kvm,honor-guest-pat=on"
        kernel_ok = kernel_ok and _version_tuple(report.versions.get("yttrium", "")) >= (11, 0, 0)
    add("Venus kernel and driver requirements", kernel_ok, kernel_detail)
    report.ready = all(check.ok for check in report.checks if check.required)
    report.errors = [check.detail for check in report.checks if check.required and not check.ok]
    report.yttrium_ready = report.ready
    return report
