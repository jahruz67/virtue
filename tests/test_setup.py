"""Automatic setup checks with synthetic downloads and media; no VM is launched."""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from virtue.domain import VMConfig, ValidationError
from virtue import provisioning, setup


class Response(io.BytesIO):
    url = "https://example.test/driver.zip"

    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def test_download_verifies_content_and_reuses_cache(tmp_path, monkeypatch):
    data = b"driver archive"
    spec = {"url": Response.url, "sha256": hashlib.sha256(data).hexdigest(), "filename": "driver.zip"}
    calls = []

    def download(*args, **kwargs):
        calls.append(args)
        return Response(data)

    monkeypatch.setattr(setup, "urlopen", download)
    messages = []
    path = setup.download_asset(spec, tmp_path, messages.append)
    assert path.read_bytes() == data
    assert setup.download_asset(spec, tmp_path) == path
    assert len(calls) == 1 and messages


def test_corrupt_download_is_not_published(tmp_path, monkeypatch):
    spec = {"url": Response.url, "sha256": "a" * 64, "filename": "driver.zip"}
    monkeypatch.setattr(setup, "urlopen", lambda *args, **kwargs: Response(b"corrupt archive"))
    with pytest.raises(ValidationError, match="verification"):
        setup.download_asset(spec, tmp_path)
    assert not list(tmp_path.iterdir())


def test_answer_file_installs_only_the_new_vm_disk_and_escapes_values():
    config = VMConfig.new({"name": "Windows"})
    password = 'V!<password&"'
    image = {"image_index": 6, "locale": "en-GB"}
    root = ET.fromstring(provisioning.answer_file(config, image, password))
    ns = {"u": "urn:schemas-microsoft-com:unattend"}
    assert root.find(".//u:Disk/u:DiskID", ns).text == "0"
    assert root.find(".//u:Disk/u:WillWipeDisk", ns).text == "true"
    assert root.find(".//u:InstallFrom/u:MetaData/u:Value", ns).text == "6"
    assert all(value.text == password for value in root.findall(".//u:Password/u:Value", ns))
    assert root.find(".//u:FirstLogonCommands/u:SynchronousCommand/u:CommandLine", ns) is not None
    assert "__" not in ET.tostring(root, encoding="unicode")


def test_iso_detection_selects_x64_pro_and_its_language(tmp_path, monkeypatch):
    iso = tmp_path / "Windows.iso"
    iso.write_bytes(b"synthetic ISO")
    xml = b'''<WIM>
    <IMAGE INDEX="1"><WINDOWS><ARCH>0</ARCH><EDITIONID>Core</EDITIONID></WINDOWS></IMAGE>
    <IMAGE INDEX="6"><WINDOWS><ARCH>9</ARCH><EDITIONID>Professional</EDITIONID>
    <VERSION><MAJOR>10</MAJOR><BUILD>26100</BUILD></VERSION>
    <LANGUAGES><DEFAULT>de-DE</DEFAULT></LANGUAGES></WINDOWS></IMAGE></WIM>'''

    def extract(args):
        destination = next(arg[2:] for arg in args if arg.startswith("-o"))
        (Path(destination) / "install.wim").write_bytes(b"synthetic WIM")

    monkeypatch.setattr(provisioning.shutil, "which", lambda name: name)
    monkeypatch.setattr(provisioning.shutil, "disk_usage", lambda path: SimpleNamespace(free=10 ** 12))
    monkeypatch.setattr(provisioning, "run_setup", extract)
    monkeypatch.setattr(provisioning.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=xml))
    assert provisioning.inspect_windows_iso(iso, tmp_path) == {"image_index": 6, "windows_version": "11", "locale": "de-DE"}


@pytest.mark.parametrize("missing_component", [None, "qemu-system-x86_64", "qemu-img", "swtpm", "7zip", "firmware"])
def test_runtime_installs_all_required_components_without_interactive_flags(tmp_path, monkeypatch, missing_component):
    monkeypatch.setattr(setup.sys, "platform", "linux")
    host_prepared = missing_component is None
    authorizations = []

    def which(name):
        missing = name == missing_component or (missing_component == "7zip" and name in {"7z", "7zz"})
        return None if missing and not host_prepared else name

    def authorize():
        nonlocal host_prepared
        host_prepared = True
        authorizations.append(True)

    monkeypatch.setattr(setup.shutil, "which", which)
    monkeypatch.setattr(setup, "authorize_host", authorize)
    monkeypatch.setattr(setup, "find_firmware", lambda: {} if missing_component == "firmware" and not host_prepared
                        else {"code_plain": "CODE.fd", "vars_plain": "VARS.fd"})
    monkeypatch.setattr(setup.os, "access", lambda *args: True)
    monkeypatch.setattr(setup, "cache_root", lambda: tmp_path)
    specs = {key: {"filename": key, "sha256": "a" * 64} for key in ("host", "guest", "virtio", "vulkan")}
    specs["host"]["freedesktop_runtime"] = "25.08"
    monkeypatch.setattr(setup, "asset_specs", lambda: specs)
    downloaded = []

    def download(spec, cache, progress):
        downloaded.append(spec["filename"])
        return cache / spec["filename"]

    monkeypatch.setattr(setup, "download_asset", download)
    monkeypatch.setattr(setup.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    commands = []
    monkeypatch.setattr(setup, "run_setup", lambda args: commands.append(args))
    paths = setup.prepare_runtime()
    assert authorizations == ([] if missing_component is None else [True])
    assert set(paths) == set(downloaded) == {"host", "guest", "virtio", "vulkan"}
    installs = [args for args in commands if args[1] == "install"]
    assert len(installs) == 2
    assert all("--user" in args and "--noninteractive" in args and "--assumeyes" in args for args in installs)
    assert json.loads((tmp_path / "host-runtime.json").read_text())["sha256"] == "a" * 64


def test_incomplete_host_preparation_stops_before_runtime_downloads(monkeypatch):
    monkeypatch.setattr(setup.sys, "platform", "linux")
    monkeypatch.setattr(setup.shutil, "which", lambda name: None if name == "qemu-img" else name)
    monkeypatch.setattr(setup.os, "access", lambda *args: True)
    monkeypatch.setattr(setup, "find_firmware", lambda: {"code_plain": "CODE.fd", "vars_plain": "VARS.fd"})
    monkeypatch.setattr(setup, "authorize_host", lambda: None)
    monkeypatch.setattr(setup, "asset_specs", lambda: pytest.fail("Downloads must wait for host preparation"))
    with pytest.raises(ValidationError, match="Host preparation is incomplete: qemu-img"):
        setup.prepare_runtime()


def test_ready_requires_observed_hardware_rendering(tmp_path):
    from virtue.services import VMService
    from virtue.store import VMStore

    store = VMStore(tmp_path)
    config = VMConfig.new({"name": "Windows"})
    store.save(config)
    (store.directory(config.id) / "virtue-setup.iso").write_bytes(b"media")
    report = {"schema_version": 1, "opengl": {"renderer": "llvmpipe", "draw_test_passed": True}}
    backend = SimpleNamespace(setup_status=lambda vm_id: {"state": "ready", "graphics_report": report})
    service = VMService(store, backend=backend)
    assert not service._reconcile_setup(config).installed
    assert json.loads((store.directory(config.id) / "setup-progress.json").read_text())["state"] == "error"
    report["opengl"]["renderer"] = "zink Vulkan (Virtio-GPU Venus (Intel Arc))"
    assert service._reconcile_setup(config).installed
    assert store.load(config.id).iso_path == ""
