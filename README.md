<div align="center">

# ⚡ pymp305b

**An unofficial Python driver for the [ISDT MP305B](https://www.isdt.co/) smart bench power supply.**

Control voltage, current, and output over USB — no app, no cloud, just Python.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![Transport: USB-HID](https://img.shields.io/badge/transport-USB--HID-success.svg)](./PROTOCOL.md)

```text
   ┌─────────────────────────────┐
   │  ISDT MP305B   30V · 5A · 305W│        USB-C / HID          ╔═══════════╗
   │   ┌───────┐  ┌────┐  ┌────┐  │  ◀───────────────────────▶  ║  Python   ║
   │   │ 12.34V│  │1.50A│  │18.5W│ │     VID 0x28E9 · rep 1      ║ pymp305b  ║
   │   └───────┘  └────┘  └────┘  │                             ╚═══════════╝
   └─────────────────────────────┘
```

</div>

---

## Why

The MP305B is a slick little programmable PSU, but the only ways to drive it are ISDT's
phone app (BLE) and their [WebLink](https://www.isdt.co/weblink/) web app (WebHID).
This library speaks the **same USB-HID protocol the web app uses**, so you can script your
bench from Python: automated test rigs, battery cycling, data logging, CI for hardware.

The protocol was reverse-engineered from WebLink's *public* source-maps and is fully
documented in **[PROTOCOL.md](./PROTOCOL.md)**.

> ⚠️ **Heads-up:** the framing/decoding layer is covered by passing golden-vector tests,
> but this has **not yet been validated against physical hardware**. First-run bring-up
> notes are in [`python/README.md`](./python/README.md). Reports welcome!

## Features

- 🔌 **Zero-config connect** — auto-discovers the device by USB vendor id
- 🎛️ **Full PSU control** — set V/I, toggle output, take/release remote control
- 📈 **Live telemetry** — voltage, current, power, energy, temperature, runtime, errors
- 🔋 **Charge mode** — battery charging by chemistry / cells / current
- 🧱 **Clean layers** — pure `protocol.py` framing you can reuse over BLE too
- 🧪 **Tested without hardware** — checksum/stuffing/units verified by golden vectors
- 🪪 **MIT licensed**, no ISDT code shipped (see *Clean-room* below)

## Install

```bash
pip install hidapi
pip install -e python/        # or: cd python && pip install -e .
```

Linux: add a udev rule so you don't need root —
see [`python/README.md`](./python/README.md#install).

## Quick start

```python
from pymp305b import MP305B

with MP305B.open() as psu:
    print(psu.hardware_info())                      # name + firmware versions

    psu.set_output(voltage=5.0, current=1.0, on=True)   # remote control + output ON

    st = psu.read_state()
    print(f"{st.voltage:.2f} V  {st.current:.3f} A  {st.power:.2f} W  {st.temperature} °C")

    psu.output_off()
    psu.release_remote()                            # give the front panel control back
```

Live-streaming example: [`python/examples/basic.py`](./python/examples/basic.py).

## Protocol at a glance

| What | Command | Response | Notes |
|------|:-------:|:--------:|-------|
| Hardware / firmware info | `0xE0` | `0xE1` | device id + HW/boot/app versions |
| Live state | `0xBD` / `0xC2` | `0xC3` | V·I·W·Wh·°C·output·errors |
| System settings | `0xC4` / `0xC6` | `0xC5` / `0xC7` | brightness, OCP, auto-off… |
| **Set output / V / I** | `0xC8` | `0xC9` | the main control command |
| Charge mode | `0xEE` | `0xEF` | LiHv/LiPo/LiFe/Pb/NiMH… |
| Reboot / bootloader | `0xFCCA` / `0xF0AC` | — | danger zone |

Frames are `[len, 0xAA, 0x12, paylen, cmd, …LE-payload, checksum]` with `0xAA` byte-stuffing.
Full field-level spec, units, and error tables: **[PROTOCOL.md](./PROTOCOL.md)**.

## Repo layout

```
pymp305b/
├── PROTOCOL.md                 # the wire protocol, documented
├── tools/
│   └── fetch_weblink_sources.py# reproduce the RE material from ISDT's public source-maps
├── python/
│   ├── pymp305b/               # the library (protocol · responses · device)
│   ├── examples/basic.py
│   └── tests/test_protocol.py  # golden-vector framing tests (no hardware needed)
└── reversing/                  # ← git-ignored: recovered ISDT source, kept local only
```

## Clean-room & copyright

This repository contains **only original work** (the Python driver, the protocol
documentation, and the fetch tool). It does **not** redistribute any ISDT code.

ISDT's WebLink app is their copyright. The reverse-engineering material derived from it
lives under `reversing/`, which is **git-ignored and never published**. To regenerate it
locally from ISDT's *public* source-maps:

```bash
python tools/fetch_weblink_sources.py     # -> reversing/recovered-src/  (local only)
```

Protocol/interoperability facts (command bytes, field layouts) are not themselves
copyrightable; the implementation here is independent.

## Roadmap

- [ ] Validate against real hardware (incoming 🛒)
- [ ] BLE transport via `bleak` (same command set, reuses `responses.py`)
- [ ] USB-PD (PDO) and programmable-sequence helpers
- [ ] OTA firmware flashing

## License

[MIT](./LICENSE) — *Not affiliated with or endorsed by ISDT. "MP305B" and "ISDT" are
trademarks of their respective owner.*
