#!/usr/bin/env python3
import os
import sys
import time
import socket
import fcntl
import struct
from datetime import timezone

# ---- OLED (luma.oled) ----
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from PIL import Image, ImageDraw, ImageFont

# ---- Django setup ----
# Adjust these 2 lines to match your project layout:
DJANGO_PROJECT_DIR = "/home/an/pidjango"          # directory that contains manage.py
DJANGO_SETTINGS_MODULE = "pidjango.settings"      # your settings module

os.environ.setdefault("DJANGO_SETTINGS_MODULE", DJANGO_SETTINGS_MODULE)
sys.path.insert(0, DJANGO_PROJECT_DIR)

import django  # noqa: E402
django.setup()  # noqa: E402

# Import your models:
# Adjust app/model names if yours differ.
from AtticGuard.models import Reading  # noqa: E402


def get_hostname() -> str:
    return socket.gethostname()


def get_ip_for_iface(ifname: str = "wlan0") -> str:
    """
    Best-effort local IP for an interface. Falls back to a socket trick.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(
            fcntl.ioctl(
                s.fileno(),
                0x8915,  # SIOCGIFADDR
                struct.pack("256s", ifname[:15].encode("utf-8")),
            )[20:24]
        )
    except Exception:
        pass

    # Fallback: outbound socket “trick”
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "No IP"


def get_sensor_count_and_last_update():
    """
    Assumptions:
      - Reading table has a field 'loc' (or similar) identifying a sensor/location.
      - We count distinct loc values as "number of sensors".
    If your sensor identity field differs (e.g., device_id, mac, node_id), change it below.
    """
    # CHANGE THIS if needed:
    SENSOR_ID_FIELD = "loc"

    try:
        sensor_count = (
            Reading.objects.values(SENSOR_ID_FIELD).distinct().count()
        )
    except Exception:
        # If the field name is wrong, show unknown but keep running.
        sensor_count = -1

    latest = Reading.objects.order_by("-created_at").only("created_at").first()
    if not latest or not latest.created_at:
        return sensor_count, "No data"

    dt = latest.created_at
    # Ensure timezone-aware display
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone()  # system local tz
    return sensor_count, local_dt.strftime("%b %d %H:%M:%S")


def draw_screen(device, hostname: str, ip: str, sensors: int, last_update: str):
    # Built-in default font is fine on 128x64; you can swap for a TTF if you want.
    font = ImageFont.load_default()

    image = Image.new("1", device.size)
    draw = ImageDraw.Draw(image)

    # Layout tuned for 128x64
    y = 0
    draw.text((0, y), "StructureHub Server", font=font, fill=255)
    # y += 12
    #draw.text((64, y), f"Host: {hostname}", font=font, fill=255)
    y += 11
    draw.text((0, y), f"IP: {ip}", font=font, fill=255)
    #y += 11

    if sensors >= 0:
        draw.text((74, y), f"Sensors: {sensors}", font=font, fill=255)
    else:
        draw.text((74, y), "Sensors: ?", font=font, fill=255)
    y += 11

    draw.text((0, y), "Last:", font=font, fill=255)
    #y += 12
    draw.text((38, y), f"{last_update}", font=font, fill=255)

    device.display(image)


def main():
    # If your OLED is not at 0x3C, change address to 0x3D.
    serial = i2c(port=1, address=0x3C)
    device = ssd1306(serial, width=128, height=32)

    hostname = get_hostname()

    while True:
        ip = get_ip_for_iface("wlan0")  # change to "eth0" if wired
        sensors, last_update = get_sensor_count_and_last_update()
        draw_screen(device, hostname, ip, sensors, last_update)
        time.sleep(10)  # refresh every 10 seconds


if __name__ == "__main__":
    main()

