# Cocotb testbench: write/read all 4 AXI-Lite registers, no bridge needed

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

from cocotemu.axi_master import CocotemuAxiMaster
from cocotemu.protocol import MmioOp, MmioRequest


async def reset_dut(dut, cycles=5):
    dut.aresetn.value = 0
    await ClockCycles(dut.aclk, cycles)
    dut.aresetn.value = 1
    await RisingEdge(dut.aclk)


@cocotb.test()
async def test_write_read_all_regs(dut):
    """Write a unique value to each register, then read back and verify."""
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    axi = CocotemuAxiMaster(dut)

    test_data = {
        0x00: 0xDEADBEEF,
        0x04: 0xCAFEBABE,
        0x08: 0x12345678,
        0x0C: 0xA5A5A5A5,
    }

    # Drive gpio_in so reg3[7:0] latches the expected low byte
    dut.gpio_in.value = 0xA5
    await RisingEdge(dut.aclk)

    # Write all registers
    for addr, val in test_data.items():
        req = MmioRequest(op=MmioOp.WRITE, size=4, addr=addr, val=val)
        await axi.execute(req)
        dut._log.info(f"Wrote 0x{val:08X} to 0x{addr:02X}")

    # Read back and verify
    # Note: reg3[7:0] is driven by gpio_in, so expected value reflects that
    for addr, expected in test_data.items():
        req = MmioRequest(op=MmioOp.READ, size=4, addr=addr)
        got = await axi.execute(req)
        dut._log.info(f"Read 0x{got:08X} from 0x{addr:02X} (expected 0x{expected:08X})")
        assert got == expected, f"Mismatch at 0x{addr:02X}: got 0x{got:08X}, expected 0x{expected:08X}"


@cocotb.test()
async def test_byte_strobe(dut):
    """Verify that partial writes (byte strobes) work correctly."""
    clock = Clock(dut.aclk, 10, units="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    axi = CocotemuAxiMaster(dut)

    # Write full word first
    await axi.execute(MmioRequest(op=MmioOp.WRITE, size=4, addr=0x00, val=0xFFFFFFFF))

    # Overwrite just the low byte
    await axi.execute(MmioRequest(op=MmioOp.WRITE, size=1, addr=0x00, val=0x42))

    got = await axi.execute(MmioRequest(op=MmioOp.READ, size=4, addr=0x00))
    dut._log.info(f"After byte write: 0x{got:08X}")
    assert got == 0xFFFFFF42, f"Byte strobe failed: got 0x{got:08X}"
