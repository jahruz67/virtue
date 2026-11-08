# CAD and graphics validation procedure

**No SOLIDWORKS, Windows guest, integrated GPU, or Linux KVM performance result has been measured in this development environment.** The CAD preset is a resource configuration, not a compatibility guarantee. Yttrium is not SOLIDWORKS certified graphics.

[SOLIDWORKS system requirements](https://www.solidworks.com/support/system-requirements) specify x86-64, 16 GB RAM with 32 GB recommended, and certified graphics cards/drivers. Large assemblies can need more memory than the CAD preset; preserve enough memory for Linux. Licensing/media must be supplied by the tester. Windows on ARM and Apple Silicon SOLIDWORKS compatibility are outside the implemented Linux target.

## Lower-level executable smoke test

Run the PowerShell collector in [Yttrium setup](yttrium.md) after driver installation. It executes an OpenGL triangle/pixel-readback test and records the renderer, vendor, version and Vulkan devices. A Zink/Venus hardware renderer plus successful draw is the minimum evidence for the OpenGL path; GDI Generic or llvmpipe is a failed acceleration test. Test Windows 11 and legacy Windows 10 separately. Do not record the Windows development host's graphics output as a guest result.

Run a visible Vulkan workload and a visible OpenGL workload using the fixed upstream FurMark version described there. Capture the window with the workload running, exact settings, GPU report and QEMU log. Check texture corruption, resizing, fullscreen, minimization and recovery after full shutdown. Record CPU fallback even if the scene appears smooth. Do not use invented frame rates or compare different workload versions.

## Reproducible comparison

Use the same x86-64 machine, Windows build, application version, project files, display resolution, power profile and thermal conditions for these configurations:

| Configuration | CPU / RAM | Graphics | Result |
|---|---|---|---|
| Native Windows | Record exact resources | Vendor driver/version | Not measured |
| QEMU/KVM safe display | Same VM resources | Non-GL display | Not measured |
| Virtue/Yttrium | Same VM resources | Recorded host/guest bundle | Not measured |

Keep a versioned CSV with columns `timestamp,host_gpu,host_kernel,host_driver,windows_build,config,workload,run,seconds,frame_ms_p50,frame_ms_p95,host_cpu_percent,host_rss_mib,result,notes`. Leave unavailable values empty. Record at least five warm repetitions after one excluded warmup. Report median and spread; separate cold disk cache runs. Poll host process CPU/RSS with a profiler or `pidstat` and record the interval/tool. Record boot wall time from QEMU launch to the same visible sign-in milestone, rather than comparing QMP startup time with a usable Windows desktop.

## SOLIDWORKS 2025 workload

1. Install the licensed Windows x64 application and the same service pack in all configurations. Record SOLIDWORKS Rx graphics diagnostics and whether software OpenGL is selected. Do not enable unsupported registry workarounds to claim certification.
2. Open the same local part and assembly files, recording their SHA256 and assembly component count. Measure file-open and launch time separately, using a stopwatch/video or consistent application instrumentation.
3. Use the same scripted or carefully repeated 60-second rotation, 30-second zoom/pan, sketch edit/rebuild and assembly manipulation. Record frame-time percentiles using the same capture tool and include visible rendering defects. If the tool cannot capture OpenGL reliably, state that limitation and use video/latency observations explicitly.
4. Repeat save/reopen, large assembly load, suspend prohibition, normal reboot and normal shutdown. Record driver resets, crashes and data consistency. Test fallback launch and full pre-driver checkpoint restoration on disposable data.
5. Test Intel UHD/Iris Xe/Arc integrated and AMD Radeon integrated independently from discrete GPUs. Preserve exact CPU, GPU PCI ID, Vulkan driver, kernel, host bundle, guest driver, resolution and report JSON for each row in the matrix below.

## Compatibility matrix

| Host GPU family | Windows guest | Vulkan/GL probe | CAD workload | Status |
|---|---|---|---|---|
| Intel UHD / Iris Xe integrated | 11 x64 | Pending | Pending | Untested |
| Intel Arc integrated | 11 x64 | Pending | Pending | Untested |
| AMD Radeon integrated | 11 x64 | Pending | Pending | Untested |
| AMD Radeon discrete | 11 x64 | Pending | Pending | Untested |
| NVIDIA discrete | 11 x64 | Pending | Pending | Untested |

Upstream's stated qualification target is Windows 10 x64 on Intel Arc A580. That upstream statement does not validate these rows. Add actual evidence links and replace “Untested” only after running the procedure on that hardware.
