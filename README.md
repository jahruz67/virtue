# Virtue

A Linux-first Qt 6 desktop application for running Windows with QEMU/KVM. The
engine launches real QEMU and swtpm processes, manages them through QMP, and stores
each VM independently of the desktop application's lifetime.

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

```sh
sudo apt update
sudo apt install qemu-system-x86 qemu-system-gui qemu-utils ovmf swtpm \
  swtpm-tools libegl1 libopengl0 libxkbcommon0 libxcb-cursor0 libpulse0 \
  python3-venv
# Add your account to the kvm group if /dev/kvm is inaccessible, then log out/in.
sudo usermod -aG kvm "$USER"
python3 -m venv .venv
.venv/bin/pip install '.[test]'
.venv/bin/virtue
```

Never run the desktop as root. The app's **Host diagnostics** shows missing
dependencies and permissions. See [build and packaging](docs/build.md) for Fedora,
native DEB packaging, headless diagnostics, and reproducible builds.

## Install Windows

1. Open Virtue and choose **New Windows**. Select a Windows 11 x64 ISO, resource
   preset or explicit RAM/vCPU/disk values, and optionally a VirtIO driver ISO.
2. Create the VM, then launch it. Windows Setup runs in QEMU's separate GTK window.
   Click that window and press a key when the UEFI installer requests it.
3. Complete ordinary Windows installation and activation. The initial AHCI disk
   and Intel network adapter work with Windows' built-in drivers. After the first
   reboot, let the hard disk boot instead of starting Setup again.
4. Shut down Windows. Open VM settings and mark installation complete to detach
   the installer on later boots. Drivers media can remain attached.
5. Optionally install VirtIO drivers from the attached ISO, shut down, and enable
   VirtIO networking in settings. Changing the system disk to VirtIO requires
   loading the storage driver first; keep a snapshot and consult the driver docs.

Windows remains a separately managed guest. Fullscreen, scaling, keyboard/mouse
capture and audio use QEMU GTK (View menu; release captured input with Ctrl+Alt+G).
The default user network permits outbound connections without opening host ports.
Clipboard, shared folders, seamless individual apps and multi-monitor integration
are not implemented in this preview.

## Experimental graphics and recovery

Install the compatible upstream Yttrium Flatpak stack and inspect **Host
diagnostics**. Stop the VM, open graphics setup and explicitly accept the guest
driver security implications. Virtue creates an offline recovery snapshot before
enabling the Yttrium device. Follow [Yttrium setup](docs/yttrium.md) for guest
Secure Boot, test-signing, certificates and drivers. These steps remain explicit
guest administration; Virtue does not modify host security controls.

**Launching a VM does not prove acceleration.** Collect real guest rendering
evidence with the supplied script, import the report, and run the graphics/CAD
acceptance tests. A report is historical, user-supplied evidence. SOLIDWORKS is
not certified or confirmed compatible. Use **Launch with safe display** or restore
the pre-driver snapshot after a driver failure. Snapshot restore also restores
firmware and TPM state; retain your Windows BitLocker recovery key separately.

## Data and lifecycle

VMs live in `$XDG_DATA_HOME/virtue/vms` (normally
`~/.local/share/virtue/vms`). Set `--data-dir /absolute/path` to use another root.
Closing Virtue leaves Windows running; reopen Virtue to reconnect. **Shut down**
sends an ACPI power event and waits for Windows to finish on its own. Force stop
is a separate destructive action. Pause/resume is unavailable with Yttrium.

Cold snapshots include disk, UEFI variables and TPM state. A restore preserves a
recovery snapshot first. Export produces a complete offline `.virtue` archive;
import verifies hashes and paths and assigns a fresh VM ID. Backups contain user
data and TPM secrets: store them privately. Imported VMs boot with safe graphics.
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

