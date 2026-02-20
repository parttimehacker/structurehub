import asyncio
import bleak
from bleak import BleakScanner

def cb(dev, adv):
    md = adv.manufacturer_data or {}
    if not md:
        return
    if 0xFFFF in md:
        b = md[0xFFFF]
        print(dev.address, "rssi", adv.rssi,
              "len", len(b),
              "first2", b[:2].hex(),
              "data", b.hex())

async def main():
    print("Bleak imported successfully")
    print("Bleak module location:", bleak.__file__)
    async with BleakScanner(cb):
        await asyncio.sleep(10)

asyncio.run(main())

