"""Small, synchronized guest-agent connection for automatic setup status."""
import base64
import json
import os
from pathlib import Path
import socket


class GuestAgent:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.socket.settimeout(2)
            self.socket.connect(str(self.path))
            self.stream = self.socket.makefile("rwb", buffering=0)
            token = int.from_bytes(os.urandom(4), "big")
            self.socket.sendall(b"\xff")
            if self.execute("guest-sync-delimited", {"id": token}) != token:
                raise ValueError("Windows integration did not synchronize.")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        try:
            if hasattr(self, "stream"):
                self.stream.close()
        finally:
            self.socket.close()

    def execute(self, command: str, arguments: dict):
        token = os.urandom(8).hex()
        request = {"execute": command, "arguments": arguments, "id": token}
        self.socket.sendall(json.dumps(request).encode("utf-8") + b"\n")
        for _ in range(16):
            line = self.stream.readline(256 * 1024)
            if not line or len(line) == 256 * 1024:
                raise ValueError("Windows integration returned an incomplete response.")
            try:
                response = json.loads(line.lstrip(b"\xff"))
            except (ValueError, UnicodeError):
                continue
            if not isinstance(response, dict) or response.get("id") != token:
                continue
            if "error" in response:
                raise ValueError(str(response["error"]))
            return response.get("return")
        raise ValueError("Windows integration did not respond to this request.")

    def read_text(self, path: str, limit: int, *, tail: bool = False) -> str:
        handle = self.execute("guest-file-open", {"path": path, "mode": "rb"})
        try:
            skipped = 0
            if tail:
                end = self.execute("guest-file-seek", {"handle": handle, "offset": 0, "whence": 2})
                size = end["position"]
                if type(size) is not int or size < 0:
                    raise ValueError("Windows returned an invalid diagnostic file size.")
                skipped = max(0, size - limit)
                self.execute("guest-file-seek", {"handle": handle, "offset": skipped, "whence": 0})
            chunks = []
            remaining = limit
            eof = False
            while remaining > 0:
                count = min(48 * 1024, remaining)
                result = self.execute("guest-file-read", {"handle": handle, "count": count})
                data = base64.b64decode(result["buf-b64"], validate=True)
                if len(data) > count:
                    raise ValueError("Windows returned more diagnostic data than requested.")
                chunks.append(data)
                remaining -= len(data)
                eof = result.get("eof", False)
                if eof or not data:
                    break
            text = b"".join(chunks).decode("utf-8-sig", errors="replace")
            if skipped:
                text = f"[Earlier {skipped} bytes omitted; showing recent log output.]\n" + text
            if remaining == 0 and not eof:
                text += f"\n[Truncated after {limit} bytes.]"
            return text
        finally:
            self.execute("guest-file-close", {"handle": handle})

    def setup_status(self) -> dict:
        handle = self.execute("guest-file-open", {"path": "C:\\ProgramData\\Virtue\\setup-status.json", "mode": "r"})
        try:
            result = self.execute("guest-file-read", {"handle": handle, "count": 128 * 1024})
            data = base64.b64decode(result["buf-b64"], validate=True)
            if len(data) >= 128 * 1024:
                raise ValueError("The Windows setup report is too large.")
            value = json.loads(data.decode("utf-8-sig"))
            if not isinstance(value, dict) or value.get("state") not in {"installing", "ready", "error"}:
                raise ValueError("Windows returned an invalid setup state.")
            return value
        finally:
            self.execute("guest-file-close", {"handle": handle})
