"""Platform adapter selection; service constructor injection remains supported."""
import sys


def create_backend(root, host=None):
    if sys.platform == "darwin":
        from .macos import MacOSBackend
        return MacOSBackend(root, host)
    from .linux import LinuxBackend
    return LinuxBackend(root, host)
