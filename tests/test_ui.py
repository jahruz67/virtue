"""Offscreen Qt checks. Service fakes exercise UI wiring, not a hypervisor."""

import os
from threading import Event
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QProgressBar, QPushButton

from virtue.ui import MainWindow
from virtue.ui.dialogs import TextDialog, VMDialog, diagnostics_html


HOST = {"ready": True, "memory_mib": 32768, "cpus": 12, "platform": "Linux", "architecture": "x86_64", "checks": [], "yttrium_ready": False}
VM = {"id": "vm-test", "name": "Windows CAD", "windows_version": "11", "memory_mib": 8192, "cpus": 4, "disk_gib": 128, "state": "stopped", "installed": True, "graphics": "safe"}


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


class FakeService:
    def __init__(self):
        self.calls = []
        self.worker_thread = None

    def list_vms(self):
        return [dict(VM)]

    def start_vm(self, vm_id, safe_graphics=False):
        self.calls.append(("start", vm_id, safe_graphics))
        self.worker_thread = QThread.currentThread()

    def enable_yttrium(self, vm_id, consent):
        self.calls.append(("yttrium", vm_id, consent))


def wait_for_idle(app, window):
    until = time.monotonic() + 3
    while window.tasks.busy and time.monotonic() < until:
        app.processEvents()
        time.sleep(0.005)
    assert not window.tasks.busy, "UI operation did not finish"


def ready_window(app):
    service = FakeService()
    window = MainWindow(service, auto_refresh=False)
    window.host = dict(HOST)
    window.render_host()
    window.render_vms([dict(VM)])
    return service, window


def test_launch_button_uses_background_service_and_safe_mode(app):
    service, window = ready_window(app)
    launch = next(button for button in window.findChildren(QPushButton) if button.text() == "Start")
    launch.click()
    wait_for_idle(app, window)
    assert service.calls == [("start", "vm-test", False)]
    assert service.worker_thread != app.thread()
    window.start_vm(VM, safe=True)
    wait_for_idle(app, window)
    assert service.calls[-1] == ("start", "vm-test", True)
    window.close()


def test_status_poll_stays_interactive_and_queues_user_actions(app):
    service, window = ready_window(app)
    release_poll = Event()
    original_card = window.cards_layout.itemAt(0).widget()

    def list_vms():
        if not release_poll.wait(timeout=3):
            raise TimeoutError("The test did not release the status poll")
        return [dict(VM)]

    service.list_vms = list_vms
    try:
        window.refresh_list(quiet=True)
        assert window.tasks.busy
        assert not window.tasks.blocking_busy
        assert not window.findChildren(QProgressBar)
        assert window.diagnostics_button.isEnabled()
        assert window.cards.isEnabled()
        window.start_vm(VM)
        assert window.tasks.blocking_busy
        assert not window.cards.isEnabled()
    finally:
        release_poll.set()
        wait_for_idle(app, window)
        window.close()
    assert service.calls == [("start", "vm-test", False)]
    assert window.cards_layout.itemAt(0).widget() is original_card


@pytest.mark.parametrize("state, installed, setup_state", [
    ("running", True, "ready"),
    ("running", False, "error"),
    ("stopped", False, "installing"),
    ("paused", False, "installing"),
])
def test_installation_keeps_recovery_available_while_mutations_stay_locked(app, state, installed, setup_state):
    service, window = ready_window(app)
    vm = {**VM, "auto_setup": True, "installed": False, "state": "running",
          "setup": {"state": "installing", "phase": 1}}
    try:
        window.render_vms([vm])
        window._busy_changed(False)
        assert window.diagnostics_button.isEnabled()
        assert window.cards.isEnabled()
        assert not window.create_button.isEnabled()
        assert not window.import_button.isEnabled()
        actions = {action.text(): action for action in window.vm_menu(VM).actions()}
        assert not actions["Settings…"].isEnabled()
        assert not actions["New checkpoint…"].isEnabled()
        assert actions["Runtime log…"].isEnabled()
        assert all(bar.objectName() == "setupProgress" for bar in window.findChildren(QProgressBar))
        window.start_vm(vm)
        assert service.calls == []
        window.render_vms([{**vm, "state": state, "installed": installed, "setup": {"state": setup_state}}])
        assert window.diagnostics_button.isEnabled()
        assert window.cards.isEnabled()
        assert window.create_button.isEnabled()
        assert all(bar.objectName() == "setupProgress" for bar in window.findChildren(QProgressBar))
    finally:
        window.confirm = lambda *_args, **_kwargs: True
        window.close()


@pytest.mark.parametrize("method, expected", [
    ("show_log", "log"),
    ("show_windows_credentials", "credentials"),
    ("show_diagnostics", "diagnostics"),
])
def test_background_installation_allows_diagnostic_actions(app, monkeypatch, method, expected):
    service, window = ready_window(app)
    vm = {**VM, "auto_setup": True, "installed": False, "state": "running",
          "setup": {"state": "installing", "phase": 1}}
    service.read_log = lambda vm_id: service.calls.append("log") or "VM log"
    service.windows_credentials = lambda vm_id: service.calls.append("credentials") or {"username": "Virtue", "password": "example"}
    service.host_report = lambda: service.calls.append("diagnostics") or dict(HOST)
    monkeypatch.setattr(TextDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)
    window.confirm = lambda *_args, **_kwargs: True
    try:
        window.render_vms([vm])
        if method == "show_diagnostics":
            window.show_diagnostics()
        else:
            getattr(window, method)(vm)
        assert window.tasks.blocking_busy
        assert not window.cards.isEnabled()
        wait_for_idle(app, window)
        assert service.calls == [expected]
        assert window.cards.isEnabled()
    finally:
        window.close()


def test_stalled_installation_can_be_force_stopped(app):
    service, window = ready_window(app)
    vm = {**VM, "auto_setup": True, "installed": False, "state": "running",
          "setup": {"state": "installing", "phase": 1}}
    service.force_stop_vm = lambda vm_id: service.calls.append(("force_stop", vm_id))
    window.confirm = lambda *_args, **_kwargs: True
    try:
        window.render_vms([vm])
        actions = {action.text(): action for action in window.vm_menu(vm).actions()}
        assert actions["Force stop…"].isEnabled()
        actions["Force stop…"].trigger()
        wait_for_idle(app, window)
        assert service.calls == [("force_stop", vm["id"])]
    finally:
        window.close()


def test_failed_vm_can_retry_while_another_vm_installs(app):
    service, window = ready_window(app)
    installing = {**VM, "id": "installing", "auto_setup": True, "installed": False,
                  "state": "running", "setup": {"state": "installing", "phase": 1}}
    failed = {**installing, "id": "failed", "setup": {"state": "error", "phase": 2}}
    service.retry_setup = lambda vm_id, progress: service.calls.append(("retry", vm_id))
    service.list_vms = lambda: [installing, failed]
    window.confirm = lambda *_args, **_kwargs: True
    try:
        window.render_vms([installing, failed])
        retry = window._vm_cards[failed["id"]].retry
        assert not retry.isHidden()
        assert retry.isEnabled()
        retry.click()
        wait_for_idle(app, window)
        assert service.calls == [("retry", "failed")]
    finally:
        window.close()


def test_missing_host_disables_creation_and_launch(app):
    service, window = ready_window(app)
    window.host = {"ready": False, "checks": [{"name": "KVM permissions", "required": True, "ok": False}]}
    window.render_host()
    window.render_vms([dict(VM)])
    assert not window.create_button.isEnabled()
    assert "KVM permissions" in window.banner_text.text()
    launch = [button for button in window.cards.findChildren(QPushButton) if button.text() == "Start"][-1]
    assert not launch.isEnabled()
    window.close()


def test_graphics_requires_explicit_consent(app):
    service, window = ready_window(app)
    window.confirm = lambda *_args, **_kwargs: False
    window.enable_graphics(VM)
    assert service.calls == []
    window.confirm = lambda *_args, **_kwargs: True
    window.show_graphics_guide = lambda: None
    window.enable_graphics(VM)
    wait_for_idle(app, window)
    assert service.calls == [("yttrium", "vm-test", True)]
    window.close()


def test_presets_are_explicit_even_on_small_host(app):
    dialog = VMDialog({"memory_mib": 8192, "cpus": 4})
    dialog.preset.setCurrentText("CAD / Engineering")
    options = dialog.options()
    assert options["memory_mib"] == 16384
    assert options["cpus"] == 8
    assert options["disk_gib"] == 256
    assert options["storage_bus"] == "sata"
    assert options["virtio_network"] is False
    dialog.memory.setValue(20)
    assert dialog.preset.currentText() == "Custom"
    dialog.close()


def test_automatic_defaults_and_advanced_override(app):
    dialog = VMDialog({"memory_mib": 8192, "cpus": 4})
    assert dialog.preset.currentText() == "Automatic"
    assert dialog.advanced.isHidden()
    assert dialog.options()["resource_mode"] == "automatic"
    assert dialog.options()["memory_mib"] <= 8192 - 2048
    dialog.advanced_toggle.setChecked(True)
    dialog.memory.setValue(5)
    assert dialog.preset.currentText() == "Custom"
    assert dialog.options()["resource_mode"] == "manual"
    dialog.close()


def test_existing_manual_resources_are_preserved(app):
    dialog = VMDialog(HOST, {**VM, "memory_mib": 12288, "cpus": 6, "disk_gib": 384})
    assert dialog.options()["resource_mode"] == "manual"
    assert dialog.options()["memory_mib"] == 12288
    assert dialog.options()["cpus"] == 6
    dialog.preset.setCurrentText("Automatic")
    assert dialog.options()["disk_gib"] == 384
    dialog.close()


def test_installed_vm_can_drop_missing_installation_media(app):
    dialog = VMDialog(HOST, {**VM, "iso_path": "/media/removed/windows.iso"})
    dialog.validate()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.options()["iso_path"] == ""


def test_existing_vm_can_replace_missing_driver_media(app, tmp_path):
    replacement = tmp_path / "virtio.iso"
    replacement.write_bytes(b"replacement driver media")
    dialog = VMDialog(HOST, {**VM, "drivers_iso_path": "/removed/virtio.iso"})
    assert not dialog.drivers.isHidden()
    dialog.drivers.path.setText(str(replacement))
    dialog.validate()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.options()["drivers_iso_path"] == str(replacement)
    dialog.close()


def test_host_readiness_does_not_claim_guest_acceleration():
    text = diagnostics_html({**HOST, "yttrium_ready": True}, [{**VM, "graphics": "yttrium", "state": "running"}])
    assert "No guest acceleration report imported" in text
    assert "do not prove" in text
    text = diagnostics_html(HOST, [{**VM, "name": "<script>"}])
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_live_process_remains_manageable_after_qmp_failure(app):
    _, window = ready_window(app)
    vm = {**VM, "state": "error", "process_active": True}
    menu = window.vm_menu(vm)
    actions = {action.text(): action for action in menu.actions()}
    assert actions["Force stop…"].isEnabled()
    assert not actions["Settings…"].isEnabled()
    assert not actions["New checkpoint…"].isEnabled()
    assert not actions["Remove…"].isEnabled()
    assert window.vm_active(vm)
    window.close()


def test_safe_launch_allows_pause_using_actual_display_mode(app):
    _, window = ready_window(app)
    vm = {**VM, "state": "running", "graphics": "yttrium", "active_graphics": "safe"}
    actions = {action.text(): action for action in window.vm_menu(vm).actions()}
    assert actions["Pause"].isEnabled()
    window.close()


