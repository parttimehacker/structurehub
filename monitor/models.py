# AtticGuard/models.py
from __future__ import annotations

from django.db import models


class Reading(models.Model):
    """
    One decoded BLE advertisement / sensor measurement.

    Notes:
    - We keep raw canonical sensor fields (temp_c, hum_pct, press_hpa, batt_mv).
    - We ALSO store a few V3A-derived fields when available (nullable):
        batt_pct, uptime_min, dew_point_c
      This keeps your UI fast/consistent, while remaining backward-compatible with V2 rows.
    - V4 adds a 2-bit DIP-derived "location" (attic/crawlspace/basement/other).
      This is best treated as sensor context/metadata (not a measurement) and is nullable
      so older rows remain valid.
    - Fahrenheit, battery volts, and risk labels can still be computed at render time.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # Sensor identity / radio metadata
    source = models.CharField(max_length=64, blank=True, default="", db_index=True)
    rssi = models.IntegerField(default=0)

    # ---- V4 sensor context (nullable; old V2/V3A rows remain valid) ----
    class Location(models.IntegerChoices):
        ATTIC = 0, "Attic"
        CRAWLSPACE = 1, "Crawlspace"
        BASEMENT = 2, "Basement"
        OTHER = 3, "Other"

    # 0..3 from DIP switches on the StructureNode (stored per-reading for simplicity)
    location = models.IntegerField(
        choices=Location.choices,
        null=True,
        blank=True,
        db_index=True,
        help_text="V4: DIP-derived location (0=Attic, 1=Crawlspace, 2=Basement, 3=Other).",
    )

    # Canonical measurements (V2+)
    temp_c = models.FloatField(null=True, blank=True)
    hum_pct = models.FloatField(null=True, blank=True)
    press_hpa = models.FloatField(null=True, blank=True)

    batt_mv = models.IntegerField(default=0)     # millivolts
    flags = models.IntegerField(default=0)
    seq = models.IntegerField(default=0, db_index=True)

    # Motion counters (V2 lifetime was 32-bit; V3A is 16-bit and may wrap, DB can store either)
    motion0 = models.BigIntegerField(default=0)
    motion1 = models.BigIntegerField(default=0)

    # ---- V3A optional fields (nullable; old V2 rows remain valid) ----
    batt_pct = models.IntegerField(null=True, blank=True)      # 0-100, from sensor if available
    uptime_min = models.IntegerField(null=True, blank=True)    # minutes since boot
    dew_point_c = models.FloatField(null=True, blank=True)     # dew point (C)

    class Meta:
        # Helpful for queries like "latest readings" and "latest per sensor"
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["source", "-created_at"]),
            models.Index(fields=["source", "seq"]),
            models.Index(fields=["location", "-created_at"]),  # V4: fast "latest per location"
        ]

        # Optional but recommended if your seq is per-device monotonic and you want to prevent duplicates.
        # If you already dedupe in ble_worker.py, you can still keep this off.
        # constraints = [
        #     models.UniqueConstraint(fields=["source", "seq"], name="uniq_reading_source_seq"),
        # ]

    def __str__(self) -> str:
        src = self.source or "unknown"
        loc = self.get_location_display() if self.location is not None else "n/a"
        return f"Reading({src} loc={loc} seq={self.seq} at {self.created_at:%Y-%m-%d %H:%M:%S})"
