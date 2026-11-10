"""Linux adapter and compatible imports for shared QEMU operations."""
import os
import shutil
import socket
import struct
import subprocess

from .qemu import QemuBackend, _atomic_json, _process_identity, _read_json, _sync_directory


class LinuxBackend(QemuBackend):
    @staticmethod
    def _process_identity(pid: int) -> dict | None:
        return _process_identity(pid)
