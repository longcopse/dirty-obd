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


# ---------- Protocol enumeration + resolution ----------
# Known constants (some may be absent depending on python-OBD version)
_PROTO_CANDIDATES = [
    "AUTO",
    "ISO_15765_4_CAN",
    "ISO_15765_4_CAN_29BIT",
    "ISO_15765_4_CAN_11BIT_250K",
    "ISO_15765_4_CAN_29BIT_250K",
    "ISO_9141_2",
    "ISO_14230_4_KWP",       # 5-baud init
    "ISO_14230_4_KWP_FAST",  # fast init
    "SAE_J1850_PWM",         # Ford
    "SAE_J1850_VPW",         # GM
]

_PROTO_LABEL = {
    "AUTO": "Auto-detect",
    "ISO_15765_4_CAN": "ISO 15765-4 CAN (11-bit 500k)",
    "ISO_15765_4_CAN_29BIT": "ISO 15765-4 CAN (29-bit 500k)",
    "ISO_15765_4_CAN_11BIT_250K": "ISO 15765-4 CAN (11-bit 250k)",
    "ISO_15765_4_CAN_29BIT_250K": "ISO 15765-4 CAN (29-bit 250k)",
    "ISO_9141_2": "ISO 9141-2",
    "ISO_14230_4_KWP": "ISO 14230-4 KWP2000 (5-baud)",
    "ISO_14230_4_KWP_FAST": "ISO 14230-4 KWP2000 (fast)",
    "SAE_J1850_PWM": "SAE J1850 PWM (Ford)",
    "SAE_J1850_VPW": "SAE J1850 VPW (GM)",
}

# ELM327 ATSP protocol codes expected by python-OBD 0.7.x when a string is supplied
_ATSP_CODE = {
    "AUTO": "0",                   # Auto-detect
    "SAE_J1850_PWM": "1",          # Ford
    "SAE_J1850_VPW": "2",          # GM
    "ISO_9141_2": "3",
    "ISO_14230_4_KWP": "4",        # 5-baud init
    "ISO_14230_4_KWP_FAST": "5",   # fast init
    "ISO_15765_4_CAN": "6",        # 11-bit 500k
    "ISO_15765_4_CAN_29BIT": "7",  # 29-bit 500k
    "ISO_15765_4_CAN_11BIT_250K": "8",
    "ISO_15765_4_CAN_29BIT_250K": "9",
    # "A" is J1939; not standard OBD-II for this app
}

def enumerate_python_obd_protocols():
    """
    Return a list of (key, label) options available at runtime. Always includes AUTO.
    """
    out = []
    if obd is None:
        return [("AUTO", _PROTO_LABEL["AUTO"])]
    for key in _PROTO_CANDIDATES:
        if key == "AUTO":
            out.append((key, _PROTO_LABEL[key])); continue
        if hasattr(obd.protocols, key):
            out.append((key, _PROTO_LABEL.get(key, key)))
    return out

def resolve_protocol_arg(key: str):
    """
    What to pass as `protocol=` to `obd.OBD(...)`.
    - None => AUTO (let python-OBD decide)
    - ATSP code string ('1'..'9') => legacy-compatible path (v0.7.x)
    - protocol constant (if present) => forward-compatible path
    """
    if not key or key == "AUTO":
        return None
    code = _ATSP_CODE.get(key)
    if code:
        return code
    try:
        return getattr(obd.protocols, key)
    except Exception:
        return None


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


# -------- Live OBD Worker (dynamic PID sampling + full protocol advert) --------
class OBDWorker(threading.Thread):
    def __init__(
        self,
        port="/dev/rfcomm0",
        timeout=2.5,
        sample_interval=1.0,
        dtc_debounce=3,
        on_update=None,
        protocol="AUTO",
        selected_names=None,  # dynamic selection (list of python-OBD command names)
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

        # advertised protocol options (static for this process)
        self._protocol_options = enumerate_python_obd_protocols()

    # allow app to update the selection without restarting thread
    def set_selected(self, names):
        with self._sel_lock:
            self._selected_names = list(names or [])

    def set_protocol(self, key: str):
        """Allow UI to switch protocol. Closes and reconnects next loop."""
        self.protocol = key or "AUTO"
        try:
            if self._conn:
                self._conn.close()
        except Exception:
            pass
        self._conn = None  # force reconnect

    def stop(self): self._stop.set()

    def run(self):
        backoff = 1.0
        # Immediately tell UI what protocols are available
        self._push_update(available_protocols=self._protocol_options, selected_protocol=self.protocol)

        while not self._stop.is_set():
            try:
                self._ensure_connection()
                if not self._conn:
                    self._push_update(
                        adapter_ok=False,
                        last_error="No adapter/connection.",
                        available_protocols=self._protocol_options,
                        selected_protocol=self.protocol,
                    )
                    time.sleep(min(10.0, backoff)); backoff = min(backoff*2, 10.0)
                    continue

                data = {
                    "adapter_ok": True,
                    "last_error": "",
                    "supported_count": len(self._supported_names),
                    "available_protocols": self._protocol_options,
                    "selected_protocol": self.protocol,
                }

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
                self._push_update(
                    adapter_ok=False,
                    last_error=str(e),
                    available_protocols=self._protocol_options,
                    selected_protocol=self.protocol,
                )
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
            proto_arg = resolve_protocol_arg(self.protocol)
            self._conn = obd.OBD(self.port, fast=False, timeout=self.timeout, protocol=proto_arg)
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
                    try:
                        return OBDCommand(
                            "PENDING_DTC_FALLBACK",
                            "Pending trouble codes (Mode 07)",
                            b"07",
                            0,
                            DTC_DECODER,
                            ECU.ALL,
                            False,
                        )
                    except Exception: pass
                if name in ("PERMANENT_DTC","GET_PERMANENT_DTC"):
                    try:
                        return OBDCommand(
                            "PERMANENT_DTC_FALLBACK",
                            "Permanent trouble codes (Mode 0A)",
                            b"0A",
                            0,
                            DTC_DECODER,
                            ECU.ALL,
                            False,
                        )
                    except Exception: pass
            if not optional: logger.debug(f"Missing OBD command: {name}")
            return None

    def _safe_query(self, cmd, retries=0, delay=0.1):
        if not self._conn or cmd is None: return None
        for i in range(retries+1):
            try:
                return self._conn.query(cmd, force=True)  # force custom commands
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

    def _debounce_dtcs(self, dtcs):
        """Stabilize DTC reporting: publish only when the same set repeats `dtc_debounce` cycles."""
        if not isinstance(dtcs, list):
            dtcs = list(dtcs) if dtcs else []
        if dtcs == getattr(self, "_last_dtcs", []):
            self._pending_dtcs = dtcs
            self._pending_count = getattr(self, "_pending_count", 0) + 1
            if self._pending_count >= getattr(self, "dtc_debounce", 3):
                self._last_dtcs = self._pending_dtcs
                return self._last_dtcs
            return None
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


# ------------------- CLI probe (with --list-protocols) -------------------
if __name__ == "__main__":
    import argparse, sys
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="Run a one-shot DTC probe and exit")
    parser.add_argument("--list-protocols", action="store_true", help="List available protocols and exit")
    parser.add_argument("--port", default="/dev/rfcomm0")
    parser.add_argument("--protocol", default="AUTO", help="AUTO or a constant like ISO_15765_4_CAN")
    args = parser.parse_args()

    if args.list_protocols:
        opts = enumerate_python_obd_protocols()
        print("Available protocols:")
        for key, label in opts:
            atsp = _ATSP_CODE.get(key, "?")
            print(f"  {key:>28}  [{atsp}]  -  {label}")
        sys.exit(0)

    if args.probe:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        logging.getLogger("obd").setLevel(logging.DEBUG)

        try:
            import obd  # noqa
        except Exception as e:
            print("python-OBD not importable:", e); sys.exit(2)

        proto_arg = resolve_protocol_arg(args.protocol)
        print(f"Connecting to {args.port} protocol={args.protocol}")
        conn = obd.OBD(args.port, fast=False, timeout=3.0, protocol=proto_arg)

        if not conn.is_connected():
            print("NOT CONNECTED"); sys.exit(3)

        def q(cmdname, retries=1):
            cmd = getattr(obd.commands, cmdname, None)
            if cmd is None:
                print(f"{cmdname}: MISSING in python-OBD")
                return None
            r = None
            for i in range(retries+1):
                try:
                    r = conn.query(cmd)
                    break
                except Exception as e:
                    if i==retries: raise
            print(f"{cmdname}: raw={getattr(r,'value',None)}")
            return r

        st = q("STATUS")
        if st and st.value:
            try:
                mil = getattr(st.value, "MIL", None)
                cnt = getattr(st.value, "DTC_count", None)
            except Exception:
                mil = cnt = None
            print(f"STATUS -> MIL={mil} DTC_count={cnt}")

        vin = q("VIN")
        print("VIN parsed:", str(getattr(vin, "value", "")).strip() if vin and vin.value else "")

        stored = q("GET_DTC")
        if stored and stored.value:
            try:
                stored_codes = sorted({str(t[0]).strip() for t in stored.value if t and t[0]})
                print("STORED_DTC:", stored_codes)
            except Exception as e:
                print("STORED_DTC parse error:", e)

        # Pending (Mode 07)
        pending = getattr(obd.commands, "PENDING_DTC", None)
        if pending is None and OBDCommand and ECU and DTC_DECODER:
            pending = OBDCommand(
                "PENDING_DTC_FALLBACK",
                "Pending trouble codes (Mode 07)",
                b"07",
                0,
                DTC_DECODER,
                ECU.ALL,
                False,
            )
        if pending is not None:
            r = conn.query(pending, force=True)
            print("PENDING_DTC: raw=", getattr(r, "value", None))
            if r and r.value:
                pending_codes = sorted({str(t[0]).strip() for t in r.value if t and t[0]})
                print("PENDING_DTC:", pending_codes)

        # Permanent (Mode 0A)
        permanent = getattr(obd.commands, "PERMANENT_DTC", None)
        if permanent is None and OBDCommand and ECU and DTC_DECODER:
            permanent = OBDCommand(
                "PERMANENT_DTC_FALLBACK",
                "Permanent trouble codes (Mode 0A)",
                b"0A",
                0,
                DTC_DECODER,
                ECU.ALL,
                False,
            )
        if permanent is not None:
            r = conn.query(permanent, force=True)
            print("PERMANENT_DTC: raw=", getattr(r, "value", None))
            if r and r.value:
                permanent_codes = sorted({str(t[0]).strip() for t in r.value if t and t[0]})
                print("PERMANENT_DTC:", permanent_codes)

        sc = getattr(conn, "supported_commands", None)
        if sc:
            names = sorted({getattr(c,"name","") for c in sc if c})
            print(f"Supported commands ({len(names)}):", ", ".join(names)[:500], "...")
        conn.close()
        sys.exit(0)
