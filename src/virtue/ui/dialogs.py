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
        root.addWidget(label("Choose your installation media and a predictable amount of host resources.", "muted", True))
        tabs = QTabWidget()
        root.addWidget(tabs)
        basics, resources, devices = QWidget(), QWidget(), QWidget()
        tabs.addTab(basics, "Windows & media")
        tabs.addTab(resources, "Resources")
        tabs.addTab(devices, "Devices")
        form = QFormLayout(basics)
        form.setSpacing(14)
        self.name = QLineEdit(vm.get("name", "Windows 11"))
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
        form.addRow("Windows version", self.version)
        form.addRow("Windows ISO", self.iso)
        form.addRow("VirtIO drivers ISO", self.drivers)
        help_text = label("Use a Windows ISO obtained from Microsoft and your own valid license. A VirtIO ISO is optional for the compatible SATA disk and network defaults; attach it to install faster device drivers later.", "muted", True)
        form.addRow(help_text)
        self.installed = QCheckBox("Installation complete · boot the installed Windows disk")
        self.installed.setChecked(bool(vm.get("installed", False)))
        self.installed.setVisible(bool(self.vm))
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
        self.memory.setValue(max(4, int(vm.get("memory_mib", 8192)) // 1024))
        self.cpus.setValue(int(vm.get("cpus", 4)))
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
        self.preset.setCurrentText("Custom" if self.vm else "Balanced")
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
        form3.addRow("Storage interface", self.storage_bus)
        form3.addRow("Networking", self.virtio_network)
        form3.addRow(label("Install VirtIO storage and network drivers inside Windows before switching an existing VM. Without the storage driver, Windows may be unable to boot. Network access uses private user networking with no inbound ports exposed.", "muted", True))
        form3.addRow(label("UEFI and a virtual TPM are configured automatically. Experimental graphics has a separate recovery and consent workflow.", "muted", True))
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
        requirement = "required" if check.get("required") else "optional"
        parts.append(f"<p><b style='color:{color}'>{'PASS' if check.get('ok') else 'CHECK'} · {esc(check.get('name', 'Dependency'))}</b> ({requirement})<br>{esc(check.get('detail', ''))}</p>")
    parts.append("<h2>Experimental graphics</h2><p>Host readiness checks do not prove that guest GPU acceleration works. Import a guest verification report to record evidence.</p>")
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
    parts.append("<p>Stable display remains available from each Windows menu. Clipboard, automatic guest display resizing and shared folders depend on additional guest integration and are not advertised as available in this build.</p>")
    return "".join(parts)


GRAPHICS_GUIDE = """
<h2>Yttrium guest setup</h2>
<p>This is experimental software. Enable it only on a stopped, fully installed Windows environment. Virtue creates a recovery checkpoint before changing the graphics configuration.</p>
<p>The experimental guest drivers can require disabling guest Secure Boot and enabling Windows test signing. This weakens guest driver-signing protections and permits test-signed kernel drivers. Host security is not changed. Turning on the graphics configuration does not install guest drivers or prove acceleration.</p>
<ol>
<li>Download the matching Yttrium Windows driver and Mesa user-mode components from the official release. Keep the original archives for recovery.</li>
<li>Disable Secure Boot manually in the guest’s UEFI setup. Press Escape early during guest boot, open Device Manager → Secure Boot Configuration, disable Attempt Secure Boot, save, and restart. Existing enrolled keys and TPM data are preserved. If Windows uses BitLocker, obtain the recovery key before changing guest boot security.</li>
<li>Boot Windows. Follow the release’s certificate and driver installation instructions. If required by that release, run <code>bcdedit /set testsigning on</code> in an Administrator terminal and restart. The bundled <code>Prepare-YttriumGuest.ps1</code> offers an explicit confirmation workflow.</li>
<li>Disable guest hibernation and Fast Startup if the release requires it: <code>powercfg /h off</code>. This also removes guest hibernation support.</li>
<li>Install the Vulkan runtime and run <code>Collect-VirtueGpuReport.ps1</code> inside Windows. Use Help &amp; guest tools → Open guest scripts to find the scripts on the host, then transfer them into Windows. Inspect Vulkan and OpenGL renderer evidence and test a real rendering workload.</li>
<li>Import the resulting JSON report through the Windows menu. A running VM alone never counts as working GPU acceleration.</li>
</ol>
<p>If Windows fails to boot, use Launch with safe display or restore the recovery checkpoint while stopped. Restoring a checkpoint replaces subsequent Windows data; export a full backup first if it is needed.</p>
<p><a href="https://github.com/arehnman/yttrium-virtio-gpu">Official Yttrium documentation and releases</a></p>
<p>SOLIDWORKS is not certified for this virtual GPU. Run the documented CAD tests before relying on it for production work.</p>
"""
