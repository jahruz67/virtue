from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLayout, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .dialogs import ElidedLabel, GRAPHICS_GUIDE, TextDialog, VMDialog, diagnostics_html, label
from .display import host_display
from .icons import action_icon, app_icon, icon_button
from .style import STYLE
from .tasks import Tasks


def windows_artwork():
    """Decorative environment artwork, not a live guest screenshot."""
    pixmap = QPixmap(288, 180)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, 288, 180), 16, 16)
    painter.setClipPath(clip)
    gradient = QLinearGradient(0, 0, 288, 180)
    gradient.setColorAt(0, QColor("#183b91"))
    gradient.setColorAt(1, QColor("#498af0"))
    painter.fillRect(pixmap.rect(), gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    for color, rect in (
        ("#3579e6", QRectF(-65, 70, 330, 220)),
        ("#65a5fa", QRectF(105, -120, 290, 250)),
        ("#2863cf", QRectF(135, -95, 240, 205)),
    ):
        painter.setBrush(QColor(color))
        painter.drawEllipse(rect)
    painter.setBrush(QColor("#ffffff"))
    for x in (123, 146):
        for y in (69, 92):
            painter.drawRect(x, y, 19, 19)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return pixmap


class VMCard(QFrame):
    """Keep the same controls while adapting their arrangement to card width."""

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.row = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.row.setContentsMargins(20, 22, 20, 22)
        self.row.setSpacing(20)
        self.preview = QLabel()
        self.preview.setObjectName("vmPreview")
        self.preview.setFixedSize(144, 90)
        self.preview.setPixmap(windows_artwork())
        self.row.addWidget(self.preview)
        self.content = QVBoxLayout()
        self.content.setSpacing(7)
        self.row.addLayout(self.content, 1)
        self.actions = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.row.addLayout(self.actions)
        self._compact = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < 560
        if compact == self._compact:
            return
        self._compact = compact
        self.preview.setVisible(not compact)
        self.row.setDirection(QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
        margins = (16, 16, 16, 16) if compact else (20, 22, 20, 22)
        self.row.setContentsMargins(*margins)
        self.row.setSpacing(14 if compact else 20)
        self.actions.setDirection(QBoxLayout.Direction.LeftToRight if compact else QBoxLayout.Direction.TopToBottom)


class MainWindow(QMainWindow):
    def __init__(self, service, *, auto_refresh=True, settings=None):
        super().__init__()
        self.service = service
        self.settings = settings
        self.host = {}
        self.vms = []
        self._vm_cards = {}
        self._empty_card = None
        self._last_poll_error = ""
        self.tasks = Tasks(self)
        self.tasks.changed.connect(self._busy_changed)
        self.tasks.progress.connect(self._setup_progress)
        self.setWindowTitle("Windows")
        self.setWindowIcon(app_icon())
        self.setStyleSheet(STYLE)
        self.resize(1060, 740)
        self.setMinimumSize(420, 320)
        if settings is not None and (geometry := settings.value("window/geometry")) is not None:
            self.restoreGeometry(geometry)
        self._fullscreen_was_maximized = self.isMaximized()
        self.fullscreen_action = QAction("Fullscreen", self)
        self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.setShortcut("F11")
        self.fullscreen_action.triggered.connect(self.toggle_fullscreen)
        self.addAction(self.fullscreen_action)
        self.exit_fullscreen_action = QAction("Leave fullscreen", self)
        self.exit_fullscreen_action.setShortcut("Escape")
        self.exit_fullscreen_action.triggered.connect(self.leave_fullscreen)
        self.exit_fullscreen_action.setEnabled(self.isFullScreen())
        self.addAction(self.exit_fullscreen_action)
        self.fullscreen_action.setChecked(self.isFullScreen())
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        header = QHBoxLayout(toolbar)
        header.setContentsMargins(24, 16, 24, 16)
        header.setSpacing(10)
        self.diagnostics_button = icon_button("monitor", "Host diagnostics")
        self.diagnostics_button.clicked.connect(self.show_diagnostics)
        header.addWidget(self.diagnostics_button)
        header.addStretch()
        self.import_button = icon_button("import", "Import Windows backup")
        self.import_button.clicked.connect(self.import_vm)
        header.addWidget(self.import_button)
        self.create_button = icon_button("add", "Create Windows")
        self.create_button.setEnabled(False)
        self.create_button.clicked.connect(self.create_vm)
        header.addWidget(self.create_button)
        view_button = icon_button("monitor", "View")
        view_menu = QMenu(view_button)
        view_menu.addAction(self.fullscreen_action)
        view_menu.addAction(self.exit_fullscreen_action)
        view_button.setMenu(view_menu)
        header.addWidget(view_button)
        help_button = icon_button("help", "Help")
        help_menu = QMenu(help_button)
        help_menu.addAction("Setup guide", self.show_graphics_guide)
        help_menu.addAction("Display shortcuts", self.show_display_guide)
        help_menu.addAction("Open guides", lambda: self.open_support_folder("docs"))
        help_button.setMenu(help_menu)
        header.addWidget(help_button)
        root.addWidget(toolbar)
        workspace = QWidget()
        workspace.setObjectName("workspace")
        body = QVBoxLayout(workspace)
        body.setContentsMargins(28, 28, 28, 16)
        body.setSpacing(20)
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(16, 12, 16, 12)
        self.banner_text = ElidedLabel("Checking host…")
        banner_layout.addWidget(self.banner_text, 1)
        self.repair_button = QPushButton("Repair")
        self.repair_button.setToolTip("Retry / repair setup")
        self.repair_button.clicked.connect(self.repair_setup)
        repair_policy = self.repair_button.sizePolicy()
        repair_policy.setRetainSizeWhenHidden(True)
        self.repair_button.setSizePolicy(repair_policy)
        self.repair_button.hide()
        banner_layout.addWidget(self.repair_button)
        body.addWidget(self.banner)
        body.addWidget(label("Windows: Ctrl+Alt+F enters or exits fullscreen. "
                             f"Ctrl+Alt+G returns control to {'macOS' if sys.platform == 'darwin' else 'Linux'}.\n"
                             "Launch fullscreen: ⋯ → Start fullscreen.", "muted", True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.setMinimumSize(0, 0)
        self.cards = QWidget()
        self.cards.setObjectName("vmList")
        self.cards_layout = QVBoxLayout(self.cards)
        self.cards_layout.setContentsMargins(0, 0, 6, 0)
        self.cards_layout.setSpacing(12)
        self.cards_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.cards_layout.addStretch()
        scroll.setWidget(self.cards)
        body.addWidget(scroll, 1)
        root.addWidget(workspace, 1)
        status = self.statusBar()
        status.setSizeGripEnabled(False)
        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self._poll)
        if auto_refresh:
            QTimer.singleShot(0, self.refresh_all)
            self.timer.start()
        else:
            self.render_vms([])

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.leave_fullscreen()
        else:
            self._fullscreen_was_maximized = self.isMaximized()
            self.showFullScreen()

    def leave_fullscreen(self):
        if self._fullscreen_was_maximized:
            self.showMaximized()
        else:
            self.showNormal()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "fullscreen_action"):
            self.fullscreen_action.setChecked(self.isFullScreen())
            self.exit_fullscreen_action.setEnabled(self.isFullScreen())
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            for button in self.findChildren(QPushButton):
                if name := button.property("actionIcon"):
                    button.setIcon(action_icon(name, button.palette().buttonText().color().name()))

    def _busy_changed(self, busy):
        locked = busy or self._installation_running()
        self.create_button.setEnabled(not locked and bool(self.host.get("ready")))
        self.import_button.setEnabled(not locked)
        self.diagnostics_button.setEnabled(not busy)
        self.cards.setEnabled(not busy)
        self.repair_button.setEnabled(not locked)
        for card in self._vm_cards.values():
            card.launch.setEnabled(self._can_launch(card.vm))

    def _can_launch(self, vm):
        return vm.get("state") == "paused" or (
            not self._installation_running()
            and not (vm.get("auto_setup") and not vm.get("installed", False))
            and vm.get("state", "stopped") == "stopped" and not self.vm_active(vm)
            and bool(self.host.get("ready"))
        )

    def _installation_running(self):
        return any(
            vm.get("auto_setup") and not vm.get("installed", False)
            and vm.get("state") in {"running", "starting", "shutdown"}
            and vm.get("setup", {}).get("state") != "error"
            for vm in self.vms
        )

    def _setup_progress(self, message):
        self.statusBar().showMessage(message)
        if not self.host.get("ready"):
            self.banner_text.setText(message)

    def _setup_failed(self, error):
        if not self.host.get("ready"):
            self.banner_text.setText(str(error))
            self.banner.show()
            self.repair_button.show()
        self._error(error)
        self.refresh_list(quiet=True)

    def _error(self, error):
        message = str(error) or type(error).__name__
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "Unable to complete action", message)

    def _perform(self, message, fn, success=None, *, refresh=True, failure=None, allow_during_setup=False):
        if self.tasks.blocking_busy or (self._installation_running() and not allow_during_setup):
            return
        self.statusBar().showMessage(message)

        def complete(value):
            self.statusBar().clearMessage()
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
            if self._last_poll_error and self.statusBar().currentMessage() == self._last_poll_error:
                self.statusBar().clearMessage()
            self._last_poll_error = ""
            if vms != self.vms:
                self.render_vms(vms)

        def failure(error):
            # A disconnected VM should not generate a modal alert every four seconds.
            if quiet:
                if not self.tasks.blocking_busy:
                    self._last_poll_error = f"Status refresh failed: {error}"
                    self.statusBar().showMessage(self._last_poll_error)
            else:
                self._error(error)

        # Polls share the serial task queue without flashing the global busy UI.
        self.tasks.submit(self.service.list_vms, updated, failure, blocking=False)

    def render_host(self):
        if self.host.get("ready"):
            self.banner_text.clear()
            self.banner.setObjectName("banner")
        else:
            missing = [c.get("name", "Dependency") for c in self.host.get("checks", []) if c.get("required") and not c.get("ok")]
            detail = ", ".join(missing) if missing else "a compatible host and complete virtualization runtime"
            self.banner_text.setText(f"Setup required: {detail}.")
            self.banner.setObjectName("warning")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self._busy_changed(self.tasks.blocking_busy)
        self.repair_button.setVisible(not bool(self.host.get("ready")))
        self.banner.setVisible(not bool(self.host.get("ready")))

    def render_vms(self, vms):
        self.vms = vms
        self.create_button.setObjectName("primary" if not vms else "")
        self.create_button.setIcon(action_icon("add", self.create_button.palette().buttonText().color().name()))
        self.create_button.style().unpolish(self.create_button)
        self.create_button.style().polish(self.create_button)
        vm_ids = {vm["id"] for vm in vms}
        for vm_id in self._vm_cards.keys() - vm_ids:
            card = self._vm_cards.pop(vm_id)
            self.cards_layout.removeWidget(card)
            card.hide()
            card.deleteLater()
        if self._empty_card is not None:
            self.cards_layout.removeWidget(self._empty_card)
            self._empty_card.hide()
        if not vms and self._empty_card is None:
            empty = QFrame()
            empty.setObjectName("card")
            layout = QVBoxLayout(empty)
            layout.setContentsMargins(30, 48, 30, 48)
            layout.setSpacing(14)
            artwork = QLabel()
            artwork.setPixmap(windows_artwork())
            artwork.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(artwork)
            title = label("No Windows environments", "sectionTitle", True)
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(title)
            sub = label("Select + to add Windows.", "muted", True)
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(sub)
            self._empty_card = empty
        if not vms:
            self.cards_layout.insertWidget(0, self._empty_card)
            self._empty_card.show()
        for index, vm in enumerate(vms):
            card = self._vm_cards.get(vm["id"])
            if card is None:
                card = self.vm_card(vm)
                self._vm_cards[vm["id"]] = card
            else:
                self._update_vm_card(card, vm)
            if self.cards_layout.indexOf(card) != index:
                self.cards_layout.insertWidget(index, card)
        self._busy_changed(self.tasks.blocking_busy)

    def vm_card(self, vm):
        card = VMCard()
        content = card.content
        top = QHBoxLayout()
        card.title = label("", "vmTitle", True)
        top.addWidget(card.title, 1)
        card.status = label("", "statusChip")
        top.addWidget(card.status, alignment=Qt.AlignmentFlag.AlignTop)
        content.addLayout(top)
        card.summary = label("", "muted", True)
        content.addWidget(card.summary)
        card.indicator = None
        card.setup_text = ElidedLabel()
        card.setup_text.setObjectName("muted")
        content.addWidget(card.setup_text)
        card.error_text = label("", "attention", True)
        card.error_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        content.addWidget(card.error_text)
        card.launch = QPushButton()
        card.launch.setObjectName("primary")
        card.launch.clicked.connect(lambda: self.resume_vm(card.vm) if card.vm.get("state") == "paused" else self.start_vm(card.vm))
        card.actions.addWidget(card.launch)
        card.retry = QPushButton("Retry")
        card.retry.setToolTip("Retry automatic setup")
        card.retry.clicked.connect(lambda: self._perform("Retrying automatic Windows setup…", lambda: self.service.retry_setup(card.vm["id"], self.tasks.progress.emit), allow_during_setup=True))
        card.actions.addWidget(card.retry)
        card.more = icon_button("more", "Manage Windows")
        card.actions.addWidget(card.more)
        self._update_vm_card(card, vm)
        return card

    def _update_vm_card(self, card, vm):
        card.vm = vm
        card.title.setText(vm.get("name", "Windows"))
        state = str(vm.get("state", "stopped"))
        card.status.setText(state.replace("_", " ").capitalize())
        if card.status.property("running") != (state == "running"):
            card.status.setProperty("running", state == "running")
            card.status.style().unpolish(card.status)
            card.status.style().polish(card.status)
        version = str(vm.get("windows_version", "11"))
        used = vm.get("storage_bytes", 0) or 0
        disk = vm.get("disk_gib", 0)
        description = f"{used / (1024 ** 3):.1f} GiB on disk / {disk} GiB capacity"
        active_graphics = vm.get("active_graphics", vm.get("graphics", "safe"))
        if self.vm_active(vm) and active_graphics == "safe" and vm.get("graphics") in {"yttrium", "triton"}:
            description += "  ·  Compatible display (safe launch)"
        else:
            description += {"yttrium": "  ·  Yttrium graphics", "triton": "  ·  DirectX graphics"}.get(active_graphics, "  ·  Compatible display")
        if not vm.get("installed", False) and not vm.get("auto_setup"):
            description += "  ·  Installation in progress"
        architecture = " ARM64" if vm.get("execution_profile") == "windows-arm64-virt-triton-v1" else ""
        card.summary.setText(f"Windows {version}{architecture}  ·  {vm.get('memory_mib', 0) / 1024:g} GiB  ·  {vm.get('cpus', '?')} CPUs")
        card.summary.setToolTip(description)
        card.summary.setAccessibleDescription(description)
        setup = vm.get("setup", {})
        setup_pending = bool(vm.get("auto_setup")) and not vm.get("installed", False)
        if setup_pending:
            if card.indicator is None:
                card.indicator = self.setup_indicator(vm)
                card.content.insertWidget(2, card.indicator)
            else:
                self.setup_indicator(vm, card.indicator)
        if card.indicator is not None:
            card.indicator.setVisible(setup_pending)
        card.setup_text.setText(str(setup.get("message", "Installing Windows automatically…")))
        card.setup_text.setVisible(bool(setup) and not vm.get("installed", False) and not setup_pending)
        card.error_text.setText(str(vm.get("error") or ""))
        card.error_text.setVisible(bool(vm.get("error")))
        card.launch.setText("Resume" if state == "paused" else "Start")
        card.launch.setAccessibleName(("Resume " if state == "paused" else "Start ") + vm.get("name", "Windows"))
        card.launch.setEnabled(self._can_launch(vm))
        if setup_pending and state != "paused":
            card.launch.setToolTip("Windows installs automatically. Launch is available after setup completes. If installation stops, use Retry automatic setup.")
        else:
            card.launch.setToolTip("Open the QEMU desktop window. A running VM already has its own display window." if state == "running" else "Boot your Windows environment")
        card.retry.setVisible(setup.get("state") == "error" or (setup_pending and state == "stopped" and not self.vm_active(vm)))
        card.more.setAccessibleName("Manage " + vm.get("name", "Windows"))
        old_menu = card.more.menu()
        card.more.setMenu(self.vm_menu(vm, card.more))
        if old_menu is not None:
            old_menu.deleteLater()

    def setup_indicator(self, vm, indicator=None):
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
        title = f"{phase + 1} / {len(stages)} · {stages[phase]}"
        message = str(setup.get("message") or "Windows Setup is running. Driver installation follows automatically; Windows may restart several times.")
        if complete:
            title = "Setup complete"
            message = "Windows is ready."
        elif failed:
            title = "Setup failed"
        elif paused or stopped:
            title = "Setup paused" if paused else "Setup stopped"
            instruction = "Resume Windows to continue." if paused else "Use Retry automatic setup to continue."
            message = f"{instruction}\nLast update: {message}"

        if indicator is None:
            indicator = self._new_setup_indicator(vm)
        heading = indicator.heading
        heading.setText(title)
        heading_style = "attention" if attention else "success" if complete else "muted"
        if heading.objectName() != heading_style:
            heading.setObjectName(heading_style)
            heading.style().unpolish(heading)
            heading.style().polish(heading)
        heading.setToolTip(message)
        heading.setAccessibleDescription(message)
        progress = indicator.progress
        if progress.property("attention") != attention:
            progress.setProperty("attention", attention)
            progress.style().unpolish(progress)
            progress.style().polish(progress)
        progress.setAccessibleDescription(title)
        limits = (0, 0) if not complete and not attention and phase == 0 else (0, len(stages))
        if (progress.minimum(), progress.maximum()) != limits:
            progress.setRange(*limits)
        if limits[1]:
            progress.setValue(len(stages) if complete else phase)
        indicator.failure_actions.setVisible(failed)
        return indicator

    def _new_setup_indicator(self, vm):
        indicator = QWidget()
        layout = QVBoxLayout(indicator)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)
        indicator.heading = ElidedLabel()
        layout.addWidget(indicator.heading)
        indicator.progress = QProgressBar()
        indicator.progress.setObjectName("setupProgress")
        indicator.progress.setAccessibleName("Windows setup progress")
        indicator.progress.setTextVisible(False)
        indicator.progress.setFixedHeight(6)
        indicator.progress.setToolTip("Stages: Windows installation → integration drivers → Yttrium and Vulkan")
        layout.addWidget(indicator.progress)
        indicator.failure_actions = QWidget()
        buttons = QHBoxLayout(indicator.failure_actions)
        buttons.setContentsMargins(0, 0, 0, 0)
        copy_error = QPushButton("Copy")
        copy_error.setToolTip("Copy the setup error, graphics report and guest diagnostic logs.")
        copy_error.clicked.connect(lambda: self.show_setup_diagnostics(vm, copy=True))
        buttons.addWidget(copy_error)
        view_details = QPushButton("Details")
        view_details.clicked.connect(lambda: self.show_setup_diagnostics(vm))
        buttons.addWidget(view_details)
        buttons.addStretch()
        layout.addWidget(indicator.failure_actions)
        return indicator

    def show_setup_diagnostics(self, vm, *, copy=False):
        def show(text):
            if copy:
                QApplication.clipboard().setText(text)
                self.statusBar().showMessage("Error and diagnostics copied.", 5000)
            else:
                TextDialog("Setup details", text, self).exec()
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

        action("Settings…", lambda: self.edit_vm(vm), offline_changes)
        solidworks = action("Fix SolidWorks", lambda checked: self.fix_solidworks(vm, checked),
                            offline_changes and bool(vm.get("installed")))
        solidworks.setCheckable(True)
        solidworks.setChecked(bool(vm.get("solidworks_compatibility")))
        solidworks.setToolTip("Shut down Windows after setup to enable or undo SolidWorks compatibility. "
                              "Reduces VM detection signals; license acceptance is not guaranteed.")
        action("SolidWorks graphics help…", self.show_solidworks_graphics_help, bool(vm.get("installed")))
        action("Start fullscreen", lambda: self.start_vm(vm, fullscreen=True), self._can_launch(vm) and stopped)
        safe_graphics = action("Start with safe graphics", lambda: self.start_vm(vm, safe=True),
                               self._can_launch(vm) and stopped)
        safe_graphics.setToolTip("Bypass the experimental graphics driver for this Windows session. "
                                "3D rendering may be slower. Use Start for normal graphics next time.")
        action("Shut down", lambda: self.shutdown_vm(vm), active)
        action("Restart…", lambda: self.restart_vm(vm), vm.get("state") == "running")
        if vm.get("state") == "paused":
            action("Resume", lambda: self.resume_vm(vm))
        else:
            action("Pause", lambda: self.pause_vm(vm), vm.get("state") == "running" and vm.get("active_graphics", vm.get("graphics")) not in {"yttrium", "triton"})
        action("Force stop…", lambda: self.force_stop_vm(vm), active)
        menu.addSeparator()
        action("New checkpoint…", lambda: self.snapshot_vm(vm), offline_changes)
        action("Restore checkpoint…", lambda: self.restore_vm(vm), offline_changes)
        action("Expand disk…", lambda: self.expand_vm(vm), offline_changes)
        action("Export backup…", lambda: self.export_vm(vm), offline_changes)
        menu.addSeparator()
        action("Sign-in…", lambda: self.show_windows_credentials(vm), bool(vm.get("auto_setup")))
        action("Import GPU report…", lambda: self.import_report(vm), not self._installation_running())
        action("Setup guide", self.show_graphics_guide)
        menu.addSeparator()
        action("Open folder", lambda: self._perform("Opening folder…", lambda: self.service.open_vm_folder(vm["id"]), refresh=False, allow_during_setup=True))
        action("Runtime log…", lambda: self.show_log(vm))
        action("Remove…", lambda: self.delete_vm(vm), offline_changes)
        return menu

    def create_vm(self):
        dialog = VMDialog(self.host, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            options = dialog.options()
            self._perform("Installing Windows and all required features…", lambda: self.service.create_vm(options, self.tasks.progress.emit), failure=self._setup_failed)

    def show_windows_credentials(self, vm):
        def show(details):
            QMessageBox.information(self, "Sign-in", f"Username: {details['username']}\nPassword: {details['password']}")
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

    def start_vm(self, vm, safe=False, *, fullscreen=False):
        mode = host_display(vm, self.screen())
        options = {"display_mode": mode} if mode is not None else {}
        if fullscreen:
            options["fullscreen"] = True
        message = "Starting Windows with safe graphics…" if safe else "Starting Windows in its desktop window…"
        self._perform(message,
                      lambda: self.service.start_vm(vm["id"], safe_graphics=safe, **options))

    def show_solidworks_graphics_help(self):
        graphics_driver = "Triton/DXMT" if self.host.get("platform") == "Darwin" else "Yttrium/Zink"
        TextDialog("SolidWorks graphics troubleshooting", (
            f"A crash after selecting a model can be caused by the experimental {graphics_driver} graphics driver. "
            "The Fix SolidWorks setting changes VM detection signals; it does not fix graphics drivers.\n\n"
            "Try the SolidWorks-only workaround first:\n"
            "1. Open SolidWorks with no documents open. Go to Tools > Options > System Options > Performance.\n"
            "2. Clear Enhanced graphics performance, click OK, then close and reopen SolidWorks.\n"
            "3. Return to Performance with no documents open and enable Use Software OpenGL.\n"
            "4. Open your model again. Software rendering can be slower.\n\n"
            "If that option is unavailable or crashes continue, shut down Windows completely, then choose "
            "Start with safe graphics in this menu. This bypasses the experimental driver for the whole Windows session. "
            "Use the normal Start button after the next shutdown to return to normal graphics.\n\n"
            "If software rendering still crashes, collect the Application Error for SLDWORKS.exe in Windows "
            "Event Viewer, including the faulting module, and check whether a new blank part also crashes. "
            "The cause has not been confirmed without guest crash evidence.\n\n"
            "SOLIDWORKS guidance:\n"
            "https://help.solidworks.com/2025/english/SolidWorks/sldworks/c_Performance_Settings_with_OpenGL.htm"
        ), self).exec()

    def fix_solidworks(self, vm, enabled):
        def applied(updated):
            if updated["solidworks_compatibility"]:
                message = (
                    "SolidWorks compatibility is enabled. Start Windows, then retry the SolidWorks installer "
                    "with your license.\n\n"
                    "This reduces common VM detection signals, but license acceptance is not guaranteed. "
                    "Performance may be lower, and Windows or other apps may request reactivation.\n\n"
                    "Try this inside SolidWorks:\n"
                    "1. With no documents open, go to Tools → Options → System Options → Performance.\n"
                    "2. Disable Enhanced graphics performance, then restart SolidWorks.\n"
                    "3. Enable Use Software OpenGL, then reopen your model.\n\n"
                    "To undo, shut down Windows and uncheck Fix SolidWorks."
                )
            else:
                message = "SolidWorks compatibility is disabled. Normal VM settings will be used the next time Windows starts."
            QMessageBox.information(self, "Fix SolidWorks", message)

        self._perform("Saving SolidWorks compatibility settings…",
                      lambda: self.service.update_vm(vm["id"], {"solidworks_compatibility": enabled}), applied)

    def shutdown_vm(self, vm):
        self._perform("Requesting a safe Windows shutdown…", lambda: self.service.shutdown_vm(vm["id"]), allow_during_setup=True)

    def restart_vm(self, vm):
        if not self.confirm("Restart?", "Save your work in Windows before restarting."):
            return
        self._perform("Requesting a Windows restart…", lambda: self.service.restart_vm(vm["id"]), allow_during_setup=True)

    def pause_vm(self, vm):
        self._perform("Pausing Windows…", lambda: self.service.pause_vm(vm["id"]), allow_during_setup=True)

    def resume_vm(self, vm):
        self._perform("Resuming Windows…", lambda: self.service.resume_vm(vm["id"]), allow_during_setup=True)

    def force_stop_vm(self, vm):
        if self.confirm("Force stop?", "Unsaved work will be lost. The Windows disk may need repair. Try Shut down first.", danger=True):
            self._perform("Forcing power off…", lambda: self.service.force_stop_vm(vm["id"]), allow_during_setup=True)

    def delete_vm(self, vm):
        name, ok = QInputDialog.getText(self, "Remove?", f"Files and checkpoints move to the storage folder’s .trash directory for recovery.\nType {vm['name']} to confirm:")
        if ok and name == vm["name"]:
            self._perform("Moving Windows to recoverable storage trash…", lambda: self.service.delete_vm(vm["id"]))
        elif ok:
            QMessageBox.information(self, "Name does not match", "Windows was kept. The confirmation must exactly match the environment name.")

    def snapshot_vm(self, vm):
        name, ok = QInputDialog.getText(self, "New checkpoint", "Name (letters, numbers, - or _):")
        if ok and name.strip():
            self._perform("Saving the stopped disk, firmware and TPM state. This may take a while…", lambda: self.service.snapshot_vm(vm["id"], name.strip()))

    def restore_vm(self, vm):
        def choose(names):
            if not names:
                QMessageBox.information(self, "No recovery checkpoints", "Create a checkpoint while Windows is stopped first.")
                return
            name, ok = QInputDialog.getItem(self, "Restore checkpoint", "Checkpoint", names, 0, False)
            if ok and self.confirm("Restore checkpoint?", "Replaces Windows files and TPM state. Changes since this checkpoint will be lost. Export a backup to keep them.", danger=True):
                self._perform("Restoring Windows disk, firmware and TPM state…", lambda: self.service.restore_vm(vm["id"], name))
        self._perform("Reading recovery checkpoints…", lambda: self.service.list_snapshots(vm["id"]), choose, refresh=False)

    def expand_vm(self, vm):
        size, ok = QInputDialog.getInt(self, "Expand disk", "New limit (GiB). Extend the partition in Windows Disk Management afterward:", int(vm["disk_gib"]) + 64, int(vm["disk_gib"]) + 1, 1048576)
        if ok:
            self._perform("Expanding the virtual disk…", lambda: self.service.expand_vm(vm["id"], size))

    def export_vm(self, vm):
        path, _ = QFileDialog.getSaveFileName(self, "Export backup", vm.get("name", "Windows") + ".virtue", "VM backup (*.virtue)")
        if path:
            if not path.lower().endswith(".virtue"):
                path += ".virtue"
            self._perform("Exporting the stopped Windows disk, configuration, firmware and TPM. This may take a while…", lambda: self.service.export_vm(vm["id"], path), lambda _: QMessageBox.information(self, "Backup exported", f"The full Windows backup was saved to:\n{path}"))

    def import_vm(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import backup", "", "VM backup (*.virtue)")
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
            TextDialog("Diagnostics", diagnostics_html(result, self.vms), self, rich=True).exec()
        self._perform("Refreshing host and graphics diagnostics…", self.service.host_report, show, refresh=False, allow_during_setup=True)

    def show_graphics_guide(self):
        if self.host.get("platform") == "Darwin":
            TextDialog("Mac Windows setup", (
                "Use a Windows 11 ARM64 ISO. Graphics and integration drivers are bundled and installed automatically. "
                "Windows runs x64 applications through its compatibility layer. Existing x64 Windows environments are preserved without conversion.\n\n"
                "For the SolidWorks workaround used by the Linux edition, disable Enhanced graphics performance, restart SolidWorks, "
                "then enable Use Software OpenGL with no documents open. This setting applies to SolidWorks; Windows retains its DirectX graphics driver. "
                "It does not establish which API the SolidWorks software renderer uses internally.\n\n"
                "The Mac graphics path is experimental. Successful driver installation does not establish application compatibility or performance."
            ), self).exec()
            return
        TextDialog("Setup", GRAPHICS_GUIDE, self, rich=True).exec()

    def show_display_guide(self):
        TextDialog("Display", "Windows fullscreen:\n"
                   "In the Windows window, press Ctrl+Alt+F to enter fullscreen. "
                   "Press Ctrl+Alt+F again to exit fullscreen.\n"
                   "To launch fullscreen, open the Windows menu (⋯) and choose Start fullscreen. "
                   "For every launch, enable Start Windows in fullscreen in Settings → Display.\n\n"
                   "Windows captures keyboard input in fullscreen. While captured, the Windows key and "
                   "Alt+Tab control Windows.\n\n"
                   "Windows viewer (controls hidden by default):\nCtrl+Alt+M    Show / hide controls\n"
                   "Ctrl+Alt+F    Enter / exit fullscreen\n"
                   "Ctrl+Alt+G    Return input to the host\n\n"
                   "Virtue library: F11 enters or exits fullscreen; Escape exits fullscreen.\n\n"
                   "Settings → Display controls startup fullscreen, monitor matching and input capture.\n"
                   "Matching selects a supported guest mode; Zoom To Fit scales the resulting image.\n"
                   "Use Shut down in the library to stop Windows safely.", self).exec()

    @staticmethod
    def support_folder(name):
        from ..runtime import support_folder
        try:
            return support_folder(name)
        except FileNotFoundError:
            return None

    def open_support_folder(self, name):
        folder = self.support_folder(name)
        if folder is None:
            QMessageBox.information(self, "Guides unavailable", "Reinstall the app or open docs in the source folder.")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.information(self, "Open support folder", f"Open this folder in your file manager:\n{folder}")

    def confirm(self, title, detail, *, danger=False):
        icon = QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question
        box = QMessageBox(icon, title, detail, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, self)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText("Continue")
        if danger:
            box.button(QMessageBox.StandardButton.Yes).setObjectName("danger")
        return box.exec() == QMessageBox.StandardButton.Yes

    def closeEvent(self, event):
        if self.tasks.busy:
            QMessageBox.information(self, "Operation in progress", "Wait for the current operation to finish.")
            event.ignore()
            return
        active = any(self.vm_active(vm) for vm in self.vms)
        if active and not self.confirm("Leave Windows running?", "Windows keeps running after this window closes. To stop it, choose Shut down first."):
            event.ignore()
            return
        self.timer.stop()
        if self.settings is not None:
            self.settings.setValue("window/geometry", self.saveGeometry())
        event.accept()


def create_window(service, *, settings=None):
    return MainWindow(service, settings=settings)
