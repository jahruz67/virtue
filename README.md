# Virtue

A Linux-first Qt 6 desktop application for running Windows with QEMU/KVM. The
engine launches real QEMU and swtpm processes, manages them through QMP, and stores
each VM independently of the desktop application's lifetime.

An Apple Silicon macOS port is implemented in source using HVF and
Triton/Neptune/DXMT. The release workflow now builds its native runtime and app
automatically on a Mac runner; this build recipe and the port remain unvalidated. See the
[Mac build and implementation status](docs/macos-validation.md).

**Release status: engineering preview, not a validated Linux release.** The handoff
records partial automated test runs on Windows; final suite results are unconfirmed.
This environment has no Linux/KVM host, Windows ISO, or SOLIDWORKS license.
Windows installation, real GPU acceleration,
audio, and CAD compatibility must be validated on the target hardware. See
[validation](docs/validation.md) for reported evidence, gaps, and acceptance checks.

## Install on Linux x86-64

Ubuntu 24.04 / Debian 12 or later, a graphical Wayland or X11 session, hardware
virtualization enabled in firmware, and enough RAM to leave at least 2 GiB (or
20%, whichever is larger) for the host are required. Windows 11 requires at least
4 GiB guest RAM, 2 vCPUs and a 64 GiB disk. A legal Windows ISO and license are
provided by the user.

Open the downloaded DEB or RPM in your desktop's software installer, install it,
and launch **Virtue** from the applications menu. Required system packages are
dependencies, not optional recommendations. On first launch, Virtue downloads
and verifies the Yttrium runtime, Windows drivers and Vulkan runtime. A desktop
administrator prompt handles host changes when needed. No terminal is required.

Never run the desktop as root. The app's **Host diagnostics** shows missing
dependencies and permissions. See [build and packaging](docs/build.md) for Fedora,
native DEB packaging, headless diagnostics, and reproducible builds.

Each push to `main` is configured to publish a GitHub prerelease after the Linux
and Mac builds succeed, containing an amd64 DEB for Ubuntu/Debian, an x86_64 RPM
for Fedora, and an Apple Silicon `Virtue.app` ZIP, with SHA-256 checksums.
Open the downloaded Linux package in the desktop's
software installer. The RPM uses Fedora's
system Python and PySide6. These remain engineering previews requiring target-host
validation. The Mac package targets macOS 26 or later and uses ad-hoc signing.

## Install Windows

1. Choose **Create Windows** and select your Windows 10/11 x64 ISO.
2. Select **Create Windows**. Virtue reads the ISO, selects its x64 image, prepares
   an answer file and installation media, and starts Windows installation.
3. Leave the VM running while Windows and its required integration drivers,
   Yttrium graphics and Vulkan runtime install across automatic restarts.
4. The Windows card reports progress and becomes ready after a hardware rendering
   check. Virtue detaches the installer on subsequent boots and signs Windows in
   automatically. Generated credentials are available in **Manage Windows**.

No driver downloads, guest terminal commands, manual firmware changes, optional
graphics toggle or manual installation-complete checkbox are part of this flow.

New installations also remove OneDrive through its registered Microsoft uninstaller
(unless an account or folder backup needs attention), uninstall a fixed list of optional apps
using Windows package management, reduce desktop animations, and turn off Edge
startup boost and background apps by default. Edge preferences remain changeable.
Store, App Installer, security, updates, font smoothing, and graphics acceleration
are preserved. GPU scheduling stays at the driver default; it is not a guaranteed
performance improvement for a virtual GPU. Optional cleanup failures do not block
setup and are recorded in `C:\ProgramData\Virtue\windows-optimization.json`.
Repairing an existing installation does not opt it into this cleanup.
Use **Retry / repair setup** or **Retry automatic setup** if setup reports a failure.

Windows remains a separately managed guest. Fullscreen, scaling, keyboard/mouse
capture and audio use QEMU GTK. **Settings → Display** saves startup fullscreen,
the monitor to match, relative mouse capture for CAD/games and automatic keyboard
capture on hover in windowed mode (enabled by default). **Start fullscreen** is also
available in the Windows menu and always enables input capture.
The guest requests the selected monitor's physical resolution and chooses the
closest supported refresh rate after sign-in; unsupported modes leave its current
display intact. Windows DPI is preserved. The desktop chooses the VM window's
placement, so move it to the matching monitor if needed. The viewer menu bar is hidden;
Ctrl+Alt+M toggles the controls, Ctrl+Alt+F toggles fullscreen, and Ctrl+Alt+G releases
input to Linux. While captured, the Windows key and Alt+Tab control Windows.
GTK uses its normal desktop/Flatpak theme selection. F11 toggles Virtue's own fullscreen; Escape exits
it. Virtue remembers its window geometry and fullscreen state.
**Host diagnostics** distinguishes the requested monitor mode from the confirmed
Windows mode. Existing managed guests receive display integration automatically
through the guest agent if needed. Matching runs asynchronously and cannot block
VM boot; it requires signing in to the generated Virtue Windows account.
The default user network permits outbound connections without opening host ports.
Clipboard, shared folders, seamless individual apps and multi-monitor integration
are not implemented in this preview.

## Required Yttrium graphics and recovery

Yttrium is the default graphics stack for new VMs and is required by host readiness
checks. Virtue installs its pinned Flatpak and matching Windows driver automatically.
New VMs use unenrolled UEFI variables and Windows test signing as required by the
current upstream driver. Certificates, the Vulkan runtime, hibernation settings,
driver installation and restarts are handled by the provisioning worker.

**Launching a VM does not prove acceleration.** Automatic setup collects an actual
OpenGL draw result and checks for hardware Venus/Zink rendering before reporting
completion. SOLIDWORKS compatibility and Direct3D workloads still require their
own acceptance tests. Snapshot restore also restores firmware and TPM state;
retain your Windows BitLocker recovery key separately.

## Data and lifecycle

New VMs use **Automatic** resources: memory and CPU limits are chosen before a
cold start, leaving at least 2 GiB (or 20%) of RAM for Linux and accounting for
other active Virtue VMs. Manual settings are under **Resources → Advanced**.
Existing VMs keep their saved allocations until Automatic is selected. This
does not resize RAM while Windows is running; virtual disks still grow as needed
up to their configured capacity.

VMs live in `$XDG_DATA_HOME/virtue/vms` (normally
`~/.local/share/virtue/vms`). Set `--data-dir /absolute/path` to use another root.
Closing Virtue leaves Windows running; reopen Virtue to reconnect. **Shut down**
sends an ACPI power event and waits for Windows to finish on its own. Force stop
is a separate destructive action. Pause/resume is unavailable with Yttrium.

Cold snapshots include disk, UEFI variables and TPM state. A restore preserves a
recovery snapshot first. Export produces a complete offline `.virtue` archive;
import verifies hashes and paths and assigns a fresh VM ID. Backups contain user
data, Windows sign-in details and TPM secrets: store them privately. Imported VMs
retain their configured graphics stack and must pass the destination host checks.
Keep external backups; local snapshots do not protect against a failed host disk.

## Development

```sh
.venv/bin/python -m pytest -q
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui.py -q
.venv/bin/python -m build
.venv/bin/python tools/linux_smoke.py --help
```

Python 3.11+ with PySide6 was selected to make the Qt 6 application executable and
testable in the available environment without a C++ toolchain. Native QEMU/KVM
performs guest execution; Python is not in the graphics or CPU emulation path.
The model, storage, service and backend boundaries permit a later macOS backend.
macOS virtualization is deliberately unimplemented while Linux remains unvalidated.

Application source is MIT licensed. Qt/PySide6, QEMU, OVMF, swtpm and Yttrium retain
their own licenses; see [third-party notices](THIRD_PARTY.md).

