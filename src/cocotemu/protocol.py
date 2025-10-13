# Wire protocol matching QEMU mmio_stub_msg_hdr (18 bytes, little-endian)

import enum
import struct
from dataclasses import dataclass

HDR_FMT = "<BBQQ"
HDR_SIZE = struct.calcsize(HDR_FMT)  # 18

WRITE_ACK = b"\x01"


class MmioOp(enum.IntEnum):
    READ = 0
    WRITE = 1


@dataclass
class MmioRequest:
    op: MmioOp
    size: int      # 1, 2, 4, or 8
    addr: int      # uint64
    val: int = 0   # uint64 (only meaningful for WRITE)

    def pack(self) -> bytes:
        return struct.pack(HDR_FMT, self.op, self.size, self.addr, self.val)

    @classmethod
    def unpack(cls, data: bytes) -> "MmioRequest":
        op, size, addr, val = struct.unpack(HDR_FMT, data)
        return cls(op=MmioOp(op), size=size, addr=addr, val=val)
