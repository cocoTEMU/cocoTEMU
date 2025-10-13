# Qiling bridge: maps PL address space via MMIO hooks, communicates with cocotb via queues.

import logging
import queue
import threading

import cocotb
from cocotb.triggers import Timer

from .protocol import MmioOp, MmioRequest

logger = logging.getLogger(__name__)


class QilingBridge:
    """Bridges Qiling MMIO accesses to cocotb AXI transactions.

    Threading model:
      - Thread 1 (sim): cocotb coroutine polls _req_queue
      - Thread 2 (OS): ql.run() — Unicorn CPU loop, MMIO callbacks
                        block on _resp_queue waiting for AXI result
    """

    def __init__(self, ql, axi_handler, base_addr, size):
        """
        Args:
            ql: Qiling instance (must not be started yet)
            axi_handler: async callable(MmioRequest) -> int
            base_addr: PL base address in Qiling memory map
            size: Size of the PL MMIO region
        """
        self._ql = ql
        self._axi_handler = axi_handler
        self._base_addr = base_addr
        self._size = size
        self._req_queue = queue.Queue()
        self._resp_queue = queue.Queue()
        self._running = False

    def _mmio_read_cb(self, ql, addr, size):
        """Called by Qiling in the Unicorn thread on PL reads."""
        offset = addr - self._base_addr
        req = MmioRequest(op=MmioOp.READ, size=size, addr=offset)
        logger.debug("QilingBridge read: addr=0x%X size=%d", offset, size)
        self._req_queue.put(req)
        result = self._resp_queue.get()
        return result

    def _mmio_write_cb(self, ql, addr, size, value):
        """Called by Qiling in the Unicorn thread on PL writes."""
        offset = addr - self._base_addr
        req = MmioRequest(op=MmioOp.WRITE, size=size, addr=offset, val=value)
        logger.debug("QilingBridge write: addr=0x%X size=%d val=0x%X", offset, size, value)
        self._req_queue.put(req)
        self._resp_queue.get()  # wait for ack

    def _run_qiling(self):
        """Runs ql.run() in a daemon thread."""
        try:
            self._ql.run()
        except Exception:
            logger.exception("Qiling run() raised an exception")
        finally:
            self._running = False
            # Unblock the poll loop if it's waiting
            self._req_queue.put(None)

    async def start(self):
        """Start the bridge — call from a cocotb coroutine."""
        # Map MMIO region in Qiling
        self._ql.mem.map_mmio(
            self._base_addr,
            self._size,
            self._mmio_read_cb,
            self._mmio_write_cb,
        )

        self._running = True

        # Start Qiling in a daemon thread
        t = threading.Thread(target=self._run_qiling, daemon=True)
        t.start()
        logger.info("QilingBridge: Qiling thread started")

        # Poll loop: pick up requests from the queue, drive AXI, return results
        while self._running:
            req = await cocotb.external(self._req_queue.get)()
            if req is None:
                break
            result = await self._axi_handler(req)
            self._resp_queue.put(result)

        logger.info("QilingBridge: poll loop exited")

    def stop(self):
        """Signal the bridge to shut down."""
        self._running = False
        self._ql.emu_stop()
        self._req_queue.put(None)
