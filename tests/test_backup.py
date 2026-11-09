import io
import tarfile
import pytest

from virtue.backup import export_backup, import_backup
from virtue.domain import VMConfig, ValidationError
from virtue.store import VMStore


def populated(tmp_path):
    store = VMStore(tmp_path / "vms")
    vm = VMConfig.new({"name": "Engineering", "installed": True})
    store.save(vm)
    directory = store.directory(vm.id)
    (directory / "disk.qcow2").write_bytes(b"fixture disk, not a bootable image")
    (directory / "OVMF_VARS.fd").write_bytes(b"firmware fixture")
    (directory / "OVMF_CODE.fd").write_bytes(b"firmware code fixture")
    (directory / "firmware.json").write_text('{"code":"OVMF_CODE.fd"}')
    (directory / "windows-credentials.json").write_text('{"username":"Virtue","password":"fixture"}')
    (directory / "tpm").mkdir()
    (directory / "tpm" / "tpm2-00.permall").write_bytes(b"TPM fixture")
    return store, vm, directory


def test_offline_archive_roundtrip_assigns_id_and_preserves_state(tmp_path):
    store, vm, directory = populated(tmp_path)
    backup = tmp_path / "windows.virtue"
    export_backup(directory, backup)
    imported = import_backup(backup, store.root)
    assert imported.id != vm.id
    assert imported.graphics == vm.graphics == "yttrium"
    for name in ("disk.qcow2", "OVMF_VARS.fd", "tpm/tpm2-00.permall", "windows-credentials.json"):
        assert (store.directory(imported.id) / name).read_bytes() == (directory / name).read_bytes()
    with pytest.raises(ValidationError, match="already exists"):
        export_backup(directory, backup)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "tpm/../../escape", "tpm\\outside"])
def test_malicious_archive_paths_are_rejected(tmp_path, name):
    backup = tmp_path / "bad.virtue"
    with tarfile.open(backup, "w") as archive:
        info = tarfile.TarInfo(name)
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    root = tmp_path / "vms"
    root.mkdir()
    with pytest.raises(ValidationError):
        import_backup(backup, root)
    assert list(root.iterdir()) == []


def test_tampering_fails_checksum_before_publish(tmp_path):
    store, _, directory = populated(tmp_path)
    original = tmp_path / "original.virtue"
    corrupt = tmp_path / "corrupt.virtue"
    export_backup(directory, original)
    with tarfile.open(original) as reader, tarfile.open(corrupt, "w") as writer:
        for member in reader:
            payload = reader.extractfile(member).read()
            if member.name == "disk.qcow2":
                payload = b"x" * len(payload)
            writer.addfile(member, io.BytesIO(payload))
    before = set(store.root.iterdir())
    with pytest.raises(ValidationError, match="checksum"):
        import_backup(corrupt, store.root)
    assert set(store.root.iterdir()) == before


def test_never_started_vm_import_recreates_empty_tpm(tmp_path):
    store, _, directory = populated(tmp_path)
    (directory / "tpm" / "tpm2-00.permall").unlink()
    backup = tmp_path / "never-started.virtue"
    export_backup(directory, backup)
    imported = import_backup(backup, store.root)
    assert (store.directory(imported.id) / "tpm").is_dir()
    assert (store.directory(imported.id) / "snapshots").is_dir()
