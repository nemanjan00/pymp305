#!/usr/bin/env python3
"""Fetch ISDT's public WebLink web-app source maps and unpack the original sources.

The official WebLink app (https://www.isdt.co/weblink/) is built with Vite and ships
*public* JavaScript source-maps (`.js.map`) containing `sourcesContent` — i.e. the original,
un-minified source. This script downloads the current bundle, discovers every code-split
chunk and its map, and writes the embedded original sources to an output directory.

This is how the protocol in PROTOCOL.md was derived. We ship this fetcher (our own code)
rather than ISDT's source itself: the recovered output is ISDT's copyright, so it lands in
`reversing/` which is git-ignored and never published.

Usage:
    python tools/fetch_weblink_sources.py                 # -> reversing/recovered-src/
    python tools/fetch_weblink_sources.py --out /tmp/x    # custom output dir
    python tools/fetch_weblink_sources.py --keep-node-modules
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

BASE = "https://www.isdt.co/weblink"
ASSETS = f"{BASE}/assets"
UA = {"User-Agent": "Mozilla/5.0 (pymp305b source fetcher)"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _try_get(url: str) -> bytes | None:
    try:
        return _get(url)
    except Exception as e:  # noqa: BLE001
        print(f"  ! {url} -> {e}")
        return None


def discover_assets() -> set[str]:
    """Return the set of `assets/*.js` chunk filenames referenced by the app."""
    print(f"fetching {BASE}/ ...")
    html = _get(f"{BASE}/").decode("utf-8", "replace")
    entries = set(re.findall(r'/weblink/assets/([\w.\-]+\.js)', html))
    if not entries:
        # fall back to the conventional entry name pattern
        entries = set(re.findall(r'assets/(index-[\w.\-]+\.js)', html))
    found = set(entries)
    # Walk each chunk and harvest the chunk filenames it imports (Vite emits these literally).
    queue = list(entries)
    while queue:
        name = queue.pop()
        data = _try_get(f"{ASSETS}/{name}")
        if data is None:
            continue
        text = data.decode("utf-8", "replace")
        for ref in re.findall(r'["\'](?:\./|assets/)?([\w.\-]+\.js)["\']', text):
            if ref not in found:
                found.add(ref)
                queue.append(ref)
    return found


def unpack_map(map_bytes: bytes, out_dir: str, keep_node_modules: bool) -> int:
    d = json.loads(map_bytes)
    sources = d.get("sources", []) or []
    contents = d.get("sourcesContent") or []
    written = 0
    for i, src in enumerate(sources):
        if i >= len(contents) or contents[i] is None:
            continue
        rel = src.lstrip("./").replace("../", "")
        if not keep_node_modules and rel.startswith("node_modules/"):
            continue
        path = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(contents[i])
        written += 1
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--out", default=os.path.join(here, "reversing", "recovered-src"),
                    help="output directory (default: reversing/recovered-src)")
    ap.add_argument("--keep-node-modules", action="store_true",
                    help="also write vendored node_modules sources (large)")
    args = ap.parse_args()

    assets = discover_assets()
    js = sorted(a for a in assets if a.endswith(".js"))
    print(f"discovered {len(js)} chunk(s): {', '.join(js)}")

    total_files = 0
    total_maps = 0
    for name in js:
        m = _try_get(f"{ASSETS}/{name}.map")
        if m is None:
            continue
        n = unpack_map(m, args.out, args.keep_node_modules)
        total_maps += 1
        total_files += n
        print(f"  {name}.map -> {n} source file(s)")

    if total_maps == 0:
        print("\nNo source maps found — ISDT may have stopped publishing them.", file=sys.stderr)
        return 1
    print(f"\nUnpacked {total_files} source file(s) from {total_maps} map(s) into {args.out}")
    print("Reminder: this output is ISDT's copyright — keep it under git-ignored reversing/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
