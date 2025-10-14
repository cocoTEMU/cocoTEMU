# QEMU bridge: Unix socket server that translates mmio_stub messages to AXI transactions

import os
import queue
import socket
import threading
import logging

import cocotb
from cocotb.triggers import Timer

from .protocol import HDR_SIZE, MmioOp, MmioRequest, WRITE_ACK

logger = logging.getLogger(__name__)


class QemuBridge:
    """Listens on a Unix socket for QEMU mmio-stub connections.

    Threading model (queue-based, avoids @cocotb.function deadlocks):
      - Thread 1 (sim): cocotb poll loop picks requests from _req_queue,
        drives AXI, puts results on _resp_queue
      - Thread 2 (OS): _recv_loop does socket recv, puts MmioRequest on
        _req_queue, blocks on _resp_queue for the result, sends response
    """

    def __init__(self, axi_handler, sock_path="/tmp/cocotemu.sock"):
        """
        Args:
            axi_handler: async callable(MmioRequest) -> int
            sock_path: Unix socket path to listen on
        """
        self._axi_handler = axi_handler
        self._sock_path = sock_path
        self._running = False
        self._server_sock = None
        self._req_queue = queue.Queue()
        self._resp_queue = queue.Queue()

    async def start(self, poll_ns=100):
        """Start the bridge — call from a cocotb coroutine.

        Args:
            poll_ns: sim-time polling interval in nanoseconds (default 100).
        """
        self._running = True

        # Start recv loop in a daemon thread
        t = threading.Thread(target=self._recv_loop, daemon=True)
        t.start()

        cocotb.log.info("QemuBridge: poll loop started (poll_ns=%d)", poll_ns)

        # Sim-time poll loop: check queue each cycle, drive AXI, return results
        while self._running:
            try:
                req = self._req_queue.get_nowait()
            except queue.Empty:
                await Timer(poll_ns, units="ns")
                continue
            if req is None:
                break
            cocotb.log.info("QemuBridge: AXI %s addr=0x%X size=%d val=0x%X",
                            req.op.name, req.addr, req.size, req.val)
            result = await self._axi_handler(req)
            cocotb.log.info("QemuBridge: AXI result=0x%X", result)
            self._resp_queue.put(result)

        cocotb.log.info("QemuBridge: poll loop exited")

    def stop(self):
        """Signal the bridge to shut down."""
        self._running = False
        # Unblock the poll loop
        self._req_queue.put(None)
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass

    def _recv_loop(self):
        """Blocking socket accept + recv loop. Runs in an OS thread."""
        # Clean up stale socket
        if os.path.exists(self._sock_path):
            os.unlink(self._sock_path)

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(self._sock_path)
        self._server_sock.listen(1)
        self._server_sock.settimeout(1.0)
        print(f"QemuBridge: listening on {self._sock_path}", flush=True)

        try:
            while self._running:
                try:
                    conn, _ = self._server_sock.accept()
                except socket.timeout:
                    continue
                print("QemuBridge: client connected", flush=True)
                self._handle_client(conn)
                print("QemuBridge: client disconnected", flush=True)
                # Signal the poll loop that the session is over
                self._running = False
                self._req_queue.put(None)
        finally:
            self._server_sock.close()
            if os.path.exists(self._sock_path):
                os.unlink(self._sock_path)

    def _handle_client(self, conn: socket.socket):
        """Process messages from a single QEMU client connection."""
        conn.settimeout(2.0)
        msg_count = 0
        idle_timeouts = 0
        max_idle = 3  # exit after 3 consecutive timeouts (6s idle)
        try:
            while self._running:
                data = self._recv_exact(conn, HDR_SIZE)
                if data is None:
                    print("QemuBridge: recv returned None (client closed)", flush=True)
                    break
                if data == b"":
                    # Timeout with no data — check for idle
                    idle_timeouts += 1
                    if msg_count > 0 and idle_timeouts >= max_idle:
                        print(f"QemuBridge: idle for {idle_timeouts * 2}s after "
                              f"{msg_count} messages, assuming firmware done", flush=True)
                        break
                    continue
                idle_timeouts = 0
                req = MmioRequest.unpack(data)
                msg_count += 1
                print(f"QemuBridge: rx #{msg_count}: {req}", flush=True)

                # Put request on queue, wait for sim thread to process it
                self._req_queue.put(req)
                result = self._resp_queue.get()

                if req.op == MmioOp.READ:
                    resp = result.to_bytes(req.size, byteorder="little")
                    conn.sendall(resp)
                else:
                    conn.sendall(WRITE_ACK)
        except (ConnectionError, OSError) as exc:
            logger.warning("QemuBridge: connection error: %s", exc)
        finally:
            conn.close()

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes | None:
        """Receive exactly n bytes.

        Returns:
            bytes: exactly n bytes of data
            b"": timeout with no partial data (caller can retry)
            None: client disconnected
        """
        buf = bytearray()
        while len(buf) < n:
            if not self._running:
                return None
            try:
                chunk = sock.recv(n - len(buf))
            except socket.timeout:
                if len(buf) == 0:
                    return b""  # no partial data, safe to return timeout
                continue  # partial message, keep trying
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)
