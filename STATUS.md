# Virtue project handoff

**Updated:** 8 November 2026  
**Workspace:** `C:\Users\360he\Documents\ChatGPT\virt`  
**Product target:** a Linux-first desktop for installing and running Windows on QEMU/KVM, with carefully gated experimental Yttrium graphics.  
**Current maturity:** a substantial, buildable engineering preview with a real Linux QEMU/KVM code path and Qt desktop; **not yet validated as a working Linux Windows-VM product**.

This file is an implementation handoff. Read it with [README.md](README.md), [Yttrium setup](docs/yttrium.md), [CAD testing](docs/cad-testing.md), [build notes](docs/build.md), and the code. Do not infer successful Linux VM boot or GPU performance from unit tests, a desktop screenshot, dependency detection, or the existence of the backend.

## Goal and priorities

The goal is a straightforward desktop application for Windows on Linux, especially engineering workloads such as SOLIDWORKS. The user should be able to create a persistent Windows VM from their own legitimate ISO, set RAM/virtual CPUs/disk, launch the Windows desktop, manage shutdown and recovery, and later enable Yttrium only with clear guest-security consent. Windows 11 x64 on Linux x86-64 is the first target. A fallback display must continue to work without Yttrium.

Continue in this order: (1) build and validate the Linux package on actual Linux, (2) exercise real Windows installation and VM lifecycle, (3) fix integration issues uncovered on Linux, (4) validate guest I/O and recovery, (5) validate the pinned Yttrium stack on real GPUs, including integrated GPUs, (6) conduct honest OpenGL/CAD testing and record evidence, and (7) only after Linux is operational, scope macOS. Do not spend the remaining project effort on a macOS implementation before Linux works.

## What exists

- Python 3.11+ application using PySide6 / Qt 6 Widgets; Linux x86-64 is the only implemented VM host. The standard-library Python service manages QEMU; it is not a hypervisor or rendering engine. UI operations run on a serialized background worker.
- A dark Qt desktop with host checks, VM list, creation wizard, resource presets, installation ISO and VirtIO ISO selection, VM settings, storage/network driver warnings, QEMU display launch, safe-display launch, lifecycle menu, snapshots, disk expansion, complete offline export/import, diagnostics, guest graphics-report import, logs, and help links. Actual Windows work runs in QEMU's separate GTK window.
- `src/virtue/domain.py` defines validated VM settings and resource limits. `store.py` implements private per-user storage, validated IDs, cross-process locking, and atomic JSON writes. Default data lives under `$XDG_DATA_HOME/virtue/vms`, normally `~/.local/share/virtue/vms`.
- `src/virtue/backends/linux.py` implements QEMU/KVM and swtpm process management, QMP lifecycle/reconnect, PID and UUID checks, Q35/UEFI/TPM, thin QCOW2 creation, ISO boot, SATA/e1000e compatibility defaults, optional VirtIO, guest-agent restart, snapshots, disk growth, and recoverable removal. QEMU arguments are built as an argument array without a shell. The backend refuses non-Linux operation and does not silently fall back to CPU emulation.
- Host probes in `host.py` check KVM by attempting KVM API/VM creation, required tools, firmware, display support, memory/CPU and Vulkan/Yttrium prerequisites. These identify host capabilities; they do not establish guest operation.
- `backup.py` exports and imports offline VM bundles with a manifest and SHA256 checks; imported data is staged and validated. `storage.py` rejects QCOW2 external backing/data-file references before guest launch.
- `graphics.py` conservatively classifies imported Windows evidence. Windows PowerShell helpers in `guest/` gather graphics evidence and assist with guest preparation. Enabling Yttrium requires explicit acknowledgement and creates a checkpoint; the user handles guest UEFI Secure Boot and Windows test-signing themselves. The host is not modified for guest signing.
- `resources/yttrium-stack.json` and [docs/yttrium.md](docs/yttrium.md) pin/research the current upstream Yttrium release components, checksums, requirements, known limitations, consent, verification, and recovery. The integration uses the upstream QEMU Flatpak where its checks pass. GPU acceleration is not marked successful just because the VM starts.
- A DEB builder, Linux smoke script, desktop entry/icon, third-party notices and GitHub CI definition are present. The builder downloads Linux Qt wheels and constructs a DEB on a build machine; this alone does not prove the package installs or launches correctly on Ubuntu/Debian.
- No substantial macOS backend has been started, by design.

## Work completed in this session

The initial checkout was empty. A Python/Qt architecture and Linux QEMU/KVM implementation were created, along with the UI, host/GPU probes, guest scripts, recovery/export code, Linux package builder, documentation and test suite. QEMU/HVF and Yttrium component requirements were researched against the [QEMU invocation documentation](https://www.qemu.org/docs/master/system/invocation.html), [QMP specification](https://www.qemu.org/docs/master/interop/qmp-spec.html), and the [upstream Yttrium repository](https://github.com/arehnman/yttrium-virtio-gpu).

The UI was opened through Qt's offscreen platform with the **actual** service on the current Windows development host. It correctly reports that a Linux x86-64 host is required, leaves Create Windows disabled, and displays the empty state. This is a host-readiness/UI observation only. A screenshot of that truthful empty state is at `.test-artifacts/desktop.png` (ignored by Git).

Several integration and safety issues were found and addressed during review: preserve host CPU/memory, bind VM settings changes to stopped guests, boot with Windows-compatible SATA/network defaults, keep VM-specific verified UEFI firmware for portability, create cold snapshots of disk + UEFI + TPM together, make removal recoverable, verify imported backup contents before publishing, reject dangerous QCOW2 external references, distinguish the active safe display from the saved graphics preference, handle a running QEMU whose QMP connection fails, and refresh host diagnostics. The latest backend edit also corrected file-descriptor sync for Windows-side snapshot tests. Recheck the final working tree and validation before relying on those fixes.

## What has and has not been verified

The host is Windows 11 x64. WSL is not installed. There is no Linux/KVM host, QEMU/KVM, Linux GUI/QEMU display, Windows ISO, Windows guest, physical GPU qualification system, or SOLIDWORKS installation/license in this environment. The project deliberately reports this limitation rather than pretending Windows-native development proves Linux virtualization.

Checks completed earlier in the session:

- Qt UI tests passed (first 6, then expanded to 9) using `QT_QPA_PLATFORM=offscreen`; the latest UI suite included service startup on this Windows host.
- Host and graphics unit tests passed (14 total according to the host/GPU implementer). Both PowerShell scripts parsed successfully, but the GPU collector could not execute because the machine's existing PowerShell execution policy blocked it. The policy was not changed.
- A combined suite reached 71 passing and 2 failing tests because snapshot copy called `fsync` on a read-only file handle on Windows. The backend agent changed it to an `r+b` handle after that run. No fresh final test result is recorded here.
- Earlier core/backup tests exposed the same read-only `fsync` issue; the backup writer was changed to a read/write handle. Verify this in the final test run.
- DEB build was attempted from Windows and successfully downloaded the pinned Linux Qt wheels and built the application wheel; the terminal session ended before a confirmed final DEB success was captured. Check `dist/` and rerun the builder on Linux for a real package check.
- `python -m virtue --diagnose` ran successfully and honestly reported Windows as unsupported for VM execution.
- No QEMU/KVM VM was launched. No Windows install, shutdown, restore, network, audio, display, VirtIO driver, Yttrium GPU render, benchmark, or SOLIDWORKS test has succeeded in this environment.

## Immediate handoff checklist

1. Review `git status --short` and the current diff. This repository has no commits yet; files are currently untracked. Check there are no local files to preserve before future cleanup. Do not remove user data.
2. Run the final Windows-compatible unit/UI suite from the project root:

   ```powershell
   $env:QT_QPA_PLATFORM = 'offscreen'
   .\.venv\Scripts\python.exe -m pytest -q
   .\.venv\Scripts\python.exe -m virtue --diagnose
   .\.venv\Scripts\python.exe -m build
   ```

   If QMP tests require AF_UNIX, the protocol tests should use an injected loopback transport on Windows and real AF_UNIX on Linux; do not remove the protocol coverage. Record the final pass/fail count and resolve remaining failures.
3. Review service and backend alignment after the latest changes: `prepare_offline` before archive export; safe process stop/restart; no symlink following; firmware copies and snapshots; VM config restored with its snapshot; start-time QCOW2 safety checks; never-started VM import creates its TPM working directory. Ensure each has a meaningful test.
4. Inspect the DEB builder's result. Build a native Linux wheel and DEB on Ubuntu 24.04 or Debian 12, install into a clean test VM/container with GUI libraries, inspect `dpkg-deb --info`/`--contents`, verify Qt license/source obligations, launch Virtue as an ordinary user, and exercise diagnostics. CI should build Linux artifacts only on Linux and publish artifacts with truthful engineering-preview status.
5. On actual supported Linux hardware, execute `python tools/linux_smoke.py --iso /path/to/Windows.iso --data-dir /safe/test/location`. Inspect logs and verify KVM from QMP. Finish installing Windows in the GTK display and independently test cold reopen/reconnect, ACPI shutdown/restart, keyboard/mouse, display/fullscreen, outbound network, audio, installer detachment, VirtIO drivers, snapshots and recovery. Preserve the test VM; the script does not delete it.
6. Test archive export/import with a real stopped Windows VM, including TPM/NVRAM, disk integrity and backup restore. Keep BitLocker recovery keys outside the VM. Test process/reboot failure paths before broad use.
7. On each GPU under test, record model, kernel, Mesa/Vulkan driver, QEMU/Flatpak/virgl revisions and exact driver archive hash. Install the guest driver only after external backup and checkpoint; manually manage guest Secure Boot/test-signing. Import real guest reports. Test Windows rendering and the upstream graphics sample workload. Distinguish driver discovery from a completed draw and from stable Direct3D/CAD behavior.
8. Follow [docs/cad-testing.md](docs/cad-testing.md) for comparative native/QEMU/Yttrium measurements and SOLIDWORKS procedure. Do not claim SOLIDWORKS certification, compatibility, or performance until tested. Record unavailable licensing separately from failures.
9. Update [docs/validation.md](docs/validation.md) with commands, host models, exact software versions, results and known issues. This file is linked by README but **does not exist yet**; create it as the evidence ledger before claiming a release. Update README's project state at the same time.
10. Only when Linux installation, Windows lifecycle/recovery, packaging, and reproducible host/GPU evidence are satisfactory should a separate macOS implementation be considered. Mac needs its own architecture, hardware validation, Windows guest choices and graphics story.

## Current known gaps / risk points

- The primary completion blocker is missing Linux hardware execution evidence. A passing unit suite cannot turn this into a usable Windows VM release.
- Windows installation depends on guest setup choices (notably selecting the hard disk after the installer's first reboot). Confirm the workflow against actual Windows 11 installation media and update the wizard/documentation if users need more guidance.
- QEMU display lifecycle and user window ownership are distinct from the main Qt window. Check what happens if GTK fails to open, a window closes, QEMU crashes, or the application closes during a guest shutdown.
- Cold snapshots copy large VM disks and may need substantial host disk space. Check space estimates, interruption recovery, directory durability and copy/reflink behavior on Linux filesystems.
- `resources/yttrium-stack.json` records researched current upstream assets, not a tested or reproducible build of all source inputs. Verify all checksums on Linux and track the upstream source-manifest caveat.
- QEMU `pipewire`/PulseAudio backend availability varies by distro and session. Verify real audio output and gracefully handle no audio backend.
- Review every platform/version statement in host detection and packaging against the actual install target. Support for Wayland/X11 is not proven by Windows offscreen UI tests.
- The project is uncommitted. Establish a baseline commit only after reviewing the worktree and confirming the desired project repository identity; no remote or release has been configured.

## Useful files

- Frontend: `src/virtue/ui/main.py`, `dialogs.py`, `style.py`, `tasks.py`
- Domain/persistence/services: `src/virtue/domain.py`, `store.py`, `services.py`, `backup.py`, `storage.py`
- Linux engine and QMP: `src/virtue/backends/linux.py`, `command.py`, `qmp.py`
- Host and GPU evidence: `src/virtue/host.py`, `graphics.py`, `guest/`, `resources/yttrium-stack.json`
- Tests: `tests/`
- Build/smoke tools: `tools/build_deb.py`, `tools/linux_smoke.py`
- Guides: `docs/build.md`, `docs/yttrium.md`, `docs/cad-testing.md`
