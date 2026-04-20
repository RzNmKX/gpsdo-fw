#!/usr/bin/env python3
"""Live streaming plotter + CSV logger for GPSDO $PGPSD telemetry.

Usage: python3 plot_telem.py [port] [baud]
  Default: /dev/cu.usbserial-0001 9600

Logs every sample to logs/gpsdo_YYYYMMDD_HHMMSS.csv alongside the plot.
"""

import sys
import os
import time
import csv
import serial
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import deque
from datetime import datetime, timezone

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/cu.usbserial-0001"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 9600
WINDOW = 300  # seconds of history on screen

# --- CSV setup ---
log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(log_dir, exist_ok=True)
log_path = os.path.join(log_dir, f"gpsdo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
log_file = open(log_path, "w", newline="")
csv_writer = csv.writer(log_file)
csv_writer.writerow(["utc", "elapsed_s", "ppb_x100", "pwm", "pps_ns", "freq", "lock", "uptime", "status", "temp", "pressure"])
print(f"Logging to {log_path}")

# --- Ring buffers for plot ---
ts       = deque(maxlen=WINDOW)
ppb      = deque(maxlen=WINDOW)
pwm      = deque(maxlen=WINDOW)
pps_us   = deque(maxlen=WINDOW)
freq_err = deque(maxlen=WINDOW)

ser = serial.Serial(PORT, BAUD, timeout=0.1)
ser.reset_input_buffer()
t0 = time.monotonic()

fig, axes = plt.subplots(4, 1, figsize=(12, 8), sharex=True)
fig.suptitle(f"GPSDO Telemetry — {PORT} @ {BAUD}", fontsize=11)

titles = ["PPB (×0.01)", "PWM (DAC)", "PPS Error (μs)", "Freq Error (Hz)"]
ylabels = ["ppb×100", "PWM", "μs", "Hz"]
colors = ["#2196F3", "#FF9800", "#4CAF50", "#E91E63"]
lines = []

for i, ax in enumerate(axes):
    ln, = ax.plot([], [], color=colors[i], linewidth=1)
    lines.append(ln)
    ax.set_ylabel(ylabels[i], fontsize=9)
    ax.set_title(titles[i], fontsize=9, loc="left")
    ax.grid(True, alpha=0.3)
    ax.tick_params(labelsize=8)

axes[-1].set_xlabel("Time (s)")
fig.tight_layout()


def parse_pgpsd(line):
    """Parse $PGPSD,ppb,pwm,pps_ns,freq,lock,uptime,status,temp,pressure*XX"""
    try:
        if not line.startswith("$PGPSD,"):
            return None
        body = line.split("*")[0]
        fields = body.split(",")
        if len(fields) < 8:
            return None
        return {
            "ppb":      int(fields[1]),
            "pwm":      int(fields[2]),
            "pps_ns":   int(fields[3]),
            "freq":     int(fields[4]),
            "lock":     fields[5],
            "uptime":   fields[6],
            "status":   fields[7],
            "temp":     fields[8] if len(fields) > 8 else "",
            "pressure": fields[9] if len(fields) > 9 else "",
        }
    except (ValueError, IndexError):
        return None


def update(_frame):
    while ser.in_waiting:
        try:
            raw = ser.readline().decode("ascii", errors="replace").strip()
        except Exception:
            continue
        d = parse_pgpsd(raw)
        if d is None:
            continue

        t = time.monotonic() - t0
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        # Log every sample
        csv_writer.writerow([
            now_utc, f"{t:.3f}",
            d["ppb"], d["pwm"], d["pps_ns"], d["freq"],
            d["lock"], d["uptime"], d["status"],
            d["temp"], d["pressure"],
        ])
        log_file.flush()

        # Plot buffers
        ts.append(t)
        ppb.append(d["ppb"])
        pwm.append(d["pwm"])
        pps_us.append(d["pps_ns"] / 1000.0)
        freq_err.append(d["freq"] - 70_000_000)

    if not ts:
        return lines

    t_list = list(ts)
    datasets = [list(ppb), list(pwm), list(pps_us), list(freq_err)]

    for i, (ln, data) in enumerate(zip(lines, datasets)):
        ln.set_data(t_list, data)
        ax = axes[i]
        ax.set_xlim(max(0, t_list[-1] - WINDOW), t_list[-1] + 2)
        if data:
            lo, hi = min(data), max(data)
            margin = max(abs(hi - lo) * 0.1, 1)
            ax.set_ylim(lo - margin, hi + margin)

    return lines


try:
    ani = animation.FuncAnimation(fig, update, interval=500, blit=False, cache_frame_data=False)
    plt.show()
finally:
    log_file.close()
    ser.close()
    print(f"\nSaved {log_path}")
