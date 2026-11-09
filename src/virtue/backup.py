"""Portable offline VM archives. No general-purpose archive extraction is used."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import uuid

from .domain import VMConfig, ValidationError


def _allowed(name: str) -> bool:
    path = PurePosixPath(name)
    return (not path.is_absolute() and ".." not in path.parts and "\\" not in name
            and (name in {"config.json", "disk.qcow2", "OVMF_VARS.fd", "OVMF_CODE.fd", "firmware.json", "windows-credentials.json"}
                 or (len(path.parts) > 1 and path.parts[0] == "tpm"
                     and all(part not in {"", ".", ".."} for part in path.parts))))


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def export_backup(directory: Path, destination: Path) -> None:
    directory = directory.resolve()
    destination = destination.expanduser().absolute()
    if destination.is_relative_to(directory):
        raise ValidationError("Save the backup outside the VM directory.")
    if destination.exists():
        raise ValidationError("A backup already exists at this path. Choose a new filename.")
    paths = []
    for name in ("config.json", "disk.qcow2", "OVMF_VARS.fd", "OVMF_CODE.fd", "firmware.json", "windows-credentials.json", "tpm"):
        path = directory / name
        if path.is_symlink():
            raise ValidationError("Cannot back up symbolic links in VM storage.")
        if path.is_dir():
            paths.extend(p for p in path.rglob("*") if p.is_file() or p.is_symlink())
        elif path.exists():
            paths.append(path)
    if not {"config.json", "disk.qcow2", "OVMF_VARS.fd", "OVMF_CODE.fd", "firmware.json"}.issubset({p.name for p in paths}):
        raise ValidationError("The VM is missing its disk or firmware state; backup cancelled.")
    for path in paths:
        if path.is_symlink() or path.resolve().is_relative_to(directory) is False:
            raise ValidationError("Cannot back up symbolic links or external state.")
    needed = sum(p.stat().st_size for p in paths) + 1024 * 1024
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < needed:
        raise ValidationError("Not enough space for a complete VM backup.")
    fd, temporary = tempfile.mkstemp(prefix=".virtue-backup-", dir=destination.parent)
    os.close(fd)
    try:
        manifest = {"format": "virtue-backup", "version": 1, "files": {}}
        with tarfile.open(temporary, "w") as archive:
            for path in sorted(paths):
                name = path.relative_to(directory).as_posix()
                if not _allowed(name):
                    raise ValidationError("Unexpected VM state file.")
                manifest["files"][name] = {"sha256": _digest(path), "size": path.stat().st_size}
                archive.add(path, arcname=name, recursive=False)
            payload = json.dumps(manifest, indent=2).encode()
            import io
            info = tarfile.TarInfo("manifest.json")
            info.size = len(payload)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(payload))
        with open(temporary, "r+b") as stream:
            os.fsync(stream.fileno())
        # Do not overwrite a backup created by another process during this operation.
        os.link(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def import_backup(source: Path, root: Path) -> VMConfig:
    """Verify checksums into a private staging directory, then publish atomically."""
    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=root))
    try:
        with tarfile.open(source, "r:*") as archive:
            members = []
            names = set()
            total = 0
            for member in archive:
                if len(members) > 4096:
                    raise ValidationError("Backup has too many state files.")
                if member.name in names or not member.isfile():
                    raise ValidationError("Backup contains duplicate, linked, or non-file entries.")
                if not _allowed(member.name) and member.name != "manifest.json":
                    raise ValidationError("Backup contains an unsafe or unexpected path.")
                names.add(member.name)
                total += member.size
                if total > shutil.disk_usage(root).free - 1024 * 1024:
                    raise ValidationError("Not enough disk space to import this backup.")
                members.append(member)
            if not {"manifest.json", "config.json", "disk.qcow2", "OVMF_VARS.fd", "OVMF_CODE.fd", "firmware.json"}.issubset(names):
                raise ValidationError("Backup is missing required files.")
            manifest_member = next(m for m in members if m.name == "manifest.json")
            if manifest_member.size > 1048576:
                raise ValidationError("Oversized backup manifest.")
            manifest = json.load(archive.extractfile(manifest_member))
            if manifest.get("format") != "virtue-backup" or manifest.get("version") != 1:
                raise ValidationError("Unknown backup format.")
            if set(manifest.get("files", {})) != names - {"manifest.json"}:
                raise ValidationError("Backup manifest does not match its contents.")
            for member in members:
                if member.name == "manifest.json":
                    continue
                expected = manifest["files"][member.name]
                if expected.get("size") != member.size:
                    raise ValidationError("Backup file size verification failed.")
                target = staging / member.name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                digest = hashlib.sha256()
                with archive.extractfile(member) as reader, target.open("xb") as writer:
                    while chunk := reader.read(1024 * 1024):
                        digest.update(chunk)
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                if digest.hexdigest() != expected.get("sha256"):
                    raise ValidationError("Backup checksum failed. The original VM has not been changed.")
                target.chmod(0o600)
            config = VMConfig.from_dict(json.loads((staging / "config.json").read_text(encoding="utf-8")))
            config.id = uuid.uuid4().hex
            config.name = (config.name[:69] + " (imported)")
            # Installation media is machine-local; preserve the installed graphics stack.
            config.iso_path = ""
            config.drivers_iso_path = ""
            (staging / "tpm").mkdir(mode=0o700, exist_ok=True)
            (staging / "snapshots").mkdir(mode=0o700, exist_ok=True)
            from .store import atomic_json
            atomic_json(staging / "config.json", config.to_dict())
            staging.rename(root / config.id)
            return config
    except (tarfile.TarError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Invalid VM backup: {exc}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)

