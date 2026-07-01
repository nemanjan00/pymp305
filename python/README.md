# pymp305

A pure-Python driver for the **ISDT MP305** smart bench power supplies (**MP305A** and
**MP305B**) over **USB-HID** (sync) and **Bluetooth** (async) — the same protocol the
official [WebLink](https://www.isdt.co/weblink/) web app and PolyLink phone app use.
Reverse-engineered from WebLink's public source-maps; the full protocol is documented in
[`../PROTOCOL.md`](../PROTOCOL.md). (The recovered upstream JS is ISDT's copyright and is
kept locally under `reversing/`, which is git-ignored and not published.)

> **Status: partially validated on hardware (v0.6.0).** Verified on a physical **MP305B**:
> all telemetry reads, DC PSU control (V/I/output), and `set_mode()`. Charge / USB-PD /
> programmable *control* and the whole **MP305A** are not yet confirmed. `open()` still emits
> a one-time `UserWarning`, and OTA flashing / `soft_reset()` remain unverified — OTA is gated
> behind an explicit `allow_untested_ota=True`. See *Bring-up* below.

## Install

```bash
pip install pymp305          # USB-HID (pulls in hidapi)
pip install pymp305[ble]     # + Bluetooth transport (bleak)
```

From source (development): `pip install -e .` from this directory.

A Dracula-themed **PyQt6 desktop GUI** (live charts + simulator) lives in [`../gui`](../gui).

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
| `set_language(i)` | `0xA2` |
| `read_charge_state()` / `read_charge_settings()` | `0xEC`→`0xED` / `0xEA`→`0xEB` (`ChargeState` / `ChargeInfo`) |
| `charge(ChargeCommand)` | `0xEE` start/stop a battery charge |
| `read_pdo(id)` / `pdo_connect(PDOConnect)` / `write_pdo(...)` | `0xD0`→`0xD1` read / `0xE8`→`0xE9` select / `0xD2`→`0xD3` define a PD profile |
| `read_program_list()` / `read_program_steps(id, n)` | `0xD4`→`0xD5` / `0xD8`→`0xD9` |
| `read_program_state()` / `program_connect(ProgramConnect)` | `0xDE`→`0xDF` / `0xE2`→`0xE3` run a sequence |
| `write_program(id, steps)` / `program_change(...)` | `0xDA`→`0xDB` write steps / `0xD6`→`0xD7` create/rename/delete slot |
| `read_emarker()` | USB-C cable e-marker info (speed/format labelled) |
| `flash(Firmware, allow_untested_ota=True)` | **experimental** OTA over HID (see warnings) |
| `reboot()` / `enter_bootloader()` | danger zone |
| `send(cmd, payload)` / `request(cmd, expect, payload)` / `read_frame()` | raw access for any other command |

Units are converted for you: `voltage`/`set_voltage` in **V**, `current`/`set_current`
in **A**, `power` in **W**, `energy` in **Wh**, `temperature` in **°C**, `working_time` in **s**.

### Transports

- **`MP305`** — synchronous, USB-HID (this table).
- **`MP305BLE`** — the same API but `async` over Bluetooth (`bleak`); methods are coroutines
  and connection is `await MP305BLE.open()`. Adds `flash_ble(IntelHexFirmware, allow_untested_ota=True)`
  (experimental BLE OTA over `fee0/fee1`). See [`examples/ble.py`](./examples/ble.py).

### Firmware (`pymp305.ota`)

- **`Firmware.parse(bytes)`** — decrypt + verify an official `.bin` (key is in the header).
- **`IntelHexFirmware.parse(...)`** — the BLE FEE1 firmware format.
- `tools/fetch_firmware.py` downloads + decrypts official images into git-ignored `reversing/`.
- OTA *writing* is experimental and untested on hardware — see the repo banner.

### Experimental / undisclosed commands

Reverse-engineered from the firmware (handled by the device but never sent by the official
app). **Untested on hardware** — see notes in `reversing/FINDINGS-commands.md`.

- **`get_language()`** — read the UI language index (cmd `0xA0`→`0xA1`), the read counterpart
  of `set_language()`. Read-only; the safe first probe.
- **`soft_reset(confirm=True)`** — magic-gated (`0xFE AA 55`) soft re-init of the regulator/
  USB-PD state to defaults + control-task restart. Static analysis shows no flash/NVM access
  (can't brick), but it resets the live output, so it's gated behind `confirm=True`.

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
pytest                 # runs all suites (framing, charge/PD/program, OTA, experimental)
```
All run without hardware — they validate framing/checksum/stuffing, unit decoding, the
firmware decryptor, and the command builders against golden vectors.

## BLE transport

Implemented via `MP305BLE` (async, `bleak`) — see *Transports* above and
[`examples/ble.py`](./examples/ble.py). Over BLE the same command set is used but frames
drop the length/0xAA/checksum wrapper (`[0x12, cmd, …payload]`): commands go to GATT
characteristic `af01`, responses arrive as notifications (parsed at index 2, reusing
`responses.py`), and an `af02` binding handshake starts the session; `fee0/fee1` carry BLE OTA.

MP305 has Bluetooth, but its BLE-module firmware isn't published in the OTA feed and this
transport is **unverified on hardware** — USB-HID is the primary path.
