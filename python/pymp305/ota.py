"""Firmware parsing + OTA for the ISDT MP305.

Two firmware formats, matching the WebLink app:

* **Encrypted ``.bin``** (USB-HID / UART OTA path) — :class:`Firmware`. A 32-byte header
  carries the (plaintext) ``encryption_key`` and initial ``file_checksum``; the body is a
  reversible XOR keystream derived entirely from those two values. So these images are
  fully decryptable with nothing external — handy for inspection and for re-flashing a
  known-good image. Obtain official images from ISDT's OTA endpoints (see
  ``tools/fetch_firmware.py``); the protocol has no flash-read, so you cannot dump the
  unit's current firmware.

* **Intel HEX** (BLE FEE1 OTA path) — :class:`IntelHexFirmware`.

⚠️  The flashing routines (:meth:`MP305.flash` etc.) write to the device's application
region and are **experimental and untested against hardware**. A failed *app* write is
normally recoverable (the bootloader is untouched and you just re-flash), but treat OTA as
risky. They refuse to run unless called with ``confirm=True``.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field

from . import protocol as P


def _u32le(b: bytes, off: int = 0) -> int:
    return b[off] | (b[off + 1] << 8) | (b[off + 2] << 16) | (b[off + 3] << 24)


def xor_crypt(body: bytes, encryption_key: int, file_checksum: int) -> bytes:
    """The MP305 firmware keystream (its own inverse — same call encrypts or decrypts).

    For each 4-byte little-endian word: ``out = word XOR ks`` then
    ``ks = ((ks + key) mod 2^32) XOR key``, starting from ``ks = file_checksum``.
    """
    key = encryption_key & 0xFFFFFFFF
    ks = file_checksum & 0xFFFFFFFF
    out = bytearray()
    for off in range(0, len(body) - 3, 4):
        word = _u32le(body, off)
        dec = (word ^ ks) & 0xFFFFFFFF
        ks = ((ks + key) & 0xFFFFFFFF) ^ key
        out += struct.pack("<I", dec)
    return bytes(out)


@dataclass
class Firmware:
    """A decrypted MP305 ``.bin`` firmware image (HID/UART OTA)."""
    encryption_key: int = 0
    file_checksum: int = 0
    app_storage_offset: int = 0
    data_storage_offset: int = 0
    app_size: int = 0
    data_size: int = 0
    original_baud: int = 0
    rapid_baud: int = 0
    firmware_data: bytes = b""        # decrypted app(+data) payload
    checksum_ok: bool = False
    device_id: int = 0
    hw_major: int = 0
    hw_minor: int = 0
    HEADER_SIZE = 32

    @classmethod
    def parse(cls, data: bytes, clear_app_flag: bool = True) -> "Firmware":
        b = bytes(data)
        h = [_u32le(b, i * 4) for i in range(8)]
        fw = cls(
            encryption_key=h[0], file_checksum=h[1],
            app_storage_offset=h[2], data_storage_offset=h[3],
            app_size=h[4], data_size=h[5],
            original_baud=h[6], rapid_baud=h[7],
        )
        body = b[cls.HEADER_SIZE: cls.HEADER_SIZE + fw.app_size + fw.data_size]
        decrypted = bytearray(xor_crypt(body, fw.encryption_key, fw.file_checksum))
        # checksum over the decrypted words must equal file_checksum
        total = 0
        for off in range(0, len(decrypted) - 3, 4):
            total = (total + _u32le(decrypted, off)) & 0xFFFFFFFF
        fw.checksum_ok = (total == (fw.file_checksum & 0xFFFFFFFF))
        fw._extract_device_info(decrypted)
        if clear_app_flag:
            fw._clear_app_flag(decrypted)
        fw.firmware_data = bytes(decrypted)
        return fw

    def _extract_device_info(self, decrypted: bytearray):
        # mirror FirmwareParser: scan 4 candidate table addresses for the 0xAA55CC33 marker
        for i in range(4):
            ref = 28 + i * 4
            if ref + 4 > len(decrypted):
                continue
            table_add = _u32le(decrypted, ref) - self.app_storage_offset
            if 0 <= table_add < self.app_size and table_add + 16 <= len(decrypted):
                if _u32le(decrypted, table_add) == 0xAA55CC33:
                    self.device_id = int.from_bytes(decrypted[table_add + 4: table_add + 12], "little")
                    self.hw_major = decrypted[table_add + 12]
                    self.hw_minor = decrypted[table_add + 13]
                    return

    def _clear_app_flag(self, decrypted: bytearray):
        for i in range(4):
            base = 0x1C + 4 * i
            if base + 4 > len(decrypted):
                continue
            off = _u32le(decrypted, base) - self.app_storage_offset
            if 0 <= off < self.app_size and off + 4 <= len(decrypted):
                decrypted[off:off + 4] = b"\xFF\xFF\xFF\xFF"
                return

    # ---- HID OTA frame builders (transcribed from Cmd.js) ----------------
    def erase_app_frame(self) -> bytes:
        payload = b"\x00" + struct.pack("<II", self.app_storage_offset, self.app_size)
        return P.build_frame(0xF2, payload)

    def write_app_frame(self, write_bit: int) -> bytes:
        data = self.firmware_data[write_bit: write_bit + 128]
        addr = self.app_storage_offset + write_bit
        return P.build_frame(0xF4, b"\x00" + struct.pack("<I", addr) + data)

    def app_checksum(self) -> int:
        total = 0
        for off in range(0, self.app_size - 3, 4):
            total = (total + _u32le(self.firmware_data, off)) & 0xFFFFFFFF
        return total

    def checksum_app_frame(self) -> bytes:
        payload = b"\x35\x00" + struct.pack("<III", self.app_storage_offset,
                                            self.app_size, self.app_checksum())
        return P.build_frame(0xF6, payload)

    def write_data_frame(self, write_bit: int) -> bytes:
        chunk = self.firmware_data[self.app_size + write_bit: self.app_size + write_bit + 128]
        addr = self.data_storage_offset + write_bit
        payload = (b"\x05\x00\x00\x00" + struct.pack("<I", addr)
                   + struct.pack("<I", len(chunk)) + b"\x00" * 16 + chunk)
        return P.build_frame(0x20, payload)

    def data_checksum(self) -> int:
        total = 0
        for i in range(self.data_size):
            total = (total + self.firmware_data[self.app_size + i]) & 0xFFFFFFFF
        return total

    def checksum_data_frame(self) -> bytes:
        payload = (b"\x06\x00\x00\x00" + struct.pack("<II", self.data_storage_offset,
                                                     self.data_size) + struct.pack("<I", self.data_checksum()))
        return P.build_frame(0x20, payload)


# ---- HID fragmentation (sendDataInChunks) -------------------------------
def fragment_frame(frame: bytes, package_size: int = 61) -> list[bytes]:
    """Split a >63-byte frame into HID output reports exactly like the WebLink app:
    chunk 0 is the first 61 bytes; later chunks are prefixed with 0x00; every chunk's
    first byte is overwritten with (chunk_len-1). Each returned report still needs the
    report-id prepended before writing."""
    reports = []
    n = max(1, -(-len(frame) // package_size))
    for i in range(n):
        start, end = i * package_size, min((i + 1) * package_size, len(frame))
        chunk = bytearray(frame[start:end]) if i == 0 else bytearray(b"\x00") + frame[start:end]
        chunk[0] = (len(chunk) - 1) & 0xFF
        reports.append(bytes(chunk))
    return reports


def parse_stat_address(values: bytes, index: int) -> int:
    """The written-address echo in a 0xF5 ack (Cmd.parseStatAddress)."""
    return (values[index + 2] | (values[index + 3] << 8)
            | (values[index + 4] << 16) | (values[index + 5] << 24)) & 0xFFFFFFFF


# ---- Intel HEX firmware (BLE FEE1 OTA) ----------------------------------
@dataclass
class IntelHexFirmware:
    """Parsed Intel-HEX firmware for the BLE FEE1 OTA path (BFirmwareParser)."""
    firmware_data: bytes = b""
    min_addr: int = -1
    max_addr: int = -1
    total_len: int = 0
    checksum: int = 0
    send_size: int = 132
    add_multiple: int = 4
    records: list = field(default_factory=list)

    @classmethod
    def parse(cls, data: bytes | str) -> "IntelHexFirmware":
        text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else data
        fw = cls()
        base = 0
        max_addr_data_len = 0
        for line in text.splitlines():
            if not line or line[0] != ":":
                continue
            ln = int(line[1:3], 16)
            offset = int(line[3:7], 16)
            rtype = int(line[7:9], 16)
            payload = bytes(int(line[9 + 2 * k: 11 + 2 * k], 16) for k in range(ln))
            if rtype == 0x00:                      # data
                addr = base + offset
                if fw.min_addr < 0 or addr < fw.min_addr:
                    fw.min_addr = addr if fw.min_addr < 0 else min(addr, fw.min_addr)
                if fw.max_addr < 0:
                    fw.max_addr, max_addr_data_len = addr, len(payload)
                else:
                    fw.max_addr = max(addr, fw.max_addr)
                    if addr == fw.max_addr:
                        max_addr_data_len = len(payload)
                fw.records.append((addr, payload))
                fw.total_len += len(payload)
            elif rtype == 0x01:                    # EOF
                break
            elif rtype == 0x02:                    # extended segment address
                base = ((payload[0] << 8) | payload[1]) << 4
            elif rtype == 0x04:                    # extended linear address
                base = ((payload[0] << 8) | payload[1]) << 16
            # 0x03/0x05 (start address) ignored
        real_size = fw.max_addr - fw.min_addr + max_addr_data_len
        fw.total_len = max(real_size, fw.total_len)
        buf = bytearray(fw.total_len)
        for addr, payload in fw.records:
            o = addr - fw.min_addr
            buf[o:o + len(payload)] = payload
        fw.firmware_data = bytes(buf)
        total = 0
        for off in range(0, len(buf) - 3, 4):
            total = (total + _u32le(buf, off)) & 0xFFFFFFFF
        fw.checksum = total
        return fw

    # BLE FEE1 command builders (BFirmwareParser)
    def erase_command(self, block_size: int) -> bytes:
        n_blocks = (self.total_len + (block_size - 1)) // block_size
        addr = self.min_addr // self.add_multiple
        return bytes([0x81, 0x00, addr & 0xFF, (addr >> 8) & 0xFF,
                      n_blocks & 0xFF, (n_blocks >> 8) & 0xFF])

    def programme_command(self, addr: int, offset: int) -> bytes:
        out = bytearray([0x80, (self.send_size - 4) & 0xFF])
        ad = addr // self.add_multiple
        out += bytes([ad & 0xFF, (ad >> 8) & 0xFF])
        ln = min(self.send_size - 4, len(self.firmware_data) - offset)
        out += self.firmware_data[offset: offset + ln]
        if len(out) < self.send_size:
            out += b"\x00" * (self.send_size - len(out))
        return bytes(out)

    def programme_length(self, offset: int) -> int:
        return min(self.send_size - 4, len(self.firmware_data) - offset)

    def checksum_command(self) -> bytes:
        addr = self.min_addr // self.add_multiple
        return bytes([0x85, 0x00, addr & 0xFF, (addr >> 8) & 0xFF]) \
            + struct.pack("<II", len(self.firmware_data), self.checksum)

    def end_command(self) -> bytes:
        return bytes([0x83, (self.send_size - 2) & 0xFF])
