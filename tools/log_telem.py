#!/usr/bin/env python3
"""Serial logger for GPSDO $PGPSD telemetry. Writes every sample to CSV.

Usage: python3 log_telem.py [port] [baud]
  Default: /dev/cu.usbserial-0001 115200

Run separately from plot_telem.py. This never drops samples.
"""

import sys
import os
import csv
import serial
from datetime import datetime, timezone

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/cu.usbserial-0001"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200

HEADER = ["utc", "elapsed_s", "ppb_x100", "pwm", "pps_ns", "freq",
          "lock", "uptime", "status", "temp", "pressure"]

log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(log_dir, exist_ok=True)
log_path = os.path.join(log_dir, f"gpsdo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")

ser = serial.Serial(PORT, BAUD, timeout=1)
ser.reset_input_buffer()

log_file = open(log_path, "w", newline="")
writer = csv.writer(log_file)
writer.writerow(HEADER)
print(f"Logging to {log_path}")
print(f"Port: {PORT} @ {BAUD}")
print("Ctrl-C to stop.\n")

count = 0
try:
    while True:
        try:
            raw = ser.readline().decode("ascii", errors="replace").strip()
        except Exception:
            continue
        if not raw.startswith("$PGPSD,"):
            continue
        body = raw.split("*")[0]
        fields = body.split(",")
        if len(fields) < 8:
            continue
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            writer.writerow([
                now, "",
                fields[1], fields[2], fields[3], fields[4],
                fields[5], fields[6], fields[7],
                fields[8] if len(fields) > 8 else "",
                fields[9] if len(fields) > 9 else "",
            ])
            log_file.flush()
            count += 1
            uptime = fields[6]
            ppb = fields[1]
            pwm = fields[2]
            print(f"\r[{count}] uptime={uptime}  ppb={ppb}  pwm={pwm}    ", end="", flush=True)
        except (ValueError, IndexError):
            continue
except KeyboardInterrupt:
    pass
finally:
    log_file.close()
    ser.close()
    print(f"\n\nSaved {count} samples to {log_path}")
