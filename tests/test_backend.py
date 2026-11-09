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
        (self.directory / "OVMF_CODE.fd").write_bytes(b"code-after")
        (self.directory / "firmware.json").write_text('{"changed": true}')
        changed_config = {**asdict(self.config), "memory_mib": 4096}
        (self.directory / "config.json").write_text(json.dumps(changed_config))
        (self.directory / "tpm" / "state").write_bytes(b"tpm-after")
        self.backend.restore_snapshot(self.config, "working")
        self.assertEqual((self.directory / "disk.qcow2").read_bytes(), b"disk-before")
        self.assertEqual((self.directory / "OVMF_VARS.fd").read_bytes(), b"vars-before")
        self.assertEqual((self.directory / "OVMF_CODE.fd").read_bytes(), b"code-before")
        self.assertEqual((self.directory / "firmware.json").read_bytes(), b'{}')
        self.assertEqual(json.loads((self.directory / "config.json").read_text()), asdict(self.config))
        self.assertEqual((self.directory / "tpm" / "state").read_bytes(), b"tpm-before")
        snapshots = self.backend.list_snapshots(self.config)
        recovery = next(name for name in snapshots if name.startswith("before-restore-"))
        self.assertEqual((self.directory / "snapshots" / recovery / "disk.qcow2").read_bytes(), b"disk-after")
        self.assertEqual(json.loads((self.directory / "snapshots" / recovery / "config.json").read_text()), changed_config)
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

    def test_qemu_identity_records_management_socket_host_pid(self):
        vm_uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        identity = {"pid": 4242, "start_ticks": "same", "cmdline": ["qemu", "-uuid", vm_uuid]}
        client = SimpleNamespace(
            socket=SimpleNamespace(getsockopt=lambda *_: struct.pack("3i", 4242, 1000, 1000)),
            execute=lambda _: {"UUID": vm_uuid},
        )
        with patch("virtue.backends.linux.socket.SO_PEERCRED", 17, create=True), \
             patch("virtue.backends.linux.os.getuid", return_value=1000, create=True), \
             patch("virtue.backends.linux._process_identity", return_value=identity) as probe:
            self.assertEqual(self.backend._qemu_identity(client, self.config.id), identity)
        probe.assert_called_once_with(4242)

    def test_qemu_socket_identity_rejects_wrong_owner_process_or_vm(self):
        vm_uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        other_uuid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        identity = {"pid": 4242, "start_ticks": "same", "cmdline": ["qemu", "-uuid", vm_uuid]}
        cases = (
            (1001, identity, vm_uuid),
            (1000, None, vm_uuid),
            (1000, {**identity, "cmdline": ["qemu", "-uuid", other_uuid]}, vm_uuid),
            (1000, identity, other_uuid),
        )
        for uid, process, reported_uuid in cases:
            client = SimpleNamespace(
                socket=SimpleNamespace(getsockopt=lambda *_: struct.pack("3i", 4242, uid, 1000)),
                execute=lambda _: {"UUID": reported_uuid},
            )
            with self.subTest(uid=uid, process=process, reported_uuid=reported_uuid), \
                 patch("virtue.backends.linux.socket.SO_PEERCRED", 17, create=True), \
                 patch("virtue.backends.linux.os.getuid", return_value=1000, create=True), \
                 patch("virtue.backends.linux._process_identity", return_value=process), \
                 self.assertRaises(BackendError):
                self.backend._qemu_identity(client, self.config.id)

    def test_create_uses_matching_firmware_and_the_selected_qemu_machine(self):
        files = {"CODE.fd": b"secure code", "VARS.fd": b"enrolled variables",
                 "CODE_PLAIN.fd": b"plain code", "VARS_PLAIN.fd": b"unenrolled variables",
                 "Windows.iso": b"installation media"}
        for name, data in files.items():
            (self.root / name).write_bytes(data)
        host = SimpleNamespace(
            qemu="system-qemu", qemu_img="qemu-img", qemu_prefix=["native-qemu"],
            yttrium_prefix=["flatpak", "run", "--user", "org.qemu.yttrium"], ovmf_secure=True,
            ovmf_code=str(self.root / "CODE.fd"), ovmf_vars=str(self.root / "VARS.fd"),
            ovmf_code_plain=str(self.root / "CODE_PLAIN.fd"), ovmf_vars_plain=str(self.root / "VARS_PLAIN.fd"),
        )
        backend = LinuxBackend(self.root, host)
        for vm_id, graphics, secure in (("b" * 32, "yttrium", False), ("c" * 32, "safe", True)):
            config = VMConfig(id=vm_id, name="Windows", iso_path=str(self.root / "Windows.iso"),
                              graphics=graphics, secure_boot=secure)
            queried = []

            def provision(args, timeout=60):
                if args[-2:] == ["-machine", "help"]:
                    queried.append(args[:-2])
                    self.assertIn(args[:-2], (host.qemu_prefix, host.yttrium_prefix))
                    version = "9.1" if args[:-2] == host.yttrium_prefix else "10.0"
                    return SimpleNamespace(stdout=f"pc-q35-8.2 older\npc-q35-{version} current\n")
                self.assertEqual(args[0], "qemu-img")
                Path(args[-2]).write_bytes(b"new disk")

            with self.subTest(graphics=graphics), patch.object(backend, "_supported"), \
                 patch.object(backend, "_run", side_effect=provision):
                backend.create(config)
                self.assertEqual(config.machine_type, "pc-q35-8.2" if graphics == "yttrium" else "pc-q35-10.0")
                self.assertEqual(queried, [host.yttrium_prefix, host.qemu_prefix] if graphics == "yttrium" else [host.qemu_prefix])
                self.assertEqual((self.root / vm_id / "OVMF_CODE.fd").read_bytes(),
                                 files["CODE.fd" if secure else "CODE_PLAIN.fd"])
                self.assertEqual((self.root / vm_id / "OVMF_VARS.fd").read_bytes(),
                                 files["VARS.fd" if secure else "VARS_PLAIN.fd"])

    def test_incompatible_qemu_machine_versions_are_rejected_before_creation(self):
        self.backend.host = SimpleNamespace(qemu_prefix=["native-qemu"], yttrium_prefix=["yttrium-qemu"])
        def machines(args, timeout=60):
            version = "8.2" if args[0] == "native-qemu" else "9.1"
            return SimpleNamespace(stdout=f"pc-q35-{version} machine\n")
        with patch.object(self.backend, "_run", side_effect=machines), \
             self.assertRaisesRegex(BackendError, "no shared Q35"):
            self.backend._q35_machine_type("yttrium")

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
        host = SimpleNamespace(qemu_img="qemu-img", ovmf_secure=True, ovmf_code=str(code),
                               ovmf_vars=str(template), ovmf_code_plain=str(code), ovmf_vars_plain=str(template))
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

    def test_setup_status_uses_the_newest_report_and_preserves_serial_fallback(self):
        serial_report = {"state": "error", "phase": 2, "message": "Driver failed",
                         "updated_at": "2026-11-09T20:00:00+00:00"}
        (self.directory / "setup-events.jsonl").write_text(json.dumps(serial_report) + "\n")
        fresh_report = {"state": "ready", "phase": 3, "updated_at": "2026-11-09T20:01:00+00:00"}
        stale_report = {"state": "installing", "phase": 1, "updated_at": "2026-11-09T19:59:00+00:00"}
        for report, expected in ((fresh_report, fresh_report), (stale_report, serial_report),
                                 (OSError("agent unavailable"), serial_report)):
            with self.subTest(report=report), \
                 patch.object(self.backend, "_metadata", return_value={"vm_id": self.config.id}), \
                 patch.object(self.backend, "_identity_matches", return_value=True), \
                 patch.object(self.backend, "_runtime_dir", return_value=self.root / "runtime"), \
                 patch("virtue.backends.guest_agent.GuestAgent") as guest_agent:
                if isinstance(report, OSError):
                    guest_agent.return_value.__enter__.side_effect = report
                else:
                    guest_agent.return_value.__enter__.return_value.setup_status.return_value = report
                self.assertEqual(self.backend.setup_status(self.config.id), expected)

    def test_repair_replaces_setup_media_and_detaches_windows_installer_without_agent(self):
        repair_iso = self.directory / "virtue-repair.iso"
        repair_iso.write_bytes(b"updated repair media")
        commands = []

        def execute(command, arguments=None):
            commands.append((command, arguments))
            if command == "query-block":
                return [{"device": "installer"}, {"device": "virtue_setup"}, {"device": "osdisk"}]

        client = SimpleNamespace(execute=execute, close=lambda: None)
        with patch.object(self.backend, "_qmp", return_value=client), \
             patch.object(self.backend, "_resume_guest_setup", side_effect=OSError("agent unavailable")):
            self.assertFalse(self.backend.retry_setup(self.config.id, repair_iso=repair_iso))
        self.assertIn(("eject", {"device": "installer", "force": True}), commands)
        self.assertIn(("blockdev-change-medium", {"device": "virtue_setup", "filename": str(repair_iso), "format": "raw"}), commands)
        self.assertEqual((self.directory / "disk.qcow2").read_bytes(), b"disk-before")

    def test_repair_boot_selects_repair_media_without_stale_file_bind_or_windows_iso(self):
        self.config.auto_setup, self.config.installed = True, False
        self.config.iso_path = "/removed/Windows.iso"
        repair_iso = self.directory / "virtue-repair.iso"
        repair_iso.write_bytes(b"updated repair media")
        self.backend.host = SimpleNamespace(yttrium_ready=True, yttrium_prefix=["flatpak", "run", "org.qemu.yttrium"])
        self.assertEqual(self.backend._setup_iso(self.config, self.directory), repair_iso)
        prefix = self.backend._prefix("yttrium", self.directory, self.root / "runtime", self.config)
        self.assertIn(f"--filesystem={self.directory}", prefix)
        self.assertNotIn(f"--filesystem={repair_iso}:ro", prefix)
        self.assertNotIn(self.config.iso_path, " ".join(prefix))

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
