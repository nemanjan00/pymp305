"""Wire-protocol framing for the ISDT MP305 (MP305A / MP305B) over USB-HID.

Transcribed from the official WebLink web app (see ../../PROTOCOL.md). All framing
quirks (length byte, 0xAA stuffing, checksum) are reproduced exactly so the
checksums match what the firmware expects.
"""
from __future__ import annotations

from dataclasses import dataclass

# USB-HID identity (from HIDDeviceManager.connect)
VENDOR_ID = 0x28E9          # GigaDevice
HID_USAGE_PAGE = 0x01
HID_USAGE = 0x04
REPORT_ID = 0x01
REPORT_SIZE = 64            # output report payload size (zero padded)

FRAME_START = 0xAA
GROUP_ID = 0x12

# ---- command bytes -------------------------------------------------------
CMD_HW_INFO        = 0xE0   # -> 0xE1
CMD_STATE_INFO     = 0xC2   # -> 0xC3
CMD_REALTIME       = 0xBD   # -> 0xC3
CMD_SYS_GET        = 0xC4   # -> 0xC5
CMD_SYS_SET        = 0xC6   # -> 0xC7
CMD_CONTROL        = 0xC8   # -> 0xC9
CMD_CHARGE_INFO    = 0xEC   # -> 0xED
CMD_CHARGE_SEARCH  = 0xEA   # -> 0xEB
CMD_CHARGE_CONTROL = 0xEE   # -> 0xEF
CMD_SET_LANGUAGE   = 0xA2   # -> 0xA3

# response command bytes
RESP_HW_INFO        = 0xE1
RESP_STATE          = 0xC3
RESP_SYS            = 0xC5
RESP_SYS_SET        = 0xC7
RESP_CONTROL        = 0xC9
RESP_CHARGE         = 0xED
RESP_CHARGE_INFO    = 0xEB
RESP_PDO            = 0xD1
RESP_PDO_CONNECT    = 0xE9
RESP_PROGRAM_LIST   = 0xD5
RESP_PROGRAM_STEPS  = 0xD9
RESP_PROGRAM_STATE  = 0xDF
RESP_PROGRAM_CONNECT = 0xE3

# raw multi-byte payloads for boot/reboot (cmd byte + extra data bytes)
BOOT_PAYLOAD   = bytes([0xF0, 0xAC])   # jump to bootloader
REBOOT_PAYLOAD = bytes([0xFC, 0xCA])   # reboot device


def _add_0xAA(data: list[int]) -> list[int]:
    """Double every 0xAA in the DATA region (index > 5), matching Cmd.add0xAA."""
    out: list[int] = []
    for i, b in enumerate(data):
        out.append(b)
        if b == FRAME_START and i > 5:
            out.append(FRAME_START)
    return out


def _process_hex_array(arr: list[int]) -> list[int]:
    """Set the length byte [0] and checksum byte [last]; matches Cmd.processHexArray."""
    a = list(arr)
    a[0] = (len(a) - 1) & 0xFF

    total = 0
    prev_is_aa = False
    for i in range(2, len(a) - 1):          # index 2 .. second-to-last
        v = a[i]
        cur_is_aa = v == FRAME_START
        if cur_is_aa and prev_is_aa:        # consecutive 0xAA counted once
            prev_is_aa = False
            continue
        total += v
        prev_is_aa = cur_is_aa
    a[-1] = total & 0xFF

    if a[-1] == FRAME_START:                # avoid a checksum that looks like a marker
        a.append(FRAME_START)
        a[0] = (a[0] + 1) & 0xFF
    return a


def build_frame(cmd: int, payload: bytes = b"") -> bytes:
    """Build a complete HID frame (without the report-ID byte) for `cmd`+`payload`."""
    body = [0x00, FRAME_START, GROUP_ID, (1 + len(payload)) & 0xFF, cmd, *payload, 0x00]
    body = _add_0xAA(body)
    return bytes(_process_hex_array(body))


def build_report(cmd: int, payload: bytes = b"", report_size: int = REPORT_SIZE) -> bytes:
    """Build the full bytes to hand to a hidapi `write()`:  report-id + frame + padding."""
    frame = build_frame(cmd, payload)
    buf = bytes([REPORT_ID]) + frame
    if report_size and len(buf) < report_size + 1:
        buf += b"\x00" * (report_size + 1 - len(buf))
    return buf


@dataclass
class Frame:
    cmd: int
    payload: bytes      # bytes after the cmd byte, de-stuffed
    values: bytes       # full de-stuffed frame: [N, 0xAA, 0x12, L, cmd, ...]


def parse_report(raw: bytes) -> Frame | None:
    """De-stuff and parse a raw HID input report (may or may not include the report-ID).

    Returns None if no valid [0xAA, 0x12] header is found.
    """
    buf = bytes(raw)
    # Locate the 0xAA,0x12 header; the byte before it is the length byte N.
    start = -1
    for i in range(len(buf) - 1):
        if buf[i] == FRAME_START and buf[i + 1] == GROUP_ID:
            start = i - 1
            break
    if start < 0:
        return None
    frame = list(buf[start:])

    # De-stuff consecutive 0xAA (keep first), decrement N for each dropped byte.
    values: list[int] = []
    prev_is_aa = False
    for b in frame:
        cur_is_aa = b == FRAME_START
        if cur_is_aa and prev_is_aa:
            if values:
                values[0] = (values[0] - 1) & 0xFF
        else:
            values.append(b)
        prev_is_aa = cur_is_aa

    if len(values) < 6:
        return None
    cmd = values[4]
    payload = bytes(values[5:-1])   # exclude the trailing checksum byte
    return Frame(cmd=cmd, payload=payload, values=bytes(values))


# ---- BLE transport ------------------------------------------------------
# Over BLE GATT the same command set is used, but frames drop the
# length/0xAA/checksum wrapper: commands written to characteristic AF01 are just
# [0x12, cmd, ...payload]; AF02 carries the binding/hardware-info handshake whose
# frames start directly with the command byte (0x18/0x19/0xE0/0xE1).
BLE_PARSE_INDEX = 2          # payload index for AF01 data frames ([0x12, cmd, ...])
BLE_SERVICE_AF00 = "0000af00-0000-1000-8000-00805f9b34fb"
BLE_CHAR_AF01    = "0000af01-0000-1000-8000-00805f9b34fb"   # command + notify
BLE_CHAR_AF02    = "0000af02-0000-1000-8000-00805f9b34fb"   # binding / hw-info / chunked
BLE_SERVICE_FEE0 = "0000fee0-0000-1000-8000-00805f9b34fb"   # OTA
BLE_CHAR_FEE1    = "0000fee1-0000-1000-8000-00805f9b34fb"
BLE_NAME_PREFIX  = "0000MP305"
BLE_CMD_BIND     = 0x18      # -> 0x19
BLE_CMD_BIND_RESP = 0x19


def build_ble_frame(cmd: int, payload: bytes = b"") -> bytes:
    """A BLE AF01 command frame: [0x12, cmd, ...payload] (no length/checksum)."""
    return bytes([GROUP_ID, cmd]) + bytes(payload)


def build_ble_binding(uuid16: bytes, fast_binding: int = 0, status: int = 0) -> bytes:
    """The AF02 binding packet: [0x18, *uuid(16), fastBinding, status]."""
    if len(uuid16) != 16:
        raise ValueError("binding uuid must be 16 bytes")
    return bytes([BLE_CMD_BIND, *uuid16, fast_binding & 0xFF, status & 0xFF])


def parse_ble_notification(data: bytes) -> Frame | None:
    """Parse a BLE notification.

    AF01 data frames start with 0x12 → cmd at index 1, payload at index 2.
    AF02 handshake frames (binding 0x19, hardware info 0xE1, …) start with the
    command byte itself → cmd at index 0, payload at index 1.
    """
    b = bytes(data)
    if len(b) < 1:
        return None
    if b[0] == GROUP_ID and len(b) >= 2:
        return Frame(cmd=b[1], payload=b[2:], values=b)
    return Frame(cmd=b[0], payload=b[1:], values=b)
