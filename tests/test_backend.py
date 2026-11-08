"""File recovery and process-identity tests; these do not emulate a KVM boot."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from virtue.backends.backend import BackendError
from virtue.backends.linux import LinuxBackend, _atomic_json
from virtue.domain import VMConfig


class BackendRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.backend = LinuxBackend(self.root, SimpleNamespace(qemu_img="qemu-img"))
        original_run = self.backend._run
        def run_with_synthetic_image_info(args, timeout=60):
            if args[0] == "qemu-img":
                return SimpleNamespace(stdout="{}")
            return original_run(args, timeout)
        self.probe = patch.object(self.backend, "_run", side_effect=run_with_synthetic_image_info)
        self.probe.start()
        # Recovery tests use tiny opaque files; format validation is tested
        # separately below with real QCOW2 header layouts.
        self.header_probe = patch.object(self.backend, "_validate_disk_header")
        self.header_probe.start()
        self.config = VMConfig(id="a" * 32, name="Windows", installed=True)
        self.directory = self.root / self.config.id
        self.directory.mkdir()
        for name, data in {"disk.qcow2": b"disk-before", "OVMF_VARS.fd": b"vars-before",
                           "OVMF_CODE.fd": b"code-before",
                           "firmware.json": b'{}', "config.json": json.dumps(asdict(self.config)).encode()}.items():
            (self.directory / name).write_bytes(data)
        (self.directory / "tpm").mkdir()
        (self.directory / "tpm" / "state").write_bytes(b"tpm-before")

    def tearDown(self):
        self.probe.stop()
        self.header_probe.stop()
        self.temp.cleanup()

    def test_snapshot_restores_disk_nvram_tpm_together_and_preserves_current(self):
        self.assertEqual(self.backend.snapshot(self.config, "working"), "working")
        (self.directory / "disk.qcow2").write_bytes(b"disk-after")
        (self.directory / "OVMF_VARS.fd").write_bytes(b"vars-after")
        (self.directory / "tpm" / "state").write_bytes(b"tpm-after")
        self.backend.restore_snapshot(self.config, "working")
        self.assertEqual((self.directory / "disk.qcow2").read_bytes(), b"disk-before")
        self.assertEqual((self.directory / "OVMF_VARS.fd").read_bytes(), b"vars-before")
        self.assertEqual((self.directory / "tpm" / "state").read_bytes(), b"tpm-before")
        snapshots = self.backend.list_snapshots(self.config)
        recovery = next(name for name in snapshots if name.startswith("before-restore-"))
        self.assertEqual((self.directory / "snapshots" / recovery / "disk.qcow2").read_bytes(), b"disk-after")
        self.assertFalse((self.directory / "restore_pending.json").exists())

    def test_reject_snapshot_path_traversal(self):
        for name in ("../outside", "/absolute", ".hidden", "bad/child", "bad\\child"):
            with self.subTest(name=name), self.assertRaises(BackendError):
                self.backend.snapshot(self.config, name)

    def test_running_vm_cannot_snapshot_delete_expand_or_restore(self):
        with patch.object(self.backend, "is_active", return_value=True):
            for action in (lambda: self.backend.snapshot(self.config, "unsafe"),
                           lambda: self.backend.delete(self.config),
                           lambda: self.backend.expand_disk(self.config, 256),
                           lambda: self.backend.restore_snapshot(self.config, "unsafe")):
                with self.assertRaisesRegex(BackendError, "Shut down"):
                    action()
        self.assertTrue((self.directory / "disk.qcow2").exists())

    def test_delete_is_recoverable_move(self):
        # File-lock-open directory rename works on Linux, but Windows may reject
        # it. Simulate the lock there so this exercises the storage policy only.
        from contextlib import contextmanager
        @contextmanager
        def unlocked(_):
            yield self.directory
        with patch.object(self.backend, "_lock", unlocked):
            self.backend.delete(self.config)
        self.assertFalse(self.directory.exists())
        saved = list((self.root / ".trash").iterdir())
        self.assertEqual(len(saved), 1)
        self.assertEqual((saved[0] / "disk.qcow2").read_bytes(), b"disk-before")

    def test_invalid_vm_identifier_never_opens_other_directory(self):
        for value in ("../escape", "", "a" * 31, "A" * 32):
            with self.subTest(value=value), self.assertRaises(BackendError):
                self.backend.status(value)

    def test_pid_reuse_not_recognized_as_our_guest(self):
        metadata = {"vm_id": self.config.id, "qemu": {"pid": 42, "start_ticks": "old"}}
        with patch("virtue.backends.linux._process_identity", return_value={"pid": 42, "start_ticks": "new", "cmdline": []}):
            self.assertFalse(self.backend._identity_matches(metadata))

    def test_different_uuid_not_recognized_as_our_guest(self):
        metadata = {"vm_id": self.config.id, "qemu": {"pid": 42, "start_ticks": "same"}}
        with patch("virtue.backends.linux._process_identity", return_value={"pid": 42, "start_ticks": "same", "cmdline": ["qemu", "-uuid", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"]}):
            self.assertFalse(self.backend._identity_matches(metadata))

    def test_create_rolls_back_only_files_it_created(self):
        vm_id = "b" * 32
        target = self.root / vm_id
        target.mkdir()
        (target / "user-note.txt").write_text("preserve")
        code = self.root / "CODE.fd"
        template = self.root / "VARS.fd"
        iso = self.root / "Windows.iso"
        for path in (code, template, iso):
            path.write_bytes(b"example")
        host = SimpleNamespace(qemu_img="qemu-img", ovmf_secure=True, ovmf_code=str(code), ovmf_vars=str(template))
        backend = LinuxBackend(self.root, host)
        def provision(args, timeout=60):
            (target / "disk.qcow2").write_bytes(b"new")
        config = VMConfig(id=vm_id, name="Windows", iso_path=str(iso))
        with patch.object(backend, "_supported"), patch.object(backend, "_run", side_effect=provision), \
             patch("virtue.backends.linux.shutil.copy2", side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                backend.create(config)
        self.assertEqual((target / "user-note.txt").read_text(), "preserve")
        self.assertFalse((target / "disk.qcow2").exists())

    def test_invalid_manifest_cannot_restore(self):
        self.backend.snapshot(self.config, "saved")
        manifest = self.directory / "snapshots" / "saved" / "manifest.json"
        data = json.loads(manifest.read_text())
        data["vm_id"] = "b" * 32
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(BackendError, "manifest"):
            self.backend.restore_snapshot(self.config, "saved")

    def test_expand_never_shrinks(self):
        for size in (64, 128, True):
            with self.assertRaises(BackendError):
                self.backend.expand_disk(self.config, size)

    def test_prepare_offline_checks_managed_paths(self):
        with patch.object(self.backend, "_require_stopped") as stopped:
            self.backend.prepare_offline(self.config)
            stopped.assert_called_once_with(self.config.id)

    def test_failed_restore_rolls_back_disk_nvram_and_tpm(self):
        self.backend.snapshot(self.config, "saved")
        (self.directory / "disk.qcow2").write_bytes(b"disk-current")
        (self.directory / "OVMF_VARS.fd").write_bytes(b"vars-current")
        original_rename = Path.rename
        failed = [False]
        def fail_one_rename(source, target):
            if source.parent.name.startswith(".restore-") and source.name == "config.json" and not failed[0]:
                failed[0] = True
                raise OSError("Injected commit failure")
            return original_rename(source, target)
        with patch.object(Path, "rename", fail_one_rename):
            with self.assertRaisesRegex(OSError, "Injected"):
                self.backend.restore_snapshot(self.config, "saved")
        self.assertEqual((self.directory / "disk.qcow2").read_bytes(), b"disk-current")
        self.assertEqual((self.directory / "OVMF_VARS.fd").read_bytes(), b"vars-current")
        self.assertEqual((self.directory / "tpm" / "state").read_bytes(), b"tpm-before")
        self.assertFalse((self.directory / "restore_pending.json").exists())


class DiskFormatTests(unittest.TestCase):
    def test_standalone_headers_and_external_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            disk = Path(temporary) / "disk.qcow2"
            for version in (2, 3):
                header = bytearray(104)
                header[:4] = b"QFI\xfb"
                struct.pack_into(">I", header, 4, version)
                disk.write_bytes(header)
                LinuxBackend._validate_disk_header(disk)
            struct.pack_into(">Q", header, 72, 1 << 2)
            disk.write_bytes(header)
            with self.assertRaisesRegex(BackendError, "external data"):
                LinuxBackend._validate_disk_header(disk)
            struct.pack_into(">Q", header, 72, 0)
            struct.pack_into(">Q", header, 8, 104)
            disk.write_bytes(header)
            with self.assertRaisesRegex(BackendError, "backing"):
                LinuxBackend._validate_disk_header(disk)

    def test_truncated_image_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            disk = Path(temporary) / "disk.qcow2"
            disk.write_bytes(b"QFI\xfb")
            with self.assertRaises(BackendError):
                LinuxBackend._validate_disk_header(disk)


if __name__ == "__main__":
    unittest.main()
