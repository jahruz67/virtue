import struct
import pytest

from virtue.domain import ValidationError
from virtue.storage import validate_qcow2_header


def header(**kwargs):
    data = bytearray(104)
    data[:4] = b"QFI\xfb"
    struct.pack_into(">I", data, 4, 3)
    for offset, fmt, key in [(8, ">Q", "backing"), (16, ">I", "backing_size"),
                              (32, ">I", "crypt"), (72, ">Q", "features")]:
        struct.pack_into(fmt, data, offset, kwargs.get(key, 0))
    return data


@pytest.mark.parametrize("fields,reason", [({"backing": 128}, "backing"),
    ({"backing_size": 5}, "backing"), ({"features": 4}, "external data"),
    ({"features": 2}, "corrupt"), ({"crypt": 1}, "Encrypted")])
def test_refuses_external_references_before_qemu_opens_disk(tmp_path, fields, reason):
    disk = tmp_path / "disk.qcow2"
    disk.write_bytes(header(**fields))
    with pytest.raises(ValidationError, match=reason):
        validate_qcow2_header(disk)


def test_accepts_standalone_header_but_is_not_a_full_integrity_check(tmp_path):
    disk = tmp_path / "disk.qcow2"
    disk.write_bytes(header())
    validate_qcow2_header(disk)
