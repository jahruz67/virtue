from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .dialogs import GRAPHICS_GUIDE, TextDialog, VMDialog, diagnostics_html, label
from .style import STYLE
from .tasks import Tasks


def app_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#64dcc1"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 15, 15)
    painter.setBrush(QColor("#102c32"))
    painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in [(14, 19), (24, 19), (32, 41), (40, 19), (50, 19), (36, 49), (28, 49)]]))
    painter.end()
    return QIcon(pixmap)


class MainWindow(QMainWindow):
    def __init__(self, service, *, auto_refresh=True):
        super().__init__()
        self.service = service
        self.host = {}
        self.vms = []
        self._last_poll_error = ""
        self.tasks = Tasks(self)
        self.tasks.changed.connect(self._busy_changed)
        self.tasks.progress.connect(self._setup_progress)
        self.setWindowTitle("Virtue · Windows, within reach")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(STYLE)
        self.resize(1060, 780)
        self.setMinimumSize(760, 600)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(34, 26, 34, 16)
        root.setSpacing(20)
        header = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(app_icon().pixmap(QSize(38, 38)))
        header.addWidget(mark)
        header.addWidget(label("virtue", "brand"))
        header.addStretch()
        self.diagnostics_button = QPushButton("Host && diagnostics")
        self.diagnostics_button.clicked.connect(self.show_diagnostics)
        header.addWidget(self.diagnostics_button)
        self.import_button = QPushButton("Import backup…")
        self.import_button.clicked.connect(self.import_vm)
        header.addWidget(self.import_button)
        root.addLayout(header)
        heading = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(7)
        words.addWidget(label("YOUR WORKSPACE", "eyebrow"))
        words.addWidget(label("Windows, within reach.", "headline"))
        words.addWidget(label("A familiar desktop. A place for your Windows work.", "muted"))
        heading.addLayout(words, 1)
        self.create_button = QPushButton("+  Create Windows")
        self.create_button.setObjectName("primary")
        self.create_button.setEnabled(False)
        self.create_button.clicked.connect(self.create_vm)
        heading.addWidget(self.create_button, alignment=Qt.AlignmentFlag.AlignBottom)
        root.addLayout(heading)
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(16, 12, 16, 12)
        self.banner_text = label("Checking your host and virtualization dependencies…", wrap=True)
        banner_layout.addWidget(self.banner_text, 1)
        self.repair_button = QPushButton("Retry / repair setup")
        self.repair_button.clicked.connect(self.repair_setup)
        self.repair_button.hide()
        banner_layout.addWidget(self.repair_button)
        root.addWidget(self.banner)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.cards = QWidget()
        self.cards_layout = QVBoxLayout(self.cards)
        self.cards_layout.setContentsMargins(0, 0, 8, 0)
        self.cards_layout.setSpacing(14)
        scroll.setWidget(self.cards)
        root.addWidget(scroll, 1)
        footer = QHBoxLayout()
        footer.addWidget(label("LINUX FIRST  ·  QEMU / KVM", "eyebrow"))
        footer.addStretch()
        help_button = QPushButton("Help  ▾")
        help_menu = QMenu(help_button)
        help_menu.addAction("Automatic Windows setup", self.show_graphics_guide)
        help_menu.addAction("Open setup guides", lambda: self.open_support_folder("docs"))
        help_button.setMenu(help_menu)
        footer.addWidget(help_button)
        root.addLayout(footer)
        self.statusBar().showMessage("Ready")
        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self._poll)
        if auto_refresh:
            QTimer.singleShot(0, self.refresh_all)
            self.timer.start()
        else:
            self.render_vms([])

    def _busy_changed(self, busy):
        locked = busy or self._installation_running()
        self.create_button.setEnabled(not locked and bool(self.host.get("ready")))
        self.import_button.setEnabled(not locked)
        self.diagnostics_button.setEnabled(not busy)
        self.cards.setEnabled(not busy)
        self.repair_button.setEnabled(not locked)

    def _installation_running(self):
        return any(
            vm.get("auto_setup") and not vm.get("installed", False)
            and vm.get("state") in {"running", "starting", "shutdown"}
            and vm.get("setup", {}).get("state") != "error"
            for vm in self.vms
        )

    def _setup_progress(self, message):
        self.banner_text.setText(message)
        self.statusBar().showMessage(message)

    def _setup_failed(self, error):
        self.banner_text.setText(str(error))
        self.repair_button.show()
        self._error(error)
        self.refresh_list(quiet=True)

    def _error(self, error):
        message = str(error) or type(error).__name__
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "Unable to complete this action", message)

    def _perform(self, message, fn, success=None, *, refresh=True, failure=None, allow_during_setup=False):
        if self.tasks.blocking_busy or (self._installation_running() and not allow_during_setup):
            return
        self.statusBar().showMessage(message)

        def complete(value):
            self.statusBar().showMessage("Ready")
            if success:
                success(value)
            if refresh:
                self.refresh_list()

        self.tasks.submit(fn, complete, failure or self._error)

    def _poll(self):
        if not self.tasks.busy and QApplication.activeModalWidget() is None and QApplication.activePopupWidget() is None:
            self.refresh_list(quiet=True)

    def refresh_all(self):
        def prepare():
            self.service.prepare_host(self.tasks.progress.emit)
            return self.service.host_report(), self.service.list_vms()
        self._perform("Preparing all required components…", prepare, self._loaded, refresh=False, failure=self._setup_failed)

    def repair_setup(self):
        def repair():
            self.service.prepare_host(self.tasks.progress.emit, repair=True)
            return self.service.host_report(), self.service.list_vms()
        self._perform("Repairing the Windows runtime…", repair, self._loaded, refresh=False, failure=self._setup_failed)

    def _loaded(self, result):
        self.host, vms = result
        self.render_host()
        self.render_vms(vms)

    def refresh_list(self, *, quiet=False):
        if self.tasks.busy:
            return

        def updated(vms):
            if self.tasks.blocking_busy or QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
                return
            if vms != self.vms:
                self.render_vms(vms)

        def failure(error):
            # A disconnected VM should not generate a modal alert every four seconds.
            if quiet:
                self.statusBar().showMessage(f"Status refresh failed: {error}")
            else:
                self._error(error)

        # Polls share the serial task queue without flashing the global busy UI.
        self.tasks.submit(self.service.list_vms, updated, failure, blocking=False)

    def render_host(self):
        if self.host.get("ready"):
            self.banner_text.setText("Ready. Choose your Windows ISO; Virtue handles installation, drivers and Yttrium graphics automatically.")
            self.banner.setObjectName("banner")
        else:
            missing = [c.get("name", "Dependency") for c in self.host.get("checks", []) if c.get("required") and not c.get("ok")]
            detail = ", ".join(missing) if missing else "a compatible Linux x86-64 host and working KVM"
            self.banner_text.setText(f"Setup needs attention: {detail}. Use Retry / repair setup; no terminal is needed.")
            self.banner.setObjectName("warning")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self._busy_changed(self.tasks.blocking_busy)
        self.repair_button.setVisible(not bool(self.host.get("ready")))

    def render_vms(self, vms):
        self.vms = vms
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if not vms:
            empty = QFrame()
            empty.setObjectName("card")
            layout = QVBoxLayout(empty)
            layout.setContentsMargins(30, 48, 30, 48)
            layout.setSpacing(14)
            title = label("Your next Windows desktop starts here.", "sectionTitle")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(title)
            sub = label("Bring your Windows ISO. We’ll handle the virtual disk, firmware and trusted platform module.", "muted", True)
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(sub)
            persistence = label("Your files and installed applications stay with your Windows environment.", "muted", True)
            persistence.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(persistence)
            self.cards_layout.addWidget(empty)
        for vm in vms:
            self.cards_layout.addWidget(self.vm_card(vm))
        self.cards_layout.addStretch()
        self._busy_changed(self.tasks.blocking_busy)

    def vm_card(self, vm):
        card = QFrame()
        card.setObjectName("card")
        row = QHBoxLayout(card)
        row.setContentsMargins(24, 23, 20, 23)
        row.setSpacing(22)
        tile = QLabel("W")
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setFixedSize(68, 68)
        tile.setStyleSheet("background:#254451; border-radius:14px; color:#84ead5; font-size:34px; font-weight:600;")
        row.addWidget(tile)
        content = QVBoxLayout()
        content.setSpacing(7)
        top = QHBoxLayout()
        top.addWidget(label(vm.get("name", "Windows"), "vmTitle"))
        top.addStretch()
        state = str(vm.get("state", "stopped"))
        status = label("●  " + state.replace("_", " ").capitalize())
        status.setStyleSheet("color: #80dfc8;" if state == "running" else "color: #b2c2d5;")
        top.addWidget(status)
        content.addLayout(top)
        version = str(vm.get("windows_version", "11"))
        content.addWidget(label(f"Windows {version}  ·  {vm.get('memory_mib', 0) / 1024:g} GiB RAM  ·  {vm.get('cpus', '?')} vCPUs", "muted"))
        used = vm.get("storage_bytes", 0) or 0
        disk = vm.get("disk_gib", 0)
        description = f"{used / (1024 ** 3):.1f} GiB on disk / {disk} GiB capacity"
        active_graphics = vm.get("active_graphics", vm.get("graphics", "safe"))
        if self.vm_active(vm) and active_graphics == "safe" and vm.get("graphics") == "yttrium":
            description += "  ·  Compatible display (safe launch)"
        else:
            description += "  ·  Yttrium graphics" if active_graphics == "yttrium" else "  ·  Compatible display"
        if not vm.get("installed", False) and not vm.get("auto_setup"):
            description += "  ·  Installation in progress"
        content.addWidget(label(description, "muted", True))
        setup = vm.get("setup", {})
        if vm.get("auto_setup"):
            content.addWidget(self.setup_indicator(vm))
        elif setup:
            content.addWidget(label(str(setup.get("message", "Installing Windows automatically…")), "muted", True))
        if vm.get("error"):
            error = label(str(vm["error"]), wrap=True)
            error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            error.setStyleSheet("color:#ffc08c;")
            content.addWidget(error)
        row.addLayout(content, 1)
        actions = QVBoxLayout()
        setup_pending = bool(vm.get("auto_setup")) and not vm.get("installed", False)
        launch = QPushButton("Resume Windows" if state == "paused" else "Launch Windows")
        launch.setObjectName("primary")
        launch.setEnabled(state == "paused" or (not self._installation_running() and not setup_pending and state == "stopped" and not self.vm_active(vm) and bool(self.host.get("ready"))))
        launch.clicked.connect(lambda: self.resume_vm(vm) if state == "paused" else self.start_vm(vm))
        if setup_pending and state != "paused":
            launch.setToolTip("Windows installs automatically. Launch is available after setup completes. If installation stops, use Retry automatic setup.")
        else:
            launch.setToolTip("Open the QEMU desktop window. A running VM already has its own display window." if state == "running" else "Boot your Windows environment")
        actions.addWidget(launch)
        if setup.get("state") == "error" or (setup_pending and state == "stopped" and not self.vm_active(vm)):
            retry = QPushButton("Retry automatic setup")
            retry.clicked.connect(lambda: self._perform("Retrying automatic Windows setup…", lambda: self.service.retry_setup(vm["id"], self.tasks.progress.emit), allow_during_setup=True))
            actions.addWidget(retry)
        more = QPushButton("Manage Windows  ▾")
        more.setMenu(self.vm_menu(vm, more))
        actions.addWidget(more)
        row.addLayout(actions)
        return card

    def setup_indicator(self, vm):
        stages = ("Installing Windows", "Installing integration drivers",
                  "Installing Yttrium and Vulkan")
        setup = vm.get("setup", {})
        phase = setup.get("phase", 0)
        if type(phase) is not int or phase < 0:
            phase = 0
        else:
            phase = min(phase, len(stages) - 1)
        complete = bool(vm.get("installed"))
        failed = setup.get("state") == "error"
        paused = vm.get("state") == "paused"
        stopped = not self.vm_active(vm)
        attention = not complete and (failed or paused or stopped)
        title = f"Step {phase + 1} of {len(stages)} · {stages[phase]}"
        message = str(setup.get("message") or "Windows Setup is running. Driver installation follows automatically; Windows may restart several times.")
        if complete:
            title = "Setup complete · All 3 stages finished"
            message = "Windows is ready."
        elif failed:
            title = "Setup needs attention"
        elif paused or stopped:
            title = "Setup paused" if paused else "Setup stopped"
            instruction = "Resume Windows to continue." if paused else "Use Retry automatic setup to continue."
            message = f"{instruction}\nLast update: {message}"

        indicator = QWidget()
        layout = QVBoxLayout(indicator)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)
        heading = label(title, "muted", True)
        if complete or attention:
            heading.setStyleSheet("color: #ffc08c;" if attention else "color: #80dfc8;")
        layout.addWidget(heading)
        progress = QProgressBar()
        progress.setObjectName("setupProgress")
        progress.setProperty("attention", attention)
        progress.setAccessibleName("Windows setup progress")
        progress.setAccessibleDescription(title)
        progress.setTextVisible(False)
        if not complete and not attention and phase == 0:
            progress.setRange(0, 0)
        else:
            progress.setRange(0, len(stages))
            progress.setValue(len(stages) if complete else phase)
        progress.setToolTip("Stages: Windows installation → integration drivers → Yttrium and Vulkan")
        layout.addWidget(progress)
        details = label(message, "muted", True)
        details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        layout.addWidget(details)
        if failed:
            buttons = QHBoxLayout()
            buttons.setContentsMargins(0, 0, 0, 0)
            copy_error = QPushButton("Copy error")
            copy_error.setToolTip("Copy the setup error, graphics report and guest diagnostic logs.")
            copy_error.clicked.connect(lambda: self.show_setup_diagnostics(vm, copy=True))
            buttons.addWidget(copy_error)
            view_details = QPushButton("View details")
            view_details.clicked.connect(lambda: self.show_setup_diagnostics(vm))
            buttons.addWidget(view_details)
            buttons.addStretch()
            layout.addLayout(buttons)
        return indicator

    def show_setup_diagnostics(self, vm, *, copy=False):
        def show(text):
            if copy:
                QApplication.clipboard().setText(text)
                self.statusBar().showMessage("Error and diagnostics copied.", 5000)
            else:
                TextDialog("Windows setup details", text, self).exec()
        self._perform("Reading Windows diagnostics…",
                      lambda: self.service.setup_diagnostics(vm["id"]), show,
                      refresh=False, allow_during_setup=True)

    @staticmethod
    def vm_active(vm):
        return bool(vm.get("process_active")) or vm.get("state") in {"running", "paused", "starting", "shutdown"}

    def vm_menu(self, vm, parent=None):
        menu = QMenu(parent or self)
        active = self.vm_active(vm)
        stopped = not active and vm.get("state") == "stopped"
        offline_changes = stopped and not self._installation_running()

        def action(title, callback, enabled=True):
            item = menu.addAction(title)
            item.setEnabled(enabled)
            item.triggered.connect(callback)
            return item

        action("Windows settings…", lambda: self.edit_vm(vm), offline_changes)
        action("Shut down Windows", lambda: self.shutdown_vm(vm), active)
        action("Restart Windows", lambda: self.restart_vm(vm), vm.get("state") == "running")
        if vm.get("state") == "paused":
            action("Resume Windows", lambda: self.resume_vm(vm))
        else:
            action("Pause Windows", lambda: self.pause_vm(vm), vm.get("state") == "running" and vm.get("active_graphics", vm.get("graphics")) != "yttrium")
        action("Force power off…", lambda: self.force_stop_vm(vm), active)
        menu.addSeparator()
        action("Create recovery checkpoint…", lambda: self.snapshot_vm(vm), offline_changes)
        action("Restore checkpoint…", lambda: self.restore_vm(vm), offline_changes)
        action("Expand disk…", lambda: self.expand_vm(vm), offline_changes)
        action("Export full backup…", lambda: self.export_vm(vm), offline_changes)
        menu.addSeparator()
        action("Windows sign-in details…", lambda: self.show_windows_credentials(vm), bool(vm.get("auto_setup")))
        action("Import guest GPU report…", lambda: self.import_report(vm), not self._installation_running())
        action("Guest graphics setup guide", self.show_graphics_guide)
        menu.addSeparator()
        action("Open Windows folder", lambda: self._perform("Opening folder…", lambda: self.service.open_vm_folder(vm["id"]), refresh=False, allow_during_setup=True))
        action("View runtime log…", lambda: self.show_log(vm))
        action("Remove Windows from library…", lambda: self.delete_vm(vm), offline_changes)
        return menu

    def create_vm(self):
        dialog = VMDialog(self.host, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            options = dialog.options()
            self._perform("Installing Windows and all required features…", lambda: self.service.create_vm(options, self.tasks.progress.emit), failure=self._setup_failed)

    def show_windows_credentials(self, vm):
        def show(details):
            QMessageBox.information(self, "Windows sign-in", f"Username: {details['username']}\nPassword: {details['password']}")
        self._perform("Reading Windows sign-in details…", lambda: self.service.windows_credentials(vm["id"]), show, refresh=False, allow_during_setup=True)

    def edit_vm(self, vm):
        dialog = VMDialog(self.host, vm, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            options = dialog.options()
            options.pop("windows_version", None)
            if (options["storage_bus"] == "virtio" and vm.get("storage_bus", "sata") != "virtio") or (options["virtio_network"] and not vm.get("virtio_network")):
                if not self.confirm("Switch virtual devices?", "Install the corresponding VirtIO drivers inside Windows before switching. Missing storage drivers can prevent Windows from booting; missing network drivers disable guest networking. Have you installed the required drivers?"):
                    return
            self._perform("Saving Windows settings…", lambda: self.service.update_vm(vm["id"], options))

    def start_vm(self, vm, safe=False):
        self._perform("Starting Windows in its desktop window…", lambda: self.service.start_vm(vm["id"], safe_graphics=safe))

    def shutdown_vm(self, vm):
        self._perform("Requesting a safe Windows shutdown…", lambda: self.service.shutdown_vm(vm["id"]), allow_during_setup=True)

    def restart_vm(self, vm):
        if not self.confirm("Restart Windows?", "Windows will restart. Save your work in the guest first."):
            return
        self._perform("Requesting a Windows restart…", lambda: self.service.restart_vm(vm["id"]), allow_during_setup=True)

    def pause_vm(self, vm):
        self._perform("Pausing Windows…", lambda: self.service.pause_vm(vm["id"]), allow_during_setup=True)

    def resume_vm(self, vm):
        self._perform("Resuming Windows…", lambda: self.service.resume_vm(vm["id"]), allow_during_setup=True)

    def force_stop_vm(self, vm):
        if self.confirm("Force Windows to power off?", "This is like unplugging the power cable. Unsaved work will be lost and the Windows filesystem may need repair. Try Shut down Windows first.", danger=True):
            self._perform("Forcing power off…", lambda: self.service.force_stop_vm(vm["id"]), allow_during_setup=True)

    def delete_vm(self, vm):
        name, ok = QInputDialog.getText(self, "Remove Windows from library", f"This removes Windows from the library. Files and checkpoints are kept in the storage folder’s .trash directory for manual recovery.\nType the environment name to confirm: {vm['name']}")
        if ok and name == vm["name"]:
            self._perform("Moving Windows to recoverable storage trash…", lambda: self.service.delete_vm(vm["id"]))
        elif ok:
            QMessageBox.information(self, "Name does not match", "Windows was kept. The confirmation must exactly match the environment name.")

    def snapshot_vm(self, vm):
        name, ok = QInputDialog.getText(self, "Create recovery checkpoint", "Checkpoint name (letters, numbers, hyphens or underscores):")
        if ok and name.strip():
            self._perform("Saving the stopped disk, firmware and TPM state. This may take a while…", lambda: self.service.snapshot_vm(vm["id"], name.strip()))

    def restore_vm(self, vm):
        def choose(names):
            if not names:
                QMessageBox.information(self, "No recovery checkpoints", "Create a checkpoint while Windows is stopped first.")
                return
            name, ok = QInputDialog.getItem(self, "Restore checkpoint", "Checkpoint", names, 0, False)
            if ok and self.confirm("Replace Windows with this checkpoint?", "All changes made after the checkpoint will be replaced, including Windows files and TPM state. Export a full backup first if you need the current data.", danger=True):
                self._perform("Restoring Windows disk, firmware and TPM state…", lambda: self.service.restore_vm(vm["id"], name))
        self._perform("Reading recovery checkpoints…", lambda: self.service.list_snapshots(vm["id"]), choose, refresh=False)

    def expand_vm(self, vm):
        size, ok = QInputDialog.getInt(self, "Expand Windows disk", "New maximum capacity in GiB (then grow the Windows partition in Disk Management):", int(vm["disk_gib"]) + 64, int(vm["disk_gib"]) + 1, 1048576)
        if ok:
            self._perform("Expanding the virtual disk…", lambda: self.service.expand_vm(vm["id"], size))

    def export_vm(self, vm):
        path, _ = QFileDialog.getSaveFileName(self, "Export complete Windows backup", vm.get("name", "Windows") + ".virtue", "Virtue full VM backup (*.virtue)")
        if path:
            if not path.lower().endswith(".virtue"):
                path += ".virtue"
            self._perform("Exporting the stopped Windows disk, configuration, firmware and TPM. This may take a while…", lambda: self.service.export_vm(vm["id"], path), lambda _: QMessageBox.information(self, "Backup exported", f"The full Windows backup was saved to:\n{path}"))

    def import_vm(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import complete Windows backup", "", "Virtue full VM backup (*.virtue)")
        if path:
            self._perform("Validating and importing the Windows backup…", lambda: self.service.import_vm(path))

    def enable_graphics(self, vm):
        detail = (
            "Yttrium is experimental. Virtue will create a recovery checkpoint first, then configure VirtIO GPU with Venus. "
            "You must disable guest Secure Boot manually in UEFI setup, install the matching guest drivers and may need Windows test-signing mode. These changes weaken guest driver-signing protections and permit test-signed kernel drivers. Host security is unchanged.\n\n"
            "Continue only if you understand and accept this guest security change. A successful launch does not verify hardware acceleration."
        )
        if self.confirm("Enable experimental graphics?", detail):
            self._perform("Creating a recovery checkpoint and configuring Yttrium…", lambda: self.service.enable_yttrium(vm["id"], consent=True), lambda _: self.show_graphics_guide())

    def disable_graphics(self, vm):
        self._perform("Returning to compatible display…", lambda: self.service.disable_yttrium(vm["id"]))

    def import_report(self, vm):
        path, _ = QFileDialog.getOpenFileName(self, "Import guest GPU verification report", "", "JSON report (*.json)")
        if path:
            self._perform("Checking guest graphics evidence…", lambda: self.service.import_gpu_report(vm["id"], path), lambda result: TextDialog("Guest graphics verification", json.dumps(result, indent=2), self).exec())

    def show_log(self, vm):
        self._perform("Reading runtime log…", lambda: self.service.read_log(vm["id"]), lambda text: TextDialog(vm.get("name", "Windows") + " · runtime log", text, self).exec(), refresh=False, allow_during_setup=True)

    def show_diagnostics(self):
        def show(result):
            self.host = result
            self.render_host()
            TextDialog("Host & diagnostics", diagnostics_html(result, self.vms), self, rich=True).exec()
        self._perform("Refreshing host and graphics diagnostics…", self.service.host_report, show, refresh=False, allow_during_setup=True)

    def show_graphics_guide(self):
        TextDialog("Automatic Windows setup", GRAPHICS_GUIDE, self, rich=True).exec()

    @staticmethod
    def support_folder(name):
        roots = [Path(__file__).resolve().parents[3], Path(sys.prefix) / "share" / "virtue", Path("/opt/virtue/share")]
        if os.environ.get("VIRTUE_DATA_DIR"):
            roots.insert(0, Path(os.environ["VIRTUE_DATA_DIR"]))
        for root in roots:
            if (root / name).is_dir():
                return root / name
        return None

    def open_support_folder(self, name):
        folder = self.support_folder(name)
        if folder is None:
            QMessageBox.information(self, "Support files unavailable", "Install the complete Virtue package or open the docs and guest directories in the source checkout.")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.information(self, "Open support folder", f"Open this folder in your file manager:\n{folder}")

    def confirm(self, title, detail, *, danger=False):
        icon = QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question
        box = QMessageBox(icon, title, detail, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, self)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText("Continue")
        return box.exec() == QMessageBox.StandardButton.Yes

    def closeEvent(self, event):
        if self.tasks.busy:
            QMessageBox.information(self, "An operation is in progress", "Wait for the current disk or VM operation to finish before closing Virtue.")
            event.ignore()
            return
        active = any(self.vm_active(vm) for vm in self.vms)
        if active and not self.confirm("Leave Windows running?", "Closing Virtue leaves your Windows desktop and VM running. You can reopen Virtue to manage it. To stop Windows, choose Shut down Windows first."):
            event.ignore()
            return
        self.timer.stop()
        event.accept()


def create_window(service):
    return MainWindow(service)
