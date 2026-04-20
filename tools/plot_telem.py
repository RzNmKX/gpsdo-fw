#!/usr/bin/env python3
"""Plot GPSDO telemetry from a CSV log file.

Usage: python3 plot_telem.py [csvfile]
  Default: most recent file in logs/

Use log_telem.py to capture data, then plot_telem.py to view it.
Rerun to refresh with new data.
"""

import sys
import os
import glob
import csv
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

def find_latest_log():
    log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    files = glob.glob(os.path.join(log_dir, "gpsdo_*.csv"))
    if not files:
        print("No log files found in logs/")
        sys.exit(1)
    return max(files, key=os.path.getmtime)

csv_path = sys.argv[1] if len(sys.argv) > 1 else find_latest_log()
print(f"Plotting {csv_path}")

ts, ppb, pwm, pps_us, freq_err = [], [], [], [], []

with open(csv_path) as f:
    reader = csv.reader(f)
    next(reader)  # skip header
    for row in reader:
        try:
            t = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            ts.append(t)
            ppb.append(int(row[2]))
            pwm.append(int(row[3]))
            pps_us.append(int(row[4]) / 1000.0)
            freq_err.append(int(row[5]) - 70_000_000)
        except (ValueError, IndexError):
            continue

if not ts:
    print("No valid samples found.")
    sys.exit(1)

span_s = (ts[-1] - ts[0]).total_seconds()
print(f"Samples: {len(ts)}, span: {span_s/3600:.1f} hours")

fig, axes = plt.subplots(4, 1, figsize=(14, 9), sharex=True)
fig.suptitle(f"GPSDO Telemetry — {os.path.basename(csv_path)}", fontsize=11)

titles = ["PPB (\u00d70.01)", "PWM (DAC)", "PPS Error (\u03bcs)", "Freq Error (Hz)"]
ylabels = ["ppb\u00d7100", "PWM", "\u03bcs", "Hz"]
colors = ["#2196F3", "#FF9800", "#4CAF50", "#E91E63"]
datasets = [ppb, pwm, pps_us, freq_err]

for i, ax in enumerate(axes):
    ax.plot(ts, datasets[i], color=colors[i], linewidth=0.6, alpha=0.7)
    ax.set_ylabel(ylabels[i], fontsize=9)
    ax.set_title(titles[i], fontsize=9, loc="left")
    ax.grid(True, alpha=0.3)
    ax.tick_params(labelsize=8)

# Format x-axis based on span
if span_s > 7200:
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[-1].xaxis.set_major_locator(mdates.HourLocator())
else:
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))

axes[-1].set_xlabel("UTC")
fig.tight_layout()
plt.show()
