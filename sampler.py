import os, json, time, threading, logging, csv
logger = logging.getLogger("obd")

try:
    import obd
except Exception:
    obd = None  # UI / replay can still run

# Optional fallback creation for Mode 07 / 0A if build lacks them
try:
    from obd import OBDCommand, ECU
    from obd.decoders import dtc as DTC_DECODER
except Exception:
    OBDCommand = None
    ECU = None
    DTC_DECODER = None


def load_dtc_descriptions(vehicle_key: str, base_dir: str) -> dict:
    for fname in (f"dtc_{vehicle_key}.json", "dtc_default.json"):
        path = os.path.join(base_dir, fname)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
    return {}


# -------- Replay Worker (unchanged) --------
class ReplayWorker(threading.Thread):
    def __init__(self, csv_path, jsonl_path=None, speed=1.0, loop=True, on_update=None):
        super().__init__(daemon=True)
        self.csv_path = csv_path
        self.jsonl_path = jsonl_path
        self.speed = max(0.01, float(speed))
        self.loop = bool(loop)
        self.on_update = on_update or (lambda d: None)
        self._stop = threading.Event()

    def stop(self): self._stop.set()

    def _load(self):
        with open(self.csv_path, "r") as f:
            rows = list(csv.DictReader(f))
        events = []
        if self.jsonl_path and os.path.exists(self.jsonl_path):
            with open(self.jsonl_path, "r") as f:
                for line in f:
                    try: events.append(json.loads(line))
                    except Exception: pass
        return rows, events

    def run(self):
        from datetime import datetime
        def to_ts(s):
            if not s: return None
            return datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()

        rows, events = self._load()
        if not rows: return
        row_ts = [to_ts(r.get("ts","")) for r in rows]
        t0 = min([t for t in row_ts if t is not None], default=None)

        while not self._stop.is_set():
            start_wall = time.time()
            for i,r in enumerate(rows):
                if self._stop.is_set(): break
                if t0 is not None and row_ts[i] is not None:
                    dt = (row_ts[i]-t0)/self.speed
                    target = start_wall + max(0,dt)
                    now = time.time()
                    if target>now: time.sleep(target-now)
                out = {"adapter_ok": True, "last_error": "", "vin": r.get("vin","")}
                for k,v in r.items():
                    if k in ("ts","vin"): continue
                    try: out[k] = float(v) if v not in (None,"","None") else None
                    except Exception: out[k] = None
                if events and row_ts[i] is not None:
                    cur = row_ts[i]; last=None
                    for ev in events:
                        try: t = to_ts(ev.get("ts",""))
                        except Exception: t = None
                        if t is not None and t<=cur: last = ev
                    if last:
                        out["dtcs"] = last.get("dtcs") or []
                        if last.get("freeze_frame"): out["freeze_frame"] = last["freeze_frame"]
                try: self.on_update(out)
                except Exception: pass
            if not self.loop: break


# -------- Live OBD Worker (dynamic PID sampling) --------
class OBDWorker(threading.Thread):
    def __init__(
        self,
        port="/dev/rfcomm0",
        timeout=2.5,
        sample_interval=1.0,
        dtc_debounce=3,
        on_update=None,
        protocol="AUTO",
        selected_names=None,  # NEW: dynamic selection (list of python-OBD command names)
    ):
        super().__init__(daemon=True)
        self.port = port
        self.timeout = timeout
        self.sample_interval = sample_interval
        self.dtc_debounce = dtc_debounce
        self.on_update = on_update or (lambda d: None)
        self.protocol = protocol

        self._stop = threading.Event()
        self._conn = None
        self._connecting = False

        self._vin = ""
        self._last_dtcs = []
        self._pending_dtcs = []
        self._pending_count = 0

        self._supported = set()
        self._supported_names = set()
        self._dtc_diag = {"stored": None, "pending": None, "permanent": None}

        # dynamic PIDs from UI
        self._sel_lock = threading.Lock()
        self._selected_names = list(selected_names or [])  # names as in python-OBD (e.g., "RPM","SPEED")

    # allow app to update the selection without restarting thread
    def set_selected(self, names):
        with self._sel_lock:
            self._selected_names = list(names or [])

    def stop(self): self._stop.set()

    def run(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._ensure_connection()
                if not self._conn:
                    self._push_update(adapter_ok=False, last_error="No adapter/connection.")
                    time.sleep(min(10.0, backoff)); backoff = min(backoff*2, 10.0)
                    continue

                data = {"adapter_ok": True, "last_error": "", "supported_count": len(self._supported_names)}

                # VIN occasionally
                if not self._vin or int(time.time()) % 30 == 0:
                    self._vin = self._vin_if_supported()
                data["vin"] = self._vin

                # STATUS (MIL + DTC count)
                st = self._status_info()
                data["mil"] = st["mil"]; data["dtc_count_reported"] = st["dtc_count"]

                # Multi-mode DTCs (stored/pending/permanent)
                stored    = self._dtc_list_from("STORED")
                pending   = self._dtc_list_from("PENDING")
                permanent = self._dtc_list_from("PERMANENT")
                stable = self._debounce_dtcs(stored)
                if stable is not None:
                    data["dtcs"] = stable
                    ff = self._safe_freeze_frame()
                    if ff: data["freeze_frame"] = ff
                data["dtcs_stored"] = stored; data["dtcs_pending"] = pending; data["dtcs_permanent"] = permanent
                data["dtc_diag"] = dict(self._dtc_diag)

                # --- Dynamic PIDs ---
                with self._sel_lock:
                    selected = list(self._selected_names)
                dyn = {}
                for name in selected:
                    val = self._num_if_supported(name, optional=True)
                    if val is not None:
                        dyn[name] = val
                data["dyn_values"] = dyn
                data["supported_names"] = sorted(self._supported_names)  # for the picker

                self._push_update(**data)
                backoff = 1.0
                time.sleep(self.sample_interval)

            except Exception as e:
                logger.warning(f"Sampler error: {e}")
                self._push_update(adapter_ok=False, last_error=str(e))
                time.sleep(min(10.0, backoff)); backoff = min(backoff*2, 10.0)

        try:
            if self._conn: self._conn.close()
        except Exception:
            pass

    # ---- connection / discovery ----
    def _ensure_connection(self):
        if self._conn or self._connecting or obd is None:
            return
        self._connecting = True
        try:
            proto_obj=None
            if self.protocol and self.protocol!="AUTO":
                try: proto_obj = getattr(obd.protocols, self.protocol)
                except Exception: proto_obj=None
            self._conn = obd.OBD(self.port, fast=False, timeout=self.timeout, protocol=proto_obj)
            _ = self._conn.query(obd.commands.STATUS); time.sleep(0.15)
            self._discover_supported()
        except Exception:
            self._conn=None
        finally:
            self._connecting = False

    def _discover_supported(self):
        names=set()
        try:
            sc = self._conn.supported_commands
            if sc:
                self._supported = set(sc)
                for c in sc:
                    try: names.add(getattr(c,"name",None) or "")
                    except Exception: pass
        except Exception:
            self._supported=set()

        # Always ensure DTC + VIN + common basics show as supported if probed OK
        probe = [
            "VIN","STATUS",
            "GET_DTC","PENDING_DTC","GET_PENDING_DTC","PERMANENT_DTC","GET_PERMANENT_DTC",
        ]
        ok_objs=set()
        for name in probe:
            cmd=self._cmd(name, optional=True)
            if not cmd: continue
            cname=getattr(cmd,"name",name)
            if cname in names:
                ok_objs.add(cmd); continue
            r=self._safe_query(cmd, retries=1)
            if r and r.value is not None:
                ok_objs.add(cmd); names.add(cname)
            time.sleep(0.05)

        self._supported |= ok_objs
        self._supported_names = {getattr(c, "name", "") for c in self._supported if c}
        logger.info(f"Supported commands detected (names): {len(self._supported_names)}")

    # ---- helpers ----
    def _push_update(self, **kw):
        try: self.on_update(kw)
        except Exception: pass

    def _cmd(self, name, optional=False):
        """Lookup a python-OBD command; synthesize Mode 07/0A if missing and runtime supports it."""
        if obd is None: return None
        try:
            return getattr(obd.commands, name)
        except AttributeError:
            if OBDCommand and ECU and DTC_DECODER:
                if name in ("PENDING_DTC","GET_PENDING_DTC"):
                    try: return OBDCommand("PENDING_DTC_FALLBACK","07",6,DTC_DECODER,ECU.ALL,False)
                    except Exception: pass
                if name in ("PERMANENT_DTC","GET_PERMANENT_DTC"):
                    try: return OBDCommand("PERMANENT_DTC_FALLBACK","0A",6,DTC_DECODER,ECU.ALL,False)
                    except Exception: pass
            if not optional: logger.debug(f"Missing OBD command: {name}")
            return None

    def _safe_query(self, cmd, retries=0, delay=0.1):
        if not self._conn or cmd is None: return None
        for i in range(retries+1):
            try: return self._conn.query(cmd)
            except Exception:
                if i<retries: time.sleep(delay); continue
                return None

    def _safe_number(self, cmd, ndp: int = 2):
        r=self._safe_query(cmd)
        if not r or r.value is None: return None
        v=getattr(r.value,"magnitude", r.value)
        try: x=float(v)
        except Exception:
            try: x=float(str(v).split()[0])
            except Exception: return None
        try: return round(x, ndp)
        except Exception: return x

    def _num_if_supported(self, name, optional=False):
        cmd=self._cmd(name, optional=optional)
        if not cmd: return None
        # Even if not in supported list, probe once (some ECUs/ELMs lie)
        return self._safe_number(cmd)

    def _vin_if_supported(self):
        cmd=self._cmd("VIN", optional=True)
        if not cmd: return ""
        r=self._safe_query(cmd)
        if not r or not r.value: return ""
        return str(r.value).strip().replace(" ","").replace("\x00","")

    # ----- DTC helpers -----
    def _dtc_list_from(self, which):
        aliases = {
            "STORED": ["GET_DTC"],
            "PENDING": ["PENDING_DTC","GET_PENDING_DTC"],
            "PERMANENT": ["PERMANENT_DTC","GET_PERMANENT_DTC"]
        }
        label = which.lower(); self._dtc_diag[label] = "missing"; out=[]
        for name in aliases[which]:
            cmd=self._cmd(name, optional=True)
            if not cmd: continue
            r=self._safe_query(cmd, retries=1)
            if not r: self._dtc_diag[label]="no-response"; continue
            if not r.value: self._dtc_diag[label]="nodata"; continue
            try:
                tmp=[]
                for tup in r.value:
                    try: code=str(tup[0]).strip()
                    except Exception: code=None
                    if code: tmp.append(code)
                if tmp:
                    out=sorted(set(tmp)); self._dtc_diag[label]="ok"; return out
                else:
                    self._dtc_diag[label]="empty"
            except Exception:
                self._dtc_diag[label]="parse-error"
        return out

    # inside class OBDWorker
    def _debounce_dtcs(self, dtcs):
        """
        Stabilize DTC reporting: only publish when the same set is seen
        `dtc_debounce` consecutive cycles.
        """
        if not isinstance(dtcs, list):
            dtcs = list(dtcs) if dtcs else []
        if dtcs == getattr(self, "_last_dtcs", []):
            # seen same set again -> lock it in
            self._pending_dtcs = dtcs
            self._pending_count = getattr(self, "_pending_count", 0) + 1
            if self._pending_count >= getattr(self, "dtc_debounce", 3):
                self._last_dtcs = self._pending_dtcs
                return self._last_dtcs
            return None

        # new set observed -> start counting
        self._pending_dtcs = dtcs
        self._pending_count = 1
        return None


    def _status_info(self):
        cmd=self._cmd("STATUS", optional=True)
        if not cmd: return {"mil":None,"dtc_count":None}
        r=self._safe_query(cmd)
        if not r or r.value is None: return {"mil":None,"dtc_count":None}
        try:
            mil = getattr(r.value,"MIL", getattr(r.value,"mil", None))
            cnt = getattr(r.value,"DTC_count", getattr(r.value,"dtc_count", None))
            if isinstance(mil,str): mil = mil.lower()=="on"
            if cnt is not None: cnt=int(cnt)
            return {"mil":mil,"dtc_count":cnt}
        except Exception:
            return {"mil":None,"dtc_count":None}

    def _safe_freeze_frame(self):
        if obd is None: return {}
        candidates=["FREEZE_DTC","FREEZE_ENGINE_RPM","FREEZE_VEHICLE_SPEED","FREEZE_ENGINE_LOAD",
                    "FREEZE_THROTTLE_POS","FREEZE_COOLANT_TEMP","FREEZE_INTAKE_TEMP","FREEZE_INTAKE_PRESSURE",
                    "FREEZE_TIMING_ADVANCE","FREEZE_MAF","FREEZE_FUEL_TRIM_SHORT_BANK1","FREEZE_FUEL_TRIM_LONG_BANK1"]
        frame={}
        for name in candidates:
            cmd=self._cmd(name, optional=True)
            if not cmd: continue
            r=self._safe_query(cmd)
            if not r or r.value is None: continue
            val=getattr(r.value,"magnitude", r.value)
            try: val=float(val)
            except Exception:
                try: val=float(str(val).split()[0])
                except Exception: val=str(val)
            frame[name]=val
        return frame

