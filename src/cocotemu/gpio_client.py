# Synchronous Python client for the GPIO bridge.

import socket
import struct
import time

from .gpio_protocol import GpioDir, GpioOp, GpioResp, GpioErr


class GpioClient:
    """Connect to a GpioBridge Unix socket and interact with DUT GPIO signals."""

    def __init__(self, sock_path: str = "/tmp/cocotemu_gpio.sock"):
        self._sock_path = sock_path
        self._sock: socket.socket | None = None
        self._signals: list[dict] = []  # [{name, width, direction}, ...]

    def connect(self, retries: int = 50, delay: float = 0.05):
        """Connect to the bridge, retrying until available. Fetches signal list."""
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        for attempt in range(retries):
            try:
                self._sock.connect(self._sock_path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(delay)
        else:
            self._sock.close()
            self._sock = None
            raise ConnectionError(
                f"Could not connect to {self._sock_path} after {retries} retries")

        self._sock.settimeout(2.0)
        # Auto-fetch signal list
        self._signals = self._list()
        return self

    def close(self):
        if self._sock:
            self._sock.close()
            self._sock = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def signals(self) -> list[dict]:
        return list(self._signals)

    def _resolve(self, name_or_idx) -> int:
        """Resolve a signal name or index to an index."""
        if isinstance(name_or_idx, int):
            return name_or_idx
        for i, sig in enumerate(self._signals):
            if sig["name"] == name_or_idx:
                return i
        raise KeyError(f"Unknown signal: {name_or_idx}")

    def _list(self) -> list[dict]:
        """Send LIST, parse LIST_RESP."""
        self._sock.sendall(bytes([GpioOp.LIST]))
        # Read LIST_RESP header: op + count
        hdr = self._recv_exact(2)
        assert hdr[0] == GpioResp.LIST_RESP
        count = hdr[1]
        signals = []
        for _ in range(count):
            name_len = self._recv_exact(1)[0]
            name = self._recv_exact(name_len).decode("ascii")
            width = self._recv_exact(1)[0]
            direction = GpioDir(self._recv_exact(1)[0])
            signals.append({"name": name, "width": width, "direction": direction})
        return signals

    def get(self, name_or_idx) -> int:
        """Read a signal value (output signals only)."""
        idx = self._resolve(name_or_idx)
        self._sock.sendall(bytes([GpioOp.GET, idx]))
        op = self._recv_exact(1)[0]
        if op == GpioResp.ERR:
            code = self._recv_exact(1)[0]
            raise RuntimeError(f"GET error: {GpioErr(code).name}")
        assert op == GpioResp.VALUE
        rest = self._recv_exact(5)  # sig_idx(1) + value(4)
        val = struct.unpack_from("<I", rest, 1)[0]
        return val

    def set(self, name_or_idx, value: int):
        """Drive a signal value (input signals only)."""
        idx = self._resolve(name_or_idx)
        msg = struct.pack("<BBi", GpioOp.SET, idx, value)
        self._sock.sendall(msg)
        resp = self._recv_exact(1)
        if resp[0] == GpioResp.ERR:
            err_code = self._recv_exact(1)
            raise RuntimeError(f"SET error: {GpioErr(err_code[0]).name}")
        assert resp[0] == GpioResp.ACK

    def subscribe(self, name_or_idx):
        """Subscribe to change notifications on an output signal."""
        idx = self._resolve(name_or_idx)
        self._sock.sendall(bytes([GpioOp.SUBSCRIBE, idx]))
        resp = self._recv_exact(1)
        if resp[0] == GpioResp.ERR:
            err_code = self._recv_exact(1)
            raise RuntimeError(f"SUBSCRIBE error: {GpioErr(err_code[0]).name}")
        assert resp[0] == GpioResp.ACK

    def unsubscribe(self, name_or_idx):
        """Unsubscribe from change notifications."""
        idx = self._resolve(name_or_idx)
        self._sock.sendall(bytes([GpioOp.UNSUB, idx]))
        resp = self._recv_exact(1)
        if resp[0] == GpioResp.ERR:
            err_code = self._recv_exact(1)
            raise RuntimeError(f"UNSUB error: {GpioErr(err_code[0]).name}")
        assert resp[0] == GpioResp.ACK

    def recv_notification(self, timeout: float = 2.0) -> tuple[int, int]:
        """Wait for an async VALUE notification.

        Returns (sig_idx, value).
        """
        old_timeout = self._sock.gettimeout()
        self._sock.settimeout(timeout)
        try:
            op = self._recv_exact(1)[0]
            assert op == GpioResp.VALUE
            rest = self._recv_exact(5)  # sig_idx(1) + value(4)
            idx = rest[0]
            val = struct.unpack_from("<I", rest, 1)[0]
            return idx, val
        finally:
            self._sock.settimeout(old_timeout)

    def _recv_exact(self, n: int) -> bytes:
        """Receive exactly n bytes."""
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Server disconnected")
            buf.extend(chunk)
        return bytes(buf)
