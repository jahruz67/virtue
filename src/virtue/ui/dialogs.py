from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)


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
    if object_name:
        result.setObjectName(object_name)
    return result


class FileField(QWidget):
    def __init__(self, value="", parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.path = QLineEdit(value or "")
        self.path.setPlaceholderText("Choose an ISO file…")
        browse = QPushButton("Browse…")
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
        self.setWindowTitle("Windows settings" if self.vm else "Create Windows")
        self.setMinimumWidth(630)
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.addWidget(label("Make room for Windows" if not self.vm else vm.get("name", "Windows"), "sectionTitle"))
        root.addWidget(label("Choose your Windows ISO. Installation, required downloads and driver setup are automatic.", "muted", True))
        tabs = QTabWidget()
        root.addWidget(tabs)
        basics, resources, devices = QWidget(), QWidget(), QWidget()
        tabs.addTab(basics, "Windows & media")
        tabs.addTab(resources, "Resources")
        tabs.addTab(devices, "Included features")
        form = QFormLayout(basics)
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
            form.addRow("Windows version", self.version)
        form.addRow("Windows ISO", self.iso)
        self.drivers.hide()
        help_text = label("Use your own Windows ISO and valid license. Virtue detects the Windows edition, downloads all required components, and installs Windows, Yttrium graphics and integration drivers. Creating Windows accepts the supplied installation media's license terms.", "muted", True)
        form.addRow(help_text)
        self.installed = QCheckBox("Installation complete · boot the installed Windows disk")
        self.installed.setChecked(bool(vm.get("installed", False)))
        self.installed.setVisible(bool(self.vm) and not vm.get("auto_setup", False))
        form.addRow(self.installed)
        self.legacy = label("Windows 10 is a legacy compatibility target. Use Windows 11 for new environments.", "muted", True)
        self.legacy.setVisible(self.version.currentData() == "10")
        self.version.currentIndexChanged.connect(lambda: self.legacy.setVisible(self.version.currentData() == "10"))
        form.addRow(self.legacy)
        form2 = QFormLayout(resources)
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
        form2.addRow("Virtual CPU allocation", self.cpus)
        form2.addRow("Maximum disk capacity", self.disk)
        total_mem = host.get("memory_mib", 0) / 1024
        form2.addRow(label(f"Host: {total_mem:g} GiB memory · {host.get('cpus', '?')} logical CPUs. Resource choices are validated before saving; leave enough memory and CPU capacity for Linux.", "muted", True))
        self.preset_note = label("Disk space is allocated as needed. The virtual disk limit is not the amount immediately used on your host.", "muted", True)
        form2.addRow(self.preset_note)
        self.cad_note = label("CAD / Engineering starts at 16 GiB. Large SOLIDWORKS assemblies may need more. Graphics compatibility and performance require testing; this preset is not a certification.", "muted", True)
        self.cad_note.hide()
        form2.addRow(self.cad_note)
        if self.vm:
            form2.addRow(label("Use Expand disk from the Windows menu to increase an existing disk. Grow the partition in Windows Disk Management afterward.", "muted", True))
        self.preset.currentTextChanged.connect(self.apply_preset)
        self.preset.setCurrentText("Custom")
        for spin in (self.memory, self.cpus, self.disk):
            spin.valueChanged.connect(self.mark_custom)
        form3 = QFormLayout(devices)
        form3.setSpacing(14)
        self.storage_bus = QComboBox()
        self.storage_bus.addItem("Compatible SATA · installer ready", "sata")
        self.storage_bus.addItem("VirtIO · guest driver required", "virtio")
        self.storage_bus.setCurrentIndex(1 if vm.get("storage_bus") == "virtio" else 0)
        self.virtio_network = QCheckBox("Use VirtIO network adapter")
        self.virtio_network.setChecked(bool(vm.get("virtio_network", False)))
        self.storage_bus.hide()
        self.virtio_network.hide()
        form3.addRow(label("Yttrium accelerated graphics, Vulkan runtime, networking, sound, UEFI, a virtual TPM, integration drivers and the Windows guest agent are included in automatic setup. No driver ISO or terminal commands are needed.", "muted", True))
        form3.addRow(label("Yttrium currently uses guest test signing with guest Secure Boot disabled. Virtue configures this for new VMs and checks hardware rendering before reporting setup complete.", "muted", True))
        self.error = label("", wrap=True)
        self.error.setStyleSheet("color: #ffb2ad;")
        self.error.hide()
        root.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save settings" if self.vm else "Create Windows")
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
        self.resize(780, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(label(title, "sectionTitle"))
        self.text = QTextBrowser()
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
        color = "#77e0c6" if check.get("ok") else "#ffc08c"
        requirement = "required" if check.get("required") else "information"
        parts.append(f"<p><b style='color:{color}'>{'PASS' if check.get('ok') else 'CHECK'} · {esc(check.get('name', 'Dependency'))}</b> ({requirement})<br>{esc(check.get('detail', ''))}</p>")
    parts.append("<h2>Yttrium graphics</h2><p>Automatic Windows setup installs the graphics driver and records a hardware rendering check before reporting completion.</p>")
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
    parts.append("<h2>Display controls</h2><p>Windows runs in QEMU’s native display window. Use its View menu for fullscreen and scaling. Ctrl+Alt+G releases captured input; Ctrl+Alt+F toggles fullscreen on supported GTK builds. A USB tablet provides absolute pointer positioning. Audio uses the host default audio backend.</p>")
    parts.append("<p>New Windows environments install their required graphics and integration drivers automatically. Installation progress and failures appear on the Windows card.</p>")
    return "".join(parts)


GRAPHICS_GUIDE = """
<h2>Automatic Windows setup</h2>
<p>Choose your Windows ISO and select Create Windows. Virtue downloads and verifies the required runtime and drivers, prepares Windows installation, and starts it automatically.</p>
<p>Windows restarts as its integration drivers, Yttrium graphics and Vulkan runtime are installed. Leave the VM running until its card reports setup complete. Windows signs in automatically; its generated sign-in details remain available in Manage Windows.</p>
<p>Yttrium is required. New VMs use the guest signing settings needed by its current driver. Virtue checks actual hardware rendering before reporting completion.</p>
<p>If a download or installation fails, use Retry / repair setup or Retry automatic setup in the app. No terminal or manual driver installation is part of this flow.</p>
"""
