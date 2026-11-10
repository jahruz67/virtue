"""Read-only native Mac runtime prerequisites, distinct from guest GPU evidence."""
import os
import platform
import re

from .domain import ValidationError
from .host import Check, HostReport, _run, memory_mib
from .macos_runtime import PROFILE, load_runtime, version_tuple


def detect_macos_host() -> HostReport:
    report = HostReport(memory_mib=memory_mib(), cpus=max(1, os.cpu_count() or 1),
                        accelerator="hvf", graphics_backend="triton-neptune-dxmt")
    def add(name, ok, detail, required=True):
        report.checks.append(Check(name, bool(ok), required, detail))
    add("Native Apple Silicon", platform.machine().lower() == "arm64",
        "The Mac engine requires native arm64 execution; x64 environments are never converted.")
    add("Host memory", report.memory_mib >= 6144, f"{report.memory_mib} MiB; keep memory available for macOS.")
    try:
        data, paths = load_runtime()
        add("Bundled native runtime", True, "Runtime inventory checksums passed.")
        add("macOS version", version_tuple(platform.mac_ver()[0]) >= version_tuple(data["minimum_macos"]),
            f"Runtime requires macOS {data['minimum_macos']} or later.")
        report.runtime_sources = data["sources"]
        for field, role in (("qemu", "qemu"), ("qemu_img", "qemu_img"), ("swtpm", "swtpm"),
                            ("ovmf_code", "firmware_code"), ("ovmf_code_plain", "firmware_code"),
                            ("ovmf_vars", "firmware_vars"), ("ovmf_vars_plain", "firmware_vars")):
            setattr(report, field, str(paths[role]))
        report.qemu_prefix = [report.qemu]
        for role in ("qemu", "qemu_img", "swtpm"):
            ok, output = _run([str(paths[role]), "--version"])
            report.versions[role] = output.splitlines()[0] if output else ""
            add(role, ok, report.versions[role])
        queries = (("HVF accelerator", ["-accel", "help"], ("hvf",)),
                   ("ARM machine", ["-machine", "help"], ("virt",)),
                   ("Native display", ["-display", "help"], ("cocoa",)),
                   ("Mac audio", ["-audiodev", "help"], ("coreaudio",)),
                   ("Triton graphics device", ["-device", "virtio-ramfb-gl,help"],
                    ("hostmem", "blob", "neptune")))
        for name, args, required in queries:
            ok, output = _run([report.qemu, *args])
            add(name, ok and all(re.search(rf"\b{word}\b", output) for word in required),
                "Required options: " + ", ".join(required) + ". " + output[:1200])
        ok, value = _run(["/usr/sbin/sysctl", "-n", "kern.hv_support"])
        add("Hypervisor framework", ok and value.strip() == "1", "Hardware virtualization must be available to the signed QEMU helper.")
        report.audio_backends = ["coreaudio"]
        report.guest_profiles = [PROFILE]
        add("Guest graphics qualification", False,
            "Triton/DXMT prerequisites are separate from observed DirectX rendering and the SolidWorks Software OpenGL workflow.", False)
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
        add("Bundled native runtime", False, str(exc))
    report.ready = all(check.ok for check in report.checks if check.required)
    report.errors = [check.detail for check in report.checks if check.required and not check.ok]
    return report
