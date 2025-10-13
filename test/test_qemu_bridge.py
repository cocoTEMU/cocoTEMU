# Replicate QEMU to check the logic
# replicate the mmio_stub_msg_hdr driver

import os
import socket
import time
import threading

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from cocotemu.axi_master import CocotemuAxiMaster
from cocotemu.qemu_bridge import QemuBridge
from cocotemu.protocol import MmioOp, MmioRequest, WRITE_ACK

SOCK_PATH = "/tmp/cocotemu_test.sock"


def fake_qemu_client(results_out):
    """
    Blocking function that acts as QEMU's mmio-stub chardev.

    Connects to the Unix socket, sends write+read requests using the
    18-byte packed protocol.  Appends (description, passed) to results_out.
    """
    # Retry connect — the bridge needs a moment to bind
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    for attempt in range(50):
        try:
            sock.connect(SOCK_PATH)
            break
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.05)
    else:
        sock.close()
        results_out.append(("connect", False))
        return

    try:
        # --- Write 0xDEADBEEF to reg0 (offset 0x00) ---
        req = MmioRequest(MmioOp.WRITE, size=4, addr=0x00, val=0xDEADBEEF)
        sock.sendall(req.pack())
        ack = sock.recv(1)
        results_out.append(("write reg0 ack", ack == WRITE_ACK))

        # --- Write 0xCAFEBABE to reg1 (offset 0x04) ---
        req = MmioRequest(MmioOp.WRITE, size=4, addr=0x04, val=0xCAFEBABE)
        sock.sendall(req.pack())
        ack = sock.recv(1)
        results_out.append(("write reg1 ack", ack == WRITE_ACK))

        # --- Read back reg0 ---
        req = MmioRequest(MmioOp.READ, size=4, addr=0x00)
        sock.sendall(req.pack())
        data = sock.recv(4)
        val = int.from_bytes(data, "little")
        results_out.append(("read reg0 == 0xDEADBEEF", val == 0xDEADBEEF))

        # --- Read back reg1 ---
        req = MmioRequest(MmioOp.READ, size=4, addr=0x04)
        sock.sendall(req.pack())
        data = sock.recv(4)
        val = int.from_bytes(data, "little")
        results_out.append(("read reg1 == 0xCAFEBABE", val == 0xCAFEBABE))

        # --- Write then read reg2 with 16-bit access ---
        req = MmioRequest(MmioOp.WRITE, size=2, addr=0x08, val=0x1234)
        sock.sendall(req.pack())
        ack = sock.recv(1)
        results_out.append(("write reg2 half ack", ack == WRITE_ACK))

        req = MmioRequest(MmioOp.READ, size=4, addr=0x08)
        sock.sendall(req.pack())
        data = sock.recv(4)
        val = int.from_bytes(data, "little")
        results_out.append(("read reg2 after half-write == 0x1234", val == 0x00001234))

    except Exception as exc:
        results_out.append((f"exception: {exc}", False))
    finally:
        sock.close()


async def reset_dut(dut, cycles=5):
    dut.aresetn.value = 0
    await ClockCycles(dut.aclk, cycles)
    dut.aresetn.value = 1
    await RisingEdge(dut.aclk)


@cocotb.test(timeout_time=30, timeout_unit="sec")
async def test_qemu_bridge_roundtrip(dut):
    """Full roundtrip: fake QEMU client -> socket -> QemuBridge -> AXI -> DUT -> back."""

    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    axi = CocotemuAxiMaster(dut)
    bridge = QemuBridge(axi.execute, sock_path=SOCK_PATH)

    # Start bridge in background (poll loop runs as cocotb coroutine)
    cocotb.start_soon(bridge.start())

    # Give the daemon thread time to bind the socket
    # (Timer advances sim time, releasing the GIL so the OS thread can run)
    for _ in range(100):
        await Timer(1, units="us")
        if os.path.exists(SOCK_PATH):
            break

    assert os.path.exists(SOCK_PATH), f"Socket {SOCK_PATH} never appeared"
    dut._log.info("Socket appeared, starting fake client")

    # Run fake client in a plain thread
    results = []
    client_thread = threading.Thread(target=fake_qemu_client, args=(results,))
    client_thread.start()

    # Keep sim time advancing while client thread runs
    while client_thread.is_alive():
        await Timer(100, units="ns")

    client_thread.join()

    # Stop the bridge
    bridge.stop()

    # Report and assert
    for desc, passed in results:
        status = "PASS" if passed else "FAIL"
        dut._log.info(f"  [{status}] {desc}")

    assert results, "No results from fake client (did it crash?)"
    failures = [desc for desc, passed in results if not passed]
    assert not failures, f"Failed checks: {failures}"
