"""Build a real amd64 DEB from published Linux Qt wheels on any build host.

No host QEMU/firmware/driver binaries are redistributed. Qt remains dynamically
linked, its notices and sources are documented, and wheel license files remain
in the package. This builder does not execute Linux binaries on Windows.
"""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
QT_REQUIREMENTS = ["PySide6-Essentials==6.8.3", "shiboken6==6.8.3"]
DEPENDENCIES = (
    "python3 (>= 3.11), python3 (<< 3.14), qemu-system-x86 (>= 8.0), "
    "qemu-system-gui, qemu-utils, ovmf, swtpm, libegl1, libopengl0, libgl1, "
    "libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, "
    "libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-shape0, "
    "libxcb-randr0, libxcb-xfixes0, libxcb-sync1, libx11-xcb1, "
    "libfontconfig1, libdbus-1-3, libpulse0, xdg-utils"
)


def run(args):
    subprocess.run(args, check=True, cwd=ROOT)


def ar_entry(stream, name: str, payload: bytes):
    header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(payload):<10}`\n"
    stream.write(header.encode("ascii"))
    stream.write(payload)
    if len(payload) % 2:
        stream.write(b"\n")


def tar_bytes(directory: Path) -> bytes:
    result = io.BytesIO()
    # gzip header timestamps are normalized as well as archive entries.
    import gzip
    with gzip.GzipFile(fileobj=result, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for path in sorted(directory.rglob("*")):
                info = archive.gettarinfo(str(path), arcname="./" + path.relative_to(directory).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mtime = 0
                info.mode = 0o755 if info.isdir() or path.name == "virtue" else 0o644
                if info.isdir():
                    archive.addfile(info)
                else:
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
    return result.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--wheelhouse", type=Path, default=ROOT / "artifacts" / "linux-wheels")
    parser.add_argument("--offline", action="store_true", help="Use only wheels already in wheelhouse")
    args = parser.parse_args()
    args.wheelhouse.mkdir(parents=True, exist_ok=True)
    if not args.offline:
        run([sys.executable, "-m", "pip", "download", "--only-binary=:all:",
             "--platform", "manylinux_2_28_x86_64", "--platform", "manylinux_2_17_x86_64",
             "--implementation", "cp", "--python-version", "312", "--abi", "abi3",
             "--dest", str(args.wheelhouse), *QT_REQUIREMENTS])
    run([sys.executable, "-m", "build", "--wheel"])
    app_wheel = ROOT / "dist" / f"virtue_desktop-{VERSION}-py3-none-any.whl"
    qt_wheels = []
    for prefix in ("PySide6_Essentials", "shiboken6"):
        matches = sorted(args.wheelhouse.glob(f"{prefix}-6.8.3-*manylinux*x86_64.whl"))
        if len(matches) != 1:
            raise RuntimeError(f"Expected exactly one Linux wheel for {prefix}, found {matches}")
        qt_wheels.append(matches[0])
    args.output.mkdir(parents=True, exist_ok=True)
    package = args.output / f"virtue-desktop_{VERSION}_amd64.deb"
    with tempfile.TemporaryDirectory(prefix="virtue-deb-") as temporary:
        stage = Path(temporary)
        payload = stage / "data"
        libraries = payload / "opt" / "virtue" / "lib"
        libraries.mkdir(parents=True)
        for wheel in [app_wheel, *qt_wheels]:
            with zipfile.ZipFile(wheel) as archive:
                # Wheelhouse is trusted build input, but still enforce path containment.
                if any(".." in Path(n).parts or n.startswith(("/", "\\")) for n in archive.namelist()):
                    raise RuntimeError("Unsafe wheel path")
                archive.extractall(libraries)
        bin_dir = payload / "usr" / "bin"
        bin_dir.mkdir(parents=True)
        launcher = bin_dir / "virtue"
        launcher.write_text("#!/usr/bin/python3\nimport sys\nsys.path.insert(0, '/opt/virtue/lib')\n"
                            "from virtue.app import main\nraise SystemExit(main())\n", encoding="utf-8", newline="\n")
        launcher.chmod(0o755)
        share = payload / "opt" / "virtue" / "share"
        share.mkdir(parents=True)
        for name in ("docs", "guest", "resources"):
            shutil.copytree(ROOT / name, share / name)
        for name in ("README.md", "LICENSE", "THIRD_PARTY.md"):
            shutil.copy2(ROOT / name, share / name)
        applications = payload / "usr" / "share" / "applications"
        applications.mkdir(parents=True)
        shutil.copy2(ROOT / "packaging/linux/virtue.desktop", applications)
        icons = payload / "usr/share/icons/hicolor/scalable/apps"
        icons.mkdir(parents=True)
        shutil.copy2(ROOT / "packaging/linux/virtue.svg", icons)
        control = stage / "control"
        control.mkdir()
        installed_size = sum(p.stat().st_size for p in payload.rglob("*") if p.is_file()) // 1024
        (control / "control").write_text(
            f"Package: virtue-desktop\nVersion: {VERSION}\nArchitecture: amd64\n"
            "Maintainer: Virtue contributors <maintainers@virtue.invalid>\nSection: otherosfs\n"
            f"Priority: optional\nInstalled-Size: {installed_size}\nDepends: {DEPENDENCIES}\n"
            "Recommends: flatpak, vulkan-tools, swtpm-tools\n"
            "Description: Linux-first Windows virtual machine desktop (engineering preview)\n"
            " Real QEMU/KVM lifecycle, private storage, recovery and experimental Yttrium.\n",
            encoding="utf-8", newline="\n")
        digest_lines = []
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                digest_lines.append(f"{hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()}  {path.relative_to(payload).as_posix()}\n")
        (control / "md5sums").write_text("".join(digest_lines), encoding="ascii", newline="\n")
        wheels = [{"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in qt_wheels]
        import json
        (share / "bundled-wheels.json").write_text(json.dumps(wheels, indent=2), encoding="utf-8")
        with package.open("wb") as stream:
            stream.write(b"!<arch>\n")
            ar_entry(stream, "debian-binary", b"2.0\n")
            ar_entry(stream, "control.tar.gz", tar_bytes(control))
            ar_entry(stream, "data.tar.gz", tar_bytes(payload))
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_suffix(".deb.sha256").write_text(f"{digest}  {package.name}\n", encoding="ascii")
    print(f"Built {package} ({package.stat().st_size:,} bytes)\nSHA256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

