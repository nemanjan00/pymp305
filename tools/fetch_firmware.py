#!/usr/bin/env python3
"""Download official ISDT MP305 firmware and decrypt it (for inspection / repair backup).

Hits ISDT's OTA manifests (the same ones the WebLink/PolyLink apps fetch), finds entries
whose deviceName matches the requested model, downloads each firmware image, and — for the
encrypted USB-HID `.bin`/`.fwd` images — decrypts them with pymp305.ota.Firmware (the key
ships in the file header). Output goes to reversing/firmware/ (git-ignored; ISDT's copyright).

Covers both hosts (www.isdt.co + the szisd.com mirror) and, with --staging, the *Test
manifests (e.g. MP305A 1.6.0.47 staging). Entries are de-duplicated by (kind, name, version)
so mirror/prod duplicates download once.

The protocol has no flash-read, so this is the only way to obtain a known-good image to
restore after a bad flash — keep one around before you OTA. (No bootloader image is
published; the bootloader is factory-flashed and only recoverable via SWD.)

Usage:
    python tools/fetch_firmware.py                 # MP305*, prod, HID + BLE feeds, both hosts
    python tools/fetch_firmware.py --staging       # also pull *Test (staging) builds
    python tools/fetch_firmware.py --match MP305B --out /tmp/fw
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))
from pymp305 import Firmware  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (pymp305 firmware fetcher)"}

# OTA endpoints live in data (ota_endpoints.json next to this script), not in code.
_DEFAULTS = {
    "hosts": ["https://www.isdt.co", "https://szisd.com"],
    "feeds": {"hid": "ota/newfirmware.json", "ble": "ota/newble.json"},
    "feeds_staging": {"hid": "ota/newfirmwareTest.json", "ble": "ota/newbleTest.json"},
}


def load_endpoints() -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ota_endpoints.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
        return {**_DEFAULTS, **cfg}
    except Exception:  # noqa: BLE001
        return _DEFAULTS


def _get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()


def _try_get(url: str) -> bytes | None:
    try:
        return _get(url)
    except Exception as e:  # noqa: BLE001
        print(f"  ! {url} -> {e}")
        return None


def _iter_entries(manifest: dict):
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
    ap.add_argument("--staging", action="store_true", help="also pull *Test (staging) builds")
    ap.add_argument("--kind", choices=["hid", "ble", "both"], default="both")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    cfg = load_endpoints()
    HOSTS = cfg["hosts"]
    feeds, feeds_staging = cfg["feeds"], cfg.get("feeds_staging", {})

    kinds = ["hid", "ble"] if args.kind == "both" else [args.kind]
    # collect candidate entries across hosts × channels, de-duped by (kind, name, version)
    seen_manifest: set[str] = set()
    chosen: dict[tuple, dict] = {}
    for kind in kinds:
        names = [feeds[kind]] + ([feeds_staging[kind]] if args.staging and kind in feeds_staging else [])
        for host in HOSTS:
            for name in names:
                url = f"{host}/{name}"
                if url in seen_manifest:
                    continue
                seen_manifest.add(url)
                raw = _try_get(url)
                if raw is None:
                    continue
                try:
                    manifest = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                for e in _iter_entries(manifest):
                    dn, fw = e.get("deviceName", ""), e.get("firmwareUrl")
                    if args.match not in dn or not fw:
                        continue
                    key = (kind, dn, e.get("versionNumber", ""))
                    chosen.setdefault(key, {**e, "_kind": kind})

    if not chosen:
        print(f"\nNo entries matching {args.match!r}. Manifest layout may have changed:")
        print("\n".join(f"  {h}/{feeds[k]}" for h in HOSTS for k in kinds))
        return 1

    files = 0
    for (kind, dn, ver), e in sorted(chosen.items()):
        fw_url = e["firmwareUrl"]
        tag = f"{dn}_{ver}".replace("/", "_").replace(" ", "")
        print(f"[{kind}] {dn} {ver}  <- {fw_url}")
        blob = _try_get(fw_url)
        if blob is None:
            continue
        raw_path = os.path.join(args.out, f"{tag}.{kind}.bin")
        open(raw_path, "wb").write(blob)
        files += 1
        print(f"    saved {raw_path} ({len(blob)} bytes)")
        if kind == "hid":
            try:
                fwp = Firmware.parse(blob, clear_app_flag=False)
                dec = os.path.join(args.out, f"{tag}.decrypted.bin")
                open(dec, "wb").write(fwp.firmware_data)
                print(f"    decrypted -> {dec}  checksum_ok={fwp.checksum_ok} "
                      f"app={fwp.app_size} data={fwp.data_size} "
                      f"devid=0x{fwp.device_id:016X} hw={fwp.hw_major}.{fwp.hw_minor}")
            except Exception as ex:  # noqa: BLE001
                print(f"    decrypt failed: {ex}")

    print(f"\nDone. {files} image(s) in {args.out} (git-ignored).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
