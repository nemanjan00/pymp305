#!/usr/bin/env python3
"""Launch the MP305 desktop GUI.

    pip install -r requirements.txt
    python run.py            # auto: use a real MP305 if present, else the simulator
    python run.py --demo     # force the built-in simulator
"""
import argparse
import sys

from mp305gui.app import run

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="force the simulator backend")
    args = ap.parse_args()
    sys.exit(run(prefer_real=not args.demo))
