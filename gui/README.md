# MP305 GUI

A modern **Dracula-themed** desktop dashboard for the ISDT MP305, built on
[`pymp305`](../python) with **PyQt6** + **pyqtgraph**. Live V/I/W readouts, a CV/CC gauge,
real output toggle, set-point controls, and rolling charts. UI/UX takes cues from ISDT's
WebLink (hero readout, mode tabs, circular gauge, live chart) and modernizes it.

![screenshot](./screenshot.png)

> Runs against a real MP305 (via `pymp305`/`hidapi`) **or** a built-in simulator, so you can
> try it with no hardware — the screenshot above is the simulator driving a CV→CC transition.
> Like the library, the hardware path is **not yet validated on a real device.**

## Run

```bash
cd gui
pip install -r requirements.txt
python run.py            # auto: real MP305 if present, else the simulator
python run.py --demo     # force the simulator
```

## What's wired

- **DC Power** tab: live voltage / current / power, energy, temperature, runtime, status.
- **Output toggle** + **set voltage / current** (spin + slider), applied live.
- **CV/CC gauge** (current vs. limit) with a constant-voltage / constant-current pill.
- **Rolling charts** (60 s) of measured voltage and current.
- **Simulator** with an adjustable load (Ω) so you can watch CV→CC behaviour.
- USB-PD / Charge / Programmable tabs are present; this build wires up DC Power + telemetry
  (the rest is available in the library and lights up on hardware).

## Architecture

- `mp305gui/backend.py` — `RealBackend` (wraps `pymp305.MP305`) and `SimBackend`, same surface.
- `mp305gui/worker.py` — a `QThread` worker; all (blocking) device I/O runs off the UI thread.
- `mp305gui/app.py` — the dashboard, custom widgets (toggle, arc gauge), and charts.
- `mp305gui/theme.py` — the Dracula palette + Qt stylesheet.

Kept as a **separate package** so the core `pymp305` library stays dependency-free (no Qt).
