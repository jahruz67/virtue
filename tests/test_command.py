import json
from pathlib import Path
import unittest

from virtue.backends.backend import BackendError
from virtue.backends.command import build_qemu_command
from virtue.domain import VMConfig


class QemuCommandTests(unittest.TestCase):
    def config(self, **kwargs):
        return VMConfig(id="a" * 32, name="Windows, engineering", iso_path="/media/Windows,11.iso", **kwargs)

    def command(self, config, **kwargs):
        return build_qemu_command(config, Path("/data/Windows, VM"), "/usr/bin/qemu-system-x86_64",
                                  "/usr/share/OVMF_CODE.fd", "/run/u/qmp.sock", "/run/u/tpm.sock", **kwargs)

    def test_yttrium_installer_kvm_uefi_tpm_no_shell(self):
        cmd = self.command(self.config())
        self.assertIsInstance(cmd, list)
        self.assertIn("kvm", cmd)
        self.assertNotIn("tcg", " ".join(cmd))
        self.assertIn("tpm-crb,tpmdev=tpm0", cmd)
        self.assertIn("gtk,gl=on,show-menubar=off,show-tabs=off,zoom-to-fit=on", cmd)
        self.assertIn("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", cmd)
        self.assertTrue(any("Windows,,11.iso" in arg for arg in cmd))
        block = json.loads(cmd[cmd.index("-blockdev") + 1])
        self.assertEqual(block["file"]["filename"], str(Path("/data/Windows, VM") / "disk.qcow2"))
        self.assertEqual(block["discard"], "unmap")
        self.assertIn("e1000e,netdev=network0", cmd)

    def test_installed_guest_detaches_installer(self):
        cmd = self.command(self.config(installed=True))
        self.assertNotIn("installer", " ".join(cmd))

    def test_yttrium_requires_security_consent(self):
        with self.assertRaises(BackendError):
            self.command(self.config(graphics="yttrium", secure_boot=False, graphics_consent=False))
        with self.assertRaises(BackendError):
            self.command(self.config(graphics="yttrium", graphics_consent=True, secure_boot=True))

    def test_yttrium_exact_stack_arguments(self):
        cmd = self.command(self.config(graphics="yttrium", secure_boot=False, graphics_consent=True),
                           accelerator="kvm,honor-guest-pat=on", audio_backend=None)
        self.assertIn("virtio-vga-gl,hostmem=4G,blob=on,venus=on", cmd)
        self.assertIn("memory-backend-memfd,id=ram,size=8192M,share=on", cmd)
        self.assertIn("kvm,honor-guest-pat=on", cmd)
        self.assertNotIn("-audiodev", cmd)

    def test_safe_fallback_preserves_configuration(self):
        config = self.config(graphics="yttrium", secure_boot=False, graphics_consent=True)
        cmd = self.command(config, safe_graphics=True)
        self.assertIn("gtk,gl=off,show-menubar=off,show-tabs=off,zoom-to-fit=on", cmd)
        self.assertNotIn("venus=on", " ".join(cmd))
        self.assertEqual(config.graphics, "yttrium")

    def test_virtio_is_explicit(self):
        cmd = self.command(self.config(storage_bus="virtio", virtio_network=True))
        self.assertIn("virtio-blk-pci,drive=osdisk,bootindex=0", cmd)
        self.assertIn("virtio-net-pci,netdev=network0", cmd)

    def test_control_characters_rejected(self):
        config = self.config()
        config.name = "Windows\n-device"
        with self.assertRaises(BackendError):
            self.command(config)

    def test_no_arbitrary_optimization_flags(self):
        with self.assertRaises(BackendError):
            self.command(self.config(), hyperv=["unvalidated-flag"])

    def test_guest_agent_and_fullscreen(self):
        cmd = self.command(self.config(), fullscreen=True, guest_agent_socket="/run/u/qga.sock")
        self.assertIn("-full-screen", cmd)
        self.assertIn("virtserialport,chardev=qga,name=org.qemu.guest_agent.0", cmd)

    def test_automatic_setup_media_and_status_channel(self):
        cmd = self.command(self.config(drivers_iso_path="/cache/virtio.iso"), setup_iso="/data/virtue-setup.iso")
        devices = [cmd[index + 1] for index, arg in enumerate(cmd[:-1]) if arg == "-device"]
        assert "ide-cd,bus=ide.0,drive=installer,bootindex=1" in devices
        assert "ide-cd,bus=ide.1,drive=drivers" in devices
        assert "ide-cd,bus=ide.2,drive=virtue_setup" in devices
        assert "isa-serial,chardev=setup_status" in devices

    def test_repair_boot_uses_existing_disk_without_windows_installer(self):
        config = self.config(auto_setup=True, drivers_iso_path="/cache/virtio.iso")
        cmd = self.command(config, setup_iso="/data/virtue-repair.iso", repair_setup=True)
        assert "installer" not in " ".join(cmd)
        assert not any("Windows,,11.iso" in arg for arg in cmd)
        assert any("virtue-repair.iso" in arg for arg in cmd)
        block = json.loads(cmd[cmd.index("-blockdev") + 1])
        assert block["file"]["filename"] == str(Path("/data/Windows, VM") / "disk.qcow2")
        assert not config.installed


if __name__ == "__main__":
    unittest.main()
