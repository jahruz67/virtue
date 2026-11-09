"""Application actions shared by the Qt desktop and command line."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .backup import export_backup, import_backup
from .domain import VMConfig, ValidationError
from .store import VMStore, atomic_json


class VMService:
    def __init__(self, store: VMStore | None = None, backend=None, host=None):
        self.store = store or VMStore()
        self._host_report = host
        self._host_override = host is not None
        self._host_lock = threading.Lock()
        self._assets = None
        if backend is None:
            from .backends.linux import LinuxBackend
            backend = LinuxBackend(self.store.root, host)
        self.backend = backend

    @property
    def host(self):
        # Native dependency probes run in the UI's worker, after its window opens.
        with self._host_lock:
            if self._host_report is None:
                from .host import detect_host
                self._host_report = detect_host()
                self.backend.host = self._host_report
            return self._host_report

    def host_report(self) -> dict:
        if not self._host_override:
            from .host import detect_host
            with self._host_lock:
                self._host_report = detect_host()
                self.backend.host = self._host_report
        report = self.host.to_dict()
        report["storage_root"] = str(self.store.root)
        report["configuration_errors"] = self.store.errors.copy()
        return report

    def prepare_host(self, progress=lambda message: None, *, repair: bool = False):
        if sys.platform != "linux":
            # Keep the existing read-only preview usable on development hosts.
            return {}
        from .setup import _missing_host_components, prepare_runtime
        if self._assets is None or repair or _missing_host_components():
            self._assets = prepare_runtime(progress, repair=repair)
            if not self._host_override:
                self._host_report = None
                self.backend.host = None
        return self._assets

    def _require_stopped(self, config: VMConfig) -> None:
        state = self.backend.status(config.id).get("state", "error")
        if state != "stopped":
            raise ValidationError("Shut down Windows completely before changing disks, firmware, or VM settings. "
                                  f"Current state: {state}.")

    def _validate_resources(self, config: VMConfig, *, starting: bool = False) -> None:
        other_memory = 0
        if starting:
            for other in self.store.list():
                if other.id != config.id and self.backend.status(other.id).get("state") in {"running", "paused", "starting"}:
                    other_memory += other.memory_mib
        config.validate(host_memory_mib=self.host.memory_mib, host_cpus=self.host.cpus,
                        check_media=True, other_memory_mib=other_memory)

    @staticmethod
    def _setup_progress(directory: Path) -> dict:
        path = directory / "setup-progress.json"
        if path.is_symlink():
            raise ValidationError("Setup progress must not be a symbolic link.")
        if not path.is_file():
            return {}
        try:
            if path.stat().st_size > 128 * 1024:
                raise ValueError("Report is too large")
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Expected a setup report")
            return value
        except (OSError, ValueError) as exc:
            raise ValidationError("Cannot read setup progress. Preserve this VM and repair its setup-progress.json before retrying.") from exc

    def _view(self, config: VMConfig) -> dict:
        result = config.to_dict()
        status = self.backend.status(config.id)
        if "graphics" in status:
            status["active_graphics"] = status.pop("graphics")
        result.update(status)
        if config.auto_setup and not config.installed:
            result["setup"] = self._setup_progress(self.store.directory(config.id))
        disk = self.store.directory(config.id) / "disk.qcow2"
        if disk.exists():
            stat = disk.stat()
            result["storage_bytes"] = getattr(stat, "st_blocks", 0) * 512 or stat.st_size
        else:
            result["storage_bytes"] = 0
        report = self.store.directory(config.id) / "gpu-report.json"
        result["gpu_verification"] = {"state": "unverified", "reasons": ["No Windows graphics evidence has been imported."]}
        if report.exists() and not report.is_symlink() and report.stat().st_size <= 2 * 1024 * 1024:
            try:
                result["gpu_verification"] = json.loads(report.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        return result

    def list_vms(self) -> list[dict]:
        with self.store.transaction():
            results = []
            for config in self.store.list():
                if config.auto_setup and not config.installed:
                    config = self._reconcile_setup(config)
                results.append(self._view(config))
            return results

    def _reconcile_setup(self, config):
        directory = self.store.directory(config.id)
        if not ((directory / "virtue-setup.iso").is_file() or (directory / "virtue-repair.iso").is_file()):
            return config
        progress = self.backend.setup_status(config.id)
        if not progress:
            return config
        saved = self._setup_progress(directory)
        if "repair_previous_status" in saved:
            signature = {key: progress.get(key) for key in ("state", "phase", "message", "updated_at")}
            if type(progress.get("phase")) is not int or signature == saved["repair_previous_status"]:
                return config
        report = progress.pop("graphics_report", None)
        if progress.get("state") == "ready":
            from .graphics import classify_guest_report
            try:
                verification = classify_guest_report(report or {})
            except (ValueError, TypeError, KeyError):
                verification = {"state": "invalid"}
            if verification["state"] != "hardware_rendering_observed":
                progress.update(state="error", message="The required accelerated graphics check did not pass.")
            else:
                atomic_json(directory / "gpu-report.json", verification)
                config = replace(config, installed=True, iso_path="")
                self.store.save(config)
        atomic_json(directory / "setup-progress.json", progress)
        return config

    def create_vm(self, options: dict, progress=lambda message: None) -> dict:
        assets = self.prepare_host(progress)
        with self.store.transaction():
            config = VMConfig.new(options)
            self._validate_resources(config)
            self.backend.create(config)
            self.store.save(config)
            try:
                from .provisioning import prepare_windows
                prepare_windows(config, self.store.directory(config.id), assets, progress)
                self.store.save(config)
                progress("Starting the automatic Windows installation…")
                self.backend.start(config)
            except Exception as exc:
                atomic_json(self.store.directory(config.id) / "setup-progress.json", {"state": "error", "message": str(exc)})
                raise
            return self._view(config)

    def retry_setup(self, vm_id: str, progress=lambda message: None):
        assets = self.prepare_host(progress)
        with self.store.transaction():
            config = self.store.load(vm_id)
            if not config.auto_setup or config.installed:
                raise ValidationError("This Windows environment has no unfinished automatic installation.")
            saved = self._setup_progress(self.store.directory(vm_id))
            if type(saved.get("phase")) is int and 1 <= saved["phase"] <= 3:
                return self._retry_installed_setup(config, assets, saved, progress)
            if self.backend.status(vm_id).get("state") == "stopped":
                from .provisioning import prepare_windows
                prepare_windows(config, self.store.directory(vm_id), assets, progress)
                self.store.save(config)
                return self.backend.start(config)
            return self.backend.retry_setup(vm_id)

    def _retry_installed_setup(self, config, assets, saved, progress):
        from .provisioning import prepare_repair
        directory = self.store.directory(config.id)
        repair_iso = prepare_repair(directory, assets, progress)
        pending = {"state": "installing", "phase": saved["phase"],
                   "message": "Applying updated setup components to the existing Windows installation…",
                   "repair_previous_status": saved.get("repair_previous_status", {
                       key: saved.get(key) for key in ("state", "phase", "message", "updated_at")})}
        atomic_json(directory / "setup-progress.json", pending)
        try:
            if self.backend.status(config.id).get("state") == "stopped":
                progress("Starting your existing Windows installation for repair…")
                self.backend.start(config)
            progress("Resuming Windows driver and graphics setup…")
            if not self.backend.retry_setup(config.id, repair_iso=repair_iso):
                pending.update(state="error", message=(
                    "The updated setup disc is ready. Windows integration is not available yet. "
                    "Once the Windows desktop appears, retry automatic setup, or open the VIRTUE_SETUP disc "
                    "in Windows and double-click Retry setup.cmd. Approve administrator access if asked. "
                    "This resumes driver setup without reinstalling Windows."))
                atomic_json(directory / "setup-progress.json", pending)
        except Exception as exc:
            pending.update(state="error", message=str(exc))
            atomic_json(directory / "setup-progress.json", pending)
            raise

    def windows_credentials(self, vm_id: str) -> dict:
        self.store.load(vm_id)
        path = self.store.directory(vm_id) / "windows-credentials.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            raise ValidationError("Windows sign-in details are unavailable for this environment.")
        return json.loads(path.read_text(encoding="utf-8"))

    def update_vm(self, vm_id: str, options: dict) -> dict:
        allowed = {"name", "memory_mib", "cpus", "disk_gib", "installed", "iso_path",
                   "drivers_iso_path", "storage_bus", "virtio_network", "hyperv"}
        if set(options) - allowed:
            raise ValidationError("These settings require the dedicated graphics or storage workflow.")
        with self.store.transaction():
            current = self.store.load(vm_id)
            self._require_stopped(current)
            config = replace(current, **options)
            self._validate_resources(config)
            if config.disk_gib < current.disk_gib:
                raise ValidationError("Disks can be expanded but never shrunk.")
            if config.disk_gib > current.disk_gib:
                self.backend.expand_disk(current, config.disk_gib)
            self.store.save(config)
            return self._view(config)

    def start_vm(self, vm_id: str, safe_graphics: bool = False):
        with self.store.transaction():
            config = self.store.load(vm_id)
            self._validate_resources(config, starting=True)
            self._check_standalone_disk(self.store.directory(vm_id) / "disk.qcow2")
            return self.backend.start(config, safe_graphics=safe_graphics)

    def _lifecycle(self, vm_id: str, operation: str):
        with self.store.transaction():
            self.store.load(vm_id)
            return getattr(self.backend, operation)(vm_id)

    def shutdown_vm(self, vm_id: str):
        return self._lifecycle(vm_id, "shutdown")

    def restart_vm(self, vm_id: str):
        return self._lifecycle(vm_id, "restart")

    def pause_vm(self, vm_id: str):
        return self._lifecycle(vm_id, "pause")

    def resume_vm(self, vm_id: str):
        return self._lifecycle(vm_id, "resume")

    def force_stop_vm(self, vm_id: str):
        return self._lifecycle(vm_id, "force_stop")

    def delete_vm(self, vm_id: str):
        with self.store.transaction():
            config = self.store.load(vm_id)
            self._require_stopped(config)
            self.backend.delete(config)

    def snapshot_vm(self, vm_id: str, name: str):
        with self.store.transaction():
            config = self.store.load(vm_id)
            self._require_stopped(config)
            return self.backend.snapshot(config, name)

    def list_snapshots(self, vm_id: str):
        with self.store.transaction():
            return self.backend.list_snapshots(self.store.load(vm_id))

    def restore_vm(self, vm_id: str, name: str):
        with self.store.transaction():
            config = self.store.load(vm_id)
            self._require_stopped(config)
            # Backend validates snapshot name before opening anything; validate here too.
            import re
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}", name):
                raise ValidationError("Invalid snapshot name.")
            self.backend.restore_snapshot(config, name)
            report = self.store.directory(vm_id) / "gpu-report.json"
            report.unlink(missing_ok=True)

    def expand_vm(self, vm_id: str, size: int):
        return self.update_vm(vm_id, {"disk_gib": size})

    def export_vm(self, vm_id: str, path: str):
        with self.store.transaction():
            config = self.store.load(vm_id)
            self._require_stopped(config)
            self.backend.prepare_offline(config)
            export_backup(self.store.directory(vm_id), Path(path))

    def import_vm(self, path: str):
        with self.store.transaction():
            config = import_backup(Path(path), self.store.root)
            # qemu-img info refuses backing-file references before any imported disk is booted.
            try:
                self._check_standalone_disk(self.store.directory(config.id) / "disk.qcow2")
            except Exception:
                import shutil
                shutil.rmtree(self.store.directory(config.id))
                raise
            return self._view(config)

    def _check_standalone_disk(self, disk: Path):
        from .storage import validate_qcow2_header
        validate_qcow2_header(disk)
        qemu_img = getattr(self.host, "qemu_img", "")
        if not qemu_img:
            raise ValidationError("Install qemu-img before importing a VM backup.")
        result = subprocess.run([qemu_img, "info", "--output=json", str(disk)], capture_output=True,
                                text=True, timeout=30, check=True)
        info = json.loads(result.stdout)
        if info.get("format") != "qcow2" or info.get("backing-filename") or info.get("full-backing-filename"):
            raise ValidationError("Only standalone QCOW2 disks may be imported.")
        specific = info.get("format-specific", {}).get("data", {})
        if specific.get("data-file") or specific.get("data-file-raw"):
            raise ValidationError("QCOW2 external data files are not allowed.")

    def enable_yttrium(self, vm_id: str, consent: bool):
        if consent is not True:
            raise ValidationError("Explicit consent to experimental guest graphics is required.")
        with self.store.transaction():
            config = self.store.load(vm_id)
            self._require_stopped(config)
            if not getattr(self.host, "yttrium_ready", False):
                raise ValidationError("The compatible Yttrium host stack is unavailable. Open host diagnostics.")
            name = "before-yttrium-" + str(time.time_ns())
            result = self.backend.snapshot(config, name)
            self.store.save(replace(config, graphics="yttrium", graphics_consent=True, secure_boot=False))
            (self.store.directory(vm_id) / "gpu-report.json").unlink(missing_ok=True)
            return result

    def disable_yttrium(self, vm_id: str):
        with self.store.transaction():
            config = self.store.load(vm_id)
            self._require_stopped(config)
            # Secure Boot remains as configured in NVRAM; snapshot restore is the full rollback.
            self.store.save(replace(config, graphics="safe"))
            (self.store.directory(vm_id) / "gpu-report.json").unlink(missing_ok=True)

    def import_gpu_report(self, vm_id: str, path: str):
        from .graphics import classify_guest_report
        with self.store.transaction():
            self.store.load(vm_id)
            source = Path(path)
            if source.stat().st_size > 2 * 1024 * 1024:
                raise ValidationError("Graphics report exceeds the 2 MiB limit.")
            report = json.loads(source.read_text(encoding="utf-8-sig"))
            result = classify_guest_report(report)
            result["imported_at"] = time.time()
            result["source"] = source.name
            result["historical"] = True
            atomic_json(self.store.directory(vm_id) / "gpu-report.json", result)
            return result

    def open_vm_folder(self, vm_id: str):
        with self.store.transaction():
            self.store.load(vm_id)
            directory = self.store.directory(vm_id)
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(directory)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif os.name == "nt":
            os.startfile(directory)

    def read_log(self, vm_id: str) -> str:
        directory = self.store.directory(vm_id)
        output = []
        for name in ("qemu.log", "swtpm.log"):
            path = directory / name
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 32768))
                    output.append(name + "\n" + stream.read().decode("utf-8", errors="replace"))
        return "\n\n".join(output) or "No VM logs yet."
