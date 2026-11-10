from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLayout, QLineEdit, QScrollArea, QSpinBox, QTabWidget,
    QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

from .icons import icon_button


PRESETS = {
    "Light": (4, 2, 64),
    "Balanced": (8, 4, 128),
    "High Performance": (16, 8, 256),
    "CAD / Engineering": (16, 8, 256),
}


def label(text, object_name=None, wrap=False):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(wrap)
    if wrap:
        policy = result.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        result.setSizePolicy(policy)
        result.setMinimumWidth(0)
    if object_name:
        result.setObjectName(object_name)
    return result


class ElidedLabel(QLabel):
    """Keep transient text to one line without losing the full accessible text."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)
        self.setAccessibleName(text)

    def sizeHint(self):
        return QSize(0, self.fontMetrics().height())

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        painter = QPainter(self)
        text = self.fontMetrics().elidedText(
            " ".join(self.text().splitlines()), Qt.TextElideMode.ElideRight,
            self.contentsRect().width(),
        )
        self.style().drawItemText(
            painter, self.contentsRect(), self.alignment(), self.palette(),
            self.isEnabled(), text, QPalette.ColorRole.WindowText,
        )


class FileField(QWidget):
    def __init__(self, value="", parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.path = QLineEdit(value or "")
        self.path.setPlaceholderText("Select ISO…")
        browse = icon_button("folder", "Choose ISO file")
        browse.clicked.connect(self.browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)

    def browse(self):
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose installation media", self.path.text(), "ISO images (*.iso);;All files (*)")
        if chosen:
            self.path.setText(chosen)

    def text(self):
        return self.path.text().strip()


class VMDialog(QDialog):
    """Creation and offline resource/media settings share one actual model."""

    def __init__(self, host: dict, vm: dict | None = None, parent=None):
        super().__init__(parent)
        self.vm = vm
        vm = vm or {}
        self.setWindowTitle("Settings" if self.vm else "New Windows")
        self.setMinimumSize(420, 320)
        self.resize(660, 520)
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        basics, resources, devices = QWidget(), QWidget(), QWidget()
        for page, title in ((basics, "General"), (resources, "Resources"), (devices, "Features")):
            page.setObjectName("settingsPage")
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, title)
        form = QFormLayout(basics)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        form.setContentsMargins(20, 20, 20, 20)
        form.setSpacing(14)
        self.name = QLineEdit(vm.get("name", "Windows"))
        self.name.setMaxLength(80)
        self.name.setObjectName("vmName")
        self.version = QComboBox()
        self.version.addItem("Windows 11 x64", "11")
        self.version.addItem("Windows 10 x64 · legacy", "10")
        self.version.setCurrentIndex(1 if str(vm.get("windows_version", "11")) == "10" else 0)
        if self.vm:
            self.version.setEnabled(False)
        self.iso = FileField(vm.get("iso_path", ""))
        self.drivers = FileField(vm.get("drivers_iso_path", ""))
        form.addRow("Name", self.name)
        if self.vm:
            form.addRow("Version", self.version)
        form.addRow("Windows ISO", self.iso)
        if self.vm:
            form.addRow("Drivers ISO", self.drivers)
        else:
            self.drivers.hide()
        help_text = label("Windows ISO and license required. Creating Windows accepts the ISO’s license terms.", "muted", True)
        form.addRow(help_text)
        self.installed = QCheckBox("Installation complete")
        self.installed.setToolTip("Boot the installed Windows disk")
        self.installed.setChecked(bool(vm.get("installed", False)))
        self.installed.setVisible(bool(self.vm) and not vm.get("auto_setup", False))
        form.addRow(self.installed)
        self.legacy = label("Windows 10 is legacy. Windows 11 recommended.", "muted", True)
        self.legacy.setVisible(self.version.currentData() == "10")
        self.version.currentIndexChanged.connect(lambda: self.legacy.setVisible(self.version.currentData() == "10"))
        form.addRow(self.legacy)
        form2 = QFormLayout(resources)
        form2.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form2.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        form2.setContentsMargins(20, 20, 20, 20)
        form2.setSpacing(14)
        self.preset = QComboBox()
        self.preset.addItems([*PRESETS, "Custom"])
        self.memory = QSpinBox()
        self.memory.setRange(4, 1048576)
        self.memory.setSuffix(" GiB")
        self.cpus = QSpinBox()
        self.cpus.setRange(2, 1024)
        self.cpus.setSuffix(" vCPUs")
        self.disk = QSpinBox()
        self.disk.setRange(64, 1048576)
        self.disk.setSuffix(" GiB")
        host_memory = host.get("memory_mib", 16384)
        recommended_memory = min(8192, max(4096, host_memory - max(2048, host_memory // 5)))
        self.memory.setValue(max(4, int(vm.get("memory_mib", recommended_memory)) // 1024))
        self.cpus.setValue(int(vm.get("cpus", min(4, max(2, host.get("cpus", 8) - 1)))))
        self.disk.setValue(int(vm.get("disk_gib", 128)))
        self.disk.setEnabled(not bool(self.vm))
        form2.addRow("Preset", self.preset)
        form2.addRow("Memory", self.memory)
        form2.addRow("CPUs", self.cpus)
        form2.addRow("Disk limit", self.disk)
        total_mem = host.get("memory_mib", 0) / 1024
        form2.addRow(label(f"Host: {total_mem:g} GiB · {host.get('cpus', '?')} CPUs", "muted", True))
        self.preset_note = label("Disk space grows as needed.", "muted", True)
        form2.addRow(self.preset_note)
        self.cad_note = label("Large CAD assemblies may need more memory. Compatibility is not certified.", "muted", True)
        self.cad_note.hide()
        form2.addRow(self.cad_note)
        if self.vm:
            form2.addRow(label("To increase capacity, use ⋯ → Expand disk, then Windows Disk Management.", "muted", True))
        self.preset.currentTextChanged.connect(self.apply_preset)
        self.preset.setCurrentText("Custom")
        for spin in (self.memory, self.cpus, self.disk):
            spin.valueChanged.connect(self.mark_custom)
        form3 = QFormLayout(devices)
        form3.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form3.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        form3.setContentsMargins(20, 20, 20, 20)
        form3.setSpacing(14)
        self.storage_bus = QComboBox()
        self.storage_bus.addItem("Compatible SATA · installer ready", "sata")
        self.storage_bus.addItem("VirtIO · guest driver required", "virtio")
        self.storage_bus.setCurrentIndex(1 if vm.get("storage_bus") == "virtio" else 0)
        self.virtio_network = QCheckBox("Use VirtIO network adapter")
        self.virtio_network.setChecked(bool(vm.get("virtio_network", False)))
        self.storage_bus.hide()
        self.virtio_network.hide()
        form3.addRow(label("Included automatically:\nGraphics · Vulkan · Network · Sound\nUEFI · TPM · Drivers · Guest agent", "muted", True))
        form3.addRow(label("Guest test signing enabled; Secure Boot disabled. Hardware rendering is verified during setup.", "muted", True))
        self.error = label("", "error", True)
        self.error.hide()
        form.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save" if self.vm else "Create")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def mark_custom(self):
        values = (self.memory.value(), self.cpus.value(), self.disk.value())
        if self.preset.currentText() in PRESETS and values != PRESETS[self.preset.currentText()]:
            self.preset.blockSignals(True)
            self.preset.setCurrentText("Custom")
            self.preset.blockSignals(False)

    def apply_preset(self, name):
        self.cad_note.setVisible(name == "CAD / Engineering")
        if name not in PRESETS:
            return
        values = PRESETS[name]
        for spin, value in zip((self.memory, self.cpus, self.disk), values):
            if spin.isEnabled():
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)

    def validate(self):
        reason = None
        if not self.name.text().strip():
            reason = "Give your Windows environment a name."
        elif not self.installed.isChecked() and not self.iso.text():
            reason = "Choose your Windows installation ISO."
        elif not self.installed.isChecked() and self.iso.text() and not Path(self.iso.text()).is_file():
            reason = "The Windows ISO does not exist or cannot be read. Choose the file again."
        elif self.drivers.text() and not Path(self.drivers.text()).is_file():
            reason = "The VirtIO drivers ISO does not exist."
        if reason:
            self.error.setText(reason)
            self.error.show()
            self.tabs.setCurrentIndex(0)
            QTimer.singleShot(0, lambda: self.tabs.widget(0).ensureWidgetVisible(self.error))
        else:
            self.accept()

    def options(self):
        return {
            "name": self.name.text().strip(),
            "windows_version": self.version.currentData(),
            "iso_path": "" if self.installed.isChecked() else self.iso.text(),
            "drivers_iso_path": self.drivers.text(),
            "memory_mib": self.memory.value() * 1024,
            "cpus": self.cpus.value(),
            "disk_gib": self.disk.value(),
            "installed": self.installed.isChecked(),
            "storage_bus": self.storage_bus.currentData(),
            "virtio_network": self.virtio_network.isChecked(),
        }


class TextDialog(QDialog):
    def __init__(self, title, text, parent=None, *, rich=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(420, 320)
        self.resize(780, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        self.text = QTextBrowser()
        self.text.setMinimumSize(0, 0)
        self.text.setOpenExternalLinks(True)
        if rich:
            self.text.setHtml(text)
        else:
            self.text.setPlainText(text)
        layout.addWidget(self.text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def diagnostics_html(host, vms):
    esc = lambda value: html.escape(str(value))
    parts = ["<h2>Host readiness</h2>", f"<p>{esc(host.get('platform', 'Unknown'))} · {esc(host.get('architecture', 'Unknown'))} · {esc(host.get('cpus', '?'))} CPUs · {host.get('memory_mib', 0) / 1024:g} GiB memory</p>"]
    for check in host.get("checks", []):
        color = "#14724d" if check.get("ok") else "#965a10"
        requirement = "required" if check.get("required") else "information"
        parts.append(f"<p><b style='color:{color}'>{'PASS' if check.get('ok') else 'CHECK'} · {esc(check.get('name', 'Dependency'))}</b> ({requirement})<br>{esc(check.get('detail', ''))}</p>")
    parts.append("<h2>Yttrium graphics (required)</h2><p>Yttrium graphics and its host prerequisites are required for Windows setup. Automatic Windows setup installs the graphics driver and records a hardware rendering check before reporting completion. Host prerequisites do not prove guest acceleration.</p>")
    parts.append(f"<p>Host stack: {'prerequisites detected' if host.get('yttrium_ready') else 'not ready or not verified'}</p>")
    for gpu in host.get("gpus", []):
        if isinstance(gpu, dict):
            parts.append("<p>" + "<br>".join(f"{esc(key)}: {esc(value)}" for key, value in gpu.items()) + "</p>")
        else:
            parts.append(f"<p>{esc(gpu)}</p>")
    for vm in vms:
        result = vm.get("gpu_verification", vm.get("gpu_status", "No guest acceleration report imported"))
        parts.append(f"<h3>{esc(vm.get('name', 'Windows'))}</h3><p>Configured display: {esc(vm.get('graphics', 'safe'))}</p>")
        if isinstance(result, dict):
            state = result.get("state", "unverified").replace("_", " ")
            parts.append(f"<p><b>Guest verification: {esc(state)}</b></p>")
            if result.get("historical"):
                parts.append("<p>Historical evidence from an imported capture. Current guest state can differ.</p>")
            for reason in result.get("reasons", []):
                parts.append(f"<p>{esc(reason)}</p>")
            evidence = result.get("evidence", {})
            if isinstance(evidence, dict):
                parts.append("<p>" + "<br>".join(f"{esc(key.replace('_', ' '))}: {esc(value)}" for key, value in evidence.items()) + "</p>")
        else:
            parts.append(f"<p>Guest verification: {esc(result)}</p>")
    parts.append("<h2>Display controls</h2><p>Windows scales to fit its window. Ctrl+Alt+M shows the controls; the View menu contains fullscreen, scaling and input capture. Ctrl+Alt+F toggles fullscreen; Ctrl+Alt+G releases input. If a shortcut does not respond, show controls first. Audio uses the host default backend.</p>")
    parts.append("<p>New Windows environments install their required graphics and integration drivers automatically. Installation progress and failures appear on the Windows card.</p>")
    return "".join(parts)


GRAPHICS_GUIDE = """
<p>Select +, choose a Windows ISO, then Create.</p>
<p>Keep Windows running through setup and restarts. Drivers and graphics install automatically. Setup finishes after hardware rendering is verified.</p>
<p>Sign-in details: ⋯ → Sign-in. If setup fails, select Retry; for host issues, select Repair.</p>
<p>Guest test signing is enabled and Secure Boot disabled for the required graphics driver.</p>
"""
