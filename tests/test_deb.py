"""Package regressions using small synthetic wheels; no application build needed."""
import gzip
import hashlib
import importlib.util
import io
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile

import pytest


spec = importlib.util.spec_from_file_location("build_deb", Path(__file__).resolve().parents[1] / "tools/build_deb.py")
build_deb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_deb)

LONG_PATH = "PySide6/Qt/plugins/platforms/" + "x" * 60 + ".so"
HELPER_PATH = "PySide6/Qt/libexec/qtpaths"


def write_wheel(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content, mode in files:
            member = zipfile.ZipInfo(name)
            member.create_system = 3
            member.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(member, content)


@pytest.fixture
def package_builder(tmp_path, monkeypatch):
    root = tmp_path / "source"
    (root / "dist").mkdir(parents=True)
    for name in ("docs", "guest", "resources"):
        (root / name).mkdir()
        (root / name / "example.txt").write_text("support file", encoding="utf-8")
    for name in ("README.md", "LICENSE", "THIRD_PARTY.md"):
        (root / name).write_text("notice", encoding="utf-8")
    packaging = root / "packaging/linux"
    packaging.mkdir(parents=True)
    for name in ("virtue.desktop", "virtue.svg", "virtue-setup-host", "70-virtue-kvm.rules"):
        (packaging / name).write_text("desktop resource", encoding="utf-8")
    write_wheel(root / "dist" / f"virtue_desktop-{build_deb.VERSION}-py3-none-any.whl",
                [("virtue/app.py", b"# application", 0o644)])
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    write_wheel(wheelhouse / "pyside6_essentials-6.8.3-cp39-abi3-manylinux_2_28_x86_64.whl", [
        (LONG_PATH, b"long-path library", 0o644),
        (HELPER_PATH, b"native executable", 0o755),
    ])
    write_wheel(wheelhouse / "shiboken6-6.8.3-cp39-abi3-manylinux_2_28_x86_64.whl",
                [("shiboken6/__init__.py", b"# binding", 0o644)])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build_deb, "ROOT", root)
    monkeypatch.setattr(build_deb, "run", lambda args: None)
    monkeypatch.setattr(sys, "argv", ["build_deb.py", "--offline", "--wheelhouse", "wheelhouse", "--output", "output"])
    package = tmp_path / "output" / f"virtue-desktop_{build_deb.VERSION}_amd64.deb"

    def build():
        assert build_deb.main() == 0
        return package

    return build, package


@pytest.fixture
def package(package_builder):
    build, _ = package_builder
    return build()


def read_deb(package):
    data = package.read_bytes()
    assert data[:8] == b"!<arch>\n"
    offset = 8
    members = {}
    while offset < len(data):
        header = data[offset:offset + 60]
        assert len(header) == 60 and header[-2:] == b"`\n"
        size = int(header[48:58])
        name = header[:16].decode("ascii").strip().removesuffix("/")
        members[name] = data[offset + 60:offset + 60 + size]
        assert len(members[name]) == size
        offset += 60 + size + size % 2
    assert offset == len(data)
    assert list(members) == ["debian-binary", "control.tar.gz", "data.tar.gz"]
    assert members["debian-binary"] == b"2.0\n"
    return members


def test_deb_directories_permissions_and_checksums(package):
    members = read_deb(package)
    with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"])) as archive:
        entries = {member.name.removeprefix("./"): member for member in archive}
        files = {name: archive.extractfile(member).read() for name, member in entries.items() if member.isfile()}
    names = list(entries)
    for name, member in entries.items():
        assert member.uid == member.gid == member.mtime == 0
        for parent in PurePosixPath(name).parents:
            if parent == PurePosixPath("."):
                continue
            assert entries[str(parent)].isdir()
            assert entries[str(parent)].mode == 0o755
            assert names.index(str(parent)) < names.index(name)
    assert entries["usr/bin/virtue"].mode == 0o755
    assert entries["usr/libexec/virtue-setup-host"].mode == 0o755
    assert entries["opt/virtue/lib/" + HELPER_PATH].mode == 0o755
    assert entries["opt/virtue/lib/" + LONG_PATH].mode == 0o644
    assert b"\r" not in files["usr/bin/virtue"]
    assert b"\r" not in files["usr/libexec/virtue-setup-host"]
    with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"])) as archive:
        control = {member.name.removeprefix("./"): archive.extractfile(member).read() for member in archive if member.isfile()}
    expected = {name: hashlib.md5(content, usedforsecurity=False).hexdigest() for name, content in files.items()}
    recorded = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in control["md5sums"].decode("ascii").splitlines()}
    assert recorded == expected
    fields = dict(line.split(": ", 1) for line in control["control"].decode("utf-8").splitlines() if ": " in line)
    assert int(fields["Installed-Size"]) * 1024 >= sum(map(len, files.values()))
    assert b"Recommends:" not in control["control"]
    for dependency in ("flatpak", "vulkan-tools", "swtpm-tools", "pkexec", "xorriso", "wimtools", "7zip"):
        assert dependency in fields["Depends"].split(", ")
    checksum = f"{hashlib.sha256(package.read_bytes()).hexdigest()}  {package.name}\n".encode("ascii")
    assert package.with_suffix(".deb.sha256").read_bytes() == checksum


def test_tar_headers_are_supported_by_dpkg(package):
    members = read_deb(package)
    for name in ("control.tar.gz", "data.tar.gz"):
        data = gzip.decompress(members[name])
        offset = 0
        types = []
        while any(data[offset:offset + 512]):
            header = data[offset:offset + 512]
            assert len(header) == 512
            types.append(header[156:157])
            size = int(header[124:136].rstrip(b"\0 "), 8)
            assert size >= 0
            offset += 512 + ((size + 511) // 512) * 512
        assert tarfile.XHDTYPE not in types
        assert tarfile.XGLTYPE not in types
        if name == "data.tar.gz":
            assert tarfile.GNUTYPE_LONGNAME in types


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("dpkg-deb") is None, reason="Requires Linux dpkg-deb")
def test_dpkg_extracts_long_paths_and_executables(package, tmp_path):
    destination = tmp_path / "installed"
    subprocess.run(["dpkg-deb", "--info", str(package)], check=True, capture_output=True)
    subprocess.run(["dpkg-deb", "--extract", str(package), str(destination)], check=True, capture_output=True)
    assert (destination / "opt/virtue/lib" / LONG_PATH).read_bytes() == b"long-path library"
    assert (destination / "opt/virtue/lib" / HELPER_PATH).stat().st_mode & 0o111


def test_failed_build_keeps_previous_package(package_builder, monkeypatch):
    build, package = package_builder
    package.parent.mkdir()
    package.write_bytes(b"previous package")
    checksum = package.with_suffix(".deb.sha256")
    checksum.write_bytes(b"previous checksum")
    original = build_deb.tar_bytes

    def fail_payload(directory, executables=None):
        if directory.name == "data":
            raise OSError("archive write failed")
        return original(directory, executables)

    monkeypatch.setattr(build_deb, "tar_bytes", fail_payload)
    with pytest.raises(OSError, match="archive write failed"):
        build()
    assert package.read_bytes() == b"previous package"
    assert checksum.read_bytes() == b"previous checksum"
    assert set(package.parent.iterdir()) == {package, checksum}
