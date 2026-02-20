# AtticGuard/management/commands/ble_worker.py
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Dict, List, Optional

from django.core.management.base import BaseCommand
from django.db import transaction

from bleak import BleakScanner

from monitor.decode_payload import decode_payload, DecodedV3A, DecodedV4
from monitor.models import Reading



def _pick_address(device) -> str:
    # bleak device typically has .address (Linux) or .identifier (macOS)
    return getattr(device, "address", None) or getattr(device, "identifier", None) or ""


# We now standardize on company id 0xFFFF on-air.
_COMPANY_ID = 0xFFFF
_COMPANY_PREFIX = (_COMPANY_ID & 0xFFFF).to_bytes(2, "little")

_LOC_LABEL = {
    0: "Attic",
    1: "Crawlspace",
    2: "Basement",
    3: "Other",
}


class Command(BaseCommand):
    help = (
        "BLE scanner worker: listens for manufacturer data under companyId 0xFFFF, decodes via "
        "decode_payload.decode_payload() (V2/V3A/V4) and writes Readings with dedupe + batch SQLite writes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--queue-max", type=int, default=2000)
        parser.add_argument("--batch-size", type=int, default=50)
        parser.add_argument("--flush-ms", type=int, default=500)
        parser.add_argument("--debug", action="store_true")
        parser.add_argument("--max-seq-cache", type=int, default=5000)

    def handle(self, *args, **options):
        asyncio.run(self._run(**options))

    async def _run(
        self,
        *,
        queue_max: int,
        batch_size: int,
        flush_ms: int,
        debug: bool,
        max_seq_cache: int,
        **_ignored,
    ) -> None:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=queue_max)

        # Dedup cache: last seq per source
        last_seq_seen: Dict[str, int] = {}

        buffer: List[dict] = []
        flush_interval = max(0.05, flush_ms / 1000.0)
        last_flush = time.monotonic()

        def _remember_seq(source: str, seq: int) -> None:
            # keep it bounded
            if len(last_seq_seen) > max_seq_cache:
                last_seq_seen.pop(next(iter(last_seq_seen)))
            last_seq_seen[source] = seq

        def _is_duplicate(source: str, seq: int) -> bool:
            prev = last_seq_seen.get(source)
            return prev is not None and prev == seq

        async def flush_buffer_if_needed(force: bool = False) -> None:
            nonlocal last_flush, buffer
            now = time.monotonic()

            if not buffer:
                last_flush = now
                return

            if not force and (len(buffer) < batch_size) and ((now - last_flush) < flush_interval):
                return

            rows = [Reading(**item) for item in buffer]
            buffer = []
            last_flush = now

            def _write_rows():
                with transaction.atomic():
                    Reading.objects.bulk_create(rows, batch_size=batch_size)

            try:
                await asyncio.to_thread(_write_rows)
                if debug:
                    self.stdout.write(f"[db] bulk_create wrote {len(rows)} rows")
            except Exception as e:
                self.stderr.write(f"[db] ERROR bulk_create: {e!r}")

        async def db_writer():
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=flush_interval)
                except asyncio.TimeoutError:
                    await flush_buffer_if_needed(force=False)
                    continue

                try:
                    buffer.append(item)
                    await flush_buffer_if_needed(force=False)
                finally:
                    q.task_done()

        writer_task = asyncio.create_task(db_writer())

        def _decode_from_mfg_value(mfg_value: bytes) -> tuple[Optional[object], int]:
            """
            Bleak on Linux gives manufacturer_data as:
              { company_id: bytes(value_without_company_id) }

            Your StructureNode now uses company_id=0xFFFF and advertises:
              FF FF + <payload>

            So Bleak returns:
              md[0xFFFF] == <payload>

            - V4: payload is 25 bytes and starts with 0x04 0x00, so decode_payload(payload) works.
            - V2/V3A: decoder expects companyId+protocol in the bytes; so we re-prefix FF FF.
            """
            raw = bytes(mfg_value)

            # Try direct (works for V4 unprefixed)
            d = decode_payload(raw)
            if d is not None:
                return d, len(raw)

            # Try prefixed (needed for V2/V3A, and OK if decoder supports prefixed V4 too)
            prefixed = _COMPANY_PREFIX + raw
            d = decode_payload(prefixed)
            if d is not None:
                return d, len(prefixed)

            return None, len(raw)

        def on_detect(device, advertisement_data):
            md = getattr(advertisement_data, "manufacturer_data", None) or {}

            # Only accept our company id now (0xFFFF)
            mfg = md.get(_COMPANY_ID)
            if not mfg:
                return

            source = _pick_address(device)
            if not source:
                return

            rssi = getattr(advertisement_data, "rssi", None)

            decoded, raw_len = _decode_from_mfg_value(bytes(mfg))
            if decoded is None:
                if debug:
                    # show the first few bytes for diagnosis
                    b = bytes(mfg)
                    self.stdout.write(
                        f"[ble] drop undecoded source={source} rssi={rssi} "
                        f"mfg_len={len(b)} first8={b[:8].hex()}"
                    )
                return

            seq = int(decoded.seq)

            if _is_duplicate(source, seq):
                if debug:
                    self.stdout.write(f"[ble] dup drop source={source} seq={seq}")
                return
            _remember_seq(source, seq)

            row = {
                "source": source,
                "rssi": int(rssi) if rssi is not None else 0,
                "temp_c": float(decoded.temp_c),
                "hum_pct": float(decoded.hum_pct),
                "press_hpa": float(decoded.press_hpa),
                "batt_mv": int(decoded.batt_mv),
                "flags": int(decoded.flags),
                "seq": seq,
                "motion0": int(decoded.motion0),
                "motion1": int(decoded.motion1),
            }

            # Optional extras: V3A and V4 include these
            if isinstance(decoded, (DecodedV3A, DecodedV4)):
                row.update(
                    {
                        "batt_pct": int(decoded.batt_pct),
                        "uptime_min": int(decoded.uptime_min),
                        "dew_point_c": float(decoded.dew_point_c),
                    }
                )

            # V4 adds location
            if isinstance(decoded, DecodedV4):
                loc = int(decoded.location)
                row["location"] = loc

            try:
                q.put_nowait(row)
            except asyncio.QueueFull:
                if debug:
                    self.stderr.write("[ble] queue full; dropping newest reading")
                return

            if debug:
                extra = ""
                if isinstance(decoded, DecodedV4):
                    loc = int(decoded.location)
                    extra = (
                        f" loc={_LOC_LABEL.get(loc,'Other')}({loc})"
                        f" batt%={decoded.batt_pct} upm={decoded.uptime_min} dpC={decoded.dew_point_c:.2f}"
                    )
                elif isinstance(decoded, DecodedV3A):
                    extra = f" batt%={decoded.batt_pct} upm={decoded.uptime_min} dpC={decoded.dew_point_c:.2f}"

                self.stdout.write(
                    f"[ble] ok source={source} rssi={row['rssi']} seq={seq} "
                    f"t={row['temp_c']:.2f}C rh={row['hum_pct']:.2f}% p={row['press_hpa']:.1f}hPa "
                    f"batt={row['batt_mv']}mV flags=0x{row['flags']:04X}{extra} len={raw_len}"
                )

        self.stdout.write("Starting BLE scan (companyId 0xFFFF; decode_payload: V2/V3A/V4)...")
        scanner = BleakScanner(detection_callback=on_detect)

        try:
            await scanner.start()
            while True:
                await asyncio.sleep(1.0)
                await flush_buffer_if_needed(force=False)
        except asyncio.CancelledError:
            pass
        finally:
            with contextlib.suppress(Exception):
                await scanner.stop()

            with contextlib.suppress(Exception):
                await q.join()
            with contextlib.suppress(Exception):
                await flush_buffer_if_needed(force=True)

            writer_task.cancel()
            with contextlib.suppress(Exception):
                await writer_task
