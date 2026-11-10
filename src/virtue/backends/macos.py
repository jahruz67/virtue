"""Native Apple Silicon adapter, sharing QEMU lifecycle and cold recovery."""
from pathlib import Path
import hashlib
import os
import platform
import re
import sys
import uuid

from .backend import BackendError
from .macos_command import build_macos_command
from .macos_process import peer_identity, process_identity
from .qemu import QemuBackend
from ..macos_runtime import PROFILE, load_runtime


class MacOSBackend(QemuBackend):
    _process_identity = staticmethod(process_identity)

    def _supported(self):
        if sys.platform != "darwin" or platform.machine().lower() != "arm64":
            raise BackendError("The native Mac backend requires Apple Silicon.")

    def validate_profile(self, config):
        config.validate()
        if config.execution_profile != PROFILE:
            raise BackendError("This x64 Windows environment is preserved, but the native Mac backend runs Windows 11 ARM. Start the x64 environment on its original Linux host; it has not been converted.")

    def _runtime_dir(self, vm_id):
        self._directory(vm_id)
        base = Path("/private/tmp") / f"virtue-{os.getuid()}"
        library = hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        directory = base / library / vm_id[:12]
        for path in (base, base / library, directory):
            if not path.exists():
                try:
                    path.mkdir(mode=0o700)
                except FileExistsError:
                    pass
            if (path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid()
                    or path.stat().st_mode & 0o077):
                raise BackendError("The Mac VM runtime directory must be private to this user.")
        return directory

    def _qemu_identity(self, client, vm_id):
        if client.socket is None:
            raise BackendError("The management connection is not open.")
        identity = peer_identity(client.socket)
        expected = str(uuid.UUID(vm_id))
        if (expected not in identity["cmdline"]
                or client.execute("query-uuid").get("UUID", "").lower() != expected):
            raise BackendError("The management socket belongs to a different VM.")
        executable = load_runtime(verify=False)[1]["qemu"].resolve()
        if Path(identity["executable"]).resolve() != executable:
            raise BackendError("The management socket is not served by the bundled QEMU runtime.")
        return identity

    def _verify_peer(self, client, metadata):
        current = self._qemu_identity(client, metadata["vm_id"])
        saved = metadata.get("qemu", {})
        if current["pid"] != saved.get("pid") or current["start_ticks"] != saved.get("start_ticks"):
            raise BackendError("The management socket's process identity changed.")

    def _environment(self):
        _, paths = load_runtime(verify=False)
        environment = dict(os.environ)
        for name in ("DYLD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_FALLBACK_LIBRARY_PATH",
                     "VK_DRIVER_FILES", "VK_ICD_FILENAMES", "D3DMETAL_FRAMEWORK_PATH",
                     "NPT_D3D12_LIBRARY_PATH", "NPT_WA_FLAGS"):
            environment.pop(name, None)
        environment.update(ANGLE_DEFAULT_PLATFORM="metal", NPT_BACKEND="dxmt",
                           RENDER_SERVER_EXEC_PATH=str(paths["render_server"]),
                           NPT_D3D11_LIBRARY_PATH=str(paths["dxmt_library"]),
                           NPT_DXGI_LIBRARY_PATH=str(paths["dxmt_library"]))
        return environment

    def _prefix(self, graphics, directory, runtime, config):
        return [self._host().qemu]

    def _hyperv(self, prefix, config):
        return []

    def _audio_backend(self, prefix):
        return "coreaudio"

    def _accelerator(self, graphics):
        return "hvf"

    def _build_command(self, *args, **kwargs):
        return build_macos_command(*args, **kwargs)

    def _acceleration_status(self, client, metadata):
        return {"kvm": False, "accelerator": "hvf", "accelerator_evidence": "Explicit HVF-only launch; no emulation fallback.",
                "guest_architecture": "arm64", "graphics_backend": "safe" if metadata.get("graphics") == "safe" else "triton-neptune-dxmt"}

    def _verify_acceleration(self, client, metadata):
        if metadata.get("accelerator") != "hvf" or metadata.get("execution_profile") != PROFILE:
            raise BackendError("The running VM does not match the native Mac execution profile.")
        if not client.execute("query-status").get("running"):
            raise BackendError("The native Mac VM did not reach a running state.")

    def create(self, config, progress=None):
        self._supported()
        self.validate_profile(config)
        if not self._host().ready:
            raise BackendError("The Mac runtime is not ready: " + "; ".join(self._host().errors))
        if config.machine_type == "virt":
            machines = self._run([self._host().qemu, "-machine", "help"], timeout=10).stdout
            versions = {(int(a), int(b)) for a, b in re.findall(r"(?m)^virt-(\d+)\.(\d+)\s", machines)}
            if not versions:
                raise BackendError("The bundled QEMU has no versioned ARM virt machine.")
            major, minor = max(versions)
            config.machine_type = f"virt-{major}.{minor}"
        return super().create(config, progress)

    def pause(self, vm_id):
        if (self._metadata(vm_id) or {}).get("graphics") == "triton":
            raise BackendError("Shut down Windows normally; live pause of Triton graphics is not supported.")
        return super().pause(vm_id)
