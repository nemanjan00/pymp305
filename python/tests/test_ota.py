"""OTA: firmware decrypt round-trip, frame builders, fragmentation, Intel-HEX parsing."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymp305 import protocol as P
from pymp305 import ota


def _build_encrypted(app_words, key=0x12345678, app_off=0x8000):
    """Craft an encrypted .bin whose decrypted app is `app_words` (list of u32)."""
    app = b"".join(struct.pack("<I", w) for w in app_words)
    file_checksum = sum(app_words) & 0xFFFFFFFF
    body = ota.xor_crypt(app, key, file_checksum)     # XOR keystream is its own inverse
    header = struct.pack("<8I", key, file_checksum, app_off, 0, len(app), 0, 0, 0)
    return header + body, app, file_checksum


def test_xor_crypt_is_involution():
    data = bytes(range(64))
    enc = ota.xor_crypt(data, 0xDEADBEEF, 0x01020304)
    assert ota.xor_crypt(enc, 0xDEADBEEF, 0x01020304) == data
    assert enc != data


def test_firmware_decrypt_and_verify():
    blob, app, chk = _build_encrypted([0x11111111, 0x22222222, 0x33333333, 0x44444444])
    fw = ota.Firmware.parse(blob, clear_app_flag=False)
    assert fw.checksum_ok is True
    assert fw.firmware_data == app
    assert fw.app_size == len(app)
    assert fw.file_checksum == chk
    # corrupting the body breaks the checksum
    bad = bytearray(blob); bad[40] ^= 0xFF
    assert ota.Firmware.parse(bytes(bad), clear_app_flag=False).checksum_ok is False


def test_firmware_ota_frames_valid():
    blob, app, _ = _build_encrypted([0xAABBCCDD, 0x01020304, 0x05060708, 0x090A0B0C], app_off=0x8000)
    fw = ota.Firmware.parse(blob, clear_app_flag=False)
    # erase / checksum frames must parse back cleanly with the right command bytes
    assert P.parse_report(bytes([P.REPORT_ID]) + fw.erase_app_frame()).cmd == 0xF2
    assert P.parse_report(bytes([P.REPORT_ID]) + fw.checksum_app_frame()).cmd == 0xF6
    wf = fw.write_app_frame(0)
    assert P.parse_report(bytes([P.REPORT_ID]) + wf).cmd == 0xF4
    # app checksum matches the simple word-sum of the (16-byte) app
    assert fw.app_checksum() == (0xAABBCCDD + 0x01020304 + 0x05060708 + 0x090A0B0C) & 0xFFFFFFFF


def test_fragment_frame():
    frame = bytes(range(150))
    chunks = ota.fragment_frame(frame, package_size=61)
    assert len(chunks) == 3
    assert chunks[0][0] == 60 and len(chunks[0]) == 61          # first byte overwritten
    assert chunks[1][1] == frame[61]                             # later chunks prefixed by 0x00 slot
    # reassembling chunk bodies (minus the length byte / prefix) covers the frame tail
    assert len(chunks[2]) <= 62


def test_intel_hex_parse():
    # two 4-byte data records at 0x0000 and 0x0004, then EOF
    hexs = "\n".join([
        ":0400000001020304F2",
        ":0400040005060708DA",
        ":00000001FF",
    ])
    fw = ota.IntelHexFirmware.parse(hexs)
    assert fw.min_addr == 0 and fw.total_len == 8
    assert fw.firmware_data[:8] == bytes([1, 2, 3, 4, 5, 6, 7, 8])
    # BLE FEE1 command builders produce the documented opcodes
    assert fw.erase_command(2048)[0] == 0x81
    assert fw.checksum_command()[0] == 0x85
    assert fw.end_command()[0] == 0x83
    assert fw.programme_command(fw.min_addr, 0)[0] == 0x80


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"ok  {fn.__name__}")
    print(f"\nAll {len(fns)} OTA tests passed.")
