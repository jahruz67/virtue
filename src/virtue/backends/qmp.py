"""Bounded synchronous QMP client; call it from a worker, never the GUI thread.

Each connection negotiates capabilities and correlates response IDs. Events may
arrive between a request and its response and are retained for diagnostics.
"""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import socket
import time
from typing import Any

from .backend import BackendError


class QMPError(BackendError):
    pass


class QMPClient:
    MAX_MESSAGE = 4 * 1024 * 1024

    def __init__(self, path: str | Path, timeout: float = 3.0, *, socket_factory=None):
        self.path = str(path)
        self.timeout = timeout
        self.socket: socket.socket | None = None
        self._buffer = bytearray()
        self._id = 0
        self.events: deque[dict] = deque(maxlen=64)
        # Dependency injection is for protocol tests on non-Linux hosts. The
        # production transport remains a private Unix socket exclusively.
        self._socket_factory = socket_factory or (lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))

    def connect(self) -> "QMPClient":
        self.close()
        self._buffer.clear()
        sock = self._socket_factory()
        sock.settimeout(self.timeout)
        self.socket = sock
        try:
            sock.connect(self.path)
            greeting = self._read(time.monotonic() + self.timeout)
            if "QMP" not in greeting:
                raise QMPError("The VM management socket did not send a QMP greeting.")
            self.execute("qmp_capabilities")
            return self
        except (OSError, ValueError, QMPError) as exc:
            self.close()
            if isinstance(exc, QMPError):
                raise
            raise QMPError(f"Cannot connect to VM management: {exc}") from exc

    def _read(self, deadline: float) -> dict:
        assert self.socket is not None
        while True:
            if b"\n" in self._buffer:
                raw, _, rest = self._buffer.partition(b"\n")
                self._buffer = bytearray(rest)
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise QMPError("VM management returned invalid JSON.") from exc
                if not isinstance(value, dict):
                    raise QMPError("VM management returned an invalid message.")
                return value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QMPError("VM management timed out; the guest has not been stopped.")
            self.socket.settimeout(remaining)
            try:
                chunk = self.socket.recv(65536)
            except OSError as exc:
                raise QMPError(f"VM management connection failed: {exc}") from exc
            if not chunk:
                raise QMPError("VM management connection closed.")
            self._buffer.extend(chunk)
            if len(self._buffer) > self.MAX_MESSAGE:
                raise QMPError("VM management message exceeded the size limit.")

    def execute(self, command: str, arguments: dict | None = None) -> Any:
        if self.socket is None:
            raise QMPError("VM management is not connected.")
        self._id += 1
        request: dict = {"execute": command, "id": self._id}
        if arguments is not None:
            request["arguments"] = arguments
        deadline = time.monotonic() + self.timeout
        try:
            self.socket.sendall(json.dumps(request).encode("utf-8") + b"\r\n")
            while True:
                response = self._read(deadline)
                if "event" in response:
                    self.events.append(response)
                    continue
                if response.get("id") != self._id:
                    continue
                if "error" in response:
                    error = response["error"]
                    raise QMPError(f"{command}: {error.get('desc', error)}")
                if "return" not in response:
                    raise QMPError(f"{command}: missing response result.")
                return response["return"]
        except OSError as exc:
            raise QMPError(f"VM management request failed: {exc}") from exc

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def __enter__(self) -> "QMPClient":
        return self.connect()

    def __exit__(self, *_):
        self.close()
