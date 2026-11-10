# Automatic Yttrium setup and recovery

New Windows environments require Yttrium. Choose your Windows ISO in the app;
Virtue downloads and verifies the host bundle, matching guest drivers, VirtIO
installation media and Vulkan runtime. The desktop's administrator prompt handles
system packages and KVM access when needed. No terminal is part of the user flow.

Windows installation uses a generated answer file. Guest drivers, certificates,
test signing and required restarts are automatic. Progress and retry controls
appear on the Windows card. Setup completes after the drivers and Vulkan runtime
are installed and Windows restarts. Graphics diagnostics are optional and do not
block setup completion. The original Windows ISO is unchanged.

The answer-file flow follows [Microsoft's Windows Setup automation reference](https://learn.microsoft.com/windows-hardware/manufacture/desktop/windows-setup-automation-overview?view=windows-11).

The complete unattended ISO-to-desktop flow requires target-host acceptance before
a release is called validated. The upstream driver remains experimental even
though installing it is now mandatory.

## Maintainer reference for the prior manual installation path

The commands and older setup procedures below are retained for driver maintenance
and recovery of existing guests. They are not steps users of automatic setup need
to perform, and the older graphics-toggle UI is no longer part of normal setup.

This application implements the Linux launch path and reports host prerequisites separately from imported guest evidence. No guest hardware acceleration or GPU compatibility has been validated on the Windows development machine. Do not treat a launched VM or a green host check as a graphics test.

## Researched combination

The recorded combination is the upstream **November 2, 2026 experimental guest driver** and **v2026-11-02 host Flatpak**. Exact release URLs, SHA256 digests and source observations are in [resources/yttrium-stack.json](../resources/yttrium-stack.json). Download those assets through the upstream release pages and verify their digests before installing. The released archive contains the Windows kernel and user mode components; do not mix it with unrelated Mesa DLLs.

On Linux, install the host bundle for the current user:

```sh
sha256sum org.qemu.yttrium.flatpak
# Expected: 51095dfc456428f3c77bfffc22db2e02b86d88a1d24c72d5ce438e73f0d27791
flatpak install --user ./org.qemu.yttrium.flatpak
flatpak run org.qemu.yttrium --version
```

The published manifest consumes local QEMU and virglrenderer directories rather than pinned Git inputs. Virtue records the published binary digest; the observed source HEAD hashes are **not** asserted to be the build inputs. This is a remaining upstream source reproducibility limitation. No upstream executables or guest drivers are redistributed with Virtue.

Upstream documents QEMU 11.1.0 and virglrenderer 1.3.0 in this bundle. Virtue uses the bundle for the experimental path, avoiding arbitrary distro component combinations. NVIDIA specifically requires the patched bundle for this release. GTK with OpenGL is selected; the upstream NVIDIA EGL headless path is unsupported. The host Flatpak has broad device, home, network and runtime-directory permissions; review these using `flatpak info --show-permissions org.qemu.yttrium` before installation.

## Host checks

The diagnostics check actual KVM creation permissions, QEMU tools, TPM, matching UEFI firmware, desktop backend, Flatpak GPU properties, and hardware Vulkan enumeration. The GPU needs Vulkan 1.1 and `VK_KHR_external_memory_fd` for the Linux Venus renderer. These properties alone do not prove shared memory interoperability. Software Vulkan devices such as lavapipe do not qualify.

Current Mesa documents additional constraints: RADV needs Linux 6.13 or later; Intel Meteor Lake/xe, and a discrete GPU paired with an Intel CPU, can require Linux 6.16 or later plus QEMU 11 and `honor-guest-pat=on`. NVIDIA proprietary drivers need 570.86 or later. The checker is conservative and reports detected candidates, not a certified GPU list. Hybrid laptops may enumerate multiple GPUs; verify that the intended hardware GPU appears in the guest report. Integrated Intel and AMD GPUs remain explicit test targets, with results pending real hardware testing.

## Guest installation

1. Install Windows normally using safe display mode. Obtain your own legitimate Windows media/license. Windows 11 is the default; the current upstream qualification target remains Windows 10 x64 with Intel Arc A580. Other combinations, including Windows 11 and integrated graphics, need validation.
2. Shut Windows down completely. Save the BitLocker recovery key outside the VM before firmware changes. In Snapshots, create a manual checkpoint named `before-guest-security-changes` **before** changing guest firmware or signing policy. Enabling Yttrium also creates an automatic checkpoint before configuring the device, but that later checkpoint can already contain your disabled Secure Boot setting. Both checkpoints contain disk, NVRAM and TPM state. Keep additional external backups for important data.
3. Boot to the guest UEFI setup using Esc during the firmware splash. Disable **guest** Secure Boot there, preserving the existing NVRAM/TPM. This weakens guest boot verification. Do not disable host Secure Boot. A configuration checkbox cannot establish the actual firmware state.
4. Read and acknowledge the experimental graphics consent in Virtue. The guest must use Windows test signing for this alpha; it accepts test-signed kernel drivers and reduces the guest's normal driver trust protections. Do not use this VM for sensitive or irreplaceable work.
5. In an administrator terminal **inside Windows**, enable test signing and disable hibernation/Fast Startup:

   ```bat
   bcdedit /set testsigning on
   powercfg /hibernate off
   ```

   An optional guarded helper is [Prepare-YttriumGuest.ps1](../guest/Prepare-YttriumGuest.ps1). It requires its explicit acknowledgement switch and confirmation. It refuses while guest Secure Boot is enabled. Virtue does not run it automatically.
6. Restart Windows. Verify the ZIP SHA256 from the manifest, extract it, inspect the included `VirtIOTestCert.cer`, and explicitly import that test certificate into **Trusted Root Certification Authorities** in the guest. Trusting that root broadens guest trust. Use only the intended upstream artifact.
7. In Device Manager update the display adapter using the extracted driver directory. Remove an old experimental driver package first when upgrading, following the upstream directions. Restart again. Install the Vulkan runtime/tooling if absent.
8. Use a complete guest shutdown or restart. Fast Startup, hibernation, save-state and experimental GPU pause/resume are not validated. Do not use them for this driver.

The application changes neither host security nor Windows signing policy. The consent records permission to configure the experimental virtual device, not proof that guest setup succeeded.

## Verify actual rendering

Copy [Collect-VirtueGpuReport.ps1](../guest/Collect-VirtueGpuReport.ps1) into the guest and run:

```powershell
powershell -NoProfile -File .\Collect-VirtueGpuReport.ps1 -OutputPath "$PWD\virtue-gpu-report.json"
```

The collector creates a real OpenGL context, draws a triangle, and verifies the pixel readback. It also records Vulkan device enumeration and Windows video driver details. Transfer the JSON to the host and import it in Virtue diagnostics. The file is user-provided evidence, not a signed attestation. The result applies to the captured configuration and timestamp, not every later launch. If scripts are blocked by guest execution policy, inspect the script and use your organization's approved way to run it; the application does not change that policy.

Expected Vulkan discovery contains `Virtio-GPU Venus` and the intended hardware GPU; the OpenGL renderer should identify Zink/Venus. A successful draw with that renderer is reported as hardware rendering observed. Enumeration alone remains unverified. GDI Generic, llvmpipe and lavapipe are software fallback. Driver presence without Venus/Zink is reported separately. This test does not establish Direct3D feature correctness, frame rate, or SOLIDWORKS support.

For a visible workload, upstream currently uses FurMark **2.9.0**, with `--vkinfo`, `--glinfo`, `--demo furmark-vk` and `--demo furmark-gl`. Obtain it from its publisher and record executable hashes/settings. Upstream notes a feature negotiation crash in 2.10.2 for this configuration. Run the same scene for a fixed duration and record crashes/visual errors as well as performance; do not convert successful enumeration into a benchmark result.

## Recovery

If the driver hangs or the display goes black, request a normal shutdown first. A forced stop can lose unsaved guest writes; confirm it only after graceful shutdown fails. Launch using **Safe display** to use the stable non-GL adapter. Safe display changes the host device path; it does not remove the guest driver or restore security policy.

For a complete rollback, stop the VM and restore `before-guest-security-changes`, which returns disk, TPM and UEFI variables together to the state preceding the security changes. The automatic pre-Yttrium checkpoint restores the state at its own creation time; it cannot undo earlier firmware changes. Restoration creates a recovery checkpoint of the current state first and replaces the guest's contents with the chosen checkpoint; files created afterward belong in a separate backup. Avoid restoring only the disk across TPM/BitLocker changes. If using the current Windows installation instead, uninstall the experimental driver and certificate, disable test signing, and re-enable Secure Boot in guest UEFI once the normal driver is working. Keep recovery keys available throughout.

## Primary references

- [Yttrium source at the recorded release](https://github.com/arehnman/yttrium-virtio-gpu/tree/e9917ae25b2597306d483e1063dba08658139bc9)
- [Guest release](https://github.com/arehnman/yttrium-virtio-gpu/releases/tag/yttrium-experimental-2026-11-02)
- [Host bundle and manifest](https://github.com/arehnman/yttrium-qemu-flatpak/tree/v2026-11-02)
- [Mesa Venus requirements](https://docs.mesa3d.org/drivers/venus.html)

These are research inputs, not Virtue test results.
