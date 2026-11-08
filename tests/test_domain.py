from dataclasses import replace
import pytest

from virtue.domain import VMConfig, ValidationError


def test_resources_reserve_host_and_other_vms(tmp_path):
    iso = tmp_path / "Windows 11.iso"
    iso.write_bytes(b"test media")
    vm = VMConfig.new({"name": "Work", "iso_path": str(iso)})
    vm.validate(host_memory_mib=16384, host_cpus=8, check_media=True)
    with pytest.raises(ValidationError, match="host memory"):
        vm.validate(host_memory_mib=16384, host_cpus=8, other_memory_mib=8192)
    with pytest.raises(ValidationError, match="logical CPU"):
        replace(vm, cpus=8).validate(host_cpus=8)


@pytest.mark.parametrize("change", [
    {"id": "../other"}, {"cpus": True}, {"memory_mib": 1000}, {"disk_gib": 20},
    {"windows_version": "linux"}, {"graphics": "yttrium"}, {"name": "\n"},
    {"machine_type": "q35,accel=tcg"}, {"installed": 1}, {"iso_path": "a\x00.iso"},
])
def test_invalid_settings_rejected(change):
    vm = VMConfig.new({"name": "Windows"})
    with pytest.raises(ValidationError):
        VMConfig.from_dict({**vm.to_dict(), **change})


def test_new_cannot_skip_experimental_consent():
    vm = VMConfig.new({"name": "Windows", "graphics": "yttrium", "graphics_consent": True,
                       "secure_boot": False})
    assert vm.graphics == "safe" and vm.secure_boot and not vm.graphics_consent

