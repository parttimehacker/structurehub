from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional, Union

COMPANY_ID = 0xFFFF
PROTOCOL_V2 = 0x0002
PROTOCOL_V3A = 0x0003
PROTOCOL_V4 = 0x0004

# -----------------------------
# V2 (prefixed with companyId)
# -----------------------------
_FMT_V2 = "<H H h H H H H H I I"
_LEN_V2 = struct.calcsize(_FMT_V2)

@dataclass(frozen=True)
class DecodedV2:
    protocol: int
    temp_c: float
    hum_pct: float
    press_hpa: float
    batt_mv: int
    flags: int
    seq: int
    motion0: int
    motion1: int


# -----------------------------
# V3A (prefixed with companyId)
# -----------------------------
_FMT_V3A = "<H H h H H H H H H H B B H h"
_LEN_V3A = struct.calcsize(_FMT_V3A)

@dataclass(frozen=True)
class DecodedV3A:
    protocol: int
    temp_c: float
    hum_pct: float
    press_hpa: float
    batt_mv: int
    flags: int
    seq: int
    motion0: int
    motion1: int
    batt_pct: int
    uptime_min: int
    dew_point_c: float


# -----------------------------
# V4 (usually NOT prefixed)
# -----------------------------
_FMT_V4_NOPREFIX = "<H B h H H H H H H H B B H h"
_LEN_V4_NOPREFIX = struct.calcsize(_FMT_V4_NOPREFIX)  # 25 bytes

# Prefixed V4 (rare): companyId + V4_NOPREFIX
_FMT_V4_PREFIXED = "<H " + _FMT_V4_NOPREFIX[1:]
_LEN_V4_PREFIXED = struct.calcsize(_FMT_V4_PREFIXED)  # 27 bytes

@dataclass(frozen=True)
class DecodedV4:
    protocol: int
    location: int
    temp_c: float
    hum_pct: float
    press_hpa: float
    batt_mv: int
    flags: int
    seq: int
    motion0: int
    motion1: int
    batt_pct: int
    uptime_min: int
    dew_point_c: float


DecodedAny = Union[DecodedV2, DecodedV3A, DecodedV4]


def decode_payload(mfg: bytes) -> Optional[DecodedAny]:
    """
    Decode manufacturer bytes for V2, V3A, or V4.

    Inputs may be:
      - V2/V3A: bytes include companyId first (companyId, protocol, ...)
      - V4: bytes are usually unprefixed and begin with protocol (protocol, location, ...)

    Returns:
      - DecodedV2 if protocol==0x0002 and length matches V2
      - DecodedV3A if protocol==0x0003 and length matches V3A
      - DecodedV4 if protocol==0x0004 and length matches V4 (prefixed or unprefixed)
      - None otherwise
    """
    if not mfg or len(mfg) < 2:
        return None

    # ---- Try V4 unprefixed first (common with Bleak) ----
    if len(mfg) == _LEN_V4_NOPREFIX:
        (proto,) = struct.unpack_from("<H", mfg, 0)
        if proto == PROTOCOL_V4:
            (
                protocol, location,
                tempC_x100, hum_x100, press_x10, batt_mv, flags, seq,
                motion0, motion1,
                batt_pct, _rsv0, uptime_min, dewPointC_x100
            ) = struct.unpack(_FMT_V4_NOPREFIX, mfg)

            if location > 3:
                location = 3

            return DecodedV4(
                protocol=int(protocol),
                location=int(location),
                temp_c=float(tempC_x100) / 100.0,
                hum_pct=float(hum_x100) / 100.0,
                press_hpa=float(press_x10) / 10.0,
                batt_mv=int(batt_mv),
                flags=int(flags),
                seq=int(seq),
                motion0=int(motion0),
                motion1=int(motion1),
                batt_pct=int(batt_pct),
                uptime_min=int(uptime_min),
                dew_point_c=float(dewPointC_x100) / 100.0,
            )

    # Need at least companyId + protocol for prefixed formats
    if len(mfg) < 4:
        return None

    company, proto = struct.unpack_from("<H H", mfg, 0)
    if company != COMPANY_ID:
        return None

    # ---- V4 prefixed (rare) ----
    if proto == PROTOCOL_V4 and len(mfg) == _LEN_V4_PREFIXED:
        (
            company, protocol, location,
            tempC_x100, hum_x100, press_x10, batt_mv, flags, seq,
            motion0, motion1,
            batt_pct, _rsv0, uptime_min, dewPointC_x100
        ) = struct.unpack(_FMT_V4_PREFIXED, mfg)

        if location > 3:
            location = 3

        return DecodedV4(
            protocol=int(protocol),
            location=int(location),
            temp_c=float(tempC_x100) / 100.0,
            hum_pct=float(hum_x100) / 100.0,
            press_hpa=float(press_x10) / 10.0,
            batt_mv=int(batt_mv),
            flags=int(flags),
            seq=int(seq),
            motion0=int(motion0),
            motion1=int(motion1),
            batt_pct=int(batt_pct),
            uptime_min=int(uptime_min),
            dew_point_c=float(dewPointC_x100) / 100.0,
        )

    # ---- V2 ----
    if proto == PROTOCOL_V2:
        if len(mfg) != _LEN_V2:
            return None
        (
            company, protocol,
            tempC_x100, hum_x100, press_x10, batt_mv, flags, seq,
            motion0, motion1
        ) = struct.unpack(_FMT_V2, mfg)

        return DecodedV2(
            protocol=int(protocol),
            temp_c=float(tempC_x100) / 100.0,
            hum_pct=float(hum_x100) / 100.0,
            press_hpa=float(press_x10) / 10.0,
            batt_mv=int(batt_mv),
            flags=int(flags),
            seq=int(seq),
            motion0=int(motion0),
            motion1=int(motion1),
        )

    # ---- V3A ----
    if proto == PROTOCOL_V3A:
        if len(mfg) != _LEN_V3A:
            return None
        (
            company, protocol,
            tempC_x100, hum_x100, press_x10, batt_mv, flags, seq,
            motion0, motion1,
            batt_pct, _rsv0, uptime_min, dewPointC_x100
        ) = struct.unpack(_FMT_V3A, mfg)

        return DecodedV3A(
            protocol=int(protocol),
            temp_c=float(tempC_x100) / 100.0,
            hum_pct=float(hum_x100) / 100.0,
            press_hpa=float(press_x10) / 10.0,
            batt_mv=int(batt_mv),
            flags=int(flags),
            seq=int(seq),
            motion0=int(motion0),
            motion1=int(motion1),
            batt_pct=int(batt_pct),
            uptime_min=int(uptime_min),
            dew_point_c=float(dewPointC_x100) / 100.0,
        )

    return None
