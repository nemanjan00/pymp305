# Changelog

All notable changes to `pymp305`. Versions follow semver (pre-1.0: minor = features).

> ⚡ As of 0.6.0 the DC PSU path, all telemetry reads, and mode switching are verified on a
> physical MP305B. Charge/USB-PD/programmable *control*, the MP305A, and OTA remain unverified
> — see the README banner.

## 0.6.1

### Added
- **`set_output(..., reapply=True)`** — briefly cycles the output off→on after applying, so a
  *lowered* current limit engages constant-current mode immediately.

### Notes
- **Device quirk (MP305B, app V1.6.0.46):** lowering the current limit while the output is
  already on does **not** engage CC — the CC threshold only re-arms on output-enable (raising
  the limit live works fine). Verified against ISDT's WebLink protocol: it sends the identical
  `0xC8` command, so this is firmware behaviour, not a missing feature. Set the current limit
  *before* enabling output, or pass `reapply=True`. Data readback (V / I / W) is accurate —
  confirmed with a 390 Ω load against Ohm's law, and CC regulation itself is accurate once armed.

## 0.6.0

First release validated against physical hardware (an **MP305B**, app V1.6.0.46). Several
reverse-engineering gaps that blocked all device communication were found and fixed; the DC
PSU path, every telemetry read, and mode switching are now confirmed working on-device.

### Fixed
- **HID responses use header group `0xAA 0x21`, not `0xAA 0x12`.** `parse_report` matched only
  the command group, so every `request()` timed out. It now accepts both.
- **`read_state()` realtime poll (`0xBD`) is unanswered on this unit** — it now falls back to
  the stored-state query (`0xC2`, same `0xC3` frame) and caches the choice.
- **Control commands need a two-step remote handshake.** `0xC8` (and the `0xE2`/`0xE8`/`0xEE`
  connects) were sent with `remoteCon=1` and silently rejected (`0xC9`-style status `1`);
  setpoints never applied. The driver now requests control with `remoteCon=2` first, then
  applies with `remoteCon=1`, re-acquiring once if control is dropped.
- **Short response frames no longer crash decoding.** `read_program_state()`/`read_emarker()`
  raised `IndexError` when the device omitted trailing e-marker bytes; the int decoders now
  read past the end as `0` (matching WebLink's JS).
- **`charge()` awaited the wrong response** (`0xED` charge-info instead of `0xEF`
  charge-control).

### Added
- **`set_mode(model)`** — switch between DC / programmable / USB-PD / charge. Switching is
  routed through the current mode's connect command (as the device requires) and holds remote
  control; releasing remote reverts the unit to DC.
- **`request_remote()`** is now mode-aware (acquires control via the active mode's command).

## 0.5.3

### Fixed
- **GUI**: the over-current `CC | OCP` toggle ballooned on tall / 4K displays (it absorbed the
  column's slack to match the right column). It's now fixed-height like the other cards; the
  left column simply ends naturally (columns need not be equal height).

## 0.5.2

### Fixed
- **GUI**: the OUTPUT button opened **red with no label** — the kinetic-pass refactor moved the
  text into the toggle handler, which doesn't fire at startup. It's now labelled from the start.

### Added
- **GUI**: a `↻` button on the charts to **clear the rolling history** (reset the 60 s window).

## 0.5.1

### Changed
- **GUI Charge/USB-PD polish** (consistent input language): chemistry is now a whole-card
  button that opens a pointer-friendly **dropdown** (`Picker`), and cells is a **numeric keypad
  card** like the other inputs — no more combo boxes. In USB-PD, the e-marker cable read-out is
  **flat** (read-only) while the PD-profile rows read as clickable buttons.

## 0.5.0

### Added
- **GUI mode tabs — Charge & USB-PD**: the dashboard now covers all of the device's operating
  modes (it runs one at a time, so they're tabs, not windows):
  - **Charge** — chemistry, cell count, charge current, start/stop, live charge status
    (wraps `charge()`/`ChargeCommand`).
  - **USB-PD** — list/select PD profiles (wraps `read_pdo()`/`pdo_connect()`), plus the USB-C
    **e-marker** cable read-out (`read_emarker()`).
  - The simulator models all three modes (charge fills a virtual pack CC→CV; PD pins the output
    to the selected profile). Real-hardware charge/PD paths are wired but **untested**.

(Library unchanged — the GUI uses lib methods that already shipped in 0.3.0.)

## 0.4.8

### Added
- **GUI kinetic pass**: numeric read-outs (V/A/power), the temperature gauge, and the channel
  set-point/measurement now **ease** to new values (~180 ms, OutCubic) instead of snapping; the
  **OUTPUT** button cross-fades red↔green; the **CC | OCP** toggle's fill **slides** between
  cells (colour-blended); an **OCP trip flashes the toggle red**.
- A `setMinimumSize(1060×800)` floor so the window can't be crammed into nonsense.

### Changed
- Battery tooltip clarifies it's clickable to start/stop charging (sim).

## 0.4.7

### Changed
- **GUI keypad — visible rail clamp**: entering a value above the channel max (e.g. 35 V on a
  30 V rail) no longer clamps silently. The keypad snaps to the max, turns the display **red**
  with a "capped to max … — tap a unit to accept" message, and requires a confirming tap, so an
  over-the-rail entry can't slip through unnoticed. (The clamp itself, incl. unit conversion
  like `50000 mV → 30 V`, was already enforced.)

## 0.4.6

### Changed
- **GUI readability/polish**: channel cards now **swap** with output state — the **set-point**
  becomes the big number while the output is OFF (with a `SET` tag), and the live measurement
  is big while ON. The over-current `CC | OCP` toggle scales up to fill the left column (so it
  matches the right column's height) with larger type; the SIM LOAD value matches the other
  readouts; preset button-group corners are fixed (square inner, only the group's outer bottoms
  rounded). Added a keypad-over-dashboard screenshot.

## 0.4.5

### Fixed
- **GUI — Remote button now actually works**: it was cosmetic (only restyled itself). It's
  wired through `reqRemote` → `worker.set_remote` → backend: **take** remote control
  (`set_output()`, `remote_con=1`) or **release** it (`release_remote()`); while released the
  on-screen controls disable (the front panel has the knob), like the device's `remoteCon`.
- Audited the whole backend↔library surface against the real `pymp305` API (`State` fields,
  `ControlCommand`, `set_output`/`control`/`release_remote`) — all confirmed wired correctly.

### Changed
- **GUI**: over-current toggle is now card-shaped (no redundant wrapper card); **presets** are
  a Bootstrap-style card (title + divider + flush edge-to-edge button group); the **event log**
  moves to a **full-width, collapsible** panel under both columns.

## 0.4.4

### Changed
- **GUI layout/semantics polish**: the over-current `CC | OCP` toggle (a *setting*) moves to
  the left control column with CC/OCP descriptions (like the WebLink), pushing the event log
  to the right; removed the redundant "all off" (the OUTPUT button is the on/off); dropped the
  status-lamp strip — CV/CC are card tags and OVP/OCP surface as **log alerts** (the WebLink
  has no protection LEDs). Consistent design language: cards = interactive, flat = read-only
  (the whole right column is flat bar the energy reset button), a divider between charts and
  stats, bigger logo, keypad screenshot in the GUI README.

## 0.4.3

### Changed
- **GUI** (`gui/`): correct the control-vs-status semantics by replicating WebLink's logic —
  **over-current** `CC | OCP` is now a real selectable toggle (sets `currentOver`:
  current-limit vs trip), while **CV / CC / OVP** are read-only status lamps driven by the
  device's `outState`. The simulator models the OCP trip. Also: flat SIM/USB label, energy
  reset button, presets/CV-CC clipping fixes.

## 0.4.2

### Changed
- **GUI redesign** (`gui/`): per-quantity instrument cards that merge measured + setpoint
  (tap → keypad with digit/unit buttons); the limiting channel highlights and a **CV|CC**
  segmented indicator make the mode obvious; big green/red output card-button; **no
  scroll-to-change** (safety: a stray trackball scroll must never move the output);
  battery status (charge/discharge toggle, pulsing-red near-empty); temperature bar gauge;
  V+I presets (right-click to save). (Library unchanged.)

## 0.4.1

### Changed
- **GUI v2** (`gui/`): redesigned for **trackball-only / no-keyboard** use — stepper-chip
  setpoints, scroll-to-nudge, one-click presets, and an on-screen **keypad with digit + unit
  buttons** (V/mV, A/mA). Dual CV/CC arc gauges with a fused CV/CC tag (replaces the empty
  card), status lamps (OUT/CV/CC/OVP/OCP), an event log, and disciplined layout (controls keep
  natural height; the chart absorbs slack). (Library unchanged.)

## 0.4.0

### Added
- **Desktop GUI** (`gui/`): a Dracula-themed PyQt6 + pyqtgraph dashboard around the library —
  live V/I/W readouts, a CV/CC arc gauge, output toggle, set-point controls, rolling charts,
  and a built-in **simulator** so it runs with no hardware. Kept as a separate package so the
  core library stays Qt-free.

(The `pymp305` library itself is unchanged from 0.3.0; this release adds the GUI + docs.)

## 0.3.0

First release covering the whole MP305 line, a second transport, and the full advanced
command surface. (0.2.0 was a local-only tag, never released — superseded by this.)

### Added
- **Typed package**: ships `py.typed` so downstream type-checkers see the annotations.
- **Safety gates**: `open()` emits a one-time `UserWarning` until `HARDWARE_VALIDATED`;
  OTA `flash()` / `flash_ble()` require an explicit `allow_untested_ota=True`.
- **BLE transport** `MP305BLE` (async, `bleak`): scan/connect, AF02 binding handshake,
  request/response over AF01, and the full high-level API mirroring the USB driver.
- **Charge mode**: `read_charge_state()`, `read_charge_settings()`, `charge(ChargeCommand)`.
- **USB-PD (PDO)**: `read_pdo()`, `pdo_connect()`, `write_pdo()` + `parse_pdo_item`
  (fixed/augmented/SPR-AVS/EPR-AVS decode).
- **Programmable sequences**: `read_program_list()`, `read_program_steps()`,
  `read_program_state()`, `program_connect()`, `write_program()`, `program_change()`.
- **E-marker**: `read_emarker()` (USB-C cable info with speed/format labels).
- **OTA + firmware**: `ota.Firmware` (decrypt + verify the encrypted `.bin`; validated against
  ISDT's released MP305A/MP305B images), `ota.IntelHexFirmware`, `ota.BootInfo`,
  experimental `MP305.flash()` (HID) and `MP305BLE.flash_ble()` (BLE FEE1), gated behind `allow_untested_ota=True`.
- **Experimental/undisclosed commands** (reverse-engineered): `get_language()` (`0xA0`),
  `soft_reset()` (`0xFE`, magic-gated, confirmed non-destructive).
- **MP305A + MP305B** in one driver (`MP305`, with `MP305A`/`MP305B` aliases); model
  auto-detected for error decoding.
- Tools: `tools/fetch_firmware.py` (data-driven via `tools/ota_endpoints.json`, mirror +
  staging support); `tools/fetch_weblink_sources.py`.

### Fixed
- `parse_report` now truncates to the frame length byte `N`, so HID report zero-padding
  no longer leaks into the decoded payload (matters for real 64-byte device reports).

### Changed
- Package renamed `pymp305b` → `pymp305`; covers the whole MP305 line.
- Python 3.10–3.14 in CI.

## 0.1.0

Initial release: USB-HID driver — connect, read state, set V/I/output, system settings,
reboot/bootloader; golden-vector framing tests.
