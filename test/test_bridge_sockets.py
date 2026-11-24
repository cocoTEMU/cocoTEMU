# Socket-level tests for QemuBridge session handling. Run with pytest, no simulator needed.

import os
import socket
import sys
import threading
import time
import types

try:
    import cocotb  # noqa: F401
except ImportError:
    # The socket thread never touches cocotb, only start() does.
    fake = types.ModuleType("cocotb")
    fake.log = None
    triggers = types.ModuleType("cocotb.triggers")
    triggers.Timer = None
    ext = types.ModuleType("cocotbext")
    axi = types.ModuleType("cocotbext.axi")
    axi.AxiLiteMaster = axi.AxiLiteBus = None
    for name, mod in (("cocotb", fake), ("cocotb.triggers", triggers),
                      ("cocotbext", ext), ("cocotbext.axi", axi)):
        sys.modules[name] = mod

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cocotemu.protocol import MmioOp, MmioRequest, WRITE_ACK  # noqa: E402
from cocotemu.qemu_bridge import QemuBridge  # noqa: E402


class Harness:
    """Runs the bridge socket thread with a stand-in for the sim side of the queues."""

    def __init__(self, tmp_path, **kwargs):
        self.sock_path = str(tmp_path / "b.sock")
        self.bridge = QemuBridge(None, sock_path=self.sock_path, **kwargs)
        self.bridge._CLIENT_TIMEOUT_S = 0.1
        self.bridge._running = True
        self.sim_stop = False
        self.threads = [
            threading.Thread(target=self.bridge._recv_loop, daemon=True),
            threading.Thread(target=self._sim, daemon=True),
        ]
        for t in self.threads:
            t.start()

    def _sim(self):
        import queue
        while not self.sim_stop:
            try:
                req = self.bridge._req_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if req is None:
                continue
            self.bridge._resp_queue.put(req.addr + 1 if req.op == MmioOp.READ else 0)

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        for _ in range(100):
            try:
                s.connect(self.sock_path)
                s.settimeout(2.0)
                return s
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.02)
        raise AssertionError("bridge socket never appeared")

    def close(self):
        self.sim_stop = True
        self.bridge._running = False


def write(sock, addr):
    sock.sendall(MmioRequest(MmioOp.WRITE, 4, addr, 1).pack())
    return sock.recv(1)


def read(sock, addr):
    sock.sendall(MmioRequest(MmioOp.READ, 4, addr).pack())
    return int.from_bytes(sock.recv(4), "little")


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_no_idle_exit_survives_long_pause(tmp_path):
    h = Harness(tmp_path, idle_exit_s=None)
    try:
        c = h.connect()
        assert write(c, 0) == WRITE_ACK
        time.sleep(0.8)  # many client timeouts, the old default would have ended the session
        assert read(c, 4) == 5
        assert h.bridge._running
    finally:
        h.close()


def test_idle_exit_still_ends_session_by_default(tmp_path):
    h = Harness(tmp_path, idle_exit_s=0.3)
    try:
        c = h.connect()
        assert write(c, 0) == WRITE_ACK
        assert wait_for(lambda: not h.bridge._running)
    finally:
        h.close()


def test_exit_on_disconnect_default_ends_session(tmp_path):
    h = Harness(tmp_path, idle_exit_s=None)
    try:
        c = h.connect()
        assert write(c, 0) == WRITE_ACK
        c.close()
        assert wait_for(lambda: not h.bridge._running)
    finally:
        h.close()


def test_second_client_is_served_when_not_exiting_on_disconnect(tmp_path):
    h = Harness(tmp_path, idle_exit_s=None, exit_on_disconnect=False)
    try:
        c1 = h.connect()
        assert write(c1, 0) == WRITE_ACK
        c1.close()
        time.sleep(0.4)
        assert h.bridge._running
        c2 = h.connect()
        assert read(c2, 8) == 9
    finally:
        h.close()
