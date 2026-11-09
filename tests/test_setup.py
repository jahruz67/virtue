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


@pytest.mark.parametrize("missing", [[], ["KVM access"]])
def test_cached_host_preparation_recovers_lost_permissions(tmp_path, monkeypatch, missing):
    from virtue.services import VMService
    from virtue.store import VMStore

    service = VMService(VMStore(tmp_path), backend=SimpleNamespace())
    cached = {"virtio": tmp_path / "cached.iso"}
    refreshed = {"virtio": tmp_path / "refreshed.iso"}
    service._assets = cached
    monkeypatch.setattr("virtue.services.sys.platform", "linux")
    monkeypatch.setattr(setup, "_missing_host_components", lambda: missing)
    calls = []

    def prepare(progress, *, repair):
        calls.append(repair)
        return refreshed

    monkeypatch.setattr(setup, "prepare_runtime", prepare)
    assert service.prepare_host() is (refreshed if missing else cached)
    assert calls == ([False] if missing else [])


def test_ready_requires_observed_hardware_rendering(tmp_path):
    from virtue.services import VMService
    from virtue.store import VMStore

    store = VMStore(tmp_path)
    config = VMConfig.new({"name": "Windows", "drivers_iso_path": str(tmp_path / "removed-virtio.iso")})
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
    assert store.load(config.id).drivers_iso_path == ""


def test_cached_setup_media_restores_virtio_attachment_without_replacing_credentials(tmp_path, monkeypatch):
    config = VMConfig.new({"name": "Windows", "iso_path": "/removed/Windows.iso"})
    media = tmp_path / "virtue-setup.iso"
    media.write_bytes(b"prepared Windows setup media")
    credentials = tmp_path / "windows-credentials.json"
    account = b'{"username":"Virtue","password":"keep-existing"}'
    credentials.write_bytes(account)
    virtio = tmp_path / "virtio.iso"
    virtio.write_bytes(b"verified VirtIO media")
    monkeypatch.setattr(provisioning, "inspect_windows_iso", lambda *args: pytest.fail("Cached media must be reused"))
    monkeypatch.setattr(provisioning, "_write_setup_media", lambda *args: pytest.fail("Cached media must not be rebuilt"))

    provisioning.prepare_windows(config, tmp_path, {"virtio": virtio})

    assert config.drivers_iso_path == str(virtio)
    assert credentials.read_bytes() == account
    assert media.read_bytes() == b"prepared Windows setup media"


def test_repair_media_refreshes_scripts_without_windows_installation_answers(tmp_path, monkeypatch):
    directory = tmp_path / "vm"
    directory.mkdir()
    disk = directory / "disk.qcow2"
    disk.write_bytes(b"existing Windows and user files")
    credentials = directory / "windows-credentials.json"
    credentials.write_text('{"username":"Virtue","password":"keep-existing"}')
    account_before = credentials.read_bytes()
    guest = tmp_path / "guest"
    guest.mkdir()
    for name in ("Install-VirtueGuest.ps1", "Collect-VirtueGpuReport.ps1", "Retry setup.cmd"):
        (guest / name).write_text("version 1")
    assets = {"guest": tmp_path / "drivers.zip", "vulkan": tmp_path / "VulkanRT.exe"}
    for path in assets.values():
        path.write_bytes(b"verified payload")
    monkeypatch.setattr(provisioning, "support_folder", lambda name: guest)
    monkeypatch.setattr(provisioning, "asset_specs", lambda: {})

    def make_iso(args):
        files = Path(args[-1])
        assert not (files / "Autounattend.xml").exists()
        assert (files / "Retry setup.cmd").is_file()
        assert (files / "setup-version.txt").read_text() == provisioning.repair_version()
        assert (files / "windows-account.json").read_bytes() == account_before
        Path(args[args.index("-o") + 1]).write_bytes((files / "Install-VirtueGuest.ps1").read_bytes())

    monkeypatch.setattr(provisioning, "run_setup", make_iso)
    target = provisioning.prepare_repair(directory, assets)
    assert target.read_text() == "version 1"
    (guest / "Install-VirtueGuest.ps1").write_text("updated version")
    assert provisioning.prepare_repair(directory, assets).read_text() == "updated version"
    assert disk.read_bytes() == b"existing Windows and user files"
    assert credentials.read_bytes() == account_before


@pytest.mark.parametrize("state", ["running", "stopped", "paused"])
@pytest.mark.parametrize("agent_available", [True, False])
def test_retry_repairs_existing_windows_and_preserves_recovery_instructions(tmp_path, monkeypatch, state, agent_available):
    from virtue.services import VMService
    from virtue.store import VMStore, atomic_json

    store = VMStore(tmp_path)
    config = VMConfig.new({"name": "Windows", "iso_path": "/removed/Windows.iso"})
    store.save(config)
    directory = store.directory(config.id)
    (directory / "disk.qcow2").write_bytes(b"keep Windows")
    (directory / "windows-credentials.json").write_text('{"password":"keep"}')
    before = {name: (directory / name).read_bytes() for name in ("disk.qcow2", "config.json", "windows-credentials.json")}
    guest_status = {"state": "error", "phase": 1, "message": "Access is denied (0x80070005)"}
    atomic_json(directory / "setup-progress.json", guest_status)
    calls = []

    def prepare_repair(target, assets, progress):
        repair_iso = target / "virtue-repair.iso"
        repair_iso.write_bytes(b"updated repair-only media")
        return repair_iso

    def resume(vm_id, repair_iso=None):
        assert vm_id == config.id
        assert repair_iso == directory / "virtue-repair.iso"
        calls.append("resume")
        return agent_available

    backend = SimpleNamespace(status=lambda vm_id: {"state": state},
                              start=lambda config: calls.append("start"), retry_setup=resume,
                              resume=lambda vm_id: calls.append("unpause"),
                              setup_status=lambda vm_id: dict(guest_status))
    service = VMService(store, backend=backend)
    monkeypatch.setattr(service, "prepare_host", lambda progress: {})
    monkeypatch.setattr(provisioning, "prepare_repair", prepare_repair)
    monkeypatch.setattr(provisioning, "prepare_windows", lambda *args: pytest.fail("Windows must not be reinstalled"))
    service.retry_setup(config.id)
    assert calls == ({"stopped": ["start", "resume"], "paused": ["unpause", "resume"]}.get(state, ["resume"]))
    assert all((directory / name).read_bytes() == data for name, data in before.items())
    saved = service._setup_progress(directory)
    assert saved["phase"] == 1
    if not agent_available:
        assert saved["state"] == "error" and "Retry setup.cmd" in saved["message"]
    service._reconcile_setup(config)
    assert service._setup_progress(directory) == saved
    guest_status["updated_at"] = "new-repair-attempt"
    service._reconcile_setup(config)
    assert service._setup_progress(directory)["state"] == "error"
    assert "repair_previous_status" not in service._setup_progress(directory)
    guest_status.update(state="installing", message="Resuming Windows setup with updated components…")
    service._reconcile_setup(config)
    assert service._setup_progress(directory)["message"] == guest_status["message"]
    assert "repair_previous_status" not in service._setup_progress(directory)


def test_paused_windows_installation_resumes_without_a_guest_agent(tmp_path, monkeypatch):
    from virtue.services import VMService
    from virtue.store import VMStore, atomic_json

    store = VMStore(tmp_path)
    config = VMConfig.new({"name": "Windows"})
    store.save(config)
    atomic_json(store.directory(config.id) / "setup-progress.json", {"state": "error", "message": "Interrupted"})
    resumed = []
    backend = SimpleNamespace(status=lambda vm_id: {"state": "paused"}, resume=resumed.append,
                              retry_setup=lambda *args: pytest.fail("Windows Setup has no guest agent yet"))
    service = VMService(store, backend=backend)
    monkeypatch.setattr(service, "prepare_host", lambda progress: {})

    service.retry_setup(config.id)

    assert resumed == [config.id]
    assert service._setup_progress(store.directory(config.id))["state"] == "installing"


def test_stopped_repair_revalidates_downloads_and_refreshes_missing_virtio_media(tmp_path, monkeypatch):
    from virtue.services import VMService
    from virtue.store import VMStore, atomic_json

    store = VMStore(tmp_path / "library")
    config = VMConfig.new({"name": "Windows", "iso_path": str(tmp_path / "removed-windows.iso"),
                           "drivers_iso_path": str(tmp_path / "removed-virtio.iso")})
    store.save(config)
    directory = store.directory(config.id)
    (directory / "disk.qcow2").write_bytes(b"existing Windows")
    credentials = directory / "windows-credentials.json"
    credentials.write_bytes(b'{"username":"Virtue","password":"keep"}')
    atomic_json(directory / "setup-progress.json", {"state": "error", "phase": 2, "message": "Driver failed"})
    refreshed = {key: tmp_path / (key + ".verified") for key in ("guest", "virtio", "vulkan", "host")}
    for path in refreshed.values():
        path.write_bytes(b"verified download")
    started = []
    backend = SimpleNamespace(status=lambda vm_id: {"state": "stopped"}, start=started.append,
                              retry_setup=lambda *args, **kwargs: True)
    service = VMService(store, backend=backend)
    service._assets = {"guest": tmp_path / "corrupt.zip", "virtio": tmp_path / "removed-virtio.iso"}
    verified = []
    monkeypatch.setattr("virtue.services.sys.platform", "linux")
    monkeypatch.setattr(setup, "_missing_host_components", lambda: [])

    def prepare_runtime(progress, *, repair):
        assert not repair
        verified.append(True)
        return refreshed

    def prepare_repair(target, assets, progress):
        assert assets is refreshed
        media = target / "virtue-repair.iso"
        media.write_bytes(b"repair only")
        return media

    monkeypatch.setattr(setup, "prepare_runtime", prepare_runtime)
    monkeypatch.setattr(provisioning, "prepare_repair", prepare_repair)
    monkeypatch.setattr(provisioning, "prepare_windows", lambda *args: pytest.fail("Existing Windows must be preserved"))

    service.retry_setup(config.id)

    assert verified == [True]
    assert len(started) == 1 and started[0].drivers_iso_path == str(refreshed["virtio"])
    assert store.load(config.id).drivers_iso_path == str(refreshed["virtio"])
    assert (directory / "disk.qcow2").read_bytes() == b"existing Windows"
    assert credentials.read_bytes() == b'{"username":"Virtue","password":"keep"}'
