import time
import threading
import json
import os

try:
    import obd
except Exception:  # if python-OBD isn't installed, we allow import to fail for dev machines
    obd = None


def load_dtc_descriptions(vehicle_key: str, base_dir: str = ".") -> dict:
    """
    Load DTC descriptions from a JSON file named dtc_<vehicle_key>.json or fallback to dtc_default.json.
    Returns {} if not found. Example filenames:
      - dtc_default.json
      - dtc_jaguar_xf.json
      - dtc_r53_cooper.json
      - dtc_r53_cooper_s.json
    """
    candidates = [f"dtc_{vehicle_key}.json", "dtc_default.json"]
    for fname in candidates:
        path = os.path.join(base_dir, fname)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
    return {}


class OBDWorker(threading.Thread):
    """
    Background sampler for OBD PIDs with:
      - Safe timeouts
      - Backoff on failures
      - Debounced DTC updates (prevents flicker)
      - Partial VIN tolerance
    """

    def __init__(self, port="/dev/rfcomm0", timeout=2.0, sample_interval=0.8, dtc_debounce=3, on_update=None):
        super().__init__(daemon=True)
        self.port = port
        self.timeout = timeout
        self.sample_interval = sample_interval
        self.dtc_debounce = dtc_debounce
        self.on_update = on_update or (lambda d: None)

        self._stop = threading.Event()
        self._conn = None

        # DTC stability tracking
        self._last_dtcs = []
        self._pending_dtcs = []
        self._pending_count = 0

        # Cached VIN so we only query occasionally
        self._vin = ""

    def run(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._ensure_connection()
                if not self._conn:
                    self._push_update(adapter_ok=False, last_error="No adapter/connection.")
                    time.sleep(min(10.0, backoff))
                    backoff = min(backoff * 2, 10.0)
                    continue

                # Sampling loop
                data = {"adapter_ok": True, "last_error": ""}

                # --- VIN (query less frequently) ---
                if not self._vin or int(time.time()) % 30 == 0:
                    self._vin = self._safe_vin()
                data["vin"] = self._vin

                # --- PIDs ---
                data["rpm"] = self._safe_query(obd.commands.RPM)
                data["speed"] = self._safe_query(obd.commands.SPEED)
                data["coolant_temp"] = self._safe_query(obd.commands.COOLANT_TEMP)
                # MAF (PID 0x10)
                data["maf"] = self._safe_query(obd.commands.MAF)

                # --- DTCs with debounce ---
                dtcs = self._safe_dtcs()
                stable = self._debounce_dtcs(dtcs)
                if stable is not None:  # only push when stabilized
                    data["dtcs"] = stable

                self._push_update(**data)

                backoff = 1.0
                time.sleep(self.sample_interval)
            except Exception as e:
                # Any unexpected error: report and back off
                self._push_update(adapter_ok=False, last_error=str(e))
                time.sleep(min(10.0, backoff))
                backoff = min(backoff * 2, 10.0)

        # Cleanup when stopping
        try:
            if self._conn:
                self._conn.close()
        except Exception:
            pass

    def stop(self):
        self._stop.set()

    # --------------------------
    # Helpers
    # --------------------------
    def _ensure_connection(self):
        if self._conn:
            return
        if obd is None:
            return  # dev machines without adapter
        try:
            self._conn = obd.OBD(self.port, fast=False, timeout=self.timeout)
            # Warm-up basic AT checks by attempting a simple query
            _ = self._conn.query(obd.commands.ELM_VERSION)
        except Exception:
            self._conn = None

    def _push_update(self, **kwargs):
        try:
            self.on_update(kwargs)
        except Exception:
            pass

    def _safe_query(self, cmd):
        if not self._conn:
            return None
        try:
            r = self._conn.query(cmd)
            if r is None or r.value is None:
                return None
            # Convert units to simple numeric where possible
            try:
                return float(getattr(r.value, "magnitude", r.value))
            except Exception:
                # some values are already numeric or strings
                return str(r.value)
        except Exception:
            return None

    def _safe_dtcs(self):
        """
        Returns list of DTC strings like ["P0300","P0420"].
        """
        if not self._conn:
            return []
        try:
            r = self._conn.query(obd.commands.GET_DTC)
            if not r or not r.value:
                return []
            # python-OBD returns list of tuples: [(code, description), ...]
            # We only keep the code here; descriptions are handled by JSON tables.
            codes = []
            for tup in r.value:
                # tuple may be ("P0300", "Random/Multiple Cylinder Misfire Detected")
                if isinstance(tup, (list, tuple)) and len(tup) >= 1:
                    code = str(tup[0]).strip()
                    if code:
                        codes.append(code)
            # Deduplicate
            return sorted(set(codes))
        except Exception:
            return []

    def _debounce_dtcs(self, dtcs):
        """
        Prevents flicker: require the same set of DTCs to be observed for N consecutive samples
        before accepting the change.
        Returns stabilized list or None if still pending.
        """
        if dtcs == self._last_dtcs:
            # no change, clear pending
            self._pending_dtcs = dtcs
            self._pending_count = self.dtc_debounce
            return self._last_dtcs

        if dtcs != self._pending_dtcs:
            self._pending_dtcs = dtcs
            self._pending_count = 1
            return None

        # Same as pending; increment
        self._pending_count += 1
        if self._pending_count >= self.dtc_debounce:
            self._last_dtcs = self._pending_dtcs
            return self._last_dtcs
        return None

    def _safe_vin(self):
        """
        Attempts to read VIN via Mode 09 PID 02. Returns partial string if only partially available.
        """
        if not self._conn:
            return ""
        try:
            r = self._conn.query(obd.commands.VIN)
            if not r or not r.value:
                return ""
            v = str(r.value).strip()
            # Some ECUs return fragments/odd spacing; sanitize
            v = v.replace(" ", "").replace("\x00", "")
            # Avoid crashing if it's only a couple of chars; return as-is
            return v
        except Exception:
            return ""
