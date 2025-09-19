import csv, os, aiosqlite, asyncio
from datetime import datetime

class CSVLogger:
    def __init__(self, path: str, rotate_daily: bool = True):
        self.base = path
        self.rotate = rotate_daily
        self._file = None
        self._writer = None
        self._current_path = None

    def _path(self):
        if self.rotate:
            date = datetime.utcnow().strftime("%Y-%m-%d")
            root, ext = os.path.splitext(self.base)
            return f"{root}_{date}{ext or '.csv'}"
        return self.base

    def _ensure(self, fields):
        p = self._path()
        if p != self._current_path:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            new_file = not os.path.exists(p)
            self._file = open(p, "a", newline="")
            self._writer = csv.DictWriter(self._file, fieldnames=["ts"] + list(fields))
            if new_file:
                self._writer.writeheader()
            self._current_path = p

    def write(self, row: dict):
        fields = [k for k in row.keys() if k != "ts"]
        self._ensure(fields)
        self._writer.writerow(row)
        self._file.flush()

class SQLiteLogger:
    def __init__(self, path: str):
        self.path = path
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self._task = None

    async def start(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
            CREATE TABLE IF NOT EXISTS samples(
              ts TEXT NOT NULL,
              vin TEXT,
              rpm REAL, speed_kph REAL, coolant_c REAL, intake_air_c REAL,
              maf_gps REAL, fuel_level_pct REAL
            )""")
            await db.commit()
            while True:
                row = await self._queue.get()
                cols = ", ".join(row.keys())
                vals = ", ".join(["?"] * len(row))
                await db.execute(f"INSERT INTO samples ({cols}) VALUES ({vals})", list(row.values()))
                await db.commit()

    async def write(self, row: dict):
        await self._queue.put(row)

    async def close(self):
        if self._task:
            self._task.cancel()
