"""Fixed privileged operation invoked through the desktop's polkit prompt."""
import os
from pathlib import Path
import pwd
import platform
import shutil
import subprocess


def main() -> int:
    if os.geteuid() != 0:
        raise PermissionError("Host preparation requires administrator authentication.")
    uid = int(os.environ.get("PKEXEC_UID", "0"))
    if uid <= 0:
        raise PermissionError("Open Virtue from the ordinary user's desktop session.")
    pwd.getpwuid(uid)
    common = ["flatpak", "vulkan-tools", "swtpm", "swtpm-tools", "xorriso", "acl", "mesa-vulkan-drivers", "mate-polkit"]
    distro = platform.freedesktop_os_release()
    family = {distro.get("ID", ""), *distro.get("ID_LIKE", "").split()}
    if family & {"debian", "ubuntu"}:
        environment = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "NEEDRESTART_MODE": "l"}
        subprocess.run(["apt-get", "update"], check=True, timeout=600, env=environment)
        packages = common + ["qemu-system-x86", "qemu-system-gui", "qemu-utils", "ovmf", "wimtools", "7zip"]
        subprocess.run(["apt-get", "-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold",
                        "install", "-y", *packages], check=True, timeout=1800, env=environment)
    elif family & {"fedora", "rhel"}:
        packages = common + ["qemu-system-x86", "qemu-ui-gtk", "qemu-img", "edk2-ovmf", "wimlib-utils", "7zip"]
        subprocess.run(["dnf", "install", "-y", *packages], check=True, timeout=1800)
    else:
        raise RuntimeError("Automatic host preparation supports Debian, Ubuntu and Fedora packages.")
    if not Path("/dev/kvm").exists():
        subprocess.run(["modprobe", "kvm"], check=True, timeout=30)
        cpu = Path("/proc/cpuinfo").read_text()
        module = "kvm_intel" if "GenuineIntel" in cpu else "kvm_amd"
        subprocess.run(["modprobe", module], check=True, timeout=30)
    if not Path("/dev/kvm").exists():
        raise RuntimeError("Hardware virtualization is unavailable. Enable it in the computer's firmware.")
    subprocess.run(["setfacl", "-m", f"u:{uid}:rw", "/dev/kvm"], check=True, timeout=30)
    if shutil.which("udevadm"):
        subprocess.run(["udevadm", "control", "--reload-rules"], check=True, timeout=30)
    return 0
