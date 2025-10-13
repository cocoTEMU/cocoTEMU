# Main cocotb test entry point for HW/SW co-simulation.
# 
# Drives clock + reset, initializes the AXI master, and starts the selected
# bridge (QEMU or Qiling) based on the COCOTEMU_BRIDGE environment variable

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

    COCOTEMU_BRIDGE  - "qemu" (default) or "qiling"
    COCOTEMU_SOCK    - Unix socket path (QEMU mode, default /tmp/cocotemu.sock)
    COCOTEMU_FW      - Firmware path (Qiling mode)
    COCOTEMU_ROOTFS  - Root filesystem path (Qiling mode)
    COCOTEMU_PL_BASE - PL base address as hex (Qiling mode, default 0x43C00000)
    COCOTEMU_PL_SIZE - PL region size as hex (Qiling mode, default 0x10000)
    """
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await _reset(dut)

    axi = CocotemuAxiMaster(dut)
    bridge_type = os.environ.get("COCOTEMU_BRIDGE", "qemu").lower()

    if bridge_type == "qemu":
        sock_path = os.environ.get("COCOTEMU_SOCK", "/tmp/cocotemu.sock")

        bridge = QemuBridge(axi.execute, sock_path=sock_path)
        logger.info("Starting QEMU bridge on %s", sock_path)
        await bridge.start()

    elif bridge_type == "qiling":
        from qiling import Qiling
        from .qiling_bridge import QilingBridge

        fw_path = os.environ["COCOTEMU_FW"]
        rootfs = os.environ["COCOTEMU_ROOTFS"]
        pl_base = int(os.environ.get("COCOTEMU_PL_BASE", "0x43C00000"), 16)
        pl_size = int(os.environ.get("COCOTEMU_PL_SIZE", "0x10000"), 16)

        ql = Qiling([fw_path], rootfs)

        async def axi_handler(req):
            return await axi.execute(req)

        bridge = QilingBridge(ql, axi_handler, pl_base, pl_size)
        logger.info("Starting Qiling bridge (fw=%s, base=0x%X)", fw_path, pl_base)
        await bridge.start()

    else:
        raise ValueError(f"Unknown COCOTEMU_BRIDGE: {bridge_type!r} (expected 'qemu' or 'qiling')")
