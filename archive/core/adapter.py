import asyncio
from typing import List, Optional

ELM_PROMPT = b'>'

def _parse_hex_bytes(resp: str) -> List[int]:
    out: List[int] = []
    for line in resp.splitlines():
        s = line.strip()
        if not s or any(k in s for k in ("SEARCHING", "BUS", "STOPPED")):
            continue
        for tok in s.split():
            if len(tok) == 2:
                try:
                    out.append(int(tok, 16))
                except ValueError:
                    pass
    return out

def _slice_at_service(bytes_list: List[int], mode: int, pid: Optional[int] = None) -> List[int]:
    """
    Find the slice that starts at the positive response byte (0x40+mode).
    Example: mode=0x01 -> look for 0x41; mode=0x09 -> 0x49; mode=0x03 -> 0x43.
    If pid is provided (e.g., 0x0C), also verify the next byte matches.
    """
    want = 0x40 + (mode & 0x3F)
    b = bytes_list
    for i in range(len(b) - 1):
        if b[i] == want:
            if pid is None or (i + 1 < len(b) and b[i + 1] == pid):
                return b[i:]
    # fallback: return original (lets decoders try anyway)
    return b

class ELM327Adapter:
    def __init__(self, transport, retries: int = 3):
        self.t = transport
        self.retries = retries

    async def init(self):
        # BLE transports need explicit connect()
        if hasattr(self.t, "connect"):
            await self.t.connect()

        # Conservative, broad init
        await self._send("ATZ", swallow=True);            await asyncio.sleep(0.2)
        for cmd in ("ATE0","ATL0","ATS0","ATH1","ATAT1","ATD","ATSP0","ATST64","ATCAF1"):
            await self._send(cmd, swallow=True)
        # Ensure we see a prompt
        await self._read_prompt()

    async def close(self):
        await self.t.close()

    async def _read_prompt(self, timeout=5.0) -> str:
        raw = await self.t.readuntil(ELM_PROMPT, max_wait_s=timeout)
        txt = raw.replace(b"\r", b"\n").decode(errors="ignore")
        return txt.rsplit(">", 1)[0].strip()

    async def _send(self, cmd: str, swallow: bool = False) -> str:
        await self.t.write((cmd.strip() + "\r").encode())
        resp = await self._read_prompt()
        return "" if swallow else resp

    async def query(self, cmd: str) -> str:
        # retries for NO DATA / ? / empty
        last = ""
        for _ in range(self.retries):
            r = await self._send(cmd)
            last = r
            if r and "NO DATA" not in r and "?" not in r:
                return r
            await asyncio.sleep(0.05)
        return last

    async def request_obd(self, mode: int, pid: int | None = None) -> List[int]:
        cmd = f"{mode:02X}" if pid is None else f"{mode:02X} {pid:02X}"
        resp_text = await self.query(cmd)
        raw = _parse_hex_bytes(resp_text)           # e.g., [0xE9,0x04,0x41,0x0C,0x0B,0x08]
        sliced = _slice_at_service(raw, mode, pid)  # e.g., [0x41,0x0C,0x0B,0x08]
        return sliced

    async def protocol(self) -> str:
        return await self.query("ATDP")

    # Optional helpers you might call from elsewhere:
    async def set_timeout(self, hex_val: str = "96"):     # 0x96 ≈ 600ms
        await self._send(f"ATST{hex_val}", swallow=True)

    async def set_headers(self, on: bool):
        await self._send("ATH1" if on else "ATH0", swallow=True)

    async def set_can_autoformat(self, on: bool):
        await self._send("ATCAF1" if on else "ATCAF0", swallow=True)
