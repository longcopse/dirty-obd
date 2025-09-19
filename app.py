#!/usr/bin/env python3
import os, json, threading, logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from flask import Flask, render_template, request, redirect, url_for, jsonify
from sqlalchemy import create_engine, text

# Import helpers + workers from sampler
from sampler import (
    OBDWorker, ReplayWorker, load_dtc_descriptions,
    enumerate_python_obd_protocols
)

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR  = os.path.join(BASE_DIR, "logs")
DTC_DIR  = os.path.join(BASE_DIR, "dtc")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DTC_DIR, exist_ok=True)

logger = logging.getLogger("obd")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = RotatingFileHandler(os.path.join(LOG_DIR, "obd.log"), maxBytes=1_000_000, backupCount=5)
    h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(h)

VEHICLES = [("default","Default"), ("jaguar","Jaguar"),
            ("r53_cooper","R53 Cooper"), ("r53_cooper_s","R53 Cooper S")]

SERIAL_PORT     = os.environ.get("OBD_PORT", "/dev/rfcomm0")
SAMPLE_INTERVAL = float(os.environ.get("SAMPLE_INTERVAL", "1.0"))
OBD_TIMEOUT     = float(os.environ.get("OBD_TIMEOUT", "2.5"))
DTC_DEBOUNCE    = int(os.environ.get("DTC_DEBOUNCE", "3"))

# Dynamic protocols: advertise everything this build supports (plus AUTO)
PROTOCOLS = enumerate_python_obd_protocols()  # list of (key, label)

# ---- DB ----
DB_URL = f"sqlite:///{os.path.join(DATA_DIR, 'obd.sqlite')}"
engine = create_engine(DB_URL, echo=False, future=True)

def _db_init():
    with engine.begin() as c:
        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS samples (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
          rpm REAL, speed REAL, coolant REAL, maf REAL,
          engine_load REAL, throttle_pos REAL, timing_adv REAL,
          intake_temp REAL, short_ft_b1 REAL, long_ft_b1 REAL, map REAL,
          o2b1s1 REAL, o2b1s2 REAL, cm_voltage REAL
        )""")
        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS dtc_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
          vin TEXT, codes_json TEXT NOT NULL
        )""")
        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS freeze_frames (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
          dtc_event_id INTEGER, vin TEXT, codes_json TEXT NOT NULL, frame_json TEXT NOT NULL
        )""")
_db_init()

app = Flask(__name__, static_folder="static", template_folder="templates")
state_lock = threading.Lock()
shared_state = {
    "vehicle": "default",
    "protocol": "AUTO",
    "supported_count": 0,
    "dtc_descriptions": {},
    "vin": "",
    # dynamic selection (python-OBD command names)
    "selected_pids": ["RPM","SPEED","COOLANT_TEMP","MAF","ENGINE_LOAD","THROTTLE_POS"],
    # live dynamic values (name -> value)
    "dyn_values": {},
    # codes + frames + status
    "mil": None, "dtc_count_reported": None,
    "dtcs": [], "dtcs_stored": [], "dtcs_pending": [], "dtcs_permanent": [],
    "last_freeze_frame": {},
    "adapter_ok": False, "last_error": "",
    "supported_names": [],
}

recording = {"enabled": False, "csv_path": None, "dtc_path": None,
             "csv_handle": None, "csv_writer": None, "dtc_handle": None, "fieldnames": None}

current_worker = None
replay_worker  = None

def _utcnow(): return datetime.now(timezone.utc).isoformat()

def _db_insert_sample(d):
    with engine.begin() as c:
        c.execute(text("""INSERT INTO samples
          (ts,rpm,speed,coolant,maf,engine_load,throttle_pos,timing_adv,intake_temp,
           short_ft_b1,long_ft_b1,map,o2b1s1,o2b1s2,cm_voltage)
          VALUES (:ts,:rpm,:speed,:coolant,:maf,:engine_load,:throttle_pos,:timing_adv,:intake_temp,
                  :short_ft_b1,:long_ft_b1,:map,:o2b1s1,:o2b1s2,:cm_voltage)"""),
          {"ts":_utcnow(), "rpm":d.get("RPM"), "speed":d.get("SPEED"),
           "coolant":d.get("COOLANT_TEMP"), "maf":d.get("MAF"),
           "engine_load":d.get("ENGINE_LOAD"), "throttle_pos":d.get("THROTTLE_POS"),
           "timing_adv":d.get("TIMING_ADVANCE"), "intake_temp":d.get("INTAKE_TEMP"),
           "short_ft_b1":d.get("FUEL_TRIM_SHORT_BANK1"), "long_ft_b1":d.get("FUEL_TRIM_LONG_BANK1"),
           "map":d.get("INTAKE_PRESSURE"), "o2b1s1":d.get("O2_B1S1"), "o2b1s2":d.get("O2_B1S2"),
           "cm_voltage":d.get("CONTROL_MODULE_VOLTAGE")})

def _db_insert_dtc_event(vin,codes):
    with engine.begin() as c:
        res = c.execute(text("INSERT INTO dtc_events (ts,vin,codes_json) VALUES (:ts,:vin,:codes)"),
                        {"ts":_utcnow(), "vin":vin or "", "codes":json.dumps(codes or [])})
        return res.lastrowid

def _db_insert_freeze_frame(ev_id, vin, codes, frame):
    with engine.begin() as c:
        c.execute(text("""INSERT INTO freeze_frames (ts,dtc_event_id,vin,codes_json,frame_json)
                          VALUES (:ts,:id,:vin,:codes,:frame)"""),
                  {"ts":_utcnow(), "id":ev_id, "vin":vin or "",
                   "codes":json.dumps(codes or []), "frame":json.dumps(frame or {})})

def _reload_dtc_descriptions():
    desc = load_dtc_descriptions(shared_state["vehicle"], DTC_DIR)
    with state_lock: shared_state["dtc_descriptions"] = desc
_reload_dtc_descriptions()

def _merge_state(update: dict):
    # update runtime state
    with state_lock:
        prev_dtcs = shared_state.get("dtcs", [])
        shared_state.update(update)

        # RECORD stream (CSV of dyn_values)
        if recording["enabled"] and "dyn_values" in update:
            dv = update["dyn_values"] or {}
            if recording["fieldnames"] is None:
                recording["fieldnames"] = ["ts","vin"] + sorted(dv.keys())
                recording["csv_writer"].fieldnames = recording["fieldnames"]
                recording["csv_writer"].writeheader()
            row = {"ts": _utcnow(), "vin": shared_state.get("vin","")}
            for k in recording["fieldnames"][2:]:
                row[k] = dv.get(k)
            recording["csv_writer"].writerow(row); recording["csv_handle"].flush()

        # DB persist (common fields only, if present in dyn_values)
        if "dyn_values" in update and update["dyn_values"]:
            _db_insert_sample(update["dyn_values"])

        # DTC change + freeze frame
        now_dtcs = shared_state.get("dtcs", [])
        if now_dtcs != prev_dtcs and now_dtcs is not None:
            ev_id = _db_insert_dtc_event(shared_state.get("vin"), now_dtcs)
            if "freeze_frame" in update and update["freeze_frame"]:
                ff = update["freeze_frame"]; shared_state["last_freeze_frame"] = ff
                _db_insert_freeze_frame(ev_id, shared_state.get("vin"), now_dtcs, ff)
                if recording["enabled"]:
                    import json as _json
                    _json.dump({"ts":_utcnow(),"vin":shared_state.get("vin",""),
                               "dtcs":now_dtcs,"freeze_frame":ff}, recording["dtc_handle"])
                    recording["dtc_handle"].write("\n"); recording["dtc_handle"].flush()

# ---------- Worker mgmt ----------
def _stop_current_worker():
    global current_worker, replay_worker
    if current_worker is not None:
        try: current_worker.stop()
        except Exception: pass
        current_worker = None
    if replay_worker is not None:
        try: replay_worker.stop()
        except Exception: pass
        replay_worker = None

def _start_obd_worker():
    global current_worker
    if current_worker is not None: return
    proto = shared_state.get("protocol","AUTO")
    sel   = shared_state.get("selected_pids", [])
    worker = OBDWorker(
        port=SERIAL_PORT, timeout=OBD_TIMEOUT, sample_interval=SAMPLE_INTERVAL,
        dtc_debounce=DTC_DEBOUNCE, on_update=_merge_state, protocol=proto,
        selected_names=sel
    )
    worker.start(); current_worker = worker

_start_obd_worker()

# ---------- Routes ----------
@app.get("/")
def index():
    with state_lock: s = dict(shared_state)
    # Recompute PROTOCOLS each render (in case python-OBD gets upgraded)
    protocols = enumerate_python_obd_protocols()
    return render_template("index.html", state=s, vehicles=VEHICLES, protocols=protocols)

@app.post("/set-vehicle")
def set_vehicle():
    vehicle = request.form.get("vehicle","default")
    with state_lock: shared_state["vehicle"] = vehicle
    _reload_dtc_descriptions()
    return redirect(url_for("index"))

@app.post("/set-protocol")
def set_protocol():
    proto = request.form.get("protocol","AUTO")
    with state_lock: shared_state["protocol"] = proto
    _stop_current_worker(); _start_obd_worker()
    return redirect(url_for("index"))

@app.get("/api/state")
def api_state():
    with state_lock:
        s = dict(shared_state)
        descs = s.get("dtc_descriptions", {})
        def _desc_list(codes): return [{"code": c, "desc": descs.get(c, "—")} for c in (codes or [])]
        s["dtc_list"] = _desc_list(s.get("dtcs", []))
        s["dtc_list_stored"]    = _desc_list(s.get("dtcs_stored", []))
        s["dtc_list_pending"]   = _desc_list(s.get("dtcs_pending", []))
        s["dtc_list_permanent"] = _desc_list(s.get("dtcs_permanent", []))
        # surface protocol options to SPA dashboards, etc.
        s["available_protocols"] = enumerate_python_obd_protocols()
    return jsonify(s)

@app.post("/set-pids")
def set_pids():
    """
    Accepts JSON: {"selected_pids": ["RPM","SPEED", ...]}
    Stores as-is (python-OBD command names). Also updates the running worker.
    """
    try:
        incoming = request.get_json(force=True) or {}
        selected = incoming.get("selected_pids", [])
        if not isinstance(selected, list):
            return jsonify({"ok": False, "error": "selected_pids must be a list"}), 400

        with state_lock:
            shared_state["selected_pids"] = selected
            w = current_worker

        if w is not None:
            try: w.set_selected(selected)
            except Exception: pass

        return jsonify({"ok": True, "selected_pids": selected})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.post("/shutdown")
def shutdown():
    _stop_current_worker(); return "Stopping", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
