"""Real, unprivileged Linux QEMU/KVM VM lifecycle and cold recovery snapshots.

No shell interpretation is used. VM processes outlive the desktop manager. The
manager reconnects over private QMP sockets and verifies PID start time, UUID,
and process ownership before issuing lifecycle operations.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
import uuid

from .backend import BackendError
from .command import build_qemu_command, option
from .qmp import QMPClient, QMPError


def _atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sync_directory(path: Path):
    if sys.platform == "linux":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Expected an object")
        return value
    except (OSError, ValueError) as exc:
        raise BackendError(f"Cannot read {path.name}: {exc}") from exc


def _process_identity(pid: int) -> dict | None:
    """Linux /proc identity includes start ticks, defeating ordinary PID reuse."""
    try:
        proc = Path("/proc") / str(int(pid))
        if proc.stat().st_uid != os.getuid():
            return None
        fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return {"pid": int(pid), "start_ticks": fields[19],
                "cmdline": (proc / "cmdline").read_bytes().decode(errors="replace").split("\x00")}
    except (OSError, ValueError, IndexError, AttributeError):
        return None


class LinuxBackend:
    def __init__(self, root: Path, host: Any = None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.host = host
        self._children: dict[int, subprocess.Popen] = {}

    def _supported(self):
        if sys.platform != "linux":
            raise BackendError("Virtual machines run on x86-64 Linux with KVM. This host can inspect the application only.")

    def _host(self):
        if self.host is None:
            from virtue.host import detect_host
            self.host = detect_host()
        return self.host

    def _directory(self, vm_id: str) -> Path:
        try:
            if not re.fullmatch(r"[0-9a-f]{32}", vm_id):
                raise ValueError()
        except (ValueError, AttributeError, TypeError) as exc:
            raise BackendError("Invalid VM identifier.") from exc
        directory = self.root / vm_id
        if directory.is_symlink() or directory.resolve().parent != self.root:
            raise BackendError("The VM directory must be a direct, ordinary child of the library.")
        return directory

    @contextmanager
    def _lock(self, vm_id: str):
        directory = self._directory(vm_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = directory / ".operation.lock"
        if lock_path.is_symlink():
            raise BackendError("The VM operation lock must not be a symbolic link.")
        handle = lock_path.open("a+b")
        try:
            if sys.platform == "linux":
                import fcntl
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise BackendError("Another operation is already managing this VM.") from exc
            yield directory
        finally:
            handle.close()

    def _run(self, args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendError(f"Could not run {Path(args[0]).name}: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()[-4000:]
            raise BackendError(f"{Path(args[0]).name} failed: {detail}")
        return result

    def create(self, config: Any, progress=None) -> Any:
        self._supported()
        host = self._host()
        if not getattr(host, "qemu_img", None):
            raise BackendError("Install qemu-utils (qemu-img) before creating Windows.")
        if config.secure_boot and not getattr(host, "ovmf_secure", False):
            raise BackendError("Microsoft-enrolled Secure Boot OVMF firmware is missing. Install the distribution OVMF package.")
        code = Path(host.ovmf_code or "")
        template = Path(host.ovmf_vars or "")
        if not code.is_file() or not template.is_file():
            raise BackendError("UEFI firmware is missing. Install ovmf or edk2-ovmf.")
        if not config.secure_boot:
            plain = getattr(host, "ovmf_vars_plain", None)
            if plain:
                template = Path(plain)
            elif getattr(host, "ovmf_secure", False):
                raise BackendError("No unenrolled OVMF variable template was found. Create Windows with Secure Boot, then disable it explicitly in guest firmware before experimental graphics.")
        if not config.iso_path or not Path(config.iso_path).is_file():
            raise BackendError("Choose a readable Windows installation ISO.")
        if config.machine_type == "q35" and getattr(host, "qemu", None):
            machines = self._run([host.qemu, "-machine", "help"], timeout=10).stdout
            versions = re.findall(r"(?m)^pc-q35-(\d+)\.(\d+)\s", machines)
            if versions:
                major, minor = max((int(major), int(minor)) for major, minor in versions)
                config.machine_type = f"pc-q35-{major}.{minor}"
        with self._lock(config.id) as directory:
            disk = directory / "disk.qcow2"
            vars_file = directory / "OVMF_VARS.fd"
            if disk.exists() or disk.is_symlink() or vars_file.exists() or vars_file.is_symlink():
                raise BackendError("This VM already has a disk or firmware state. Existing files were preserved.")
            created: list[Path] = []
            try:
                if progress:
                    progress("Creating a thin-provisioned Windows disk…")
                created.append(disk)
                self._run([host.qemu_img, "create", "-f", "qcow2", "-o", "preallocation=off", str(disk), f"{config.disk_gib}G"])
                code_copy = directory / "OVMF_CODE.fd"
                if code_copy.exists() or code_copy.is_symlink():
                    raise BackendError("This VM already has firmware code. Existing files were preserved.")
                created.append(code_copy)
                shutil.copy2(code, code_copy)
                created.append(vars_file)
                shutil.copy2(template, vars_file)
                (directory / "tpm").mkdir(mode=0o700)
                created.append(directory / "tpm")
                (directory / "snapshots").mkdir(mode=0o700)
                created.append(directory / "snapshots")
                # Record firmware choice: future host updates must not silently
                # switch a VM between incompatible 2 MiB/4 MiB NVRAM layouts.
                _atomic_json(directory / "firmware.json", {
                    "code": "OVMF_CODE.fd", "code_size": code_copy.stat().st_size,
                    "code_sha256": hashlib.sha256(code_copy.read_bytes()).hexdigest(),
                    "vars_size": vars_file.stat().st_size,
                    "secure_boot_initial": bool(config.secure_boot),
                })
                created.append(directory / "firmware.json")
                if progress:
                    progress("Windows environment created.")
                return config
            except BaseException:
                # Only remove paths provisioned by this invocation.
                for path in reversed(created):
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink(missing_ok=True)
                raise

    def _runtime_dir(self, vm_id: str) -> Path:
        import hashlib
        base = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        if not base.is_dir() or base.stat().st_uid != os.getuid():
            raise BackendError("A private XDG_RUNTIME_DIR is required. Launch from a regular Linux desktop session.")
        library_id = hashlib.sha256(str(self.root).encode()).hexdigest()[:8]
        directory = base / "virtue" / library_id / vm_id[:12]
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in (directory, directory.parent, directory.parent.parent):
            if path.is_symlink() or path.stat().st_uid != os.getuid():
                raise BackendError("The runtime directory is not private to the current user.")
            path.chmod(0o700)
        if len(str(directory / "qmp.sock").encode()) > 100:
            raise BackendError("XDG_RUNTIME_DIR is too long for private VM management sockets.")
        return directory

    def _metadata(self, vm_id: str) -> dict | None:
        path = self._directory(vm_id) / "runtime.json"
        if not path.exists():
            return None
        return _read_json(path)

    def _identity_matches(self, metadata: dict, key: str = "qemu") -> bool:
        identity = metadata.get(key, {})
        try:
            current = _process_identity(int(identity["pid"]))
            return bool(current and current["start_ticks"] == identity["start_ticks"]
                        and (key != "qemu" or str(uuid.UUID(metadata["vm_id"])) in current["cmdline"]))
        except (ValueError, KeyError, TypeError):
            return False

    def _qmp(self, vm_id: str, timeout: float = 3) -> QMPClient:
        metadata = self._metadata(vm_id)
        if not metadata or not self._identity_matches(metadata):
            raise BackendError("This Windows environment is not running, or its process identity could not be verified.")
        socket_path = Path(metadata.get("qmp", ""))
        expected = self._runtime_dir(vm_id) / "qmp.sock"
        if socket_path != expected:
            raise BackendError("The stored VM management socket is outside its private runtime directory.")
        client = QMPClient(socket_path, timeout).connect()
        try:
            if client.execute("query-uuid").get("UUID", "").lower() != str(uuid.UUID(vm_id)):
                raise BackendError("The management socket belongs to a different VM. No command was sent.")
            return client
        except BaseException:
            client.close()
            raise

    def _reap(self):
        for pid, child in list(self._children.items()):
            if child.poll() is not None:
                del self._children[pid]

    def status(self, vm_id: str) -> dict:
        self._reap()
        metadata = self._metadata(vm_id)
        if metadata is None:
            return {"state": "stopped"}
        if not self._identity_matches(metadata):
            return {"state": "stopped", "last_started": metadata.get("started_at"),
                    "log": str(self._directory(vm_id) / "qemu.log")}
        try:
            client = self._qmp(vm_id, timeout=1)
            try:
                result = client.execute("query-status")
                kvm = client.execute("query-kvm")
            finally:
                client.close()
            status = result.get("status")
            state = "running" if result.get("running") else "paused" if status in ("paused", "prelaunch") else "error"
            return {"state": state, "qemu_status": status, "kvm": bool(kvm.get("enabled")),
                    "graphics": metadata.get("graphics"), "started_at": metadata.get("started_at"),
                    "pid": metadata["qemu"]["pid"], "log": str(self._directory(vm_id) / "qemu.log")}
        except BackendError as exc:
            return {"state": "error", "error": str(exc), "process_active": True,
                    "log": str(self._directory(vm_id) / "qemu.log")}

    def is_active(self, vm_id: str) -> bool:
        metadata = self._metadata(vm_id)
        return bool(metadata and self._identity_matches(metadata))

    def _require_stopped(self, vm_id: str):
        if (self._directory(vm_id) / "restore_pending.json").exists():
            raise BackendError("A snapshot restore was interrupted. Preserve the VM folder and resolve restore_pending.json before making changes.")
        if self.is_active(vm_id):
            raise BackendError("Shut down Windows fully before changing disks, snapshots, or firmware.")
        metadata = self._metadata(vm_id)
        if metadata and self._identity_matches(metadata, "swtpm"):
            self._stop_owned(metadata, "swtpm")
        # A VM launched outside this manager may not have our runtime metadata.
        # qemu-img's normal image locking detects that case before cold copies.
        disk = self._directory(vm_id) / "disk.qcow2"
        directory = self._directory(vm_id)
        for filename in ("disk.qcow2", "OVMF_CODE.fd", "OVMF_VARS.fd", "firmware.json", "tpm"):
            if (directory / filename).is_symlink():
                raise BackendError("Managed VM files and directories must not be symbolic links.")
        if sys.platform == "linux" and disk.exists():
            self._validate_disk_header(disk)
            qemu_img = getattr(self._host(), "qemu_img", "")
            if not qemu_img:
                raise BackendError("qemu-img is required to verify that the Windows disk is offline.")
            self._run([qemu_img, "info", "--output=json", str(disk)], timeout=15)

    @staticmethod
    def _validate_disk_header(disk: Path):
        """Reject external storage before QEMU/qemu-img can follow references."""
        from virtue.domain import ValidationError
        from virtue.storage import validate_qcow2_header
        try:
            validate_qcow2_header(disk)
        except ValidationError as exc:
            raise BackendError(str(exc)) from exc

    def prepare_offline(self, config: Any):
        """Verify disk is cold and stop our lingering TPM before external backup."""
        with self._lock(config.id):
            self._require_stopped(config.id)

    def _stop_owned(self, metadata: dict, key: str):
        if self._identity_matches(metadata, key):
            pid = int(metadata[key]["pid"])
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 3
            while self._identity_matches(metadata, key) and time.monotonic() < deadline:
                self._reap()
                time.sleep(0.05)
            if self._identity_matches(metadata, key):
                raise BackendError("A VM helper did not terminate; no storage changes were performed.")

    def _prefix(self, graphics: str, directory: Path, runtime: Path, config: Any) -> list[str]:
        host = self._host()
        if graphics != "yttrium":
            return list(getattr(host, "qemu_prefix", None) or [host.qemu])
        if not getattr(host, "yttrium_ready", getattr(host, "yttrium_available", False)):
            raise BackendError("The Yttrium host stack has not passed its dependency checks. Open Diagnostics for requirements.")
        prefix = list(getattr(host, "yttrium_prefix", None) or [])
        if not prefix:
            raise BackendError("Install the compatible org.qemu.yttrium Flatpak host bundle.")
        if Path(prefix[0]).name == "flatpak" and "run" in prefix:
            index = prefix.index("run") + 1
            grants = [f"--filesystem={directory}", f"--filesystem={runtime}"]
            for iso in (config.iso_path if not config.installed else "", config.drivers_iso_path):
                if iso:
                    grants.append(f"--filesystem={Path(iso).resolve()}:ro")
            setup_iso = directory / "virtue-setup.iso"
            if getattr(config, "auto_setup", False) and not config.installed and setup_iso.is_file():
                grants.append(f"--filesystem={setup_iso}:ro")
            prefix[index:index] = grants
        return prefix

    def _firmware_code(self, directory: Path) -> str:
        record = _read_json(directory / "firmware.json")
        if record.get("code") != "OVMF_CODE.fd":
            raise BackendError("The VM firmware record does not reference its private firmware copy.")
        code = directory / "OVMF_CODE.fd"
        if code.is_symlink() or not code.is_file() or code.stat().st_size != record.get("code_size"):
            raise BackendError("The VM's UEFI firmware is missing or changed size. Restore the recorded firmware package before launch.")
        if hashlib.sha256(code.read_bytes()).hexdigest() != record.get("code_sha256"):
            raise BackendError("The VM's UEFI firmware checksum changed. Restore its known recovery snapshot.")
        if (directory / "OVMF_VARS.fd").stat().st_size != record["vars_size"]:
            raise BackendError("The VM's UEFI variable store has an incompatible size.")
        return str(code)

    def _audio_backend(self, prefix: list[str]) -> str | None:
        try:
            result = self._run(prefix + ["-audiodev", "help"], timeout=10)
            text = result.stdout + result.stderr
            for driver in ("pipewire", "pa", "alsa", "sdl"):
                if re.search(rf"\b{driver}\b", text):
                    return driver
        except BackendError:
            pass
        return None

    def _hyperv(self, prefix: list[str], config: Any) -> list[str]:
        if not getattr(config, "hyperv", True):
            return []
        # QEMU validates this conservative set on KVM before the real VM starts.
        # Probe through QMP with no VM disk/TPM attached, preventing a failed
        # optimization probe from modifying the guest.
        flags = ["hv-relaxed", "hv-vapic", "hv-time"]
        request = json.dumps({"execute": "qmp_capabilities"}) + "\n"
        request += json.dumps({"execute": "quit"}) + "\n"
        command = prefix + ["-machine", "q35,accel=kvm", "-cpu", "host," + ",".join(f"{f}=on" for f in flags),
                            "-m", "128", "-S", "-nodefaults", "-display", "none", "-qmp", "stdio"]
        try:
            probe = subprocess.run(command, input=request, capture_output=True, text=True, timeout=10)
            return flags if probe.returncode == 0 and '"QMP"' in probe.stdout else []
        except (OSError, subprocess.TimeoutExpired):
            return []

    def start(self, config: Any, safe_graphics: bool = False) -> dict:
        self._supported()
        host = self._host()
        if not getattr(host, "ready", False):
            errors = getattr(host, "errors", [])
            raise BackendError("The Linux host is not ready: " + "; ".join(map(str, errors)))
        with self._lock(config.id) as directory:
            if self.is_active(config.id):
                raise BackendError("Windows is already running. Use its existing display window.")
            self._require_stopped(config.id)
            if (directory / "restore_pending.json").exists():
                raise BackendError("A snapshot restore was interrupted. Preserve the VM folder and consult recovery documentation before launch.")
            for filename in ("disk.qcow2", "OVMF_CODE.fd", "OVMF_VARS.fd", "firmware.json", "tpm", "qemu.log", "setup-events.jsonl"):
                if (directory / filename).is_symlink():
                    raise BackendError("Managed VM files and directories must not be symbolic links.")
            if not (directory / "disk.qcow2").is_file():
                raise BackendError("The Windows virtual disk is missing.")
            for iso in (config.iso_path if not config.installed else "", config.drivers_iso_path):
                if iso and not Path(iso).is_file():
                    raise BackendError(f"The attached installation media is missing: {iso}. Reattach it in Settings.")
            runtime = self._runtime_dir(config.id)
            for name in ("qmp.sock", "tpm.sock", "qga.sock", "qemu.pid"):
                (runtime / name).unlink(missing_ok=True)
            graphics = "safe" if safe_graphics else config.graphics
            setup_iso = directory / "virtue-setup.iso" if getattr(config, "auto_setup", False) and not config.installed else None
            if setup_iso and not setup_iso.is_file():
                raise BackendError("Automatic Windows installation media is missing. Retry setup in the app.")
            prefix = self._prefix(graphics, directory, runtime, config)
            code = self._firmware_code(directory)
            hyperv = self._hyperv(prefix, config)
            audio_backend = self._audio_backend(prefix)
            if not audio_backend:
                raise BackendError("The required sound components are unavailable. Use Retry / repair setup.")
            command = build_qemu_command(config, directory, prefix, code, runtime / "qmp.sock", runtime / "tpm.sock",
                                         safe_graphics=safe_graphics, audio_backend=audio_backend,
                                         hyperv=hyperv,
                                         accelerator=getattr(host, "yttrium_accel", "kvm") if graphics == "yttrium" else "kvm",
                                         guest_agent_socket=runtime / "qga.sock", setup_iso=setup_iso)
            command += ["-pidfile", str(runtime / "qemu.pid")]
            metadata = {"vm_id": config.id, "qmp": str(runtime / "qmp.sock"), "graphics": graphics,
                        "machine_type": config.machine_type, "hyperv_flags": hyperv,
                        "started_at": datetime.now(timezone.utc).isoformat()}
            tpm: subprocess.Popen | None = None
            qemu: subprocess.Popen | None = None
            with (directory / "qemu.log").open("ab", buffering=0) as log:
                try:
                    if "," in str(runtime):
                        raise BackendError("The runtime directory must not contain commas (swtpm socket syntax).")
                    tpm_args = [host.swtpm, "socket", "--tpm2", "--tpmstate", "dir=.",
                                "--ctrl", f"type=unixio,path={option(runtime / 'tpm.sock')},mode=0600",
                                "--terminate", "--flags", "not-need-init"]
                    tpm = subprocess.Popen(tpm_args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           start_new_session=True, close_fds=True, cwd=directory / "tpm")
                    self._children[tpm.pid] = tpm
                    deadline = time.monotonic() + 8
                    while not (runtime / "tpm.sock").exists():
                        if tpm.poll() is not None or time.monotonic() > deadline:
                            raise BackendError("The virtual TPM did not start. Open the VM log for details.")
                        time.sleep(0.05)
                    metadata["swtpm"] = _process_identity(tpm.pid)
                    qemu = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                            start_new_session=True, close_fds=True)
                    self._children[qemu.pid] = qemu
                    deadline = time.monotonic() + 20
                    while not ((runtime / "qemu.pid").exists() and (runtime / "qmp.sock").exists()):
                        if qemu.poll() is not None or time.monotonic() > deadline:
                            raise BackendError("Windows could not start. Open the VM log; you can retry with Safe graphics.")
                        time.sleep(0.1)
                    actual_pid = int((runtime / "qemu.pid").read_text().strip())
                    identity = _process_identity(actual_pid)
                    if not identity or str(uuid.UUID(config.id)) not in identity["cmdline"]:
                        raise BackendError("Could not verify the Windows process identity.")
                    metadata["qemu"] = identity
                    _atomic_json(directory / "runtime.json", metadata)
                    client = self._qmp(config.id)
                    try:
                        if not client.execute("query-kvm").get("enabled"):
                            raise BackendError("QEMU did not enable hardware KVM acceleration.")
                    finally:
                        client.close()
                    if setup_iso and not (directory / "setup-boot-started").exists():
                        # Windows DVDs can ask for a key before reading the answer file.
                        for _ in range(3):
                            time.sleep(3)
                            client = self._qmp(config.id)
                            try:
                                client.execute("send-key", {"keys": [{"type": "qcode", "data": "spc"}], "hold-time": 100})
                            finally:
                                client.close()
                        (directory / "setup-boot-started").touch(mode=0o600)
                    return self.status(config.id)
                except BaseException:
                    # Only processes created in this invocation are stopped.
                    # A verified actual QEMU child may differ from Flatpak's PID.
                    if metadata.get("qemu") and self._identity_matches(metadata):
                        try:
                            self._stop_owned(metadata, "qemu")
                        except BackendError:
                            pass
                    for child in (qemu, tpm):
                        if child is not None and child.poll() is None:
                            child.terminate()
                            try:
                                child.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                pass
                    raise

    def _control(self, vm_id: str, command: str):
        self._supported()
        with self._lock(vm_id):
            client = self._qmp(vm_id)
            try:
                client.execute(command)
            finally:
                client.close()

    def setup_status(self, vm_id: str) -> dict | None:
        from .guest_agent import GuestAgent
        metadata = self._metadata(vm_id)
        if not metadata or not self._identity_matches(metadata):
            return None
        journal = self._directory(vm_id) / "setup-events.jsonl"
        if journal.is_file() and not journal.is_symlink():
            with journal.open("rb") as stream:
                stream.seek(max(0, journal.stat().st_size - 256 * 1024))
                lines = stream.read(256 * 1024).splitlines()
            for line in reversed(lines):
                try:
                    value = json.loads(line.decode("utf-8-sig"))
                    if isinstance(value, dict) and value.get("state") in {"installing", "ready", "error"}:
                        return value
                except (ValueError, UnicodeError):
                    continue
        try:
            with GuestAgent(self._runtime_dir(vm_id) / "qga.sock") as agent:
                return agent.setup_status()
        except (OSError, ValueError, KeyError, TypeError):
            return {"state": "installing", "message": "Installing Windows and its integration drivers…"}

    def retry_setup(self, vm_id: str):
        from .guest_agent import GuestAgent
        client = self._qmp(vm_id)
        client.close()
        command = "$ErrorActionPreference='Stop'; Enable-ScheduledTask -TaskName VirtueFinishSetup; Start-ScheduledTask -TaskName VirtueFinishSetup"
        with GuestAgent(self._runtime_dir(vm_id) / "qga.sock") as agent:
            process = agent.execute("guest-exec", {"path": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                                    "arg": ["-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", command], "capture-output": True})
            for _ in range(20):
                result = agent.execute("guest-exec-status", {"pid": process["pid"]})
                if result.get("exited"):
                    if result.get("exitcode") != 0:
                        raise BackendError("Windows could not resume automatic setup. Its setup report has been preserved.")
                    return
                time.sleep(0.25)
            raise BackendError("Windows did not acknowledge the automatic setup retry.")

    def shutdown(self, vm_id: str):
        """Request ACPI shutdown. Never escalates a slow guest to data loss."""
        self._control(vm_id, "system_powerdown")

    def restart(self, vm_id: str):
        """Graceful reboot through QGA; no unsafe QMP system_reset fallback."""
        self._supported()
        with self._lock(vm_id):
            # Verify the VM's process and UUID before contacting its guest agent.
            client = self._qmp(vm_id)
            client.close()
            agent_path = self._runtime_dir(vm_id) / "qga.sock"
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(3)
                    sock.connect(str(agent_path))
                    token = int.from_bytes(os.urandom(4), "big")
                    request = {"execute": "guest-sync-delimited", "arguments": {"id": token}}
                    sock.sendall(b"\xff" + json.dumps(request).encode() + b"\n")
                    buffer = bytearray()
                    deadline = time.monotonic() + 3
                    synchronized = False
                    while time.monotonic() < deadline and len(buffer) < 65536:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        if b"\xff" not in buffer:
                            continue
                        after = buffer.rsplit(b"\xff", 1)[1]
                        if b"\n" not in after:
                            continue
                        response = json.loads(after.split(b"\n", 1)[0])
                        if response.get("return") == token:
                            synchronized = True
                            break
                    if not synchronized:
                        raise BackendError("The Windows guest agent is not responding.")
                    # A successful guest-shutdown has no response by protocol.
                    sock.sendall(json.dumps({"execute": "guest-shutdown", "arguments": {"mode": "reboot"}}).encode() + b"\n")
            except (OSError, ValueError, BackendError) as exc:
                raise BackendError("A graceful restart requires the Windows QEMU guest agent and VirtIO serial driver. "
                                   "Install them from the driver ISO, or choose Restart inside Windows. "
                                   f"Details: {exc}") from exc

    def pause(self, vm_id: str):
        metadata = self._metadata(vm_id) or {}
        if metadata.get("graphics") == "yttrium":
            raise BackendError("Pause is unavailable with experimental Yttrium graphics. Shut down Windows normally instead.")
        self._control(vm_id, "stop")

    def resume(self, vm_id: str):
        self._control(vm_id, "cont")

    def force_stop(self, vm_id: str):
        """Explicit hard power-off: caller must obtain a data-loss confirmation."""
        self._supported()
        with self._lock(vm_id):
            metadata = self._metadata(vm_id)
            if not metadata or not self._identity_matches(metadata):
                raise BackendError("No verified Windows process is running. No process was stopped.")
            try:
                client = self._qmp(vm_id)
                try:
                    client.execute("quit")
                finally:
                    client.close()
            except BackendError:
                # Some QEMU versions close QMP before replying to quit. When
                # still live, stop only the PID whose start time+UUID we own.
                if self._identity_matches(metadata):
                    self._stop_owned(metadata, "qemu")
            deadline = time.monotonic() + 3
            while self._identity_matches(metadata) and time.monotonic() < deadline:
                self._reap()
                time.sleep(0.05)
            if self._identity_matches(metadata):
                self._stop_owned(metadata, "qemu")
            if self._identity_matches(metadata, "swtpm"):
                self._stop_owned(metadata, "swtpm")

    @staticmethod
    def _snapshot_name(name: str) -> str:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}", name) or name.endswith((".", " ")):
            raise BackendError("Use a snapshot name of 1–64 letters, numbers, spaces, dots, dashes or underscores.")
        return name

    def _copy_file(self, source: Path, destination: Path):
        if source.is_symlink():
            raise BackendError("Snapshots do not follow symbolic links.")
        if sys.platform == "linux" and shutil.which("cp"):
            self._run([shutil.which("cp"), "--reflink=auto", "--sparse=always", "--", str(source), str(destination)], timeout=3600)
        else:
            shutil.copy2(source, destination)
        with destination.open("r+b") as stream:
            os.fsync(stream.fileno())

    def _copy_tree(self, source: Path, destination: Path):
        if source.is_symlink():
            raise BackendError("Snapshots do not follow symbolic links.")
        destination.mkdir(mode=0o700)
        for child in source.iterdir():
            target = destination / child.name
            if child.is_symlink():
                raise BackendError("Snapshots do not follow symbolic links.")
            if child.is_dir():
                self._copy_tree(child, target)
            elif child.is_file():
                self._copy_file(child, target)

    def _snapshot(self, config: Any, directory: Path, name: str) -> str:
        name = self._snapshot_name(name)
        self._require_stopped(config.id)
        snapshots = directory / "snapshots"
        if snapshots.is_symlink():
            raise BackendError("The snapshot directory must not be a symbolic link.")
        snapshots.mkdir(mode=0o700, exist_ok=True)
        destination = snapshots / name
        if destination.exists():
            raise BackendError("A snapshot with this name already exists.")
        stage = snapshots / (".creating-" + uuid.uuid4().hex)
        stage.mkdir(mode=0o700)
        try:
            files = ["disk.qcow2", "OVMF_VARS.fd", "OVMF_CODE.fd", "firmware.json", "config.json"]
            for filename in files:
                self._copy_file(directory / filename, stage / filename)
            self._copy_tree(directory / "tpm", stage / "tpm")
            _atomic_json(stage / "manifest.json", {"version": 1, "vm_id": config.id, "name": name,
                                                    "created_at": datetime.now(timezone.utc).isoformat(),
                                                    "files": files + ["tpm"]})
            stage.rename(destination)
            _sync_directory(snapshots)
            return name
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def snapshot(self, config: Any, name: str) -> str:
        with self._lock(config.id) as directory:
            return self._snapshot(config, directory, name)

    def list_snapshots(self, config: Any) -> list[str]:
        directory = self._directory(config.id) / "snapshots"
        if directory.is_symlink():
            raise BackendError("The snapshot directory must not be a symbolic link.")
        if not directory.is_dir():
            return []
        return sorted(path.name for path in directory.iterdir() if path.is_dir() and not path.is_symlink()
                      and not path.name.startswith(".") and (path / "manifest.json").is_file())

    def restore_snapshot(self, config: Any, name: str):
        name = self._snapshot_name(name)
        with self._lock(config.id) as directory:
            self._require_stopped(config.id)
            source = directory / "snapshots" / name
            if source.parent.is_symlink() or source.is_symlink() or not source.is_dir():
                raise BackendError("The selected recovery snapshot does not exist.")
            if (source / "manifest.json").is_symlink():
                raise BackendError("The snapshot manifest must not be a symbolic link.")
            manifest = _read_json(source / "manifest.json")
            expected = ["disk.qcow2", "OVMF_VARS.fd", "OVMF_CODE.fd", "firmware.json", "config.json", "tpm"]
            if manifest.get("version") != 1 or manifest.get("vm_id") != config.id or manifest.get("files") != expected:
                raise BackendError("The snapshot manifest is invalid or belongs to another VM.")
            recovery_name = "before-restore-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
            self._snapshot(config, directory, recovery_name)
            stage = directory / (".restore-" + uuid.uuid4().hex)
            old = directory / (".previous-" + uuid.uuid4().hex)
            stage.mkdir(mode=0o700)
            old.mkdir(mode=0o700)
            moved: list[str] = []
            try:
                for filename in expected:
                    if filename == "tpm":
                        self._copy_tree(source / filename, stage / filename)
                    else:
                        self._copy_file(source / filename, stage / filename)
                _atomic_json(directory / "restore_pending.json", {"stage": stage.name, "previous": old.name,
                                                                    "snapshot": name, "recovery": recovery_name})
                for filename in expected:
                    (directory / filename).rename(old / filename)
                    moved.append(filename)
                    (stage / filename).rename(directory / filename)
                _sync_directory(directory)
                (directory / "restore_pending.json").unlink()
                _sync_directory(directory)
            except BaseException:
                for filename in reversed(moved):
                    current = directory / filename
                    if current.is_dir():
                        shutil.rmtree(current)
                    else:
                        current.unlink(missing_ok=True)
                    (old / filename).rename(current)
                (directory / "restore_pending.json").unlink(missing_ok=True)
                raise
            finally:
                shutil.rmtree(stage, ignore_errors=True)
                if not (directory / "restore_pending.json").exists():
                    shutil.rmtree(old, ignore_errors=True)

    def expand_disk(self, config: Any, new_gib: int):
        if isinstance(new_gib, bool) or not isinstance(new_gib, int) or new_gib <= config.disk_gib:
            raise BackendError("Disk expansion must increase the current capacity; shrinking is not supported.")
        with self._lock(config.id) as directory:
            self._require_stopped(config.id)
            self._run([self._host().qemu_img, "resize", str(directory / "disk.qcow2"), f"{new_gib}G"])

    def delete(self, config: Any):
        """Move to a recoverable trash folder; never recursively erase live data."""
        with self._lock(config.id) as directory:
            self._require_stopped(config.id)
            trash = self.root / ".trash"
            if trash.is_symlink():
                raise BackendError("The recovery trash directory must not be a symbolic link.")
            trash.mkdir(mode=0o700, exist_ok=True)
            target = trash / (config.id + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4])
            # Linux permits renaming the directory while its advisory lock is held.
            directory.rename(target)
