# core/j1979.py
# core/j1979.py
from typing import List

def decode_supported_bitmap(base: int, data: List[int]) -> list[int]:
    res: list[int] = []
    if len(data) >= 6 and data[0] == 0x41 and data[1] == base:
        bits = data[2:6]
        for i in range(32):
            if bits[i // 8] & (1 << (7 - (i % 8))):
                res.append(base + 1 + i)
    return res

async def probe_supported_pids(adapter) -> list[int]:
    supported = []
    for base in [0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0]:
        d = await adapter.request_obd(0x01, base)
        chunk = decode_supported_bitmap(base, d)
        supported.extend(chunk)
        if not chunk:
            break
    return supported

def parse_dtc_pairs_any(data: List[int], mode: int) -> list[str]:
    """Parse DTC pairs for response to Mode 03/07/0A (0x43/0x47/0x4A)."""
    if not data or data[0] != (0x40 | (mode & 0x3F)):
        return []
    out = []
    i = 1
    while i + 1 < len(data):
        b1, b2 = data[i], data[i+1]
        i += 2
        if b1 == 0 and b2 == 0:
            continue
        letter = ["P", "C", "B", "U"][(b1 & 0xC0) >> 6]
        code = letter + f"{(b1 & 0x3F):02X}{b2:02X}"
        out.append(code)
    return out

async def read_all_dtcs(adapter) -> dict:
    """Return stored, pending, permanent DTCs."""
    return {
        "stored":    parse_dtc_pairs_any(await adapter.request_obd(0x03), 0x03),
        "pending":   parse_dtc_pairs_any(await adapter.request_obd(0x07), 0x07),
        "permanent": parse_dtc_pairs_any(await adapter.request_obd(0x0A), 0x0A),
    }

def decode_vin_mode09(data: List[int]) -> str:
    """
    Reconstruct VIN from Mode 09 PID 02 multi-frame responses:
    frames like: 49 02 01 <7 bytes>, 49 02 02 <7 bytes>, 49 02 03 <7 bytes>.
    """
    b = data
    chunks: list[int] = []
    for frag in (1, 2, 3):
        # find the next '49 02 <frag>'
        for i in range(0, len(b) - 2):
            if b[i] == 0x49 and b[i+1] == 0x02 and b[i+2] == frag:
                # next up to 7 bytes are ASCII VIN chars
                seg = []
                j = i + 3
                for _ in range(7):
                    if j < len(b):
                        seg.append(b[j]); j += 1
                chunks.extend(seg)
                break
    vin = "".join(chr(x) for x in chunks if 32 <= x < 127)
    vin = "".join(ch for ch in vin if ch.isalnum())
    return vin[:17]
