# monitor/views.py
from __future__ import annotations

from django.core.paginator import Paginator
from django.shortcuts import render

from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple
import math
from dataclasses import dataclass
from django.http import JsonResponse
from django.utils import timezone

from .models import Reading
# ----------------------------
# Unit helpers
# ----------------------------

def c_to_f(c: float) -> float:
    return (c * 9.0 / 5.0) + 32.0


def mv_to_volts(mv: Optional[int]) -> Optional[float]:
    if mv is None or mv <= 0:
        return None
    return float(mv) / 1000.0


# Simple LiPo mapping
BATT_EMPTY_MV = 3000
BATT_FULL_MV = 4200


def mv_to_percent(mv: Optional[int]) -> Optional[int]:
    if mv is None or mv <= 0:
        return None
    mv = int(mv)
    if mv <= BATT_EMPTY_MV:
        return 0
    if mv >= BATT_FULL_MV:
        return 100
    pct = int(round((mv - BATT_EMPTY_MV) * 100.0 / (BATT_FULL_MV - BATT_EMPTY_MV)))
    return max(0, min(100, pct))


def dew_point_c(temp_c: float, rh_pct: float) -> float:
    """
    Magnus formula (reasonable for attic moisture heuristics).
    """
    rh = max(0.1, min(100.0, float(rh_pct)))
    t = float(temp_c)
    a = 17.62
    b = 243.12
    gamma = (a * t / (b + t)) + math.log(rh / 100.0)
    return (b * gamma) / (a - gamma)


def risk_from_spread_f(spread_f: Optional[float]) -> tuple[str, str]:
    """
    Moisture risk based on (TempF - DewPointF).
      HIGH: < 5°F
      MED:  5–10°F
      LOW:  >= 10°F or unknown
    """
    if spread_f is None:
        return ("low", "LOW")
    if spread_f < 5.0:
        return ("high", "HIGH")
    if spread_f < 10.0:
        return ("med", "MED")
    return ("low", "LOW")


# ----------------------------
# Motion helpers (based on counter deltas)
# ----------------------------

def motion_status_for_source(source: str) -> Tuple[Optional[timezone.datetime], Optional[int]]:
    """
    Find the most recent timestamp where motion counters changed for this source.
    Returns (timestamp, age_seconds). If unknown, (None, None).
    """
    if not source:
        return (None, None)

    rows = list(
        Reading.objects
        .filter(source=source)
        .order_by("-created_at")[:50]
    )
    if len(rows) < 2:
        return (None, None)

    for i in range(len(rows) - 1):
        newer = rows[i]
        older = rows[i + 1]
        if (newer.motion0 != older.motion0) or (newer.motion1 != older.motion1):
            ts = newer.created_at
            age = int(max(0.0, (timezone.now() - ts).total_seconds()))
            return (ts, age)

    return (None, None)


def motion_level_from_age(age_sec: Optional[int]) -> tuple[str, str]:
    """
    Color levels for motion recency:
      High:  < 2 minutes
      Med:   2–15 minutes
      Low:   > 15 minutes or unknown
    """
    if age_sec is None:
        return ("low", "QUIET")
    if age_sec < 120:
        return ("high", "MOTION")
    if age_sec < 15 * 60:
        return ("med", "RECENT")
    return ("low", "QUIET")


# ----------------------------
# Location helpers
# ----------------------------

LOC_LABEL = {
    0: "Attic",
    1: "Crawlspace",
    2: "Basement",
    3: "Other",
}


def parse_loc_param(request) -> Optional[int]:
    """
    Accepts ?loc=0..3. Returns int or None.
    """
    raw = (request.GET.get("loc") or "").strip()
    if raw == "":
        return None
    try:
        v = int(raw)
    except Exception:
        return None
    return v if v in (0, 1, 2, 3) else None


# ----------------------------
# Pages
# ----------------------------

def index(request):
    """
    Home dashboard: one card per active location.

    "Active" = a location value that has ever appeared in the database.
    This avoids showing "3 stale" when you only have one StructureNode deployed.

    If there are ZERO active locations, we show placeholders for all 4 known locations.
    """
    STALE_SEC = 20 * 60  # 20 minutes

    # Active locations = distinct non-null location values present in DB
    active_locations = list(
        Reading.objects
        .exclude(location__isnull=True)
        .values_list("location", flat=True)
        .distinct()
    )

    active_count = len(active_locations)

    # If nothing has ever reported yet, show placeholders for all known locations.
    display_locations = active_locations if active_locations else [0, 1, 2, 3]

    cards = []
    last_ts_any = None
    stale_count = 0
    ok_count = 0

    for loc in display_locations:
        r = (
            Reading.objects
            .filter(location=loc)
            .order_by("-created_at")
            .first()
        )

        card = {
            "location": int(loc),
            "location_label": LOC_LABEL.get(int(loc), "Other"),
            "has_data": bool(r),
        }

        if not r:
            # Only count stale/fresh when location is active.
            card.update({
                "ts": None,
                "age_sec": None,
                "stale": False,  # "no data yet" is not "stale"
                "temp_f": None,
                "hum_pct": None,
                "batt_v": None,
                "batt_pct": None,
                "risk_level": "low",
                "risk_label": "LOW",
                "dew_point_f": None,
                "spread_f": None,
                "source": "",
                "rssi": 0,
                "border_class": "border-secondary",
                "risk_pill_class": "text-bg-secondary",
            })
            cards.append(card)
            continue

        ts = r.created_at
        age_sec = int(max(0.0, (timezone.now() - ts).total_seconds()))
        stale = age_sec > STALE_SEC

        if last_ts_any is None or ts > last_ts_any:
            last_ts_any = ts

        if stale:
            stale_count += 1
        else:
            ok_count += 1

        temp_f = None if r.temp_c is None else c_to_f(float(r.temp_c))

        # Prefer stored dew_point_c if present (V3A/V4), else compute
        dp_f = None
        spread_f = None
        risk_level = "low"
        risk_label = "LOW"
        try:
            dp_c = float(r.dew_point_c) if (r.dew_point_c is not None) else None
            if dp_c is None and (r.temp_c is not None and r.hum_pct is not None):
                dp_c = dew_point_c(float(r.temp_c), float(r.hum_pct))
            if dp_c is not None and temp_f is not None:
                dp_f = c_to_f(dp_c)
                spread_f = float(temp_f) - float(dp_f)
                risk_level, risk_label = risk_from_spread_f(spread_f)
        except Exception:
            pass

        batt_v = mv_to_volts(r.batt_mv)
        batt_pct = int(r.batt_pct) if (r.batt_pct is not None) else mv_to_percent(r.batt_mv)

        card.update({
            "ts": ts,
            "age_sec": age_sec,
            "stale": stale,
            "temp_f": None if temp_f is None else round(temp_f, 1),
            "hum_pct": None if r.hum_pct is None else round(float(r.hum_pct), 1),
            "batt_v": None if batt_v is None else round(batt_v, 2),
            "batt_pct": batt_pct,
            "risk_level": risk_level,
            "risk_label": risk_label,
            "dew_point_f": None if dp_f is None else round(dp_f, 1),
            "spread_f": None if spread_f is None else round(spread_f, 1),
            "source": r.source or "",
            "rssi": int(r.rssi or 0),
        })

        # Style classes from risk_level
        if card["risk_level"] == "high":
            card["border_class"] = "border-danger"
            card["risk_pill_class"] = "text-bg-danger"
        elif card["risk_level"] == "med":
            card["border_class"] = "border-warning"
            card["risk_pill_class"] = "text-bg-warning"
        else:
            card["border_class"] = "border-success"
            card["risk_pill_class"] = "text-bg-success"

        cards.append(card)

    # System banner:
    # - If no active locations: "No sensor data yet"
    # - Else evaluate only active locations (ok_count + stale_count)
    if active_count == 0:
        system = {
            "summary": "No sensor data yet. Start the BLE worker and power on at least one sensor.",
            "alert_class": "alert-warning",
            "last_ts": None,
        }
    else:
        if stale_count > 0 and ok_count == 0:
            system = {
                "summary": f"All active locations appear stale ({stale_count} stale).",
                "alert_class": "alert-danger",
                "last_ts": last_ts_any,
            }
        elif stale_count > 0:
            system = {
                "summary": f"Some active locations are stale ({stale_count} stale, {ok_count} fresh).",
                "alert_class": "alert-warning",
                "last_ts": last_ts_any,
            }
        else:
            system = {
                "summary": f"All active locations reporting ({ok_count} fresh).",
                "alert_class": "alert-success",
                "last_ts": last_ts_any,
            }

    return render(
        request,
        "monitor/index.html",
        {
            "cards": cards,
            "system": system,
            "active_count": active_count,
        },
    )


def live_page(request):
    # live.html can fetch /api/last?loc=
    return render(request, "monitor/live.html", {"loc": parse_loc_param(request)})


def timeline_page(request):
    return render(request, "monitor/timeline.html", {"loc": parse_loc_param(request)})


def history_page(request):
    loc = parse_loc_param(request)

    qs = Reading.objects.order_by("-created_at")
    if loc is not None:
        qs = qs.filter(location=loc)

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page", "1"))

    for r in page_obj.object_list:
        r.location_label = LOC_LABEL.get(r.location, "Other") if r.location is not None else "—"
        r.batt_v = (float(r.batt_mv) / 1000.0) if (r.batt_mv is not None and r.batt_mv > 0) else None
        r.temp_f = ((float(r.temp_c) * 9.0 / 5.0) + 32.0) if (r.temp_c is not None) else None

        dp_c = float(r.dew_point_c) if (r.dew_point_c is not None) else None
        r.dew_point_f = ((dp_c * 9.0 / 5.0) + 32.0) if (dp_c is not None) else None

        r.risk_level = "low"
        r.risk_label = "LOW"
        if r.temp_f is not None and r.dew_point_f is not None:
            spread_f = float(r.temp_f) - float(r.dew_point_f)
            r.risk_level, r.risk_label = risk_from_spread_f(spread_f)

    return render(
        request,
        "monitor/history.html",
        {
            "page_obj": page_obj,
            "loc": loc,
            "loc_label": None if loc is None else LOC_LABEL.get(loc, "Other"),
        },
    )


# ----------------------------
# APIs
# ----------------------------

def api_last(request):
    """
    Latest reading, optionally filtered by location (?loc=0..3).
    """
    loc = parse_loc_param(request)

    qs = Reading.objects.order_by("-created_at")
    if loc is not None:
        qs = qs.filter(location=loc)

    r = qs.first()
    if not r:
        return JsonResponse({"valid": False, "loc": loc})

    batt_v = mv_to_volts(r.batt_mv)
    batt_pct = int(r.batt_pct) if (r.batt_pct is not None) else mv_to_percent(r.batt_mv)

    dp_f = None
    spread_f = None
    risk_level = "low"
    risk_label = "LOW"
    try:
        if r.dew_point_c is not None:
            dp_f = c_to_f(float(r.dew_point_c))
        elif r.temp_c is not None and r.hum_pct is not None:
            dp_c = dew_point_c(float(r.temp_c), float(r.hum_pct))
            dp_f = c_to_f(dp_c)

        if r.temp_c is not None and dp_f is not None:
            temp_f = c_to_f(float(r.temp_c))
            spread_f = temp_f - dp_f
            risk_level, risk_label = risk_from_spread_f(spread_f)
    except Exception:
        pass

    last_motion_ts, last_motion_age_sec = motion_status_for_source(r.source or "")
    motion_level, motion_label = motion_level_from_age(last_motion_age_sec)

    loc_value = r.location if (r.location is not None) else None
    loc_label = None if loc_value is None else LOC_LABEL.get(int(loc_value), "Other")

    return JsonResponse({
        "valid": True,
        "ts": r.created_at.isoformat(),
        "temp_c": r.temp_c,
        "hum_pct": r.hum_pct,
        "press_hpa": r.press_hpa,
        "batt_mv": r.batt_mv,
        "batt_v": None if batt_v is None else round(batt_v, 2),
        "batt_pct": batt_pct,
        "flags": r.flags,
        "seq": r.seq,
        "motion0": r.motion0,
        "motion1": r.motion1,
        "rssi": r.rssi,
        "source": r.source,

        "location": loc_value,
        "location_label": loc_label,

        "dew_point_f": None if dp_f is None else round(dp_f, 1),
        "spread_f": None if spread_f is None else round(spread_f, 1),
        "risk_level": risk_level,
        "risk_label": risk_label,

        "motion_label": motion_label,
        "motion_level": motion_level,
        "last_motion_ts": None if last_motion_ts is None else last_motion_ts.isoformat(),
        "last_motion_age_sec": last_motion_age_sec,
    })


def api_history(request):
    """
    Timeline rows, optionally filtered by location (?loc=0..3).
    """
    rng = (request.GET.get("range") or "6h").lower()
    loc = parse_loc_param(request)
    now = timezone.now()

    if rng == "1h":
        since = now - timedelta(hours=1)
    elif rng == "24h":
        since = now - timedelta(hours=24)
    elif rng == "7d":
        since = now - timedelta(days=7)
    else:
        since = now - timedelta(hours=6)

    qs = Reading.objects.filter(created_at__gte=since)
    if loc is not None:
        qs = qs.filter(location=loc)

    qs = qs.order_by("-created_at")[:2000]
    rows = list(qs)
    rows.reverse()

    data = []
    for row in rows:
        temp_c = row.temp_c
        temp_f = None if temp_c is None else (float(temp_c) * 9.0 / 5.0 + 32.0)

        batt_v = mv_to_volts(row.batt_mv)
        batt_pct = int(row.batt_pct) if (row.batt_pct is not None) else mv_to_percent(row.batt_mv)

        data.append({
            "ts": row.created_at.isoformat(),
            "temp_f": None if temp_f is None else round(temp_f, 2),
            "hum_pct": None if row.hum_pct is None else round(float(row.hum_pct), 2),
            "press_hpa": None if row.press_hpa is None else round(float(row.press_hpa), 1),
            "batt_v": None if batt_v is None else round(batt_v, 2),
            "batt_pct": batt_pct,
            "rssi": int(row.rssi or 0),
            "seq": int(row.seq or 0),
            "source": row.source or "",
            "location": row.location,
        })

    return JsonResponse({"range": rng, "loc": loc, "count": len(data), "data": data})

# ----------------------------
# Helpers (units + scoring)
# ----------------------------
def c_to_f(c: Optional[float]) -> Optional[float]:
    if c is None:
        return None
    return (float(c) * 9.0 / 5.0) + 32.0


def mv_to_v(mv: Optional[int]) -> Optional[float]:
    if mv is None:
        return None
    return float(mv) / 1000.0


def safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def risk_from_spread_f(spread_f: Optional[float]) -> Tuple[str, str]:
    """
    Matches your Live UI legend:
      Low  >= 10°F
      Med  5–10°F
      High < 5°F
    """
    if spread_f is None or math.isnan(spread_f):
        return ("unknown", "UNKNOWN")
    if spread_f < 5.0:
        return ("high", "HIGH")
    if spread_f < 10.0:
        return ("med", "MED")
    return ("low", "LOW")


def motion_level_from_age_sec(age_sec: Optional[float]) -> Tuple[str, str]:
    """
    Matches your Motion UI legend:
      Quiet  > 15 min
      Recent 2–15 min
      Motion < 2 min
    """
    if age_sec is None:
        return ("unknown", "UNKNOWN")
    if age_sec < 120:
        return ("high", "MOTION")
    if age_sec < 900:
        return ("med", "RECENT")
    return ("low", "QUIET")


def overall_level(levels: List[str]) -> str:
    """
    Combine multiple low/med/high/unknown into one.
    """
    if "high" in levels:
        return "high"
    if "med" in levels:
        return "med"
    if all(l == "unknown" for l in levels):
        return "unknown"
    return "low"


def linear_slope_per_hour(points: List[Tuple[float, float]]) -> Optional[float]:
    """
    Simple least-squares slope (y per hour).
    points: [(t_hours_since_start, y), ...]
    """
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)

    denom = sum((x - x_mean) ** 2 for x in xs)
    if denom == 0:
        return None
    numer = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    return numer / denom


# ----------------------------
# Intelligence core
# ----------------------------
@dataclass
class Anomaly:
    code: str
    severity: str  # low/med/high
    message: str


def compute_anomalies(rows: List[Dict[str, Any]]) -> List[Anomaly]:
    """
    rows: list of dicts sorted by created_at asc, already filtered to a window.
    """
    anomalies: List[Anomaly] = []
    if len(rows) < 2:
        return anomalies

    # 1) Gaps (missing updates)
    # Estimate expected cadence from median delta (clamped).
    deltas = []
    for i in range(1, len(rows)):
        dt_sec = (rows[i]["created_at"] - rows[i - 1]["created_at"]).total_seconds()
        if dt_sec > 0:
            deltas.append(dt_sec)
    if deltas:
        deltas_sorted = sorted(deltas)
        median = deltas_sorted[len(deltas_sorted) // 2]
        expected = max(10.0, min(median, 15 * 60.0))  # clamp
        gap_threshold = expected * 6.0  # "6 missed-ish"
        big_gaps = [d for d in deltas if d >= gap_threshold]
        if big_gaps:
            anomalies.append(Anomaly(
                code="gaps",
                severity="med" if max(big_gaps) < 3600 else "high",
                message=f"Detected {len(big_gaps)} data gaps. Largest gap: {int(max(big_gaps))} seconds."
            ))

    # 2) Sudden jumps (temp/humidity)
    def jump_check(field: str, max_jump: float, max_dt: float, label: str):
        hits = 0
        for i in range(1, len(rows)):
            dt_sec = (rows[i]["created_at"] - rows[i - 1]["created_at"]).total_seconds()
            if dt_sec <= 0 or dt_sec > max_dt:
                continue
            a = safe_float(rows[i - 1].get(field))
            b = safe_float(rows[i].get(field))
            if a is None or b is None:
                continue
            if abs(b - a) >= max_jump:
                hits += 1
        if hits:
            anomalies.append(Anomaly(
                code=f"{field}_jumps",
                severity="med",
                message=f"{label}: {hits} sudden changes (≥ {max_jump} within {int(max_dt)}s)."
            ))

    jump_check("temp_c", max_jump=2.2, max_dt=120, label="Temperature")
    jump_check("hum_pct", max_jump=5.0, max_dt=120, label="Humidity")

    # 3) Counter resets (seq or motion dropping sharply)
    resets = 0
    for i in range(1, len(rows)):
        prev_seq = rows[i - 1].get("seq")
        curr_seq = rows[i].get("seq")
        if prev_seq is not None and curr_seq is not None and curr_seq < prev_seq:
            resets += 1
    if resets:
        anomalies.append(Anomaly(
            code="seq_resets",
            severity="low",
            message=f"Sequence counter decreased {resets} times (device reboot or rollover)."
        ))

    # 4) Implausible ranges (cheap sanity checks)
    # (Not “meteorology-correct”, just “sensor seems broken”)
    bad = 0
    for r in rows:
        t = safe_float(r.get("temp_c"))
        h = safe_float(r.get("hum_pct"))
        p = safe_float(r.get("press_hpa"))
        if t is not None and (t < -30 or t > 80):
            bad += 1
        if h is not None and (h < 0 or h > 100):
            bad += 1
        if p is not None and (p < 800 or p > 1100):
            bad += 1
    if bad:
        anomalies.append(Anomaly(
            code="implausible",
            severity="high" if bad > 3 else "med",
            message=f"{bad} readings contained implausible values (possible sensor glitch)."
        ))

    return anomalies


def find_last_motion(rows: List[Dict[str, Any]]) -> Optional[Any]:
    """
    Motion is based on changes in motion0/motion1 counters.
    We find the newest timestamp where either counter increased (per source).
    """
    if not rows:
        return None

    # group by source so resets don’t confuse us
    by_source: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        src = r.get("source") or "unknown"
        by_source.setdefault(src, []).append(r)

    last_motion_ts = None

    for src, lst in by_source.items():
        lst.sort(key=lambda x: x["created_at"])
        prev0 = None
        prev1 = None
        for r in lst:
            m0 = r.get("motion0")
            m1 = r.get("motion1")
            # ignore Nones
            if prev0 is not None and m0 is not None and m0 > prev0:
                last_motion_ts = r["created_at"] if (last_motion_ts is None or r["created_at"] > last_motion_ts) else last_motion_ts
            if prev1 is not None and m1 is not None and m1 > prev1:
                last_motion_ts = r["created_at"] if (last_motion_ts is None or r["created_at"] > last_motion_ts) else last_motion_ts
            prev0 = m0 if m0 is not None else prev0
            prev1 = m1 if m1 is not None else prev1

    return last_motion_ts


def recommendations(overall: str, moisture_level: str, motion_level: str, batt_pct: Optional[int], stale: bool, anomalies: List[Anomaly]) -> List[str]:
    recs: List[str] = []

    if stale:
        recs.append("Data is stale — check BLE scanner uptime, RSSI, and sensor power. Consider logging missed scan windows.")

    if moisture_level == "high":
        recs += [
            "High condensation risk — inspect roof deck/nails for moisture, check bath fan ducting leaks/disconnects, and confirm soffit→ridge airflow path.",
            "Air-seal ceiling penetrations (lights, attic hatch) to reduce warm moist air entering the attic."
        ]
    elif moisture_level == "med":
        recs += [
            "Moderate moisture risk — verify attic ventilation is unobstructed and bath fans exhaust outdoors (not into attic)."
        ]
    else:
        recs.append("Moisture conditions look stable — keep tracking seasonal changes and rainy/windy events.")

    if motion_level == "high":
        recs.append("Motion detected very recently — consider pests/animals. Check entry points at eaves, vents, and crawl openings.")
    elif motion_level == "med":
        recs.append("Recent motion — if this is unexpected, consider adding a second PIR angle or a door/hatch contact sensor.")
    else:
        recs.append("No recent motion — good baseline.")

    if batt_pct is not None and batt_pct <= 20:
        recs.append("Battery is low — consider longer sleep intervals, lower advertising rate, or a higher-capacity cell.")

    if any(a.severity == "high" for a in anomalies):
        recs.append("High-severity anomalies present — inspect sensor calibration, wiring, and look for reboots/resets.")

    # Keep it short
    return recs[:6]


# ----------------------------
# NEW API endpoint
# ----------------------------
def api_summary(request):
    """
    /api/summary.json?loc=0&window=24
      loc: optional int
      window: hours (default 24)
    """
    loc_raw = request.GET.get("loc", None)
    window_h = request.GET.get("window", "24")
    try:
        window_h = max(1, min(int(window_h), 24 * 14))  # 1h..14d
    except Exception:
        window_h = 24

    qs = Reading.objects.all()
    if loc_raw is not None and str(loc_raw).strip() != "":
        try:
            loc = int(loc_raw)
            qs = qs.filter(location=loc)
        except Exception:
            loc = None
    else:
        loc = None

    now = timezone.now()
    since = now - timedelta(hours=window_h)

    # Latest
    last = qs.order_by("-created_at").first()
    if not last:
        return JsonResponse({"valid": False, "error": "No data"}, status=200)

    # Window rows (pull only what we need)
    window_rows = list(
        qs.filter(created_at__gte=since)
          .order_by("created_at")
          .values(
              "created_at", "source", "rssi", "location",
              "temp_c", "hum_pct", "press_hpa",
              "batt_mv", "batt_pct", "dew_point_c",
              "motion0", "motion1", "seq"
          )
    )

    # Compute last-derived metrics
    temp_f = c_to_f(last.temp_c)
    dp_f = c_to_f(last.dew_point_c)
    spread_f = (temp_f - dp_f) if (temp_f is not None and dp_f is not None) else None
    moisture_level, moisture_label = risk_from_spread_f(spread_f)

    age_sec = (now - last.created_at).total_seconds()
    stale = age_sec > 60  # tune this to your expected sensor cadence
    freshness_level = "high" if age_sec < 30 else ("med" if age_sec < 120 else "high")  # “high severity” if stale

    # Motion analysis within the window
    last_motion_ts = find_last_motion(window_rows)
    last_motion_age = (now - last_motion_ts).total_seconds() if last_motion_ts else None
    motion_level, motion_label = motion_level_from_age_sec(last_motion_age)

    # Window stats
    temps = [safe_float(r.get("temp_c")) for r in window_rows if safe_float(r.get("temp_c")) is not None]
    hums  = [safe_float(r.get("hum_pct")) for r in window_rows if safe_float(r.get("hum_pct")) is not None]
    spreads = []
    for r in window_rows:
        tf = c_to_f(safe_float(r.get("temp_c")))
        dpf = c_to_f(safe_float(r.get("dew_point_c")))
        if tf is not None and dpf is not None:
            spreads.append(tf - dpf)

    # Trend slopes (use downsampled points to keep it light)
    def build_points(field: str, convert=None, step: int = 10) -> List[Tuple[float, float]]:
        pts: List[Tuple[float, float]] = []
        if not window_rows:
            return pts
        t0 = window_rows[0]["created_at"]
        for i, r in enumerate(window_rows[::step]):
            v = r.get(field)
            v = safe_float(v)
            if v is None:
                continue
            if convert:
                v = convert(v)
                if v is None:
                    continue
            th = (r["created_at"] - t0).total_seconds() / 3600.0
            pts.append((th, float(v)))
        return pts

    temp_slope = linear_slope_per_hour(build_points("temp_c", convert=c_to_f))
    hum_slope  = linear_slope_per_hour(build_points("hum_pct"))
    dp_slope   = linear_slope_per_hour(build_points("dew_point_c", convert=c_to_f))
    spread_slope = None
    spread_pts = []
    if window_rows:
        t0 = window_rows[0]["created_at"]
        for r in window_rows[::10]:
            tf = c_to_f(safe_float(r.get("temp_c")))
            dpf = c_to_f(safe_float(r.get("dew_point_c")))
            if tf is None or dpf is None:
                continue
            th = (r["created_at"] - t0).total_seconds() / 3600.0
            spread_pts.append((th, tf - dpf))
        spread_slope = linear_slope_per_hour(spread_pts)

    # Anomalies
    anomalies = compute_anomalies(window_rows)

    # Overall risk = moisture + motion + stale/anomalies + battery
    battery_level = "low"
    if last.batt_pct is not None:
        if last.batt_pct <= 15:
            battery_level = "high"
        elif last.batt_pct <= 35:
            battery_level = "med"

    overall = overall_level([
        moisture_level,
        motion_level,
        ("high" if stale else "low"),
        ("high" if any(a.severity == "high" for a in anomalies) else ("med" if anomalies else "low")),
        battery_level,
    ])

    recs = recommendations(
        overall=overall,
        moisture_level=moisture_level,
        motion_level=motion_level,
        batt_pct=last.batt_pct,
        stale=stale,
        anomalies=anomalies
    )

    payload = {
        "valid": True,
        "loc": loc,
        "window_hours": window_h,
        "ts": last.created_at.isoformat(),
        "age_sec": round(age_sec, 1),

        "latest": {
            "temp_c": last.temp_c,
            "temp_f": round(temp_f, 1) if temp_f is not None else None,
            "hum_pct": last.hum_pct,
            "press_hpa": last.press_hpa,
            "dew_point_c": last.dew_point_c,
            "dew_point_f": round(dp_f, 1) if dp_f is not None else None,
            "spread_f": round(spread_f, 1) if spread_f is not None else None,
            "batt_mv": last.batt_mv,
            "batt_v": round(mv_to_v(last.batt_mv), 3) if last.batt_mv is not None else None,
            "batt_pct": last.batt_pct,
            "rssi": last.rssi,
            "source": last.source,
            "seq": last.seq,
        },

        "risk": {
            "overall_level": overall,                 # low/med/high/unknown
            "moisture_level": moisture_level,         # low/med/high/unknown
            "moisture_label": moisture_label,         # LOW/MED/HIGH
            "motion_level": motion_level,             # low/med/high/unknown
            "motion_label": motion_label,             # QUIET/RECENT/MOTION
            "last_motion_ts": last_motion_ts.isoformat() if last_motion_ts else None,
            "last_motion_age_sec": round(last_motion_age, 1) if last_motion_age is not None else None,
            "stale": stale,
        },

        "window_stats": {
            "count": len(window_rows),
            "temp_f_min": round(c_to_f(min(temps)), 1) if temps else None,
            "temp_f_max": round(c_to_f(max(temps)), 1) if temps else None,
            "hum_min": round(min(hums), 1) if hums else None,
            "hum_max": round(max(hums), 1) if hums else None,
            "spread_f_min": round(min(spreads), 1) if spreads else None,
        },

        "trends": {
            "temp_f_slope_per_hr": round(temp_slope, 3) if temp_slope is not None else None,
            "hum_slope_per_hr": round(hum_slope, 3) if hum_slope is not None else None,
            "dew_f_slope_per_hr": round(dp_slope, 3) if dp_slope is not None else None,
            "spread_f_slope_per_hr": round(spread_slope, 3) if spread_slope is not None else None,
        },

        "anomalies": [
            {"code": a.code, "severity": a.severity, "message": a.message}
            for a in anomalies
        ],

        "recommendations": recs,
    }

    return JsonResponse(payload, json_dumps_params={"indent": 2})
