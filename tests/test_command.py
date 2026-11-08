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

    def test_safe_installer_kvm_uefi_tpm_no_shell(self):
        cmd = self.command(self.config())
        self.assertIsInstance(cmd, list)
        self.assertIn("kvm", cmd)
        self.assertNotIn("tcg", " ".join(cmd))
        self.assertIn("tpm-crb,tpmdev=tpm0", cmd)
        self.assertIn("gtk,gl=off", cmd)
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
            self.command(self.config(graphics="yttrium", secure_boot=False))
        with self.assertRaises(BackendError):
            self.command(self.config(graphics="yttrium", graphics_consent=True))

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
        self.assertIn("gtk,gl=off", cmd)
        self.assertNotIn("venus=on", " ".join(cmd))
        self.assertEqual(config.graphics, "yttrium")

    def test_virtio_is_explicit(self):
        cmd = self.command(self.config(storage_bus="virtio", virtio_network=True))
        self.assertIn("virtio-blk-pci,drive=osdisk,bootindex=1", cmd)
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


if __name__ == "__main__":
    unittest.main()
