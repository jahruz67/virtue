"""Inspect disk references before asking QEMU to open an imported QCOW2 file."""
from pathlib import Path
import struct

from .domain import ValidationError


def validate_qcow2_header(path: Path) -> None:
    if path.is_symlink():
        raise ValidationError("A VM disk must be an ordinary file, not a symbolic link.")
    with path.open("rb") as stream:
        header = stream.read(104)
    if len(header) < 72 or header[:4] != b"QFI\xfb":
        raise ValidationError("The disk is not a QCOW2 image.")
    version, = struct.unpack_from(">I", header, 4)
    if version not in (2, 3):
        raise ValidationError("Unsupported QCOW2 image version.")
    backing_offset, backing_size = struct.unpack_from(">QI", header, 8)
    if backing_offset or backing_size:
        raise ValidationError("External backing files are not allowed in VM disks.")
    encryption, = struct.unpack_from(">I", header, 32)
    if encryption:
        raise ValidationError("Encrypted QCOW2 images are unsupported; use Windows disk encryption instead.")
    if version == 3:
        if len(header) < 104:
            raise ValidationError("Truncated QCOW2 version 3 header.")
        incompatible, = struct.unpack_from(">Q", header, 72)
        if incompatible & (1 << 2):
            raise ValidationError("QCOW2 external data files are not allowed.")
        if incompatible & (1 << 1):
            raise ValidationError("The QCOW2 image is marked corrupt. Restore a backup before launching.")

