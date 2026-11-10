from types import SimpleNamespace

import pytest

from virtue.domain import VMConfig, ValidationError
from virtue.services import VMService
from virtue.store import VMStore


class Backend:
    def __init__(self, active):
        self.active = active
        self.started = []

    def status(self, vm_id):
        return {"state": "running" if vm_id in self.active else "stopped"}

    def start(self, config, **kwargs):
        self.started.append(config.to_dict())
        return {"state": "running"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = VMStore(tmp_path)
    other = VMConfig.new({"name": "Other", "installed": True, "memory_mib": 4096})
    target = VMConfig.new({"name": "Windows", "installed": True, "resource_mode": "automatic", "disk_gib": 384})
    store.save(other)
    store.save(target)
    backend = Backend({other.id})
    service = VMService(store, backend, SimpleNamespace(memory_mib=12288, cpus=4))
    monkeypatch.setattr(service, "_check_standalone_disk", lambda path: None)
    return service, backend, store, target, other


def test_boot_persists_actual_budget_without_changing_disk_limit(setup):
    service, backend, store, target, other = setup
    service.start_vm(target.id)
    saved = store.load(target.id)
    assert saved.memory_mib == 5120
    assert saved.cpus == 3
    assert saved.disk_gib == 384
    assert backend.started == [saved.to_dict()]
    assert store.load(other.id).memory_mib == 4096


def test_manual_allocation_is_rejected_instead_of_silently_changed(setup):
    service, backend, store, target, _ = setup
    target.resource_mode = "manual"
    store.save(target)
    with pytest.raises(ValidationError, match="host memory"):
        service.start_vm(target.id)
    assert not backend.started
    assert store.load(target.id).memory_mib == target.memory_mib


def test_running_vm_is_not_reconfigured(setup):
    service, backend, store, target, _ = setup
    backend.active.add(target.id)
    with pytest.raises(ValidationError, match="Shut down"):
        service.start_vm(target.id)
    assert not backend.started
    assert store.load(target.id).memory_mib == target.memory_mib


def test_automatic_does_not_overcommit_windows_minimum(setup):
    service, backend, store, target, other = setup
    other.memory_mib = 8192
    store.save(other)
    with pytest.raises(ValidationError, match="host memory"):
        service.start_vm(target.id)
    assert not backend.started
    assert store.load(target.id).memory_mib == target.memory_mib
