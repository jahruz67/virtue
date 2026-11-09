# Validation evidence and acceptance checks

**Updated:** 8 November 2026. **Release status:** engineering preview; Linux
installation, Windows guest operation, and GPU/CAD compatibility remain unverified.

## Evidence available

The prior [handoff](../STATUS.md) reports Windows 11 x64 development without WSL,
a Linux/KVM host, Windows installation media, or a SOLIDWORKS installation/license.
Those environment details and historical runs have not been independently
rechecked. Exact Python, Windows, and installed dependency versions were not
recorded with those results.

| Area | Recorded evidence | Limit |
|---|---|---|
| Windows unit suite | Handoff reports 71 passing and 2 failing tests, followed by changes to snapshot/backup file handles | No final rerun or raw report is recorded |
| Qt desktop | Handoff reports 9 offscreen UI tests and a Windows unsupported-host screen | No Linux graphical-session validation |
| Host and graphics classification | Handoff reports 14 unit tests passed | Does not prove a running Windows guest or GPU rendering |
| Guest scripts | Handoff reports PowerShell parsing succeeded; execution policy blocked the collector | No guest graphics report or completed draw |
| Diagnostics | Handoff reports Windows was correctly rejected for VM execution | No current Linux diagnostic report |
| Packaging | Handoff reports wheel downloads and an application wheel build; DEB completion was unconfirmed | No native package installation or launch result |
| Windows lifecycle, recovery, I/O | No hardware result recorded | Pending supported Linux hardware and Windows media |
| Yttrium and SOLIDWORKS | No hardware or application result recorded | Pending GPU qualification and licensed application testing |

On 8 November 2026, static inspection started from clean baseline `e99b555`
(`Add comprehensive test suite for Virtue application`) in the Windows checkout.
No `dist/` directory was present. No tests, diagnostics, builds, dependency
installation, CI runs, or application launches were performed in this continuation.

Static inspection confirmed export calls `prepare_offline`, snapshot restore
includes VM configuration and private firmware, import recreates an empty TPM
directory, and the snapshot/backup sync handles use read/write mode. This is code
inspection, not execution evidence. The QMP fixture was changed to use the
production Unix-socket transport on Linux while retaining injected TCP loopback
elsewhere. The existing snapshot test now also checks firmware/config restoration
and preservation of the pre-restore configuration. Both changes await execution.

## Windows development checks pending

When explicitly authorized, run from the project root with the existing test
dependencies installed. Record commands, versions, exit codes, and full output.
The commands in this document have not been run in this continuation.

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m virtue --diagnose
.\.venv\Scripts\python.exe -m build
```

Record test and build outcomes separately. A passing development suite does not
meet the Linux acceptance criteria below.

## Linux acceptance checks pending

Use actual Linux x86-64 hardware with KVM and a graphical session. Record the
source revision, distribution, kernel, CPU/GPU models, RAM, Python/Qt versions,
QEMU, OVMF and swtpm package versions, and Wayland/X11 session type. Follow
[build notes](build.md) for dependencies. All checks below remain **not run**.

1. Run the suite and diagnostics using the Linux virtual environment:

   ```sh
   QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
   .venv/bin/python -m virtue --diagnose
   ```

   Preserve the Unix-socket QMP results. Diagnostics must report KVM availability;
   an offscreen test pass alone does not verify display or guest operation.

2. Build the wheel and DEB natively, then inspect the package:

   ```sh
   .venv/bin/python -m build
   .venv/bin/python tools/build_deb.py
   dpkg-deb --info dist/virtue-desktop_0.1.1_amd64.deb
   dpkg-deb --contents dist/virtue-desktop_0.1.1_amd64.deb
   (cd dist && sha256sum --check ./*.deb.sha256)
   ```

   On clean Ubuntu 24.04 and Debian 12 test systems with GUI libraries, install
   the DEB and launch `virtue` as an ordinary user. Record dependency resolution,
   diagnostics, Qt plugin loading, desktop entry behavior, and bundled notices.
   Review [third-party obligations](../THIRD_PARTY.md). A package build or CI
   artifact alone does not establish installation/runtime compatibility.

3. Run the opt-in hardware smoke check with user-supplied Windows media and an
   isolated data directory. Replace the example paths before running:

   ```sh
   .venv/bin/python tools/linux_smoke.py --iso /path/to/Windows.iso \
     --data-dir /path/to/isolated/virtue-test \
     --output /path/to/linux-smoke-result.json
   ```

   Save the JSON and VM logs. Require QMP to report a running guest with KVM and
   successful manager reconnection. The script retains its VM and does not
   complete Windows installation or verify guest I/O automatically. Finish the
   installation in QEMU's GTK display before recording those results.

4. Record each manual acceptance result separately:

   | Check | Evidence required |
   |---|---|
   | Install and reopen | Installed Windows desktop, installer detachment, cold boot, manager close/reopen while Windows runs |
   | Lifecycle and failures | Guest-agent restart, ACPI shutdown completion, force stop on disposable data, QEMU/GTK failure behavior |
   | Guest I/O | Keyboard/mouse capture/release, resolution/fullscreen, outbound network, audio, optional VirtIO drivers |
   | Recovery | Disk + config + firmware + TPM snapshot/restore, disk expansion, interruption recovery, recoverable removal |
   | Backup | Offline export/import of a real stopped Windows VM, integrity checks, boot of restored guest, empty-TPM import case |

   Preserve the test VM and keep BitLocker recovery keys outside it. A shutdown
   request is not evidence of completed shutdown. Use disposable guest data for
   failure-path checks.

5. Only after safe-display operation and recovery work, follow
   [Yttrium setup](yttrium.md) and [CAD testing](cad-testing.md). Record GPU PCI ID,
   kernel/Mesa/Vulkan versions, exact host/guest bundle revisions and hashes,
   security consent/checkpoint, guest report, and visible draw/workload evidence.
   Test integrated GPUs independently. Separate driver detection, successful
   rendering, stability, and CAD results; leave unavailable measurements empty.
   Record missing SOLIDWORKS media/license as unavailable rather than a failure.

## Recording a new result

Append a dated record with source revision (including local changes), host and
guest versions, exact command or manual procedure, expected and observed outcome,
exit code/test count where applicable, artifact paths or links, and unresolved
issues. Keep historical results distinct from current runs. Update the README's
release status only when linked evidence supports the new claim. Linux acceptance
remains the priority before macOS implementation.
