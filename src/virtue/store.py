"""Private storage, atomic JSON writes, and guarded VM directory access."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import json
import os
import tempfile
import threading

from .domain import VMConfig, ValidationError, valid_id


def default_root() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base and Path(base).is_absolute():
        return Path(base) / "virtue" / "vms"
    return Path.home() / ".local" / "share" / "virtue" / "vms"


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class VMStore:
    def __init__(self, root: Path | None = None):
        self.root = (root or default_root()).expanduser().absolute()
        if self.root.is_symlink():
            raise ValidationError("The VM storage root must not be a symbolic link.")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = self.root.resolve()
        if os.name == "posix":
            self.root.chmod(0o700)
        self._thread_lock = threading.RLock()
        self.errors: list[str] = []

    def directory(self, vm_id: str) -> Path:
        if not valid_id(vm_id):
            raise ValidationError("Invalid VM identifier.")
        directory = self.root / vm_id
        if directory.is_symlink() or directory.resolve().parent != self.root:
            raise ValidationError("Unsafe VM storage path; symbolic links are not accepted.")
        return directory

    def load(self, vm_id: str) -> VMConfig:
        path = self.directory(vm_id) / "config.json"
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValidationError("Unsafe or oversized VM configuration.")
        try:
            result = VMConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:
            raise ValidationError(f"Cannot read VM {vm_id}: {exc}") from exc
        if result.id != vm_id:
            raise ValidationError("VM configuration ID does not match its storage directory.")
        return result

    def save(self, config: VMConfig) -> None:
        config.validate()
        path = self.directory(config.id) / "config.json"
        if path.is_symlink():
            raise ValidationError("VM configuration must not be a symbolic link.")
        atomic_json(path, config.to_dict())

    def list(self) -> list[VMConfig]:
        self.errors = []
        result = []
        for directory in sorted(self.root.iterdir()):
            if not valid_id(directory.name) or not directory.is_dir():
                continue
            try:
                result.append(self.load(directory.name))
            except (OSError, ValidationError) as exc:
                self.errors.append(str(exc))
        return sorted(result, key=lambda vm: vm.created_at)

    @contextmanager
    def transaction(self):
        """Serialize GUI workers and other Virtue processes without a stale PID lock."""
        with self._thread_lock:
            lock_path = self.root / ".manager.lock"
            if lock_path.is_symlink():
                raise ValidationError("Unsafe storage lock path.")
            with lock_path.open("a+b") as handle:
                if os.name == "posix":
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX)
                elif os.name == "nt":
                    import msvcrt
                    handle.seek(0, 2)
                    if handle.tell() == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    if os.name == "posix":
                        fcntl.flock(handle, fcntl.LOCK_UN)
                    elif os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
