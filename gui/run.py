#!/usr/bin/env python3
"""Launch the MP305 desktop GUI.

    pip install -r requirements.txt
    python run.py            # auto: use a real MP305 over USB if present, else the simulator
    python run.py --usb      # force USB-HID
    python run.py --ble      # force Bluetooth (needs bleak; accept the prompt on the device)
    python run.py --demo     # force the built-in simulator

The transport can also be changed live from the selector in the app's top bar (while
disconnected).
"""
import argparse
import sys

from mp305gui.app import run

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--demo", action="store_true", help="force the simulator backend")
    g.add_argument("--usb", action="store_true", help="force the USB-HID backend")
    g.add_argument("--ble", action="store_true", help="force the Bluetooth backend")
    args = ap.parse_args()
    transport = ("sim" if args.demo else "usb" if args.usb
                 else "ble" if args.ble else "auto")
    sys.exit(run(transport=transport))
