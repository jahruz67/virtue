"""Protocol tests against a local synthetic QMP server, not a guest boot test."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

from virtue.backends.qmp import QMPClient, QMPError


class QMPProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "qmp.sock")
        # Exercise the production Unix transport on Linux. Other development
        # hosts use injected loopback transport, including Windows without AF_UNIX.
        if sys.platform == "linux":
            self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.listener.bind(self.path)
        else:
            self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listener.bind(("127.0.0.1", 0))
        self.address = self.listener.getsockname()
        self.listener.listen(1)
        self.errors = []
        self.thread = None

    def client(self):
        if sys.platform == "linux":
            return QMPClient(self.path)
        address = self.address
        class FixtureSocket(socket.socket):
            def __init__(self):
                super().__init__(socket.AF_INET, socket.SOCK_STREAM)
            def connect(self, path):
                return super().connect(address)
        return QMPClient(self.path, socket_factory=FixtureSocket)

    def tearDown(self):
        self.listener.close()
        if self.thread:
            self.thread.join(timeout=3)
        self.temp.cleanup()
        if self.errors:
            raise self.errors[0]

    @staticmethod
    def send(conn, message):
        conn.sendall(json.dumps(message).encode() + b"\r\n")

    def serve(self, handler):
        def run():
            try:
                conn, _ = self.listener.accept()
                with conn:
                    conn.settimeout(2)
                    handler(conn)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                self.errors.append(exc)
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def handshake(self, conn):
        # Deliberately fragmented greeting exercises incremental framing.
        raw = b'{"QMP":{"version":{},"capabilities":[]}}\r\n'
        conn.sendall(raw[:7])
        conn.sendall(raw[7:])
        stream = conn.makefile("rb")
        request = json.loads(stream.readline())
        self.assertEqual(request["execute"], "qmp_capabilities")
        self.send(conn, {"return": {}, "id": request["id"]})
        return stream

    def test_events_and_unknown_ids_do_not_steal_response(self):
        def handler(conn):
            stream = self.handshake(conn)
            request = json.loads(stream.readline())
            self.send(conn, {"event": "STOP", "data": {}})
            self.send(conn, {"return": {"wrong": True}, "id": 999})
            self.send(conn, {"return": {"running": True}, "id": request["id"]})
            stream.close()
        self.serve(handler)
        with self.client() as client:
            self.assertEqual(client.execute("query-status"), {"running": True})
            self.assertEqual(client.events[0]["event"], "STOP")

    def test_qmp_errors_are_actionable(self):
        def handler(conn):
            stream = self.handshake(conn)
            request = json.loads(stream.readline())
            self.send(conn, {"error": {"class": "GenericError", "desc": "Guest is not running"}, "id": request["id"]})
            stream.close()
        self.serve(handler)
        with self.client() as client:
            with self.assertRaisesRegex(QMPError, "Guest is not running"):
                client.execute("stop")

    def test_invalid_greeting_rejected(self):
        self.serve(lambda conn: self.send(conn, {"return": {}}))
        with self.assertRaisesRegex(QMPError, "greeting"):
            self.client().connect()

    def test_closed_socket_rejected(self):
        self.serve(lambda conn: None)
        with self.assertRaises(QMPError):
            self.client().connect()

    def test_invalid_json_rejected(self):
        self.serve(lambda conn: conn.sendall(b"invalid\n"))
        with self.assertRaisesRegex(QMPError, "invalid JSON"):
            self.client().connect()


if __name__ == "__main__":
    unittest.main()
