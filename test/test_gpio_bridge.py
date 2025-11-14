# Integration tests for the GPIO bridge.
#
# Threading model (same as test_qemu_bridge.py):
#   - cocotb coroutine drives sim, polls bridge _req_queue
#   - OS thread runs fake GPIO client against the Unix socket

import os
import socket
import struct
import time
import threading

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from cocotemu.axi_master import CocotemuAxiMaster
from cocotemu.qemu_bridge import QemuBridge
from cocotemu.protocol import MmioOp, MmioRequest, WRITE_ACK
from cocotemu.gpio_bridge import GpioBridge
from cocotemu.gpio_protocol import GpioDir, GpioOp, GpioResp, GpioErr, GpioSignal
from cocotemu.gpio_client import GpioClient

GPIO_SOCK_PATH = "/tmp/cocotemu_gpio_test.sock"
QEMU_SOCK_PATH = "/tmp/cocotemu_qemu_gpio_test.sock"


def gpio_basic_client(results_out):
    """Test LIST, GET, SET, direction enforcement."""
    try:
        client = GpioClient(sock_path=GPIO_SOCK_PATH)
        client.connect()

        # LIST: should have 2 signals
        sigs = client.signals
        results_out.append(("list count == 2", len(sigs) == 2))
        results_out.append(("sig0 name == gpio_out",
                            sigs[0]["name"] == "gpio_out"))
        results_out.append(("sig0 dir == OUT",
                            sigs[0]["direction"] == GpioDir.OUT))
        results_out.append(("sig1 name == gpio_in",
                            sigs[1]["name"] == "gpio_in"))
        results_out.append(("sig1 dir == IN",
                            sigs[1]["direction"] == GpioDir.IN))

        # GET gpio_out (should be 0 after reset)
        val = client.get("gpio_out")
        results_out.append(("get gpio_out == 0", val == 0))

        # SET gpio_in to 0xAB
        client.set("gpio_in", 0xAB)
        results_out.append(("set gpio_in OK", True))

        # Direction enforcement: SET on output should fail
        try:
            client.set("gpio_out", 1)
            results_out.append(("set gpio_out rejected", False))
        except RuntimeError:
            results_out.append(("set gpio_out rejected", True))

        # Direction enforcement: GET on input should fail
        try:
            client.get("gpio_in")
            results_out.append(("get gpio_in rejected", False))
        except RuntimeError:
            results_out.append(("get gpio_in rejected", True))

        client.close()
    except Exception as exc:
        results_out.append((f"exception: {exc}", False))


def gpio_subscribe_client(results_out, axi_sock_path):
    """Test SUBSCRIBE: write reg0 via AXI → gpio_out changes → VALUE notification."""
    try:
        client = GpioClient(sock_path=GPIO_SOCK_PATH)
        client.connect()

        # Subscribe to gpio_out
        client.subscribe("gpio_out")
        results_out.append(("subscribe OK", True))

        # Write 0x42 to reg0 via a separate QEMU-side AXI write
        # (this changes regs[0] which drives gpio_out)
        axi_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        for _ in range(50):
            try:
                axi_sock.connect(axi_sock_path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.05)
        else:
            axi_sock.close()
            results_out.append(("axi connect", False))
            client.close()
            return

        req = MmioRequest(MmioOp.WRITE, size=4, addr=0x00, val=0x42)
        axi_sock.sendall(req.pack())
        ack = axi_sock.recv(1)
        results_out.append(("axi write ack", ack == WRITE_ACK))

        # Now wait for VALUE notification
        idx, val = client.recv_notification(timeout=5.0)
        results_out.append(("notify idx == 0", idx == 0))
        results_out.append(("notify val == 0x42", val == 0x42))

        # Verify GET agrees
        get_val = client.get("gpio_out")
        results_out.append(("get gpio_out == 0x42", get_val == 0x42))

        axi_sock.close()
        client.close()
    except Exception as exc:
        results_out.append((f"exception: {exc}", False))


def gpio_set_read_axi_client(results_out, axi_sock_path):
    """Test SET gpio_in → read reg3 via AXI → value matches."""
    try:
        client = GpioClient(sock_path=GPIO_SOCK_PATH)
        client.connect()

        # Drive gpio_in = 0x55
        client.set("gpio_in", 0x55)

        # Give sim time to latch (small delay, sim keeps advancing)
        time.sleep(0.2)

        # Read reg3 via AXI
        axi_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        for _ in range(50):
            try:
                axi_sock.connect(axi_sock_path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.05)
        else:
            axi_sock.close()
            results_out.append(("axi connect", False))
            client.close()
            return

        req = MmioRequest(MmioOp.READ, size=4, addr=0x0C)  # reg3
        axi_sock.sendall(req.pack())
        data = axi_sock.recv(4)
        val = int.from_bytes(data, "little")
        results_out.append(("reg3 == 0x55", (val & 0xFF) == 0x55))

        axi_sock.close()
        client.close()
    except Exception as exc:
        results_out.append((f"exception: {exc}", False))


async def reset_dut(dut, cycles=5):
    dut.aresetn.value = 0
    await ClockCycles(dut.aclk, cycles)
    dut.aresetn.value = 1
    await RisingEdge(dut.aclk)


def _make_gpio_signals(dut):
    return [
        GpioSignal("gpio_out", dut.gpio_out, 8, GpioDir.OUT),
        GpioSignal("gpio_in", dut.gpio_in, 8, GpioDir.IN),
    ]


@cocotb.test(timeout_time=30, timeout_unit="sec")
async def test_gpio_basic(dut):
    """LIST, GET, SET, direction enforcement."""
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    signals = _make_gpio_signals(dut)
    bridge = GpioBridge(signals, sock_path=GPIO_SOCK_PATH)
    cocotb.start_soon(bridge.start())

    # Wait for socket
    for _ in range(100):
        await Timer(1, units="us")
        if os.path.exists(GPIO_SOCK_PATH):
            break
    assert os.path.exists(GPIO_SOCK_PATH), "GPIO socket never appeared"

    results = []
    t = threading.Thread(target=gpio_basic_client, args=(results,))
    t.start()

    while t.is_alive():
        await Timer(100, units="ns")
    t.join()

    bridge.stop()

    for desc, passed in results:
        status = "PASS" if passed else "FAIL"
        dut._log.info(f"  [{status}] {desc}")

    assert results, "No results from client"
    failures = [desc for desc, passed in results if not passed]
    assert not failures, f"Failed: {failures}"


@cocotb.test(timeout_time=30, timeout_unit="sec")
async def test_gpio_subscribe(dut):
    """SUBSCRIBE → AXI write to reg0 → gpio_out changes → client gets VALUE."""
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    axi = CocotemuAxiMaster(dut)
    signals = _make_gpio_signals(dut)

    gpio_bridge = GpioBridge(signals, sock_path=GPIO_SOCK_PATH)
    qemu_bridge = QemuBridge(axi.execute, sock_path=QEMU_SOCK_PATH)

    cocotb.start_soon(gpio_bridge.start())
    cocotb.start_soon(qemu_bridge.start())

    for _ in range(100):
        await Timer(1, units="us")
        if (os.path.exists(GPIO_SOCK_PATH) and
                os.path.exists(QEMU_SOCK_PATH)):
            break

    assert os.path.exists(GPIO_SOCK_PATH), "GPIO socket never appeared"
    assert os.path.exists(QEMU_SOCK_PATH), "QEMU socket never appeared"

    results = []
    t = threading.Thread(target=gpio_subscribe_client,
                         args=(results, QEMU_SOCK_PATH))
    t.start()

    while t.is_alive():
        await Timer(100, units="ns")
    t.join()

    gpio_bridge.stop()
    qemu_bridge.stop()

    for desc, passed in results:
        status = "PASS" if passed else "FAIL"
        dut._log.info(f"  [{status}] {desc}")

    assert results, "No results from client"
    failures = [desc for desc, passed in results if not passed]
    assert not failures, f"Failed: {failures}"


@cocotb.test(timeout_time=30, timeout_unit="sec")
async def test_gpio_set_read_axi(dut):
    """SET gpio_in → read reg3 via AXI → value matches."""
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    axi = CocotemuAxiMaster(dut)
    signals = _make_gpio_signals(dut)

    gpio_bridge = GpioBridge(signals, sock_path=GPIO_SOCK_PATH)
    qemu_bridge = QemuBridge(axi.execute, sock_path=QEMU_SOCK_PATH)

    cocotb.start_soon(gpio_bridge.start())
    cocotb.start_soon(qemu_bridge.start())

    for _ in range(100):
        await Timer(1, units="us")
        if (os.path.exists(GPIO_SOCK_PATH) and
                os.path.exists(QEMU_SOCK_PATH)):
            break

    assert os.path.exists(GPIO_SOCK_PATH), "GPIO socket never appeared"
    assert os.path.exists(QEMU_SOCK_PATH), "QEMU socket never appeared"

    results = []
    t = threading.Thread(target=gpio_set_read_axi_client,
                         args=(results, QEMU_SOCK_PATH))
    t.start()

    while t.is_alive():
        await Timer(100, units="ns")
    t.join()

    gpio_bridge.stop()
    qemu_bridge.stop()

    for desc, passed in results:
        status = "PASS" if passed else "FAIL"
        dut._log.info(f"  [{status}] {desc}")

    assert results, "No results from client"
    failures = [desc for desc, passed in results if not passed]
    assert not failures, f"Failed: {failures}"
