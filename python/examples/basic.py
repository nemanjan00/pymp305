"""Basic MP305 (MP305A/MP305B) session. Connect over USB, print info, set 5 V / 1 A,
enable output, then stream live readings. Run with the device plugged in via USB-C."""
import time

from pymp305 import MP305


def main():
    print("MP305 HID interfaces found:")
    for d in MP305.list_devices():
        print(f"  VID 0x{d['vendor_id']:04X} PID 0x{d['product_id']:04X} "
              f"usage_page=0x{d.get('usage_page', 0):02X} usage=0x{d.get('usage', 0):02X} "
              f"path={d['path']}")

    with MP305.open() as psu:
        info = psu.hardware_info()
        print(f"\nDevice : {info.device_name}")
        print(f"HW {info.hardware_version}  boot {info.boot_version}  app {info.app_version}")

        # Take remote control, set 5.00 V / 1.000 A, turn the output on.
        st = psu.set_output(voltage=5.0, current=1.0, on=True)
        print(f"\nset -> {st.set_voltage:.2f} V  {st.set_current:.3f} A  output={st.output}")

        try:
            while True:
                st = psu.read_state()
                err = (" ERR:" + ",".join(st.errors)) if st.errors else ""
                print(f"{st.voltage:6.2f} V  {st.current:6.3f} A  {st.power:6.2f} W  "
                      f"{st.temperature:3d}C  out={st.output}{err}")
                time.sleep(1)
        except KeyboardInterrupt:
            psu.output_off()
            psu.release_remote()
            print("\noutput off, control released")


if __name__ == "__main__":
    main()
