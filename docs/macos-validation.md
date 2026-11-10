# macOS implementation status

Updated: 10 November 2026. Status: port in progress; no Mac execution or
accelerated SOLIDWORKS result is claimed.

## Product decisions

- The owner reports that the current Linux application runs SOLIDWORKS successfully.
  Preserve that implementation; rerunning Linux acceptance is not a prerequisite
  to this continuation. This is owner-reported experience, not a new measured result.
- The Mac edition must remain self-contained, with no paid external hypervisor.
  Parallels was explicitly declined as a dependency.
- Host and guest architecture, and renderer identity, are flexible. The reference
  workflow uses SOLIDWORKS' Software OpenGL setting while Windows retains its
  Yttrium DirectX driver. Hardware OpenGL 4.5 is not a prerequisite for this port.
  This does not assert that SOLIDWORKS' software rasterizer internally uses DirectX.
- Preserve existing x64 VM state and archives. No architecture conversion,
  reinstall, or guest-driver replacement has been implemented.
- Tests, builds, dependency installation, application launches, and hardware probes
  have not been run in this continuation.

## Implemented groundwork

The source now includes an Apple Silicon/HVF backend using native QEMU with
Triton/Neptune/DXMT, Windows ARM media inspection and unattended setup, guest
driver/agent repair, platform-specific graphics diagnostics, Mac storage/cache
paths and Finder integration. Common lifecycle, display matching, checkpoints,
firmware/TPM state, archives and disk operations share the QEMU backend. Schema-1
x64 settings retain their meaning and serialization; new ARM VMs use schema 2.
Importing an x64 archive does not make it runnable under native ARM virtualization.

The native runtime binaries are not present in this Windows checkout. No app
bundle, native runtime or SOLIDWORKS workload has been built or run here. The
source implementation is not a completed or qualified parity release. Cocoa
input/window behavior, CoreAudio microphone support and MacBook sleep/wake still
need platform work or qualification; their Linux behavior has not been replaced.

## Graphics findings

| Candidate | Upstream evidence | Remaining gap for Virtue |
| --- | --- | --- |
| Existing Yttrium/Venus/Zink | Windows OpenGL, Vulkan and Direct3D support; upstream qualification targets Windows 10 x64 on Linux with an Intel Arc A580 | No assembled native Mac runtime or Mac SOLIDWORKS result |
| UTM Triton/Neptune with DXMT | Experimental Windows DirectX rendering on Apple Silicon, through Metal | Selected implementation target; the owner's SOLIDWORKS Software OpenGL workflow remains unverified on this stack |
| Mac Venus/Vulkan host stack | UTM documents a macOS Venus port; Mesa documents KosmicKrisp on Apple Silicon with macOS 26+ | End-to-end Windows driver, Zink, shared-memory, display and SOLIDWORKS interoperability remain unresolved |
| Parallels | Vendor documents SOLIDWORKS on Apple Silicon and accelerated OpenGL | Paid external dependency; excluded by the owner's decision |

The existing app's `show_solidworks_graphics_help` and `fix_solidworks` explicitly
recommend disabling Enhanced graphics performance and enabling Use Software
OpenGL. That app setting does not switch the whole VM to its safe display device.
The Mac implementation preserves this distinction and does not make native
hardware OpenGL a gate. Upstream DirectX demonstrations establish a candidate
graphics route, not this application's SOLIDWORKS compatibility or performance.

References reviewed on 10 November 2026:

- [Yttrium's documented Windows graphics stack](https://github.com/arehnman/yttrium-virtio-gpu)
- [UTM Windows graphics capabilities and experimental-driver requirements](https://docs.getutm.app/guest-support/windows/)
- [Triton, Neptune and native Mac renderer integration](https://blog.getutm.app/2026/introducing-triton-directx-11-driver-for-qemu/)
- [Mesa KosmicKrisp platform requirements](https://docs.mesa3d.org/drivers/kosmickrisp.html)
- [SOLIDWORKS enhanced graphics pipeline](https://3dswym.3dexperience.3ds.com/wiki/solidworks-news-info/501-taking-full-advantage-of-the-enhanced-graphics-performance-in-solidworks_hXOwmXJkRF6Ju_CfdDFimQ)
- [Parallels OpenGL support](https://kb.parallels.com/en/115487/)
- [Parallels' SOLIDWORKS information](https://www.parallels.com/apps/solidworks/)

## Native app build

`tools/build_macos.py --runtime /absolute/path/to/runtime` creates an arm64
`dist/macos/Virtue.app` on an Apple Silicon build host. It requires the project's
Python/Qt dependencies and PyInstaller to be present beforehand; it does not
install dependencies or download components. The script relocates native library
references, signs nested binaries, seals the runtime's post-signing checksums, and
signs the app. `--sign-identity` selects a Developer ID; the default `-` produces
an ad-hoc engineering build. Optional `--notary-profile` submits and staples using
an existing keychain profile. Nothing publishes automatically or replaces an
existing app. End users need no terminal, Homebrew or external hypervisor.

The **Build Mac application** workflow runs on an Apple Silicon runner, either
manually or as part of **Build and release**. It installs the build tools, builds
the native runtime with `tools/build_macos_runtime.py`, and creates its manifest
and checksums automatically. `resources/macos-runtime.json` records the selected
source revisions and guest-media hashes. Native library dependencies are bundled
and relocated; source and license records accompany the runtime. The runtime and
source archive are cached for subsequent app builds. No prepared runtime or
manual workflow inputs are needed.

The workflow builds an ad-hoc-signed engineering app and uploads
`Virtue-macos-arm64.dmg`, the runtime source archive and their checksums. The disk
image contains `Virtue.app` and an Applications shortcut for drag-and-drop
installation. The calling release workflow waits for this build and the Linux job, then publishes
the packages together after a push to `main` or a `v*` tag. It does not launch
the Mac app or VM, notarize, or establish workload compatibility. This build
recipe has not been executed on a Mac here. It currently targets macOS 26+.

Supply a complete, dereferenced runtime staging tree with `manifest.json`:

- `schema_version`: 1; `host_architecture`: `arm64`;
  `execution_profile`: `windows-arm64-virt-triton-v1`.
- `minimum_macos`: the actual minimum supported by all staged components.
- `sources`: component repository, pinned revision and license records.
- `files`: every relative file path (except the manifest) mapped to its SHA256.
  Include the actual license notices and required source-distribution material.
- `paths`: inventoried paths for `qemu`, `qemu_img`, `swtpm`, `firmware_code`,
  `firmware_vars`, `extractor`, `wimlib`, `xorriso`, `guest_driver`, `virtio_iso`,
  `render_server`, `dxmt_library`.
- Optional `build_prefix`: the original absolute install prefix embedded in
  library references, if different from the supplied staging directory.

The engine must include UTM's ARM `virtio-ramfb-gl`/Neptune and Cocoa/ANGLE support,
the native DXMT renderer and render server, matching ARM UEFI/TPM support, all
native libraries, ISO tools, a complete Triton ARM graphics driver ZIP, and
matching ARM VirtIO media. The published `guest-agent/qemu-ga-x86_64.msi` runs
through Windows 11 ARM's x64 emulation, matching UTM's guest-tools installer;
the VirtIO kernel drivers are native ARM64. The graphics ZIP
must include its INF dependencies, ARM kernel driver, x64 application UMD support
and any test-signing certificates required by that build. No Linux driver ZIP,
Flatpak binary or invented download URL is substituted. The build's runtime
inventory is mandatory; missing components fail before VM creation.

The external build references above describe the upstream integration. The new
automated recipe still needs its first successful native CI build; no complete
runtime or distributable app has been produced in this Windows checkout.
