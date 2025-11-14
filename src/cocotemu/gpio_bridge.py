# GPIO bridge: Unix socket server exposing DUT GPIO signals.
#
# Threading model (mirrors QemuBridge):
#   - Thread 1 (sim): cocotb poll loop picks requests from _req_queue,
#     reads/writes signal handles, detects output changes, queues notifications
#   - Thread 2 (OS): socket accept/recv, puts parsed requests on _req_queue,
#     blocks on _resp_queue, drains _notify_queue for async VALUE pushes

import os
import queue
import socket
import struct
import threading
import logging

import cocotb
from cocotb.triggers import Timer

from .gpio_protocol import GpioDir, GpioOp, GpioResp, GpioErr, GpioSignal

logger = logging.getLogger(__name__)


class GpioBridge:
    """Exposes DUT GPIO signals over a Unix socket.

    Unlike QemuBridge, re-accepts clients after disconnect.
    """

    def __init__(self, signals: list[GpioSignal],
                 sock_path: str = "/tmp/cocotemu_gpio.sock"):
        self._signals = signals
        self._sock_path = sock_path
        self._running = False
        self._server_sock = None
        self._req_queue: queue.Queue = queue.Queue()
        self._resp_queue: queue.Queue = queue.Queue()
        self._notify_queue: queue.Queue = queue.Queue()
        # Subscriptions: set of signal indices
        self._subscriptions: set[int] = set()
        # Last-known values for change detection
        self._last_values: dict[int, int] = {}

    async def start(self, poll_ns: int = 100):
        """Start the bridge — call from a cocotb coroutine."""
        self._running = True

        t = threading.Thread(target=self._recv_loop, daemon=True)
        t.start()

        cocotb.log.info("GpioBridge: poll loop started (poll_ns=%d)", poll_ns)

        while self._running:
            # Process one request per iteration (if any)
            try:
                req = self._req_queue.get_nowait()
            except queue.Empty:
                # No request — sample outputs for change detection
                self._sample_outputs()
                await Timer(poll_ns, units="ns")
                continue

            if req is None:
                break

            op, payload = req
            self._handle_request(op, payload)

            # Also sample after handling (SET may have changed something)
            self._sample_outputs()
            await Timer(poll_ns, units="ns")

        cocotb.log.info("GpioBridge: poll loop exited")

    def stop(self):
        """Signal the bridge to shut down."""
        self._running = False
        self._req_queue.put(None)
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass

    def _sample_outputs(self):
        """Check subscribed output signals for changes, queue notifications."""
        for idx in list(self._subscriptions):
            sig = self._signals[idx]
            cur = int(sig.handle.value)
            prev = self._last_values.get(idx)
            if prev is None:
                self._last_values[idx] = cur
                continue
            if cur != prev:
                self._last_values[idx] = cur
                # Queue async VALUE notification
                resp = struct.pack("<BBi", GpioResp.VALUE, idx, cur)
                self._notify_queue.put(resp)

    def _handle_request(self, op: int, payload: bytes):
        """Process a single request in sim context, put response on _resp_queue."""
        if op == GpioOp.LIST:
            self._resp_queue.put(self._build_list_resp())
        elif op == GpioOp.GET:
            idx = payload[0]
            if idx >= len(self._signals):
                self._resp_queue.put(
                    bytes([GpioResp.ERR, GpioErr.BAD_INDEX]))
                return
            sig = self._signals[idx]
            if sig.direction != GpioDir.OUT:
                self._resp_queue.put(
                    bytes([GpioResp.ERR, GpioErr.WRONG_DIRECTION]))
                return
            val = int(sig.handle.value)
            self._resp_queue.put(
                struct.pack("<BBi", GpioResp.VALUE, idx, val))
        elif op == GpioOp.SET:
            idx = payload[0]
            if idx >= len(self._signals):
                self._resp_queue.put(
                    bytes([GpioResp.ERR, GpioErr.BAD_INDEX]))
                return
            sig = self._signals[idx]
            if sig.direction != GpioDir.IN:
                self._resp_queue.put(
                    bytes([GpioResp.ERR, GpioErr.WRONG_DIRECTION]))
                return
            val = struct.unpack_from("<I", payload, 1)[0]
            sig.handle.value = val
            self._resp_queue.put(bytes([GpioResp.ACK]))
        elif op == GpioOp.SUBSCRIBE:
            idx = payload[0]
            if idx >= len(self._signals):
                self._resp_queue.put(
                    bytes([GpioResp.ERR, GpioErr.BAD_INDEX]))
                return
            sig = self._signals[idx]
            if sig.direction != GpioDir.OUT:
                self._resp_queue.put(
                    bytes([GpioResp.ERR, GpioErr.WRONG_DIRECTION]))
                return
            self._subscriptions.add(idx)
            self._last_values[idx] = int(sig.handle.value)
            self._resp_queue.put(bytes([GpioResp.ACK]))
        elif op == GpioOp.UNSUB:
            idx = payload[0]
            if idx >= len(self._signals):
                self._resp_queue.put(
                    bytes([GpioResp.ERR, GpioErr.BAD_INDEX]))
                return
            self._subscriptions.discard(idx)
            self._last_values.pop(idx, None)
            self._resp_queue.put(bytes([GpioResp.ACK]))
        else:
            self._resp_queue.put(
                bytes([GpioResp.ERR, GpioErr.BAD_OPCODE]))

    def _build_list_resp(self) -> bytes:
        """Build LIST_RESP message."""
        buf = bytearray([GpioResp.LIST_RESP, len(self._signals)])
        for sig in self._signals:
            name_bytes = sig.name.encode("ascii")
            buf.append(len(name_bytes))
            buf.extend(name_bytes)
            buf.append(sig.width)
            buf.append(sig.direction)
        return bytes(buf)

    # ----- OS thread -----

    def _recv_loop(self):
        """Blocking socket accept loop. Runs in an OS thread."""
        if os.path.exists(self._sock_path):
            os.unlink(self._sock_path)

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(self._sock_path)
        self._server_sock.listen(1)
        self._server_sock.settimeout(1.0)
        print(f"GpioBridge: listening on {self._sock_path}", flush=True)

        try:
            while self._running:
                try:
                    conn, _ = self._server_sock.accept()
                except socket.timeout:
                    continue
                print("GpioBridge: client connected", flush=True)
                self._handle_client(conn)
                print("GpioBridge: client disconnected", flush=True)
                # Clear subscriptions for next client
                self._subscriptions.clear()
                self._last_values.clear()
                # Re-accept (unlike QemuBridge, we keep going)
        finally:
            self._server_sock.close()
            if os.path.exists(self._sock_path):
                os.unlink(self._sock_path)

    def _handle_client(self, conn: socket.socket):
        """Process messages from a single client connection."""
        conn.settimeout(0.5)
        try:
            while self._running:
                # Drain async notifications first
                self._drain_notifications(conn)

                # Read opcode byte
                try:
                    data = conn.recv(1)
                except socket.timeout:
                    continue
                if not data:
                    break

                op = data[0]

                # Read remaining payload based on opcode
                if op == GpioOp.LIST:
                    payload = b""
                elif op in (GpioOp.GET, GpioOp.SUBSCRIBE, GpioOp.UNSUB):
                    payload = self._recv_exact(conn, 1)
                    if payload is None:
                        break
                elif op == GpioOp.SET:
                    payload = self._recv_exact(conn, 5)  # idx(1) + value(4)
                    if payload is None:
                        break
                else:
                    # Unknown opcode — still queue it for ERR response
                    payload = b""

                # Put on sim queue, wait for response
                self._req_queue.put((op, payload))
                resp = self._resp_queue.get()
                conn.sendall(resp)

        except (ConnectionError, OSError) as exc:
            logger.warning("GpioBridge: connection error: %s", exc)
        finally:
            conn.close()

    def _drain_notifications(self, conn: socket.socket):
        """Send all pending async VALUE notifications."""
        while True:
            try:
                data = self._notify_queue.get_nowait()
            except queue.Empty:
                break
            try:
                conn.sendall(data)
            except (ConnectionError, OSError):
                break

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes | None:
        """Receive exactly n bytes. Returns None on disconnect."""
        buf = bytearray()
        while len(buf) < n:
            if not self._running:
                return None
            try:
                chunk = sock.recv(n - len(buf))
            except socket.timeout:
                continue
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)
