from dataclasses import replace
import pytest

from virtue.domain import VMConfig, ValidationError, automatic_resources


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
    {"windows_version": "linux"}, {"graphics": "invalid"}, {"graphics_consent": False}, {"name": "\n"},
    {"machine_type": "q35,accel=tcg"}, {"installed": 1}, {"iso_path": "a\x00.iso"},
    {"resource_mode": "elastic"}, {"resource_mode": True},
])
def test_invalid_settings_rejected(change):
    vm = VMConfig.new({"name": "Windows"})
    with pytest.raises(ValidationError):
        VMConfig.from_dict({**vm.to_dict(), **change})


def test_new_always_includes_yttrium_and_automatic_setup():
    vm = VMConfig.new({"name": "Windows", "graphics": "safe", "graphics_consent": False,
                       "secure_boot": True, "auto_setup": False})
    assert vm.graphics == "yttrium" and not vm.secure_boot and vm.graphics_consent
    assert vm.auto_setup


@pytest.mark.parametrize("memory,cpus,other", [(8192, 4, 0), (12288, 4, 4096), (65536, 32, 0)])
def test_automatic_allocation_leaves_host_budget(memory, cpus, other):
    resources = automatic_resources(memory, cpus, other)
    vm = VMConfig.new({"name": "Windows", **resources, "resource_mode": "automatic"})
    vm.validate(host_memory_mib=memory, host_cpus=cpus, other_memory_mib=other)
    assert resources["memory_mib"] + other <= memory - max(2048, memory // 5)
    assert resources["cpus"] < cpus


def test_resource_modes_preserve_explicit_and_legacy_allocations():
    automatic = VMConfig.new({"name": "Windows"})
    assert automatic.resource_mode == "automatic"
    explicit = VMConfig.new({"name": "Windows", "memory_mib": 12288})
    assert explicit.resource_mode == "manual"
    legacy = explicit.to_dict()
    legacy.pop("resource_mode")
    restored = VMConfig.from_dict(legacy)
    assert restored.resource_mode == "manual"
    assert restored.memory_mib == 12288

