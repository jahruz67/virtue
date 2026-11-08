import json
import pytest

from virtue.domain import VMConfig, ValidationError
from virtue.store import VMStore


def test_persistence_and_corrupt_vm_diagnostics(tmp_path):
    store = VMStore(tmp_path)
    config = VMConfig.new({"name": "Office"})
    store.save(config)
    assert store.load(config.id) == config
    (tmp_path / config.id / "config.json").write_text("{broken")
    assert store.list() == []
    assert store.errors


def test_refuses_cross_directory_configuration_and_path_traversal(tmp_path):
    store = VMStore(tmp_path)
    config = VMConfig.new({"name": "Work"})
    store.save(config)
    with pytest.raises(ValidationError):
        store.directory("../../outside")
    path = tmp_path / config.id / "config.json"
    data = json.loads(path.read_text())
    data["id"] = "a" * 32
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationError, match="does not match"):
        store.load(config.id)

