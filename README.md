# cocoTEMU

HW/SW co-simulation framework bridging firmware execution on the Zynq PS (via QEMU or Qiling) with PL RTL simulation (via cocotb + Verilator).

## Architecture

```
QEMU (Zynq PS firmware)                              Verilator (PL RTL)
  |                                                         |
  | mmio_stub_msg_hdr (18 bytes, <BBQQ)                     |
  |--- chardev Unix socket ---> QemuBridge                  |
                                   |                        |
                                   v                        |
                             CocotemuAxiMaster              |
                             (cocotbext-axi)                |
                                   |                        |
                                   +---- AXI4-Lite -------->| DUT
                                   |<--- response ----------|
                                   |                        |
                             response sent back             |
                             over socket to QEMU            |

Alternative PS path (Qiling mode):
  Qiling (firmware emu) --MMIO hooks--> QilingBridge --+--> same AXI path
```

The QEMU MMIO stub (`QEMU-MMIO-stub/`) sends 18-byte packed read/write messages over a chardev Unix socket. cocoTEMU receives these, translates them to AXI4-Lite bus transactions, and drives them into a Verilator-simulated HDL design via cocotb.

## Wire Protocol

Matches `mmio_stub_msg_hdr` from `QEMU-MMIO-stub/src/include/hw/misc/mmio-stub.h`:

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 1 | op | 0=READ, 1=WRITE |
| 1 | 1 | size | Access size: 1, 2, 4, or 8 bytes |
| 2 | 8 | addr | Address (uint64 LE) |
| 10 | 8 | val | Value (uint64 LE, write only) |

Total: 18 bytes, `struct.pack("<BBQQ", op, size, addr, val)`

Responses: read returns `size` bytes of data (LE); write returns 1-byte ack (`0x01`).
