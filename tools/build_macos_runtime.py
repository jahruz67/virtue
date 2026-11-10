"""Build the self-contained Apple Silicon runtime used by the Mac release job.

Requires Xcode/Metal and the Homebrew build tools installed by build-macos.yml.
Source revisions and guest-media hashes come from resources/macos-runtime.json.
This builds components, inventories their dependencies, and archives their source;
it does not launch Virtue or a virtual machine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

from build_macos import MACHO


ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "resources/macos-runtime.json"
SYSTEM_LIBRARIES = ("/System/Library/", "/usr/lib/")


def run(arguments, *, cwd=None, env=None, capture=False, timeout=7200) -> str:
    arguments = list(map(str, arguments))
    print("+ " + shlex.join(arguments), flush=True)
    result = subprocess.run(arguments, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE if capture else None,
                            text=True, check=True, timeout=timeout)
    return (result.stdout or "").strip()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checkout_sources(recipe: dict, sources: Path) -> dict:
    records = {}
    for name, spec in recipe["sources"].items():
        folder = sources / name
        run(["git", "init", folder])
        run(["git", "remote", "add", "origin", spec["repository"]], cwd=folder)
        run(["git", "fetch", "--depth=1", "--filter=blob:none", "origin", spec["revision"]], cwd=folder)
        if spec.get("sparse"):
            run(["git", "sparse-checkout", "set", *spec["sparse"]], cwd=folder)
        run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=folder)
        if spec.get("submodules"):
            run(["git", "submodule", "update", "--init", "--recursive", "--depth=1"], cwd=folder)
        revision = run(["git", "rev-parse", "HEAD"], cwd=folder, capture=True)
        if re.fullmatch(r"[a-f0-9]{40}", spec["revision"]) and revision != spec["revision"]:
            raise RuntimeError(f"Unexpected source revision for {name}")
        records[name] = {"repository": spec["repository"], "revision": revision,
                         "license": spec["license"]}
    return records


def meson_install(source: Path, build: Path, prefix: Path, env: dict, *options) -> None:
    run(["meson", "setup", build, source, "--buildtype=release", f"--prefix={prefix}",
         "--libdir=lib", *options], env=env)
    run(["meson", "install", "-C", build], env=env)


def build_angle(source: Path, build: Path, minimum: str) -> Path:
    angle = source / "Source/ThirdParty/ANGLE"
    run(["xcodebuild", "archive", "-archivePath", build, "-scheme", "ANGLE",
         "-sdk", "macosx", "-arch", "arm64", "-configuration", "Release",
         "WEBCORE_LIBRARY_DIR=/usr/local/lib", "NORMAL_UMBRELLA_FRAMEWORKS_DIR=",
         "OTHER_LDFLAGS=$(inherited) -Wl,-headerpad_max_install_names",
         "CODE_SIGNING_ALLOWED=NO", f"MACOSX_DEPLOYMENT_TARGET={minimum}"], cwd=angle)
    return Path(str(build) + ".xcarchive") / "Products/usr/local/lib"


def build_llvm(source: Path, build: Path, prefix: Path, env: dict) -> None:
    run(["cmake", "-S", source / "llvm", "-B", build, "-G", "Ninja",
         f"-DCMAKE_INSTALL_PREFIX={prefix}", "-DCMAKE_BUILD_TYPE=Release",
         "-DCMAKE_OSX_ARCHITECTURES=arm64", "-DLLVM_HOST_TRIPLE=arm64-apple-darwin",
         "-DLLVM_ENABLE_ASSERTIONS=ON", "-DLLVM_ENABLE_ZSTD=OFF",
         "-DLLVM_TARGETS_TO_BUILD=", "-DLLVM_BUILD_TOOLS=OFF",
         "-DLLVM_INCLUDE_TESTS=OFF", "-DLLVM_INCLUDE_BENCHMARKS=OFF",
         "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"], env=env)
    run(["cmake", "--build", build, "--parallel", os.cpu_count() or 2], env=env)
    run(["cmake", "--install", build], env=env)


def build_components(recipe: dict, work: Path, brew: Path) -> tuple[Path, Path]:
    sources, builds, prefix = work / "sources", work / "build", work / "prefix"
    builds.mkdir()
    prefix.mkdir()
    env = dict(os.environ, MACOSX_DEPLOYMENT_TARGET=recipe["minimum_macos"],
               PKG_CONFIG_PATH=f"{prefix}/lib/pkgconfig:{brew}/lib/pkgconfig:{brew}/opt/libffi/lib/pkgconfig",
               LDFLAGS=os.environ.get("LDFLAGS", "") + " -Wl,-headerpad_max_install_names")
    angle_lib = build_angle(sources / "angle", builds / "ANGLE", recipe["minimum_macos"])
    angle_include = sources / "angle/Source/ThirdParty/ANGLE/include"
    # Bare dlopen names do not search a relocated app's library directory.
    dispatch = sources / "epoxy/src/dispatch_common.c"
    text = dispatch.read_text(encoding="utf-8")
    for library in ("libEGL", "libGLESv1_CM", "libGLESv2"):
        text = text.replace(f'"{library}.dylib"', f'"@rpath/{library}.dylib"')
    dispatch.write_text(text, encoding="utf-8")
    meson_install(sources / "epoxy", builds / "epoxy", prefix, env,
                  f"-Dc_args=-I{angle_include}", "-Degl=yes", "-Dglx=no", "-Dx11=false", "-Dtests=false")
    build_llvm(sources / "llvm", builds / "llvm", work / "llvm", env)
    meson_install(sources / "dxmt", builds / "dxmt", prefix, env,
                  f"-Dnative_llvm_path={work / 'llvm'}")
    meson_install(sources / "virglrenderer", builds / "virglrenderer", prefix, env,
                  f"-Dc_args=-I{angle_include}", "-Dvenus=false", "-Dneptune=true",
                  "-Drender-server-worker=process", "-Dvtest=false", "-Dtests=false",
                  "-Dcheck-gl-errors=false")
    qemu = builds / "qemu"
    qemu.mkdir()
    run([sources / "qemu/configure", f"--prefix={prefix}", "--target-list=aarch64-softmmu",
         f"--extra-cflags=-I{angle_include}", f"--extra-ldflags=-L{angle_lib}",
         "--enable-cocoa", "--enable-opengl", "--enable-virglrenderer", "--enable-hvf",
         "--enable-slirp", "--enable-tpm", "--audio-drv-list=coreaudio",
         "--disable-gtk", "--disable-sdl", "--disable-docs", "--disable-werror"], cwd=qemu, env=env)
    run(["make", "-C", qemu, f"-j{os.cpu_count() or 2}", "install"], env=env)
    return prefix, angle_lib


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination, follow_symlinks=True)
    destination.chmod(destination.stat().st_mode | 0o200)


def download_asset(spec: dict, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(spec["url"], headers={"User-Agent": "Virtue-runtime-builder"})
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    if digest(temporary) != spec["sha256"]:
        raise RuntimeError(f"SHA256 verification failed: {destination.name}")
    temporary.rename(destination)


def validate_guest_media(recipe: dict, output: Path) -> None:
    extractor = output / recipe["paths"]["extractor"]
    iso = output / recipe["paths"]["virtio_iso"]
    listing = run([extractor, "l", "-slt", iso], capture=True).replace("\\", "/").lower()
    if "guest-agent/qemu-ga-x86_64.msi" not in listing:
        raise RuntimeError("The VirtIO media is missing guest-agent/qemu-ga-x86_64.msi")
    for driver in ("vioserial", "netkvm", "viorng", "vioinput"):
        if not any(f"{driver}/{windows}/arm64/" in listing for windows in ("w11", "w10")):
            raise RuntimeError(f"The VirtIO media is missing ARM64 {driver} drivers")
    with zipfile.ZipFile(output / recipe["paths"]["guest_driver"]) as archive:
        names = [name.lower() for name in archive.namelist()]
    if not any(name.endswith(".inf") for name in names) or not any(name.endswith(".sys") for name in names):
        raise RuntimeError("The Triton archive is missing its driver INF or kernel driver")


def stage_runtime(recipe: dict, prefix: Path, angle_lib: Path, output: Path) -> list:
    files = [(prefix / recipe["paths"][role], output / recipe["paths"][role])
             for role in ("qemu", "qemu_img", "firmware_code", "firmware_vars", "render_server", "dxmt_library")]
    for role, formula in (("swtpm", "swtpm"), ("extractor", "sevenzip"),
                          ("wimlib", "wimlib"), ("xorriso", "xorriso")):
        root = Path(run(["brew", "--prefix", formula], capture=True))
        name = Path(recipe["paths"][role]).name
        binary = next((root / folder / name for folder in ("bin", "sbin")
                       if (root / folder / name).is_file()), None)
        if binary is None:
            raise RuntimeError(f"Missing Homebrew helper: {name}")
        files.append((binary, output / recipe["paths"][role]))
    angle_libraries = list(angle_lib.glob("*.dylib"))
    if not angle_libraries:
        raise RuntimeError("ANGLE did not produce native libraries")
    files.extend((path, output / "lib" / path.name) for path in angle_libraries)
    for source, destination in files:
        copy_file(source, destination)
    for role, spec in recipe["assets"].items():
        download_asset(spec, output / recipe["paths"][role])
    return files


def rpaths(binary: Path) -> list[str]:
    commands = run(["/usr/bin/otool", "-l", binary], capture=True, timeout=30)
    return re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset \d+\)", commands)


def resolve_library(name: str, source: Path, executable: Path, paths: list[str], search: list[Path]) -> Path:
    def expand(value):
        return Path(value.replace("@loader_path", str(source.parent))
                    .replace("@executable_path", str(executable)))

    if name.startswith("@rpath/"):
        roots = [expand(value) for value in paths] + search
        candidates = [root / name.removeprefix("@rpath/") for root in roots]
    else:
        candidates = [expand(name)]
    for candidate in candidates:
        if candidate.is_absolute() and candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(f"Cannot bundle native dependency {name} from {source}")


def bundle_libraries(files: list, output: Path, search: list[Path]) -> set[Path]:
    pending = [(source.resolve(), destination, source.resolve().parent) for source, destination in files]
    locations = {source.resolve(): destination for source, destination in files}
    copied = {destination: source.resolve() for source, destination in files}
    visited, originals = set(), set()
    while pending:
        source, destination, executable = pending.pop()
        if destination in visited:
            continue
        visited.add(destination)
        with source.open("rb") as stream:
            if stream.read(4) not in MACHO:
                continue
        originals.add(source)
        paths = rpaths(source)
        identity = run(["/usr/bin/otool", "-D", source], capture=True, timeout=30).splitlines()[1:]
        dependencies = run(["/usr/bin/otool", "-L", source], capture=True, timeout=30).splitlines()[1:]
        for line in dependencies:
            dependency = line.strip().split(" (", 1)[0]
            if dependency in identity or dependency.startswith(SYSTEM_LIBRARIES):
                continue
            library = resolve_library(dependency, source, executable, paths, search)
            target = locations.setdefault(library, output / "lib" / library.name)
            if target in copied and copied[target] != library:
                raise RuntimeError(f"Conflicting native libraries: {target.name}")
            if target not in copied:
                copy_file(library, target)
                copied[target] = library
                pending.append((library, target, executable))
            run(["/usr/bin/install_name_tool", "-change", dependency, target, destination], timeout=30)
        if identity:
            run(["/usr/bin/install_name_tool", "-id", "@rpath/" + destination.name, destination], timeout=30)
        for value in set(paths):
            run(["/usr/bin/install_name_tool", "-delete_rpath", value, destination], timeout=30)
        relative = os.path.relpath(output / "lib", destination.parent)
        run(["/usr/bin/install_name_tool", "-add_rpath", "@loader_path/" + relative, destination], timeout=30)
    return originals


def archive_homebrew_sources(originals: set[Path], sources: Path, output: Path) -> dict:
    cellar = Path(run(["brew", "--cellar"], capture=True)).resolve()
    formulas = {path.relative_to(cellar).parts[0] for path in originals if path.is_relative_to(cellar)}
    records = {}
    for name in sorted(formulas):
        info = json.loads(run(["brew", "info", "--json=v2", name], capture=True))["formulae"][0]
        source = sources / "homebrew" / name
        source.mkdir(parents=True)
        (source / "formula.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        (source / "formula.rb").write_text(run(["brew", "cat", name], capture=True) + "\n", encoding="utf-8")
        installed = Path(run(["brew", "--prefix", name], capture=True)).resolve()
        receipt = json.loads((installed / "INSTALL_RECEIPT.json").read_text(encoding="utf-8"))
        if receipt["source"]["versions"]["stable"] != info["versions"]["stable"]:
            raise RuntimeError(f"Homebrew {name} is outdated; upgrade it to archive matching source")
        run(["brew", "fetch", "--build-from-source", name])
        archive = Path(run(["brew", "--cache", "--build-from-source", name], capture=True))
        copy_file(archive, source / archive.name)
        notices = output / "share/licenses/homebrew" / name
        for path in installed.iterdir():
            if path.is_file() and path.name.lower().startswith(("license", "copying", "notice")):
                copy_file(path, notices / path.name)
        copy_file(source / "formula.json", notices / "formula.json")
        records["homebrew/" + name] = {
            "repository": info["urls"]["stable"]["url"], "revision": installed.name,
            "license": str(info["license"]), "source_sha256": digest(archive)}
    return records


def write_manifest(recipe: dict, records: dict, sources: Path, output: Path) -> None:
    for name in recipe["sources"]:
        folder = sources / name
        for pattern in ("LICENSE*", "COPYING*", "NOTICE*", "llvm/LICENSE*", "Source/ThirdParty/ANGLE/LICENSE*"):
            for path in folder.glob(pattern):
                if path.is_file():
                    copy_file(path, output / "share/licenses" / name / path.name)
    copy_file(ROOT / "THIRD_PARTY.md", output / "share/licenses/THIRD_PARTY.md")
    manifest = {key: recipe[key] for key in ("schema_version", "host_architecture", "execution_profile", "minimum_macos", "paths")}
    manifest.update(sources=records, build_prefix=str(output), files={})
    for path in sorted(output.rglob("*")):
        if path.is_file():
            manifest["files"][path.relative_to(output).as_posix()] = digest(path)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    sys.path.insert(0, str(ROOT / "src"))
    from virtue.macos_runtime import load_runtime
    load_runtime(output)


def archive_sources(sources: Path, destination: Path) -> None:
    copy_file(RECIPE, sources / "virtue-runtime-recipe.json")
    copy_file(Path(__file__), sources / "build_macos_runtime.py")
    destination.parent.mkdir(parents=True, exist_ok=True)

    def include(member):
        return None if ".git" in Path(member.name).parts else member

    with tarfile.open(destination, "w:gz") as archive:
        archive.add(sources, arcname="Virtue-macos-runtime-sources", filter=include)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--sources-output", required=True, type=Path)
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine().lower() != "arm64":
        parser.error("Build the native runtime on an Apple Silicon Mac")
    output, work = args.output.resolve(), args.work.resolve()
    try:
        recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
        output.mkdir(parents=True, exist_ok=False)
        (work / "sources").mkdir(parents=True, exist_ok=False)
        records = checkout_sources(recipe, work / "sources")
        for role, spec in recipe["assets"].items():
            if "repository" in spec:
                records[role] = {key: spec[key] for key in ("repository", "revision", "license")}
        brew = Path(run(["brew", "--prefix"], capture=True))
        prefix, angle_lib = build_components(recipe, work, brew)
        files = stage_runtime(recipe, prefix, angle_lib, output)
        validate_guest_media(recipe, output)
        originals = bundle_libraries(files, output, [prefix / "lib", angle_lib, brew / "lib"])
        records.update(archive_homebrew_sources(originals, work / "sources", output))
        write_manifest(recipe, records, work / "sources", output)
        archive_sources(work / "sources", args.sources_output.resolve())
        print(output)
        return 0
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
