"""Native Darwin process identity and local-socket ownership verification."""
from __future__ import annotations

import ctypes
import errno
from functools import lru_cache
import os
import shutil
import socket
import struct

from .backend import BackendError


class BSDInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid",
        "rgid", "svuid", "svgid", "reserved")]
    _fields_ += [("command", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in (
        "nfiles", "pgid", "jobc", "tdev", "tpgid")]
    _fields_ += [("nice", ctypes.c_int32), ("start_seconds", ctypes.c_uint64),
                 ("start_microseconds", ctypes.c_uint64)]


@lru_cache(maxsize=1)
def libraries():
    proc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    proc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                 ctypes.c_void_p, ctypes.c_int]
    proc.proc_pidinfo.restype = ctypes.c_int
    proc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    proc.proc_pidpath.restype = ctypes.c_int
    libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
                           ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                           ctypes.c_void_p, ctypes.c_size_t]
    libc.sysctl.restype = ctypes.c_int
    libc.getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
                              ctypes.POINTER(ctypes.c_uint)]
    libc.getpeereid.restype = ctypes.c_int
    return proc, libc


def process_identity(pid: int) -> dict | None:
    if type(pid) is not int or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError as exc:
        raise BackendError("The VM process belongs to another user.") from exc
    proc, libc = libraries()
    info = BSDInfo()
    if proc.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        raise BackendError("A live VM process could not be identified; storage operations are blocked.")
    if info.status == 5:
        return None
    if info.uid != os.getuid():
        return None
    executable = ctypes.create_string_buffer(4096)
    if proc.proc_pidpath(pid, executable, len(executable)) <= 0:
        raise BackendError("Could not verify the VM executable path.")
    arguments = ctypes.create_string_buffer(1024 * 1024)
    size = ctypes.c_size_t(len(arguments))
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
    if libc.sysctl(mib, 3, arguments, ctypes.byref(size), None, 0) != 0:
        raise BackendError("Could not read the live VM's native argument record.")
    raw = arguments.raw[:size.value]
    if len(raw) < 5:
        raise BackendError("The native VM argument record is incomplete.")
    count = struct.unpack_from("=i", raw)[0]
    boundary = raw.find(b"\0", 4)
    if count < 1 or boundary < 0:
        raise BackendError("The native VM argument record is invalid.")
    values = raw[boundary + 1:].lstrip(b"\0").split(b"\0")[:count]
    if len(values) != count:
        raise BackendError("The native VM argument record is truncated.")
    return {"pid": pid, "start_ticks": f"{info.start_seconds}:{info.start_microseconds}",
            "executable": executable.value.decode("utf-8", errors="surrogateescape"),
            "cmdline": [value.decode("utf-8", errors="surrogateescape") for value in values]}


def peer_identity(connection: socket.socket) -> dict:
    _, libc = libraries()
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    if libc.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise BackendError("Could not verify the management socket owner.")
    if uid.value != os.getuid():
        raise BackendError("The management socket belongs to another user.")
    try:
        pid = struct.unpack("=i", connection.getsockopt(0, 2, 4))[0]  # SOL_LOCAL, LOCAL_PEERPID
    except (OSError, struct.error) as exc:
        raise BackendError("Could not verify the management socket process.") from exc
    identity = process_identity(pid)
    if identity is None:
        raise BackendError("The management socket process has exited.")
    return identity


def clone_file(source, destination) -> None:
    """Use APFS copy-on-write; preflight a full copy on other volumes."""
    _, libc = libraries()
    libc.clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    libc.clonefile.restype = ctypes.c_int
    if libc.clonefile(os.fsencode(source), os.fsencode(destination), 0) == 0:
        return
    error = ctypes.get_errno()
    if error not in {errno.EXDEV, errno.ENOTSUP, errno.ENOSYS, errno.EINVAL}:
        raise BackendError(f"Cannot clone recovery state: {os.strerror(error)}")
    if shutil.disk_usage(destination.parent).free < source.stat().st_size + 128 * 1024 * 1024:
        raise BackendError("This volume requires a full recovery copy. Free enough space for the complete virtual disk first.")
    shutil.copy2(source, destination)
