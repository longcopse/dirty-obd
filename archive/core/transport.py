import asyncio
import sys
from typing import Callable, Optional

# Serial transport (Classic SPP via /dev/rfcommX)
class SerialTransport:
    def __init__(self, port: str, baudrate: int = 38400, timeout_s: float = 2.0):
        import serial
        self._serial = serial.Serial(port, baudrate=baudrate, timeout=timeout_s)

    async def write(self, data: bytes):
        self._serial.write(data)
        await asyncio.sleep(0)  # yield

    async def readuntil(self, token: bytes, max_wait_s: float = 5.0) -> bytes:
        buf = bytearray()
        loop = asyncio.get_event_loop()
        end = loop.time() + max_wait_s
        while loop.time() < end:
            chunk = self._serial.read(self._serial.in_waiting or 1)
            if chunk:
                buf += chunk
                if token in buf:
                    return bytes(buf)
            await asyncio.sleep(0.01)
        return bytes(buf)

    async def close(self):
        self._serial.close()

# BLE GATT transport (Nordic UART-like)
class BLETransport:
    UART_SVC  = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
    TX_CHAR   = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # write
    RX_CHAR   = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # notify

    def __init__(self, mac: str):
        from bleak import BleakClient
        self._mac = mac
        self._client = BleakClient(mac)
        self._rx_queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def connect(self):
        await self._client.connect()
        await self._client.start_notify(self.RX_CHAR, self._handle_rx)

    def _handle_rx(self, _uuid, data: bytearray):
        # push bytes as they arrive
        self._rx_queue.put_nowait(bytes(data))

    async def write(self, data: bytes):
        await self._client.write_gatt_char(self.TX_CHAR, data)

    async def readuntil(self, token: bytes, max_wait_s: float = 5.0) -> bytes:
        buf = bytearray()
        loop = asyncio.get_event_loop()
        end = loop.time() + max_wait_s
        while loop.time() < end:
            # drain queue quickly
            try:
                while True:
                    pkt = self._rx_queue.get_nowait()
                    buf += pkt
            except asyncio.QueueEmpty:
                pass
            if token in buf:
                return bytes(buf)
            try:
                pkt = await asyncio.wait_for(self._rx_queue.get(), timeout=0.2)
                buf += pkt
            except asyncio.TimeoutError:
                pass
        return bytes(buf)

    async def close(self):
        try:
            await self._client.stop_notify(self.RX_CHAR)
        finally:
            await self._client.disconnect()

def make_transport(cfg) -> "object":
    t = cfg["transport"]["type"].lower()
    if t == "serial":
        return SerialTransport(
            cfg["transport"]["port"],
            cfg["transport"].get("baudrate", 38400),
            cfg["transport"].get("timeout_s", 2.0),
        )
    elif t == "ble":
        tr = BLETransport(cfg["transport"]["ble_mac"])
        # connect lazily in adapter.init()
        return tr
    else:
        raise ValueError(f"Unknown transport type: {t}")
