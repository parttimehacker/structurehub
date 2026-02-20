# StructureHub

StructureHub is a lightweight home structure monitoring platform built on:

- BLE-based remote sensors (nRF52840)
- Raspberry Pi BLE scanner
- Django web backend
- SQLite data store
- Apache + mod_wsgi

## Architecture

Sensor → BLE → structurehub-ble.service → Django ORM → SQLite → Apache → Web UI

## Features

- BLE manufacturer payload decoding (V3A, V4)
- Location-aware readings
- Risk analysis
- Motion detection
- Trend visualization
- Low power sensor support

## System Requirements

- Raspberry Pi (Zero 2 W or 4)
- Python 3.11+
- Django 6.x
- Bleak

# StructureHub Architecture

Sensor (nRF52840)
    ↓ BLE Manufacturer Payload
Raspberry Pi
    ↓ structurehub-ble.service
Django ORM
    ↓ SQLite (/var/lib/structurehub/db.sqlite3)
Apache + mod_wsgi
    ↓
Web UI


## 📁 Project Structure

****

## Installation

1. Clone repo
2. Create venv
3. Install requirements
4. Run migrations
5. Enable structurehub-ble.service

## License

MIT
