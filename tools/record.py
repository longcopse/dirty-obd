#!/usr/bin/env python3
import csv, json, time, argparse, os, sys
from datetime import datetime, timezone

try:
    import obd
except Exception:
    print("This recorder requires the 'obd' package. pip install obd", file=sys.stderr)
    sys.exit(1)

PIDS = {
    "rpm": "RPM",
    "speed": "SPEED",
    "coolant_temp": "COOLANT_TEMP",
    "maf": "MAF",
    "engine_load": "ENGINE_LOAD",
    "throttle_pos": "THROTTLE_POS",
    "timing_adv": "TIMING_ADVANCE",
    "intake_temp": "INTAKE_TEMP",
    "short_ft_b1": "FUEL_TRIM_SHORT_BANK1",
    "long_ft_b1": "FUEL_TRIM_LONG_BANK1",
    "map": "INTAKE_PRESSURE",
    "o2b1s1": "O2_B1S1",
    "o2b1s2": "O2_B1S2",
    "cm_voltage": "CONTROL_MODULE_VOLTAGE",
}

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def as_float(val):
    if val is None:
        return None
    v = getattr(val, "magnitude", val)
    try:
        return float(v)
    except Exception:
        try:
            return float(str(v).split()[0])
        except Exception:
            return None

def main():
    ap = argparse.ArgumentParser(description="Record OBD stream to CSV + DTC/FreezeFrame JSONL")
    ap.add_argument("--port", default="/dev/rfcomm0")
    ap.add_argument("--interval", type=float, default=0.5, help="seconds between polls")
    ap.add_argument("--out_csv", default="data/drive.csv")
    ap.add_argument("--out_dtc", default="data/drive.dtc.jsonl")
    ap.add_argument("--pids", nargs="*", default=["rpm","speed","coolant_temp","maf","engine_load","throttle_pos"])
    ap.add_argument("--timeout", type=float, default=2.0)
    args = ap.parse_args()

    sel = [p for p in args.pids if p in PIDS]
    os.makedirs(os.path.dirname(args.out_csv) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(args.out_dtc) or ".", exist_ok=True)

    conn = obd.OBD(args.port, fast=False, timeout=args.timeout)
    if not conn.is_connected():
        print("Could not connect to adapter", file=sys.stderr)
        sys.exit(2)

    vin = ""
    try:
        r = conn.query(obd.commands.VIN)
        if r and r.value:
            vin = str(r.value).strip().replace(" ", "").replace("\x00","")
    except Exception:
        pass

    # Prepare CSV
    fieldnames = ["ts","vin"] + sel
    fcsv = open(args.out_csv, "w", newline="")
    writer = csv.DictWriter(fcsv, fieldnames=fieldnames)
    writer.writeheader()

    # Prepare JSONL for DTC & freeze frames
    fdtc = open(args.out_dtc, "w")

    last_dtcs = []
    print(f"Recording… VIN={vin or 'unknown'}  CSV={args.out_csv}  DTC={args.out_dtc}")
    try:
        while True:
            row = {"ts": utcnow(), "vin": vin}
            for pid in sel:
                cmd = getattr(obd.commands, PIDS[pid])
                resp = conn.query(cmd)
                row[pid] = as_float(resp.value) if resp else None
            writer.writerow(row)
            fcsv.flush()

            # DTC & freeze frame snapshot (on change)
            try:
                r = conn.query(obd.commands.GET_DTC)
                dtcs = []
                if r and r.value:
                    for tup in r.value:
                        if isinstance(tup, (list, tuple)) and tup:
                            code = str(tup[0]).strip()
                            if code:
                                dtcs.append(code)
                    dtcs = sorted(set(dtcs))
                if dtcs != last_dtcs:
                    # Try Mode 02 freeze-frame values (best effort)
                    ff = {}
                    for name in [
                        "FREEZE_ENGINE_RPM","FREEZE_VEHICLE_SPEED","FREEZE_ENGINE_LOAD",
                        "FREEZE_THROTTLE_POS","FREEZE_COOLANT_TEMP","FREEZE_INTAKE_TEMP",
                        "FREEZE_INTAKE_PRESSURE","FREEZE_TIMING_ADVANCE","FREEZE_MAF",
                        "FREEZE_FUEL_TRIM_SHORT_BANK1","FREEZE_FUEL_TRIM_LONG_BANK1",
                        "FREEZE_DTC"
                    ]:
                        cmd = getattr(obd.commands, name, None)
                        if not cmd: continue
                        rr = conn.query(cmd)
                        if rr and rr.value is not None:
                            ff[name] = as_float(rr.value) or str(rr.value)
                    ev = {"ts": utcnow(), "vin": vin, "dtcs": dtcs, "freeze_frame": ff}
                    fdtc.write(json.dumps(ev) + "\n")
                    fdtc.flush()
                    last_dtcs = dtcs
            except Exception:
                pass

            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        fcsv.close()
        fdtc.close()

if __name__ == "__main__":
    main()
