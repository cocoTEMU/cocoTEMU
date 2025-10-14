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

logger = logging.getLogger(__name__)


async def _reset(dut, cycles=5):
    dut.aresetn.value = 0
    await ClockCycles(dut.aclk, cycles)
    dut.aresetn.value = 1
    await RisingEdge(dut.aclk)


@cocotb.test()
async def cosim_test(dut):
    """Co-simulation entry point. Configure via environment variables:

    COCOTEMU_SOCK - Unix socket path (default /tmp/cocotemu.sock)
    """
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await _reset(dut)

    axi = CocotemuAxiMaster(dut)
    sock_path = os.environ.get("COCOTEMU_SOCK", "/tmp/cocotemu.sock")

    bridge = QemuBridge(axi.execute, sock_path=sock_path)
    logger.info("Starting QEMU bridge on %s", sock_path)
    await bridge.start()
