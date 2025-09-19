# core/dtc_db.py
import os, json
from typing import Dict, Optional, List

_profiles_path = "./dtc_profiles"
_state_path = "./state/vehicle_profile.json"
_active_profile = "Default"
_base_map: Dict[str,str] = {}
_vehicle_map: Dict[str,str] = {}

def _normalize_name(name: str) -> str:
    return name.strip()

def _profile_filename(name: str) -> str:
    # "Jaguar XF" -> "jaguar_xf.txt"
    return f"{_normalize_name(name).lower().replace(' ', '_')}.txt"

def _parse_file(path: str) -> Dict[str, str]:
    """
    Accepts lines like:
      P0420 = Catalyst system efficiency below threshold (Bank 1)
      P0171: System too lean (Bank 1)
      P0301 Misfire detected (Cylinder 1)
    Ignores blank lines and lines starting with '#'
    """
    m: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "=" in s:
                    k, v = s.split("=", 1)
                elif ":" in s:
                    k, v = s.split(":", 1)
                else:
                    parts = s.split(None, 1)
                    if len(parts) != 2:
                        continue
                    k, v = parts
                m[k.strip().upper()] = v.strip()
    except FileNotFoundError:
        pass
    return m

def _load_profile_map(name: str) -> Dict[str, str]:
    path = os.path.join(_profiles_path, _profile_filename(name))
    return _parse_file(path)

def init_dtc_db(profiles_path: str, default_profile: str = "Default", state_path: str = "./state/vehicle_profile.json"):
    global _profiles_path, _state_path, _active_profile, _base_map, _vehicle_map
    _profiles_path = profiles_path
    _state_path = state_path
    os.makedirs(_profiles_path, exist_ok=True)
    os.makedirs(os.path.dirname(_state_path), exist_ok=True)

    _base_map = _load_profile_map("Default")

    # restore last active profile from state, else use default_profile
    try:
        with open(_state_path, "r", encoding="utf-8") as f:
            _active_profile = json.load(f).get("active", default_profile)
    except Exception:
        _active_profile = default_profile

    _vehicle_map = _load_profile_map(_active_profile)

def list_profiles() -> List[str]:
    names = set()
    for fn in os.listdir(_profiles_path):
        if fn.endswith(".txt"):
            n = fn[:-4].replace("_", " ").title()
            names.add(n)
    names.add("Default")
    return ["Default"] + sorted([n for n in names if n != "Default"])

def get_active_profile() -> str:
    return _active_profile

def set_active_profile(name: str):
    global _active_profile, _vehicle_map
    name = _normalize_name(name)
    _active_profile = name
    _vehicle_map = _load_profile_map(name)
    try:
        with open(_state_path, "w", encoding="utf-8") as f:
            json.dump({"active": _active_profile}, f)
    except Exception:
        pass

# ---- descriptions ----

# Built-in minimal generic set (used as last resort)
_GENERIC: Dict[str, str] = {
    "P0100": "Mass or volume air flow circuit malfunction",
    "P0101": "Mass or volume air flow circuit range/performance",
    "P0102": "Mass or volume air flow circuit low input",
    "P0103": "Mass or volume air flow circuit high input",
    "P0113": "Intake air temperature sensor circuit high",
    "P0118": "Engine coolant temperature sensor circuit high",
    "P0128": "Coolant thermostat below regulating temperature",
    "P0171": "System too lean (Bank 1)",
    "P0172": "System too rich (Bank 1)",
    "P0174": "System too lean (Bank 2)",
    "P0175": "System too rich (Bank 2)",
    "P0300": "Random/multiple cylinder misfire detected",
    "P0420": "Catalyst system efficiency below threshold (Bank 1)",
    "P0430": "Catalyst system efficiency below threshold (Bank 2)",
    "P0440": "EVAP system malfunction",
    "P0442": "EVAP system small leak detected",
    "P0455": "EVAP system gross leak detected",
    "P0500": "Vehicle speed sensor malfunction",
    "P0606": "ECM/PCM processor fault",
    "U0100": "Lost communication with ECM/PCM",
}

def _pattern_description(code: str) -> Optional[str]:
    # P0301..P0308 → Misfire cylinder N
    if code.startswith("P030") and len(code) == 5 and code[-1].isdigit() and code != "P0300":
        return f"Misfire detected (Cylinder {code[-1]})"
    return None

def describe_dtc(code: str) -> str:
    c = code.upper().strip()
    if c in _vehicle_map:
        return _vehicle_map[c]
    if c in _base_map:
        return _base_map[c]
    if c in _GENERIC:
        return _GENERIC[c]
    pat = _pattern_description(c)
    if pat:
        return pat
    domain = {"P": "Powertrain", "C": "Chassis", "B": "Body", "U": "Network"}.get(c[:1], "Unknown")
    gen = "Generic" if len(c) > 1 and c[1] == "0" else "Manufacturer-specific"
    return f"{gen} {domain} fault (no detailed mapping available)"
