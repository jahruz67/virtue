"""Build an x86_64 Fedora RPM using system Python and PySide6 (requires rpmbuild)."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def stage_payload(payload: Path, wheel: Path) -> None:
    libraries = payload / "opt/virtue/lib"
    libraries.mkdir(parents=True)
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if not (libraries / name).resolve().is_relative_to(libraries.resolve()):
                raise ValueError(f"Unsafe wheel path: {name}")
        archive.extractall(libraries)
    launcher = payload / "usr/bin/virtue"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        "#!/usr/bin/python3\nimport sys\nsys.path.insert(0, '/opt/virtue/lib')\n"
        "from virtue.app import main\nraise SystemExit(main())\n",
        encoding="utf-8", newline="\n",
    )
    launcher.chmod(0o755)
    helper = payload / "usr/libexec/virtue-setup-host"
    helper.parent.mkdir(parents=True)
    helper.write_text((ROOT / "packaging/linux/virtue-setup-host").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    helper.chmod(0o755)
    rules = payload / "usr/lib/udev/rules.d"
    rules.mkdir(parents=True)
    (rules / "70-virtue-kvm.rules").write_text((ROOT / "packaging/linux/70-virtue-kvm.rules").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    share = payload / "opt/virtue/share"
    share.mkdir(parents=True)
    for name in ("docs", "guest", "resources"):
        shutil.copytree(ROOT / name, share / name)
    for name in ("README.md", "LICENSE", "THIRD_PARTY.md"):
        shutil.copy2(ROOT / name, share / name)
    for source, destination in (
        ("virtue.desktop", "usr/share/applications"),
        ("virtue.svg", "usr/share/icons/hicolor/scalable/apps"),
    ):
        target = payload / destination
        target.mkdir(parents=True)
        shutil.copy2(ROOT / "packaging/linux" / source, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if sys.platform != "linux" or shutil.which("rpmbuild") is None:
        parser.error("Build on Linux with rpmbuild installed (Fedora: rpm-build; Ubuntu: rpm)")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    subprocess.run([sys.executable, "-m", "build", "--wheel"], cwd=ROOT, check=True)
    wheel = ROOT / "dist" / f"virtue_desktop-{version}-py3-none-any.whl"
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="virtue-rpm-") as temporary:
        work = Path(temporary)
        payload = work / "payload"
        stage_payload(payload, wheel)
        subprocess.run([
            "rpmbuild", "-bb", "--target", "x86_64",
            "--define", f"_topdir {work / 'rpmbuild'}",
            "--define", f"virtue_version {version}",
            "--define", f"virtue_payload {payload}",
            str(ROOT / "packaging/linux/virtue.spec"),
        ], cwd=ROOT, check=True)
        built = work / "rpmbuild/RPMS/x86_64" / f"virtue-desktop-{version}-1.x86_64.rpm"
        package = args.output / built.name
        shutil.copy2(built, package)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_suffix(".rpm.sha256").write_text(
        f"{digest}  {package.name}\n", encoding="ascii", newline="\n",
    )
    print(f"Built {package} ({package.stat().st_size:,} bytes)\nSHA256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
