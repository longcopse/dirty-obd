# diag_probe_el m.py
import serial, time, sys

PORT = "/dev/rfcomm0"
BAUDS = [38400, 9600]             # try both
PROTOS = ["0","6","7","8","A","3","4","5"]  # auto, CAN 11/29 @500/250, J1850, ISO/KWP
PAUSE = 0.25                      # base pause after write
VIN_WAIT = 1.2                    # VIN needs longer

def read_until_prompt(ser, timeout=3.0):
    end = time.time() + timeout
    buf = bytearray()
    while time.time() < end:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            buf += chunk
            if b">" in buf:
                break
        else:
            time.sleep(0.01)
    return buf.decode(errors="ignore")

def tx(ser, cmd, wait=None):
    # clean input, send, wait, then read until '>'
    ser.reset_input_buffer()
    ser.write((cmd + "\r").encode())
    time.sleep(wait if wait is not None else PAUSE)
    out = read_until_prompt(ser, timeout=4.0)
    print(f"\n>>> {cmd}\n{out.strip()}")
    return out

def try_session(baud):
    print(f"\n=== Trying {PORT} @ {baud} baud ===")
    try:
        ser = serial.Serial(PORT, baudrate=baud, timeout=0.3)
    except Exception as e:
        print(f"Open failed: {e}")
        return False

    try:
        # Basic init
        tx(ser, "ATZ", wait=0.6)
        for c in ("ATE0","ATL0","ATS0","ATH1","ATAT1","ATST64"):  # adaptive timing + ~400ms timeout
            tx(ser, c)

        # Identify adapter + current protocol
        tx(ser, "ATI")
        tx(ser, "ATDP")
        tx(ser, "ATDPN")  # numeric protocol code

        # First, auto protocol
        tx(ser, "ATSP0")
        o100 = tx(ser, "0100")  # supported PID bitmap
        if "41 00" in o100 or "4100" in o100.replace(" ", ""):
            print("\n✅ ECU responded on AUTO protocol.")
        else:
            print("\n⚠️  No PID response on AUTO. Cycling common protocols…")
            for p in PROTOS[1:]:
                tx(ser, f"ATSP{p}")      # force protocol
                o100 = tx(ser, "0100")
                if "41 00" in o100 or "4100" in o100.replace(" ", ""):
                    print(f"✅ ECU responded on ATSP{p}.")
                    break

        # VIN (allow longer wait)
        tx(ser, "0902", wait=VIN_WAIT)

        # RPM / Speed (do a few reads)
        for _ in range(3):
            tx(ser, "010C")
            tx(ser, "010D")
            time.sleep(0.2)

        # Final check: if we saw any 41 0C/41 0D frames, we’re good
        ser.close()
        return True
    except KeyboardInterrupt:
        ser.close()
        return False
    except Exception as e:
        print(f"Error during session: {e}")
        try: ser.close()
        except: pass
        return False

if __name__ == "__main__":
    ok = False
    for b in BAUDS:
        if try_session(b):
            ok = True
            break
    if not ok:
        print("\n❌ Still no data. See notes below.")
