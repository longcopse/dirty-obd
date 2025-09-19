import obd
c = obd.OBD("/dev/rfcomm0", fast=False, timeout=3, protocol=obd.protocols.ISO_15765_4_CAN)
print("connected?", c.is_connected())
print("rpm:", c.query(obd.commands.RPM))
print("pids_a:", c.query(obd.commands.PIDS_A))
