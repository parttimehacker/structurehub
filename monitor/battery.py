from __future__ import annotations
from dataclasses import dataclass


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# Same curve as M5Battery.h
_CURVE = [
    (4.20, 100),
    (4.10,  90),
    (4.00,  80),
    (3.92,  70),
    (3.85,  60),
    (3.79,  50),
    (3.74,  40),
    (3.70,  30),
    (3.65,  20),
    (3.55,  10),
    (3.40,   5),
    (3.30,   2),
    (3.20,   0),
]


def voltage_to_percent(v: float) -> int:
    """
    Port of M5Battery::voltageToPercent(float v)
    """
    v = clamp(float(v), 3.20, 4.20)

    for i in range(len(_CURVE) - 1):
        av, ap = _CURVE[i]
        bv, bp = _CURVE[i + 1]
        if v <= av and v >= bv:
            # t = (a.v - v) / (a.v - b.v)
            t = (av - v) / (av - bv)
            pf = lerp(float(ap), float(bp), t)
            p = int(pf + 0.5)
            return max(0, min(100, p))

    return 100 if v >= 4.20 else 0


def mv_to_volts(mv: int | float | None) -> float | None:
    if mv is None:
        return None
    return float(mv) / 1000.0


def mv_to_percent(mv: int | float | None) -> int | None:
    v = mv_to_volts(mv)
    if v is None:
        return None
    return voltage_to_percent(v)
