# Wrapper around cocotbext-axi AxiLiteMaster for MMIO request execution

from cocotbext.axi import AxiLiteMaster, AxiLiteBus

from .protocol import MmioOp, MmioRequest


class CocotemuAxiMaster:
    """Drives AXI4-Lite transactions from MmioRequest objects."""

    def __init__(self, dut, prefix="s_axil", clock_signal="aclk"):
        bus = AxiLiteBus.from_prefix(dut, prefix)
        clk = getattr(dut, clock_signal)
        self._master = AxiLiteMaster(bus, clk)

    async def execute(self, req: MmioRequest) -> int:
        """Issue an AXI read or write and return the result.

        For reads, returns the read data as an int.
        For writes, performs the write and returns 0.
        """
        if req.op == MmioOp.READ:
            data = await self._master.read(req.addr, req.size)
            return int.from_bytes(data.data, byteorder="little")
        else:
            val_bytes = req.val.to_bytes(req.size, byteorder="little")
            await self._master.write(req.addr, val_bytes)
            return 0
