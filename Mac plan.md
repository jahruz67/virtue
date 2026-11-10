# Mac plan — Virtue macOS feature parity

Prepared: 9 November 2026. Scope: an implementation plan, not a macOS implementation or a verified compatibility claim.

## 1. Product contract

Ship the same Virtue experience on macOS: the same screens, terminology, controls, automatic Windows setup, Windows applications, daily interaction, recovery options, and data protection. A person moving from the Linux app to a MacBook should not have to learn a second product or work around missing features.

- Apple Silicon MacBooks are a mandatory target. An Intel-only port does not satisfy this plan. Qualify Intel Macs separately before advertising support for them.
- Keep Python/PySide6, the existing application, and its service boundary. Do not create a separate Swift frontend or redesign the Mac edition.
- Platform-specific implementation belongs behind shared interfaces. It must not create a reduced Mac feature set.
- Preserve existing Windows installations and `.virtue` backups wherever their execution profile is supported. Do not silently replace x64 Windows with Windows on ARM, reinstall Windows, convert a VM, or alter its drivers.
- Do not introduce a terminal, Homebrew, manual driver installation, an extra host, a paid external hypervisor, or a new account into the normal user workflow.
- Safe graphics remains a recovery option. It cannot substitute for the required accelerated experience.
- No feature may disappear or become permanently disabled merely because the host is a Mac. A capability check can prevent an unsafe operation, but an unsupported required capability blocks release for that host profile.

Literal identity of two operating systems cannot be guaranteed: macOS owns its installation/security prompts, window management, and some shortcuts. These constraints must be documented; they do not authorize differences in app-controlled behavior. Exact hardware performance also needs measurement, not a promise based on matching UI.

**Completion means demonstrated parity on qualified Mac hardware. An app that opens on macOS, or a Windows VM that merely boots, is not completion.**

## 2. Repository baseline and actual blockers

This plan follows the current working-tree source and README. `STATUS.md` contains older descriptions of manual/optional Yttrium setup; current creation forces Yttrium and automatic provisioning. Preserve existing local changes when implementing this plan.

| Current source | Integration work required |
| --- | --- |
| `services.py` constructs `LinuxBackend`; `prepare_host()` skips other systems | Introduce backend/host-setup selection without changing public service actions. |
| `host.py` requires Linux x86-64, KVM, Linux tools, Flatpak and Linux GPU prerequisites | Add actual macOS readiness checks and architecture-specific runtime profiles. |
| `backends/linux.py` uses `/proc`, `SO_PEERCRED`, `/run/user`, Linux-only operation locks and KVM queries | Separate process ownership, sockets, locking, runtime paths and accelerator verification from shared QEMU lifecycle logic. |
| `backends/command.py` requires KVM, Q35, GTK, Linux audio and memfd/Venus graphics | Add validated platform profiles; allowing `hvf` in a string check is insufficient. |
| `domain.py` accepts only Windows 10/11 x64, Q35, and `safe`/`yttrium`; new VMs force Yttrium | Add explicit, versioned execution profiles without reinterpreting existing settings. |
| `provisioning.py` selects WIM architecture `9`; unattended XML and guest drivers use `amd64` | Keep the x64 flow intact. Any ARM flow needs its own verified media, firmware, drivers, answer file and repair path. |
| `setup.py` installs a Linux Flatpak stack; runtime lookup includes `/opt/virtue/share` | Bundle or securely provision Mac-native dependencies and resolve app-bundle resources. |
| `store.py` and `runtime.py` use XDG defaults; service folder opening uses `xdg-open` on Linux | Introduce Mac paths and Finder integration while preserving explicit data roots. |
| `ui/style.py` uses fonts that may resolve differently; guest display is a separate QEMU GTK window | Qualify both the Qt control center and the Windows display, including MacBook input and Retina scaling. |
| `.virtue` archives carry disk, firmware, TPM, configuration and credentials | Preserve complete state and verify destination compatibility before launch. Existing exports do not include local checkpoint history. |
| Packaging and workflows target DEB/RPM | Add a signed, notarized Mac application and architecture-specific qualification. |

`docs/validation.md` records no validated Linux Windows-VM/GPU/CAD release. Establish a working Linux reference before declaring anything equivalent to it. Static inspection today provides no new Linux or macOS runtime evidence.

## 3. Resolve architecture and graphics before committing to a shipping backend

The first implementation milestone is a feasibility decision backed by evidence. The complete Apple Silicon execution/graphics route is not established by this repository. Do not conceal that uncertainty behind a feature checklist.

| Candidate route | Required proof | Parity consequence |
| --- | --- | --- |
| Intel Mac, native macOS QEMU with HVF, existing x64 Windows/Q35 profile | Boot and install Windows; verify the accelerator, CPU features, firmware, TPM, device support and graphics. | Candidate for retaining the existing guest architecture. Not evidence of Apple Silicon support. |
| Apple Silicon, native QEMU/HVF with an ARM Windows profile | Verify same-architecture virtualization, ARM firmware/TPM, Windows provisioning, every driver and each required Windows workload. | A different guest architecture. It does not by itself preserve x64 ISO support or make existing x64 VMs transferable. |
| Apple Silicon, local full-system x64 emulation | Run an existing Linux-created x64 environment, including accelerated graphics and the agreed interactive/CAD performance workload. | Candidate for preserving x64 guest identity, but CPU performance/acceleration remains a blocking proof obligation. Never select emulation silently. |

Do not assume that HVF accelerates an x64 guest on Apple Silicon, that host Rosetta converts a Windows VM, or that Windows application emulation supplies ARM kernel drivers. Verify architecture boundaries explicitly against the selected runtime and hardware.

For graphics, investigate a macOS port of the required Yttrium stack first. The current command uses Linux memfd sharing, a Flatpak renderer and Linux Vulkan prerequisites. A Mac Vulkan-to-Metal library alone is not proof that this memory-sharing/device path works. If a different renderer is necessary, prove equivalent Windows APIs, workloads, performance, recovery and automatic installation before adopting it. Do not relabel another stack as Yttrium.

**Feasibility exit gate:** choose and pin one proven execution/display/graphics profile per supported host architecture. Demonstrate Windows setup, a real hardware-rendered guest draw, required Windows applications, working audio/input, a cold checkpoint restore and a transferred x64 backup where claimed. If Apple Silicon cannot meet the contract, record the precise blocker and obtain an explicit product-scope decision. A Windows-on-ARM-only, software-rendered, remote, or Intel-only alternative is not automatic acceptance of reduced parity.

## 4. Shared implementation design

### Backend and host boundary

1. Expand `VirtualizationBackend` in `src/virtue/backends/backend.py` to describe what `VMService` actually uses: lifecycle, `is_active`, `prepare_offline`, checkpoints, disk expansion, removal, setup status/diagnostics and retry. Preserve existing method behavior and error semantics.
2. Add `src/virtue/backends/factory.py` to select Linux or macOS by host platform and validated profile. Keep constructor injection for existing tests. Unsupported development hosts retain truthful read-only behavior.
3. Extract shared QEMU operations into `src/virtue/backends/qemu.py` as needed. Keep `LinuxBackend` and add `MacOSBackend` as small platform adapters. Avoid copying the roughly 900-line Linux implementation or rewriting it wholesale.
4. Add narrow platform helpers for runtime paths, process ownership and host resource probing. Leave QMP and guest-agent transports shared where their actual behavior is portable.
5. Extend host reports with explicit capabilities: supported guest profiles, accelerator, display/audio support, graphics prerequisites and runtime versions. Preserve existing report fields during migration. `ready` must mean every required capability passed; graphics prerequisites and observed guest rendering remain separate evidence.

### Configuration and backwards compatibility

1. Add a versioned execution profile containing guest architecture, machine/device profile and graphics implementation. Keep host-specific executable paths and temporary sockets out of portable VM configuration.
2. Implement an explicit schema migration. A schema-1 VM means the existing x64/Q35 profile, never “whatever this Mac supports.” Reject unknown versions and unsupported profile combinations before writing or starting anything.
3. Preserve existing x64 filenames, private firmware, TPM state, graphics settings, credentials and checkpoint contents. Migrate configuration in staging with a recoverable original; never rewrite the library just to enumerate it.
4. Load schema-1 configurations inside old checkpoints and archives as well as the active VM. Restore the configuration and execution profile together with disk/firmware/TPM.
5. Keep the existing archive format when its contents suffice. Version it only if new state files require it; readers must continue importing current archives. Older readers must fail clearly on unsupported new configuration rather than misinterpret it.
6. Compatibility checks must distinguish importing an intact archive from being able to run its guest. An architecture mismatch must not become silent conversion or an apparently successful launch.

### Paths, runtime ownership and safety

- Default Mac VM storage: `~/Library/Application Support/Virtue/vms`; cache: `~/Library/Caches/Virtue`. Keep `--data-dir` authoritative and preserve Linux XDG behavior.
- Resolve read-only resources from `Virtue.app/Contents/Resources`; place writable runtime downloads outside the signed bundle. Use a short, private per-user runtime directory for Unix sockets and respect socket path limits.
- Implement macOS process identity using native process/peer APIs, verifying user, process start identity, executable and QMP VM UUID. Do not replace `/proc` checks with PID existence or parse untrusted process text as sufficient ownership proof.
- Preserve per-library and per-VM cross-process locks on macOS. Treat an unresponsive but live VM as active; it must not become eligible for snapshot, import overwrite, restore, removal or disk changes.
- Keep bounded startup/shutdown timeouts and cleanup limited to processes created or independently verified as owned. Test PID reuse, stale metadata, a replaced socket and two managers racing for one VM.
- Qualify atomic writes, directory synchronization, sparse disks, available-space checks and backup publication on APFS. Do not assume Linux `cp --reflink` or hard-link publication works on every user-selected volume. Unsupported filesystems must fail before state changes, with an actionable message.
- Closing Virtue leaves Windows running, just as on Linux. Relaunch reconnects to that exact VM. Host restart ends the session; stale metadata must not imply a VM survived reboot.

## 5. Feature implementation and acceptance matrix

Every row must pass on the Linux reference and each advertised Mac profile. Existing Linux restrictions, such as no pause with active Yttrium, must behave consistently.

| Workflow | Concrete implementation and required result |
| --- | --- |
| First launch and diagnostics | Select the Mac runtime automatically. Inspect actual capabilities; show actionable failures. Normal installation needs no terminal, package manager or Linux administrator helper. |
| Create Windows | Preserve the wizard, presets, resource reservations, disk allocation, ISO selection, progress and automatic restarts. Inspect guest architecture before allocating a VM. Invalid media leaves recoverable state. |
| Automatic guest setup | Resolve bundled media tools instead of depending on shell `PATH`. Preserve generated credentials, automatic sign-in, installer detachment, drivers, graphics verification and setup progress. Do not mark ready on boot or driver enumeration alone. |
| Repair and retry | Preserve both unfinished-install retry and installed-guest repair, including idempotent phases, updated repair media, guest-agent/serial reporting, logs and no accidental reinstall. |
| Start, restart and shutdown | Prove normal boot, safe shutdown waiting for Windows, guest-agent-assisted restart and separately confirmed force stop. Preserve state after GUI closure/crash and reconnect. |
| Windows display | Preserve the separate desktop window, fit-to-window scaling, fullscreen, controls visibility, capture/release and safe graphics. Qualify renderer and display transport together; an attractive preview does not demonstrate acceleration. |
| Keyboard and pointer | Retain documented Ctrl+Alt+M/F/G actions where deliverable; provide visible menu equivalents. Check Control/Option/Command mapping, Delete, function keys, Windows key, Ctrl+Alt+Delete delivery, key repeat, focus loss and stuck-key prevention. Do not steal ordinary Windows shortcuts. |
| MacBook trackpad and Retina | Verify click, secondary click, drag, double click, scrolling, pointer alignment and capture escape. Test 1x/2x scaling, changing display scale, and moving the window between internal/external displays. Preserve coordinates and guest resolution without blur or clipping. |
| Audio | Use a qualified Mac audio backend, normally CoreAudio if supported by the pinned runtime. Verify output, guest microphone input, device changes, Bluetooth/headphones and reconnect after sleep. Handle required macOS privacy prompts only when the feature is used. |
| Networking | Preserve outbound user networking and no unsolicited host ports. Test DNS, HTTPS, downloads, Wi-Fi transitions and sleep/wake. Do not add bridged-network privileges as a default. |
| Resource settings | Keep the existing options and validation; account for other active VMs and reserve host resources. Do not change virtual storage/network devices before the guest has compatible drivers. |
| Checkpoints and restore | Require a fully stopped guest. Capture/restore disk, firmware, TPM, configuration and profile together; preserve the pre-restore recovery checkpoint and interrupted-restore protections. |
| Disk expansion | Preserve offline expansion and existing guest partition expectations. Verify no shrink, overflow, backing-image or live-disk modification. |
| Export/import | Round-trip real stopped VMs in both directions for supported profiles. Verify hashes, path safety, fresh IDs, complete firmware/TPM state, credentials and standalone disks. Confirm BitLocker recovery behavior; never promise a transfer avoids reactivation. |
| Removal and folders | Preserve recoverable removal and confirmations; open the exact VM folder in Finder through an argument-safe platform adapter or Qt local-file URL. |
| Logs and GPU reports | Preserve setup/runtime diagnostics, report import limits, honest renderer classification and clear separation of software rendering, enumeration, completed draws and application qualification. |
| Qt interface | Match navigation, action order, enabled states, confirmation text, progress, icons, spacing and app-controlled colors. Qualify long labels, modal dialogs, tab order, screen-reader names and keyboard access. |

Clipboard sharing, shared folders, seamless individual Windows apps and multi-monitor guest integration are not implemented in the current Linux preview. They are not prerequisites to port that preview, and must not be advertised as present. An external-monitor scaling test is not multi-monitor guest integration. If these features enter the Linux reference before release, add them to the same parity gate.

## 6. macOS runtime, graphics and display work

### Reproducible runtime

- Add a pinned `resources/macos-runtime.json` after feasibility selects actual components. Record host architecture, minimum macOS version, guest profiles, complete source revisions, build inputs, hashes, firmware, licenses and required entitlements. Do not invent download URLs or reuse Linux asset hashes.
- Bundle Python, Qt plugins, QEMU, `qemu-img`, TPM support, firmware and the ISO/WIM/ISO-writing tools needed by `provisioning.py`, with their shared libraries. Resolve all tools by trusted absolute path.
- Keep runtime assets architecture-specific even if the outer app is universal. Test both slices independently; a universal Qt executable does not make its QEMU/renderer helpers universal.
- Generalize `setup.py` asset selection and repair around the selected profile. Preserve checksum validation, safe staging, interrupted-download recovery and private cached guest assets. Never execute Linux `pkexec`, Flatpak or distro package commands on a Mac.
- Preserve user-provided Windows media and licensing. Do not distribute Windows or application licenses with the app.

### QEMU, firmware and guest provisioning

- Build separate validated launch profiles for macOS instead of appending exceptions to the Linux command. Explicitly select accelerator, CPU, machine, firmware, TPM, disk/network/input devices, display, audio and guest integration channels.
- Probe the selected QEMU build for supported options. Existing SMM, Hyper-V flags, Q35 devices and TPM wiring are proof obligations on a Mac, not portable assumptions.
- Verify the intended accelerator at runtime using evidence supported by that runtime; `query-kvm` cannot stand in for Mac accelerator verification. Record the actual execution mode in diagnostics and reject unexpected emulation.
- If an ARM profile is accepted after the architecture gate, implement its ARM media inspection, unattended XML, guest-agent package, storage/network/input/graphics drivers, firmware, TPM and serial/setup channel as one complete profile. Do not mutate `amd64` strings alone or assume an x64 graphics driver can be reused.

### Graphics and window integration

- First prove the selected Windows renderer with a completed draw/readback and the required API workload. Then prove presentation through the selected window transport at Retina scale. Check software-renderer fallbacks explicitly.
- Evaluate the existing QEMU GTK frontend for a distributable Mac build and matching controls. Evaluate the native QEMU Mac frontend during feasibility if necessary. Adopt neither solely because it launches.
- If neither frontend meets the contract, implement a shared Qt guest viewer for both hosts using a proven local display transport that retains the qualified graphics path. Close that transport/acceleration decision before viewer implementation; keep sockets private and expose no unauthenticated network display.
- Use the current shared Qt style and icons. Freeze a redistributable common font and, if necessary, an explicit Qt widget style after a Linux comparison; verify that this does not redesign the reference UI. Do not create a Mac-only theme.
- Keep renderer identities truthful in diagnostics. A new renderer needs profile-aware guest evidence; do not weaken the existing Venus/Zink checks into “some GPU name appeared.” Verify OpenGL, Vulkan and Direct3D separately where claimed, then run actual CAD workflows.

## 7. MacBook lifecycle qualification

Define Mac lifecycle behavior before release and preserve the existing promise that closing the manager leaves Windows running.

- Cover lid close/open, idle sleep, manual sleep, lock/unlock, battery operation, charger changes and low disk space. Never silently turn a host event into guest force stop or leave keys captured after focus returns.
- Use native power notifications only where needed. Choose and document a safe sleep policy for the selected engine; verify guest clock, network, audio, rendering and QMP recovery afterward. Do not leave permanent sleep inhibition enabled.
- Check red-window-close, application Quit, Dock relaunch and multiple manager windows/processes. Do not assume macOS application/window lifetime matches Linux automatically.
- Preserve running VM runtime/firmware compatibility during application replacement. Do not remove an old runtime while a VM uses it; refuse unsafe updates until guests are stopped. Never terminate Windows as an update side effect.

## 8. Ordered implementation batches and exit gates

Each batch is a focused, reviewable change. Add only the supporting files actually needed by the proven design.

| Batch | Main files | Done when |
| --- | --- | --- |
| P0 — Freeze reference | `docs/validation.md`, existing Linux acceptance procedures | Working Linux installation, automatic Windows setup, lifecycle, recovery and GPU/CAD evidence are recorded; every visible action has an expected result. Record exact source/runtime revisions and qualification hardware. |
| P1 — Prove Mac feasibility | Isolated Mac probes/runtime recipe; new `docs/macos-validation.md` | Architecture/graphics/display decision in section 3 is closed for Apple Silicon; Intel has its own result. Unsupported or failed routes are explicit. No product-parity claim yet. |
| P2 — Add shared boundaries | `backends/backend.py`, new `backends/factory.py`, `services.py`, `host.py`, targeted helpers | Linux keeps existing behavior; Mac selects truthful host diagnostics and an explicit profile. No fake readiness or empty-success setup. |
| P3 — Version portable state | `domain.py`, `store.py`, `runtime.py`, `backup.py`, snapshot helpers | Existing configs, checkpoints and archives remain readable; data changes are recoverable; incompatible profiles cannot launch. Mac storage and private runtime paths work. |
| P4 — Implement Mac execution | New `backends/macos.py`, shared QEMU operations, command profiles, runtime manifest | Real creation/boot/reconnect/shutdown/restart/force stop, ownership verification, locks, offline checks and cold recovery pass on Mac hardware. |
| P5 — Complete automatic setup | `setup.py`, `provisioning.py`, `resources/`, `guest/`, `graphics.py` | A fresh user-selected ISO reaches a verified usable Windows desktop without terminal/driver work; retry and installed-guest repair pass. Required graphics is active. |
| P6 — Close interaction parity | `backends/display.py` or proven viewer, `ui/main.py`, `dialogs.py`, `style.py`, platform audio/input integration | Every matrix row passes, including trackpad, Retina, keys, audio, workload rendering and MacBook lifecycle. |
| P7 — Package and qualify | New `packaging/macos/`, `tools/build_macos.py`, Mac workflow; `pyproject.toml`, `THIRD_PARTY.md` | A signed/notarized app installs and works on clean qualified Macs with no developer dependencies. Linux packaging and behavior retain their acceptance results. |
| P8 — Release decision | Validation ledgers, README and build/support documentation | All required gates pass, support matrix and known limitations match evidence, and release authorization is obtained. |

P0 and P1 may gather independent evidence, but do not begin a full Mac product implementation before the Linux reference and Mac feasibility gates are satisfied. Refactor only what the chosen design requires.

## 9. Validation to perform when execution is authorized

This request authorizes writing this plan only. It does not authorize running tests, builds, installers, probes, applications, dependency installation, benchmarks or CI. Request the relevant execution permission when implementation reaches those operations; do not treat this checklist as permission to execute everything.

### Meaningful automated coverage

- Extend existing domain/store/backup tests for schema migration, legacy checkpoints, old archives, incompatible architectures, corrupt profiles and interruption recovery.
- Extend command/host tests for explicit accelerator and device selection; Linux-only flags must never leak into Mac launch arguments. Mocked detection is not hardware proof.
- Run backend ownership/recovery tests against platform adapters, including live-but-unresponsive QMP, PID reuse, stale sockets, failed TPM startup, concurrent operations and offline disk locking.
- Preserve real Unix-socket QMP coverage on Linux/macOS; injected transports elsewhere do not replace it.
- Parameterize existing service/UI behavior tests over qualified profiles. Compare action availability, workflow states, errors and confirmations, rather than merely asserting platform-specific implementation details.
- Add Mac hardware smoke tooling only after the execution route is proven. It must use a separate test library and preserve test VMs/data by default.

### Real hardware and experience evidence

1. Linux reference: supported x86-64 host, exact driver/runtime versions, actual Windows installation and the existing acceptance procedures in `docs/validation.md` and `docs/cad-testing.md`.
2. Mac primary: an Apple Silicon MacBook with Retina display and trackpad; qualify the minimum supported hardware/memory configuration and a second representative performance configuration. Qualify an Intel MacBook separately if supported.
3. Select the minimum macOS release from the pinned Python/Qt/QEMU/renderer dependency requirements, then test that release and the current shipping release on advertised architectures. Record exact versions; do not make an unverified “all Macs” claim.
4. Exercise the same guest resources, Windows builds, applications, files and scripted interactions. Include both Windows 10 and 11 x64 if claiming the current guest matrix; an ARM-only Windows 11 result does not cover either x64 claim.
5. Record clean install, interrupted setup, GUI close/reopen, guest restarts, host reboot, sleep/wake, checkpoint restore, import/export round trips and disk expansion with real guest files. Verify file contents after recovery.
6. Compare screenshots and interaction recordings for every shared screen. Review at matched logical size and multiple scale factors. Accept only OS-owned rendering differences and rasterization noise; missing controls, changed workflows and clipped content fail.
7. Establish numeric budgets before measuring the Mac candidate: input-to-frame latency, frame pacing during desktop scrolling/window movement/CAD rotation, cold/warm boot, app launch, file open/save, representative CAD operations, memory and battery/thermal behavior. Use matched guest allocations and disclose host hardware differences. Record median/tail results and repeatability; do not tune budgets afterward to excuse a failed route.
8. Require real accelerated draw evidence plus the actual target Windows/CAD applications, including open/edit/save/reopen and sustained viewport use. A renderer string or a triangle is insufficient to establish application parity. State application versions and license availability.
9. Store redacted evidence in `docs/macos-validation.md`: source revision, host model/chip, OS, guest architecture/build, runtime/firmware/driver hashes, execution mode, workload, results and unresolved failures. Never commit VM disks, credentials or TPM secrets.

## 10. Distribution and release gates

- Produce `Virtue.app` in a DMG with the same product name/version and icon family. Add file association for `.virtue` only with safe import dispatch. Finder/Dock launch must work without shell environment setup.
- Use a reproducible Mac build recipe; inventory and license all bundled Python/Qt/QEMU/firmware/TPM/media/renderer components, including required notices and source-distribution obligations.
- Sign nested executables/frameworks and the app with the appropriate Developer ID. Determine the minimum hypervisor/JIT/audio entitlements from the selected engine and verify the signed helpers actually launch. Enable the required hardened-runtime configuration, notarize and staple the release artifact.
- Validate the downloaded, quarantined DMG on a clean standard-user Mac. Never require disabling Gatekeeper, SIP or host security to install or run Virtue.
- Keep signing/notarization credentials in the release secret store. Build workflows must not publish automatically before the parity gates and explicit release authorization; adding Mac support must not accidentally trigger publication of unfinished artifacts.
- Test app replacement, data preservation and removal. Uninstalling the manager must not delete user VMs. Existing stopped VM data must survive an app upgrade and a failed upgrade.
- Update README, `docs/build.md`, `docs/validation.md`, third-party notices and project classifiers only when corresponding support is demonstrated. Keep engineering-preview status until the relevant release gates pass.

## 11. Definition of done

- [ ] A working Linux reference exists and is recorded.
- [ ] Apple Silicon MacBooks meet the full product contract through a proven local execution and graphics route.
- [ ] Every advertised host/guest/OS profile passes the complete parity matrix; Intel support is independently qualified if claimed.
- [ ] Existing supported x64 VM state and backups transfer without silent conversion, state loss or weakened recovery guarantees.
- [ ] Automatic setup and both repair paths work without manual dependency or driver installation.
- [ ] Windows rendering and target applications meet declared functional and performance criteria on actual Mac hardware.
- [ ] Input, trackpad, Retina scaling, audio, networking and MacBook lifecycle are qualified.
- [ ] Checkpoints, restore, expansion, removal and archive workflows preserve their existing safety guarantees.
- [ ] Linux behavior and packaging retain their acceptance results after shared changes.
- [ ] A clean Mac installs and runs the signed/notarized package without developer tooling or security bypasses.
- [ ] Evidence supports every published compatibility statement; unresolved required capabilities block a parity release.

Until these boxes are supported by evidence, describe the work as a macOS port in progress, not a completed equivalent Mac version.
