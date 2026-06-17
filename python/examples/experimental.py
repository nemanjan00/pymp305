"""Safe probes of the reverse-engineered / undisclosed commands (read-only).

These are handled by the firmware but never sent by the official app — UNTESTED on hardware.
This script only does *reads*; it does NOT call soft_reset()/flash(). See
reversing/FINDINGS-commands.md for the full analysis.

    python examples/experimental.py
"""
from pymp305 import MP305


def main():
    with MP305.open() as psu:
        print("device     :", psu.hardware_info().device_name)
        print("language    :", psu.get_language())          # 0xA0 -> 0xA1 (read-only)

        em = psu.read_emarker()                             # USB-C cable e-marker (read-only)
        if em.get("present"):
            print("e-marker    : speed={speed_label} format={format_label} "
                  "Vmax={voltage} Imax={current} P={power}".format(**em))
        else:
            print("e-marker    : no e-marked cable detected")

        # NOT called here (state-changing / privileged):
        #   psu.soft_reset(confirm=True)   # 0xFE AA 55 — resets regulator state
        #   psu.flash(firmware, confirm=True)


if __name__ == "__main__":
    main()
