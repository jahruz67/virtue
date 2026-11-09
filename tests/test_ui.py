"""Offscreen Qt checks. Service fakes exercise UI wiring, not a hypervisor."""

import os
from threading import Event
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QDialog, QProgressBar, QPushButton

from virtue.ui import MainWindow
from virtue.ui.dialogs import VMDialog, diagnostics_html


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
    launch = next(button for button in window.findChildren(QPushButton) if button.text() == "Launch Windows")
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
def test_installation_keeps_controls_locked_until_setup_finishes_or_needs_attention(app, state, installed, setup_state):
    service, window = ready_window(app)
    vm = {**VM, "auto_setup": True, "installed": False, "state": "running",
          "setup": {"state": "installing", "phase": 1}}
    try:
        window.render_vms([vm])
        window._busy_changed(False)
        assert not window.diagnostics_button.isEnabled()
        assert not window.cards.isEnabled()
        assert not window.create_button.isEnabled()
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


def test_missing_host_disables_creation_and_launch(app):
    service, window = ready_window(app)
    window.host = {"ready": False, "checks": [{"name": "KVM permissions", "required": True, "ok": False}]}
    window.render_host()
    window.render_vms([dict(VM)])
    assert not window.create_button.isEnabled()
    assert "KVM permissions" in window.banner_text.text()
    launch = [button for button in window.cards.findChildren(QPushButton) if button.text() == "Launch Windows"][-1]
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


def test_installed_vm_can_drop_missing_installation_media(app):
    dialog = VMDialog(HOST, {**VM, "iso_path": "/media/removed/windows.iso"})
    dialog.validate()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.options()["iso_path"] == ""


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
    assert actions["Force power off…"].isEnabled()
    assert not actions["Windows settings…"].isEnabled()
    assert not actions["Create recovery checkpoint…"].isEnabled()
    assert not actions["Remove Windows from library…"].isEnabled()
    assert window.vm_active(vm)
    window.close()


def test_safe_launch_allows_pause_using_actual_display_mode(app):
    _, window = ready_window(app)
    vm = {**VM, "state": "running", "graphics": "yttrium", "active_graphics": "safe"}
    actions = {action.text(): action for action in window.vm_menu(vm).actions()}
    assert actions["Pause Windows"].isEnabled()
    window.close()


