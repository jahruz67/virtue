"""Conservative interpretation of imported Windows guest graphics evidence."""
from __future__ import annotations

import re


SOFTWARE = re.compile(r"llvmpipe|lavapipe|swiftshader|Microsoft Basic Render|GDI Generic|software rasterizer", re.I)


def classify_guest_report(report: dict, *, profile: str = "yttrium") -> dict:
    """Report individual API evidence and distinguish discovery from rendering.

    This is user-imported diagnostic evidence, not a signed attestation. Successful
    enumeration alone cannot establish hardware rendering or CAD compatibility.
    """
    if not isinstance(report, dict) or type(report.get("schema_version")) is not int or report.get("schema_version") != 1:
        raise ValueError("Unsupported GPU report. Use guest/Collect-VirtueGpuReport.ps1 (schema_version 1).")
    evidence = {}
    reasons = []
    opengl = report.get("opengl") or {}
    vulkan = report.get("vulkan") or {}
    rendering = report.get("render_tests") or {}
    if not all(isinstance(section, dict) for section in (opengl, vulkan, rendering)):
        raise ValueError("Malformed GPU report: opengl, vulkan and render_tests must be objects.")
    renderer = str(opengl.get("renderer", ""))
    vendor = str(opengl.get("vendor", ""))
    if profile == "triton":
        return {"state": "unverified", "reasons": [
            "This capture does not verify Triton Direct3D rendering or SolidWorks compatibility.",
            "SolidWorks may use Software OpenGL while Windows retains its DirectX driver; an OpenGL result alone does not classify the whole Mac graphics path."],
            "evidence": {"opengl_renderer": renderer, "opengl_vendor": vendor,
                         "opengl_draw_passed": opengl.get("draw_test_passed") is True,
                         "direct3d": "Unverified; no Direct3D workload is captured by this collector.",
                         "report_time": str(report.get("collected_at", "")),
                         "trust": "Imported user-provided diagnostic evidence."}}
    if profile != "yttrium":
        raise ValueError("Unknown guest graphics evidence profile.")
    devices = vulkan.get("devices", [])
    if not isinstance(devices, list):
        devices = []
    names = [str(device.get("name", "")) if isinstance(device, dict) else str(device) for device in devices]
    raw_vk = str(vulkan.get("output", ""))
    if not names:
        names = re.findall(r"deviceName\s*=\s*(.+)", raw_vk)
    venus = [name for name in names if "virtio" in name.lower() and "venus" in name.lower() and not SOFTWARE.search(name)]
    zink = "zink" in renderer.lower() and "venus" in renderer.lower() and not SOFTWARE.search(renderer)
    evidence["vulkan_devices"] = names
    evidence["opengl_renderer"] = renderer
    evidence["opengl_vendor"] = vendor
    evidence["vulkan_venus_discovered"] = bool(venus)
    evidence["opengl_zink_venus"] = zink
    # Only an actual GL draw followed by glReadPixels/error verification counts.
    gl_draw = opengl.get("draw_test_passed") is True
    vk_draw = rendering.get("vulkan", {}).get("passed") is True if isinstance(rendering.get("vulkan"), dict) else False
    evidence["opengl_draw_passed"] = gl_draw
    evidence["vulkan_draw_passed"] = vk_draw
    evidence["direct3d"] = "Unverified; dxdiag/feature enumeration is not a rendering benchmark."
    evidence["report_time"] = str(report.get("collected_at", ""))
    evidence["trust"] = "Imported user-provided diagnostic evidence; results apply only to this capture."
    if (zink and gl_draw) or (venus and vk_draw):
        state = "hardware_rendering_observed"
        reasons.append("A rendering test passed through a Venus device with a hardware renderer identity.")
        if zink and gl_draw:
            reasons.append("OpenGL drew and read back pixels through Zink/Venus in the capture.")
        reasons.append("This does not establish Direct3D, SOLIDWORKS, stability, or performance compatibility.")
    elif SOFTWARE.search(renderer) or (names and all(SOFTWARE.search(name) for name in names)):
        state = "software_fallback"
        reasons.append("The selected renderer is a CPU/software implementation.")
    elif venus or zink:
        state = "driver_detected_unverified"
        reasons.append("Venus/Zink was enumerated, but no successful rendering test was captured.")
    elif report.get("driver_installed") is True:
        state = "driver_not_functioning"
        reasons.append("A driver is reported installed but Venus/Zink is absent from the collected APIs.")
    else:
        state = "unavailable"
        reasons.append("No Yttrium Venus/Zink graphics evidence was captured.")
    return {"state": state, "reasons": reasons, "evidence": evidence}
