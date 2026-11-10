"""Build an arm64 Virtue.app from a complete, pinned native runtime staging tree.

Run only on a native Apple Silicon build host with PyInstaller already installed.
No dependency installation, runtime download, CI dispatch or publication occurs.
The supplied runtime manifest is validated before any build starts. The app is
an engineering preview; building/signing it does not certify guest compatibility.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
MACHO = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
         b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}


def run(arguments: list[str], *, timeout=1800) -> str:
    result = subprocess.run(arguments, cwd=ROOT, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, check=False, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(arguments[0]).name} failed: {(result.stderr or result.stdout)[-4000:]}")
    return result.stdout


def native_files(root: Path) -> list[Path]:
    result = []
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                if stream.read(4) in MACHO:
                    result.append(path)
    return result


def thin_arm64(binary: Path) -> None:
    architectures = run(["/usr/bin/lipo", "-archs", str(binary)], timeout=30).split()
    if "arm64" not in architectures:
        raise RuntimeError(f"The native helper has no arm64 slice: {binary.name}")
    if len(architectures) == 1:
        return
    # install_name_tool cannot change an rpath missing from another architecture.
    with tempfile.TemporaryDirectory(prefix=".virtue-arch-", dir=binary.parent) as temporary:
        destination = Path(temporary) / binary.name
        run(["/usr/bin/lipo", str(binary), "-thin", "arm64", "-output", str(destination)], timeout=30)
        destination.chmod(binary.stat().st_mode)
        destination.replace(binary)


def rebase_runtime(runtime: Path, original: Path) -> list[Path]:
    binaries = native_files(runtime)
    for binary in binaries:
        thin_arm64(binary)
        dependencies = run(["/usr/bin/otool", "-L", str(binary)], timeout=30).splitlines()[1:]
        for line in dependencies:
            dependency = line.strip().split(" (", 1)[0]
            if dependency.startswith(("/System/Library/", "/usr/lib/", "@")):
                continue
            source = Path(dependency)
            if not source.is_relative_to(original):
                raise RuntimeError(f"Unbundled dependency in {binary.name}: {dependency}")
            target = runtime / source.relative_to(original)
            if not target.is_file():
                raise RuntimeError(f"Missing native library: {target.name}")
            relative = os.path.relpath(target, binary.parent)
            run(["/usr/bin/install_name_tool", "-change", dependency,
                 "@loader_path/" + relative, str(binary)], timeout=30)
        commands = run(["/usr/bin/otool", "-l", str(binary)], timeout=30)
        rpaths = set(re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset \d+\)", commands))
        relocated = set()
        for rpath in rpaths:
            value = rpath
            if rpath.startswith("/") and Path(rpath).is_relative_to(original):
                target = runtime / Path(rpath).relative_to(original)
                value = "@loader_path/" + os.path.relpath(target, binary.parent)
                run(["/usr/bin/install_name_tool", "-rpath", rpath, value, str(binary)], timeout=30)
            elif rpath.startswith("/") and not rpath.startswith(("/System/Library/", "/usr/lib")):
                raise RuntimeError(f"Unbundled library search path in {binary.name}: {rpath}")
            relocated.add(value)
        if (runtime / "lib").is_dir():
            fallback = "@loader_path/" + os.path.relpath(runtime / "lib", binary.parent)
            if fallback not in relocated:
                run(["/usr/bin/install_name_tool", "-add_rpath", fallback, str(binary)], timeout=30)
    return binaries


def codesign_arguments(identity: str) -> list[str]:
    arguments = ["/usr/bin/codesign", "--force", "--sign", identity]
    # Hardened library validation requires an Apple-issued identity with a Team ID.
    if identity != "-":
        arguments += ["--options", "runtime", "--timestamp"]
    return arguments


def sign_runtime(runtime: Path, binaries: list[Path], identity: str, qemu: Path) -> None:
    for binary in sorted(binaries, key=lambda path: len(path.parts), reverse=True):
        arguments = codesign_arguments(identity)
        if binary == qemu:
            arguments += ["--entitlements", str(ROOT / "packaging/macos/qemu-entitlements.plist")]
        run([*arguments, str(binary)], timeout=120)
    manifest = runtime / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    files = {}
    for path in sorted(runtime.rglob("*")):
        if path.is_file() and path != manifest:
            with path.open("rb") as stream:
                files[path.relative_to(runtime).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    data["files"] = files
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def build(args) -> Path:
    sys.path.insert(0, str(ROOT / "src"))
    from virtue.macos_runtime import load_runtime
    source = args.runtime.expanduser().resolve()
    data, _ = load_runtime(source)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    output = args.output.expanduser().absolute()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "Virtue.app"
    if destination.exists() or destination.is_symlink():
        raise RuntimeError("Virtue.app already exists. Choose a new output directory.")
    with tempfile.TemporaryDirectory(prefix=".virtue-macos-", dir=output) as temporary:
        stage = Path(temporary)
        command = [sys.executable, "-m", "PyInstaller", "--windowed", "--onedir", "--name", "Virtue",
                   "--target-arch", "arm64", "--osx-bundle-identifier", "org.virtue.desktop",
                   "--paths", str(ROOT / "src"), "--distpath", str(stage / "dist"),
                   "--workpath", str(stage / "build"), "--specpath", str(stage)]
        # Passing '-' explicitly enables PyInstaller's hardened-runtime branch.
        if args.sign_identity != "-":
            command += ["--codesign-identity", args.sign_identity]
        for name in ("resources", "guest", "docs"):
            folder = stage / name
            shutil.copytree(ROOT / name, folder, ignore=shutil.ignore_patterns("macos-runtime"))
            command += ["--add-data", f"{folder}:{name}"]
        command.append(str(ROOT / "packaging/macos/launcher.py"))
        run(command)
        app = stage / "dist/Virtue.app"
        runtime = app / "Contents/Frameworks/VirtueRuntime"
        shutil.copytree(source, runtime, symlinks=False)
        prefix = Path(data.get("build_prefix", str(source)))
        if not prefix.is_absolute():
            raise RuntimeError("The runtime build_prefix must be an absolute build-time path.")
        binaries = rebase_runtime(runtime, prefix)
        sign_runtime(runtime, binaries, args.sign_identity, runtime / data["paths"]["qemu"])
        load_runtime(runtime)
        resources = app / "Contents/Resources"
        for name in ("LICENSE", "THIRD_PARTY.md"):
            shutil.copy2(ROOT / name, resources / name)
        info_path = app / "Contents/Info.plist"
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
        info.update(CFBundleDisplayName="Virtue", CFBundleShortVersionString=version,
                    CFBundleVersion=version, LSMinimumSystemVersion=data["minimum_macos"],
                    NSHighResolutionCapable=True,
                    NSMicrophoneUsageDescription="Use the microphone in your Windows environment when supported by the audio backend.")
        with info_path.open("wb") as stream:
            plistlib.dump(info, stream)
        signing = codesign_arguments(args.sign_identity)
        run([*signing, str(app)], timeout=120)
        run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], timeout=120)
        if args.notary_profile:
            archive = stage / "Virtue.zip"
            run(["/usr/bin/ditto", "-c", "-k", "--keepParent", str(app), str(archive)])
            run(["/usr/bin/xcrun", "notarytool", "submit", str(archive), "--keychain-profile",
                 args.notary_profile, "--wait"], timeout=3600)
            run(["/usr/bin/xcrun", "stapler", "staple", str(app)])
        app.rename(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path, help="Complete native runtime with manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/macos")
    parser.add_argument("--sign-identity", default="-", help="Developer ID identity; '-' makes an ad-hoc engineering build")
    parser.add_argument("--notary-profile", help="Existing notarytool keychain profile; never a raw credential")
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine().lower() != "arm64":
        parser.error("Build the native app on an Apple Silicon Mac.")
    if importlib.util.find_spec("PyInstaller") is None:
        parser.error("The build environment must already contain PyInstaller and the project dependencies.")
    if args.notary_profile and args.sign_identity == "-":
        parser.error("Notarization requires a Developer ID signing identity.")
    try:
        print(build(args))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
