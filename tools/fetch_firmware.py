#!/usr/bin/env python3
"""Download official ISDT MP305 firmware and decrypt it (for inspection / repair backup).

Hits ISDT's OTA manifests (the same ones updateOta.js fetches), finds entries whose
deviceName matches the requested model, downloads each firmware image, and — for the
encrypted USB-HID `.bin` images — decrypts them with pymp305.ota.Firmware (the key ships
in the file header). Output goes to reversing/firmware/ (git-ignored; ISDT's copyright).

The protocol has no flash-read, so this is the only way to obtain a known-good image to
restore after a bad flash — keep one around before you OTA.

Usage:
    python tools/fetch_firmware.py                  # MP305*, both HID + BLE manifests
    python tools/fetch_firmware.py --match MP305B
    python tools/fetch_firmware.py --out /tmp/fw
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

# pull in the decryptor from the package without installing it
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))
from pymp305 import Firmware  # noqa: E402

ORIGIN = "https://www.isdt.co"
MANIFESTS = {
    "hid": f"{ORIGIN}/ota/newfirmware.json",
    "ble": f"{ORIGIN}/ota/newble.json",
}
UA = {"User-Agent": "Mozilla/5.0 (pymp305 firmware fetcher)"}


def _get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()


def _iter_entries(manifest: dict):
    """Yield every device entry across all categories of an OTA manifest."""
    dl = manifest.get("downloadList", manifest)
    buckets = dl.values() if isinstance(dl, dict) else [dl]
    for bucket in buckets:
        if isinstance(bucket, list):
            yield from (e for e in bucket if isinstance(e, dict))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--out", default=os.path.join(here, "reversing", "firmware"))
    ap.add_argument("--match", default="MP305", help="substring of deviceName (default MP305)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    found = 0
    for kind, url in MANIFESTS.items():
        try:
            manifest = json.loads(_get(url))
        except Exception as e:  # noqa: BLE001
            print(f"! {url} -> {e}")
            continue
        for e in _iter_entries(manifest):
            name = e.get("deviceName", "")
            fw_url = e.get("firmwareUrl")
            if args.match not in name or not fw_url:
                continue
            found += 1
            ver = e.get("versionNumber", "")
            tag = f"{name}_{ver}".replace("/", "_").replace(" ", "")
            print(f"[{kind}] {name} {ver}  <- {fw_url}")
            try:
                blob = _get(fw_url)
            except Exception as ex:  # noqa: BLE001
                print(f"    download failed: {ex}"); continue
            raw_path = os.path.join(args.out, f"{tag}.{kind}.bin")
            open(raw_path, "wb").write(blob)
            print(f"    saved {raw_path} ({len(blob)} bytes)")
            if kind == "hid":
                try:
                    fw = Firmware.parse(blob, clear_app_flag=False)
                    dec = os.path.join(args.out, f"{tag}.decrypted.bin")
                    open(dec, "wb").write(fw.firmware_data)
                    print(f"    decrypted -> {dec}  checksum_ok={fw.checksum_ok} "
                          f"app={fw.app_size} data={fw.data_size} "
                          f"devid=0x{fw.device_id:016X} hw={fw.hw_major}.{fw.hw_minor}")
                except Exception as ex:  # noqa: BLE001
                    print(f"    decrypt failed: {ex}")

    if not found:
        print(f"\nNo entries matching {args.match!r}. The manifest layout may have changed;"
              " inspect the JSON at:\n  " + "\n  ".join(MANIFESTS.values()))
        return 1
    print(f"\nDone. {found} image(s) in {args.out} (git-ignored).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
