"""Platform backends. Linux is the only implemented hypervisor backend."""

from .backend import BackendError
from .linux import LinuxBackend

__all__ = ["BackendError", "LinuxBackend"]
