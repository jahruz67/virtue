"""Small, synchronized guest-agent connection for automatic setup status."""
import base64
import json
import os
from pathlib import Path
import socket
import time


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

    def write_text(self, path: str, text: str) -> None:
        data = text.encode("utf-8")
        if len(data) > 64 * 1024:
            raise ValueError("The Windows integration request is too large.")
        handle = self.execute("guest-file-open", {"path": path, "mode": "wb"})
        try:
            result = self.execute("guest-file-write", {"handle": handle,
                                  "buf-b64": base64.b64encode(data).decode("ascii")})
            if not isinstance(result, dict) or type(result.get("count")) is not int or result["count"] != len(data):
                raise ValueError("Windows did not write the complete integration request.")
            self.execute("guest-file-flush", {"handle": handle})
        finally:
            self.execute("guest-file-close", {"handle": handle})

    def start_display_task(self) -> None:
        self._run_display_command("C:\\Windows\\System32\\schtasks.exe", ["/Run", "/TN", "VirtueSetDisplay"])

    def install_display_task(self) -> None:
        from ..runtime import support_folder
        path = "C:\\ProgramData\\Virtue\\Set-VirtueDisplay.ps1"
        self.write_text(path, (support_folder("guest") / "Set-VirtueDisplay.ps1").read_text(encoding="utf-8"))
        self._run_display_command("C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
                                  ["-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                                   "-ExecutionPolicy", "Bypass", "-File", path, "-Register"], timeout=20)

    def _run_display_command(self, path: str, arguments: list[str], *, timeout: int = 5) -> None:
        result = self.execute("guest-exec", {"path": path, "arg": arguments, "capture-output": True})
        if not isinstance(result, dict) or type(result.get("pid")) is not int or result["pid"] <= 0:
            raise ValueError("Windows did not start the display integration task.")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.execute("guest-exec-status", {"pid": result["pid"]})
            if not isinstance(status, dict):
                raise ValueError("Windows returned an invalid display task status.")
            if status.get("exited"):
                if status.get("exitcode") != 0:
                    raise ValueError("Windows could not start display integration. Sign in to the Virtue Windows account.")
                return
            time.sleep(0.2)
        raise ValueError("Windows took too long to start display integration.")

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
