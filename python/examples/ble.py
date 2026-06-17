"""BLE (MP305A/MP305B) session via bleak: connect, print info, set 5 V / 1 A, stream.

    pip install bleak       # or: pip install pymp305[ble]
    python examples/ble.py
"""
import asyncio

from pymp305.ble import MP305BLE


async def main():
    print("scanning for MP305 over BLE…")
    devices = await MP305BLE.discover(timeout=8.0)
    for d in devices:
        print(f"  {d.address}  {d.name}")

    psu = await MP305BLE.open()           # scans + connects + binds
    try:
        info = await psu.hardware_info()
        print(f"\nModel : {psu.device_name}   app {info.app_version}")

        await psu.set_output(voltage=5.0, current=1.0, on=True)

        for _ in range(10):
            st = await psu.read_state()
            err = (" ERR:" + ",".join(st.errors)) if st.errors else ""
            print(f"{st.voltage:6.2f} V  {st.current:6.3f} A  {st.power:6.2f} W  "
                  f"out={st.output}{err}")
            await asyncio.sleep(1)
    finally:
        await psu.output_off()
        await psu.release_remote()
        await psu.close()


if __name__ == "__main__":
    asyncio.run(main())
