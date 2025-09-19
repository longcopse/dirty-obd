# core/decoders.py
from typing import List, Optional

def _pct(a: int) -> float:         return (100.0 * a) / 255.0
def _trim(a: int) -> float:        return (a - 128) / 1.28   # %
def _kpa(a: int) -> int:           return a                  # kPa
def _kpa3(a: int) -> int:          return 3 * a              # kPa (fuel pressure)
def _temp(a: int) -> int:          return a - 40             # °C
def _u16(a: int, b: int) -> int:   return (a << 8) + b

def rpm(d: List[int]) -> Optional[float]:
    if len(d) >= 4 and d[0] == 0x41 and d[1] == 0x0C:
        return _u16(d[2], d[3]) / 4.0

def speed_kph(d: List[int]) -> Optional[int]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x0D:
        return d[2]

def coolant_c(d: List[int]) -> Optional[int]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x05:
        return _temp(d[2])

def intake_air_c(d: List[int]) -> Optional[int]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x0F:
        return _temp(d[2])

def ambient_air_c(d: List[int]) -> Optional[int]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x46:
        return _temp(d[2])

def maf_gps(d: List[int]) -> Optional[float]:
    if len(d) >= 4 and d[0] == 0x41 and d[1] == 0x10:
        return _u16(d[2], d[3]) / 100.0  # g/s

def throttle_pct(d: List[int]) -> Optional[float]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x11:
        return _pct(d[2])

def engine_load_pct(d: List[int]) -> Optional[float]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x04:
        return _pct(d[2])

def map_kpa(d: List[int]) -> Optional[int]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x0B:
        return _kpa(d[2])

def baro_kpa(d: List[int]) -> Optional[int]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x33:
        return _kpa(d[2])

def fuel_level_pct(d: List[int]) -> Optional[float]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x2F:
        return _pct(d[2])

def timing_advance_deg(d: List[int]) -> Optional[float]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x0E:
        return d[2] / 2.0 - 64.0

def fuel_pressure_kpa(d: List[int]) -> Optional[int]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x0A:
        return _kpa3(d[2])  # 3 * A kPa

def stft_b1_pct(d: List[int]) -> Optional[float]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x06:
        return _trim(d[2])

def ltft_b1_pct(d: List[int]) -> Optional[float]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x07:
        return _trim(d[2])

def stft_b2_pct(d: List[int]) -> Optional[float]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x08:
        return _trim(d[2])

def ltft_b2_pct(d: List[int]) -> Optional[float]:
    if len(d) >= 3 and d[0] == 0x41 and d[1] == 0x09:
        return _trim(d[2])

def runtime_s(d: List[int]) -> Optional[int]:
    if len(d) >= 4 and d[0] == 0x41 and d[1] == 0x1F:
        return _u16(d[2], d[3])

def distance_since_clear_km(d: List[int]) -> Optional[int]:
    if len(d) >= 4 and d[0] == 0x41 and d[1] == 0x31:
        return _u16(d[2], d[3])

DECODERS = {
    (0x01, 0x0C): ("rpm", rpm),
    (0x01, 0x0D): ("speed_kph", speed_kph),
    (0x01, 0x05): ("coolant_c", coolant_c),
    (0x01, 0x0F): ("intake_air_c", intake_air_c),
    (0x01, 0x46): ("ambient_air_c", ambient_air_c),
    (0x01, 0x10): ("maf_gps", maf_gps),
    (0x01, 0x11): ("throttle_pct", throttle_pct),
    (0x01, 0x04): ("engine_load_pct", engine_load_pct),
    (0x01, 0x0B): ("map_kpa", map_kpa),
    (0x01, 0x33): ("baro_kpa", baro_kpa),
    (0x01, 0x2F): ("fuel_level_pct", fuel_level_pct),
    (0x01, 0x0E): ("timing_advance_deg", timing_advance_deg),
    (0x01, 0x0A): ("fuel_pressure_kpa", fuel_pressure_kpa),
    (0x01, 0x06): ("stft_b1_pct", stft_b1_pct),
    (0x01, 0x07): ("ltft_b1_pct", ltft_b1_pct),
    (0x01, 0x08): ("stft_b2_pct", stft_b2_pct),
    (0x01, 0x09): ("ltft_b2_pct", ltft_b2_pct),
    (0x01, 0x1F): ("runtime_s", runtime_s),
    (0x01, 0x31): ("distance_since_clear_km", distance_since_clear_km),
}
