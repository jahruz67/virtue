"""Display integration boundaries, without launching QEMU or a guest."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from virtue.backends.guest_agent import GuestAgent
from virtue.backends.linux import LinuxBackend
from virtue.domain import VMConfig


def test_synthetic_timers_fall_back_as_a_group_on_older_hosts(tmp_path, monkeypatch):
    probe = Mock(side_effect=[SimpleNamespace(returncode=1, stdout=""),
                             SimpleNamespace(returncode=0, stdout='{"QMP": {}}')])
    monkeypatch.setattr("virtue.backends.linux.subprocess.run", probe)
    backend = LinuxBackend(tmp_path)
    flags = backend._hyperv(["qemu"], VMConfig(id="a" * 32, name="Windows"))
    assert flags == ["hv-relaxed", "hv-vapic", "hv-time"]
    first_command = probe.call_args_list[0].args[0]
    first_cpu = first_command[first_command.index("-cpu") + 1]
    assert all(flag + "=on" in first_cpu for flag in ("hv-vpindex", "hv-synic", "hv-stimer"))
    assert all("disk.qcow2" not in arg for call in probe.call_args_list for arg in call.args[0])


def test_partial_guest_write_closes_handle_and_reports_failure():
    agent = GuestAgent(None)
    agent.execute = Mock(side_effect=[7, {"count": 1}, None])
    with pytest.raises(ValueError, match="complete integration request"):
        agent.write_text("C:\\ProgramData\\Virtue\\display-request.json", "request")
    assert agent.execute.call_args.args == ("guest-file-close", {"handle": 7})


def test_display_worker_confirms_the_guest_mode(tmp_path, monkeypatch):
    import json
    from virtue.backends.display import apply_guest_display

    mode = {"width": 1920, "height": 1080, "refresh_hz": 143.9, "monitor": "DP-1"}
    confirmed = {**mode, "refresh_hz": 144, "state": "applied", "request_id": "boot-1"}
    agent = Mock()
    agent.__enter__ = Mock(return_value=agent)
    agent.__exit__ = Mock(return_value=False)
    agent.read_text.return_value = json.dumps(confirmed)
    monkeypatch.setattr("virtue.backends.guest_agent.GuestAgent", Mock(return_value=agent))
    save = Mock()
    apply_guest_display(tmp_path, mode, "boot-1", lambda: True, save)
    agent.start_display_task.assert_called_once()
    assert save.call_args.args[0] == {**confirmed, "requested": mode}
