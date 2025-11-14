# GPIO bridge wire protocol — binary, compact.

import enum
from dataclasses import dataclass


class GpioDir(enum.IntEnum):
    IN = 0x00   # DUT input  — client can SET
    OUT = 0x01  # DUT output — client can GET/subscribe


class GpioOp(enum.IntEnum):
    LIST = 0x01
    GET = 0x02
    SET = 0x03
    SUBSCRIBE = 0x04
    UNSUB = 0x05


class GpioResp(enum.IntEnum):
    LIST_RESP = 0x81
    VALUE = 0x82
    ACK = 0x83
    ERR = 0x84


class GpioErr(enum.IntEnum):
    BAD_INDEX = 0x01
    WRONG_DIRECTION = 0x02
    BAD_OPCODE = 0x03


@dataclass
class GpioSignal:
    name: str
    handle: object  # cocotb signal handle
    width: int
    direction: GpioDir
