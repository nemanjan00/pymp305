# Changelog

All notable changes to `pymp305`. Versions follow semver (pre-1.0: minor = features).

> ⚠️ Nothing here has been validated against physical hardware yet — see the README banner.

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
