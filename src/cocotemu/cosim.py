# Main cocotb test entry point for HW/SW co-simulation.
#
# Drives clock + reset, initializes the AXI master, and starts the
# QEMU bridge based on environment variables.

import os
import logging

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

from .axi_master import CocotemuAxiMaster
from .qemu_bridge import QemuBridge
from .gpio_bridge import GpioBridge
from .gpio_protocol import GpioDir, GpioSignal

logger = logging.getLogger(__name__)


async def _reset(dut, cycles=5):
    dut.aresetn.value = 0
    await ClockCycles(dut.aclk, cycles)
    dut.aresetn.value = 1
    await RisingEdge(dut.aclk)


@cocotb.test()
async def cosim_test(dut):
    """Co-simulation entry point. Configure via environment variables:

    COCOTEMU_SOCK      - Unix socket path (default /tmp/cocotemu.sock)
    COCOTEMU_GPIO_SOCK - GPIO socket path (default /tmp/cocotemu_gpio.sock)
    COCOTEMU_GPIO      - set to "0" to disable GPIO bridge
    """
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await _reset(dut)

    axi = CocotemuAxiMaster(dut)
    sock_path = os.environ.get("COCOTEMU_SOCK", "/tmp/cocotemu.sock")

    bridge = QemuBridge(axi.execute, sock_path=sock_path)
    logger.info("Starting QEMU bridge on %s", sock_path)

    # --- GPIO bridge ---
    if os.environ.get("COCOTEMU_GPIO", "1") != "0":
        gpio_signals = _detect_gpio(dut)
        if gpio_signals:
            gpio_sock = os.environ.get("COCOTEMU_GPIO_SOCK",
                                       "/tmp/cocotemu_gpio.sock")
            gpio_bridge = GpioBridge(gpio_signals, sock_path=gpio_sock)
            logger.info("Starting GPIO bridge on %s with %d signals",
                        gpio_sock, len(gpio_signals))
            cocotb.start_soon(gpio_bridge.start())

    await bridge.start()


def _detect_gpio(dut) -> list[GpioSignal]:
    """Auto-detect GPIO ports on the DUT."""
    signals = []
    # Convention: gpio_out* = output, gpio_in* = input
    for name, direction in [("gpio_out", GpioDir.OUT),
                            ("gpio_in", GpioDir.IN)]:
        try:
            handle = getattr(dut, name)
            width = len(handle)
            signals.append(GpioSignal(name, handle, width, direction))
            logger.info("Detected GPIO: %s width=%d dir=%s",
                        name, width, direction.name)
        except AttributeError:
            pass
    return signals
