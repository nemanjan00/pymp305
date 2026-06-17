"""Decrypt and inspect an ISDT MP305 firmware .bin (safe — no device, no flashing).

    python examples/ota_inspect.py path/to/firmware.bin
"""
import sys

from pymp305 import Firmware


def main(path):
    fw = Firmware.parse(open(path, "rb").read(), clear_app_flag=False)
    print(f"file              : {path}")
    print(f"checksum ok       : {fw.checksum_ok}")
    print(f"app size          : {fw.app_size} bytes @ 0x{fw.app_storage_offset:08X}")
    print(f"data size         : {fw.data_size} bytes @ 0x{fw.data_storage_offset:08X}")
    print(f"encryption key    : 0x{fw.encryption_key:08X}")
    print(f"device id         : 0x{fw.device_id:016X}")
    print(f"hw version        : {fw.hw_major}.{fw.hw_minor}")
    print(f"decrypted bytes   : {len(fw.firmware_data)}")
    out = path + ".decrypted"
    open(out, "wb").write(fw.firmware_data)
    print(f"wrote             : {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__); sys.exit(1)
    main(sys.argv[1])
