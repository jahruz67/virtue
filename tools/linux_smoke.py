"""Opt-in hardware acceptance test. Creates only a new VM; never deletes user data.

Run from a Linux graphical session: python tools/linux_smoke.py --iso Windows.iso
The script opens real Windows Setup, verifies QMP/KVM, and optionally requests
ACPI shutdown. It cannot certify Windows installation or graphics on its own.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

from virtue.services import VMService
from virtue.store import VMStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", type=Path, help="Legitimate Windows x64 ISO")
    parser.add_argument("--drivers", type=Path)
    parser.add_argument("--data-dir", type=Path, help="Use an isolated library (default: new temporary directory)")
    parser.add_argument("--memory-mib", type=int, default=4096)
    parser.add_argument("--cpus", type=int, default=2)
    parser.add_argument("--shutdown-after", type=int, default=0, help="Optional seconds before ACPI shutdown request")
    parser.add_argument("--output", type=Path, default=Path("linux-smoke-result.json"))
    args = parser.parse_args()
    from virtue.host import detect_host
    host = detect_host()
    report = {"host": host.to_dict(), "created_vm": None, "observations": [],
              "guest_installation": "not_verified", "guest_gpu": "not_verified"}
    if not args.iso:
        print(json.dumps(report, indent=2))
        return 0 if host.ready else 2
    root = args.data_dir or Path(tempfile.mkdtemp(prefix="virtue-smoke-"))
    try:
        service = VMService(VMStore(root), host=host)
        vm = service.create_vm({"name": "Windows acceptance test", "windows_version": "11",
                                "memory_mib": args.memory_mib, "cpus": args.cpus,
                                "disk_gib": 64, "iso_path": str(args.iso.resolve()),
                                "drivers_iso_path": str(args.drivers.resolve()) if args.drivers else ""})
        report["created_vm"] = {"id": vm["id"], "root": str(root)}
        start = time.monotonic()
        status = service.start_vm(vm["id"])
        report["observations"].append({"event": "qmp_start", "seconds": time.monotonic() - start, "status": status})
        if status.get("state") != "running" or status.get("kvm") is not True:
            raise RuntimeError("QMP did not confirm running hardware acceleration")
        # Reconnect through a new service, as after closing/reopening the GUI.
        reopened = VMService(VMStore(root), host=host)
        reconnected = reopened.list_vms()
        report["observations"].append({"event": "manager_reconnect", "vms": reconnected})
        if not any(v["id"] == vm["id"] and v["state"] == "running" for v in reconnected):
            raise RuntimeError("VM reconnection failed")
        print("Windows Setup should be visible. Check video, keyboard, mouse, networking and audio manually.")
        print(f"The VM is retained in {root}. Reopen it with: virtue --data-dir '{root}'")
        if args.shutdown_after:
            if not 1 <= args.shutdown_after <= 3600:
                raise ValueError("shutdown-after must be between 1 and 3600 seconds")
            for _ in range(args.shutdown_after):
                time.sleep(1)
            service.shutdown_vm(vm["id"])
            report["observations"].append({"event": "acpi_shutdown_requested"})
        return 0
    except Exception as exc:
        report["error"] = str(exc)
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

