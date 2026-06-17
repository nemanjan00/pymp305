# pymp305

A pure-Python driver for the **ISDT MP305** smart bench power supplies (**MP305A** and **MP305B**), talking to it
over **USB-HID** — the same transport the official [WebLink](https://www.isdt.co/weblink/)
web app uses. Reverse-engineered from WebLink's public source-maps; the full protocol is
documented in [`../PROTOCOL.md`](../PROTOCOL.md). (The recovered upstream JS used during
RE is ISDT's copyright and is kept locally under `reversing/`, which is git-ignored and
not published.)

> Status: written from the recovered firmware protocol but **not yet validated against
> real hardware** (the device hasn't arrived). The framing layer is covered by golden-vector
> tests (`tests/test_protocol.py`, all passing). See *Bring-up* below for first-run checks.

## Install

```bash
pip install pymp305          # pulls in hidapi
```

From source (development): `pip install -e .` from this directory.

On Linux you'll need permission to access the hidraw node. Either run as root for a quick
test, or add a udev rule (recommended):

```
# /etc/udev/rules.d/99-pymp305.rules
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="28e9", MODE="0660", TAG+="uaccess"
```
then `sudo udevadm control --reload && sudo udevadm trigger` and replug.

## Quick start

```python
from pymp305 import MP305

with MP305.open() as psu:
    print(psu.hardware_info())            # name, hw/boot/app versions

    psu.set_output(voltage=5.0, current=1.0, on=True)   # takes remote control + enables output

    st = psu.read_state()
    print(f"{st.voltage:.2f} V  {st.current:.3f} A  {st.power:.2f} W  {st.temperature} C")

    psu.output_off()
    psu.release_remote()                  # hand control back to the front panel
```

See [`examples/basic.py`](./examples/basic.py) for a live-streaming example.

## API surface

| Method | Does |
|--------|------|
| `MP305.list_devices()` | enumerate VID `0x28E9` HID interfaces |
| `MP305.open(path=None)` | open (auto-picks usage_page 0x01 / usage 0x04) |
| `hardware_info()` | `0xE0`→`0xE1` device id, firmware versions |
| `read_state(realtime=True)` | `0xBD`/`0xC2`→`0xC3` live V/I/W/Wh/temp/output (`State`) |
| `read_system_settings()` | `0xC4`→`0xC5` (`SystemSettings`) |
| `set_output(voltage, current, on, model=0)` | take control + apply, returns fresh `State` |
| `output_on()` / `output_off()` | toggle output |
| `release_remote()` | `remoteCon=0` — return control to the panel |
| `control(ControlCommand)` | low-level `0xC8` |
| `set_system_settings(SystemSetCommand)` | `0xC6` |
| `charge(ChargeCommand)` | `0xEE` battery-charge mode |
| `set_language(i)` | `0xA2` |
| `reboot()` / `enter_bootloader()` | danger zone |
| `send(cmd, payload)` / `request(cmd, expect, payload)` / `read_frame()` | raw access for PDO / programmable / OTA commands not yet wrapped |

Units are converted for you: `voltage`/`set_voltage` in **V**, `current`/`set_current`
in **A**, `power` in **W**, `energy` in **Wh**, `temperature` in **°C**, `working_time` in **s**.

## Bring-up checklist (first run with hardware)

1. `python -c "from pymp305 import MP305; print(MP305.list_devices())"` — confirm the
   device shows up under VID `0x28E9` and note its `usage_page`/`usage`.
2. `psu.hardware_info()` — if the de-stuffed name/version look right, framing is correct.
3. If reads time out: the device may not use a fixed 64-byte report. Try
   `MP305(dev, report_size=N)` with other sizes, or pass an explicit interface `path`.
4. `read_state()` polls with the realtime command (`0xBD`) like the app; if that yields
   nothing, try `read_state(realtime=False)` (`0xC2`).

## Tests

```bash
python tests/test_protocol.py        # or: pytest
```
These validate the framing/checksum/stuffing and unit decoding without hardware.

## BLE (not implemented here)

The MP305 also speaks the **same command set over BLE** (used by the PolyLink phone app):
GATT service `0000af00-…`, characteristic `af01` for command/notify, `af02` for the
binding handshake and chunked writes, plus `fee0/fee1` for OTA. BLE frames drop the
length/0xAA/checksum wrapper and are just `[0x12, cmd, …payload]`. Wrapping that with
`bleak` would reuse `responses.py` directly — left as a future addition.
