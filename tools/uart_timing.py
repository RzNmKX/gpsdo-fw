#!/usr/bin/env python3
"""Measure NMEA burst timing on the GPSDO passthrough UART.

Reports per-second burst duration, idle gap, byte count, and
how much time is available for injecting telemetry sentences.
"""

import sys
import time
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/cu.usbserial-0001"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 9600
DURATION = int(sys.argv[3]) if len(sys.argv) > 3 else 15  # seconds to capture

ser = serial.Serial(PORT, BAUD, timeout=0.01)
ser.reset_input_buffer()

# Collect timestamped bytes
events = []  # (timestamp, byte_count_in_read)
t_start = time.monotonic()

while time.monotonic() - t_start < DURATION:
    data = ser.read(256)
    if data:
        events.append((time.monotonic(), len(data)))

ser.close()

if not events:
    print("No data received.")
    sys.exit(1)

# Detect bursts: gap > 100ms means new burst
GAP_THRESHOLD = 0.100
bursts = []  # [(start, end, byte_count)]
burst_start = events[0][0]
burst_bytes = events[0][1]
prev_t = events[0][0]

for t, n in events[1:]:
    if t - prev_t > GAP_THRESHOLD:
        bursts.append((burst_start, prev_t, burst_bytes))
        burst_start = t
        burst_bytes = 0
    burst_bytes += n
    prev_t = t

bursts.append((burst_start, prev_t, burst_bytes))

# Analyze
print(f"Port: {PORT} @ {BAUD} baud")
print(f"Captured {len(bursts)} bursts over {DURATION}s\n")
print(f"{'Burst':>5}  {'Start':>8}  {'Duration':>10}  {'Bytes':>6}  {'TX time':>10}  {'Gap after':>10}  {'Free':>10}")
print("-" * 78)

# bits per byte at this baud: 10 (start + 8 data + stop)
bytes_per_sec = BAUD / 10

for i, (start, end, nbytes) in enumerate(bursts):
    duration = end - start
    tx_time = nbytes / bytes_per_sec  # time to transmit at wire speed
    if i + 1 < len(bursts):
        gap = bursts[i + 1][0] - end
    else:
        gap = None

    gap_str = f"{gap * 1000:8.1f} ms" if gap is not None else "       N/A"
    free_str = gap_str  # gap is the free window

    print(f"{i + 1:>5}  {start - t_start:>7.3f}s  {duration * 1000:>8.1f} ms  {nbytes:>6}  {tx_time * 1000:>8.1f} ms  {gap_str}  {free_str}")

print()

# Summary (skip first/last partial bursts)
if len(bursts) >= 3:
    inner = bursts[1:-1]
    byte_counts = [b[2] for b in inner]
    gaps = []
    for i in range(1, len(bursts) - 1):
        if i + 1 < len(bursts):
            gaps.append(bursts[i + 1][0] - bursts[i][1])

    avg_bytes = sum(byte_counts) / len(byte_counts)
    avg_tx = avg_bytes / bytes_per_sec
    avg_gap = sum(gaps) / len(gaps) if gaps else 0

    print("=== Summary (excluding first/last partial) ===")
    print(f"  Avg bytes/burst:    {avg_bytes:.0f}")
    print(f"  Avg TX time:        {avg_tx * 1000:.1f} ms  (wire time at {BAUD} baud)")
    print(f"  Avg idle gap:       {avg_gap * 1000:.1f} ms")
    print(f"  Avg utilization:    {avg_tx / (avg_tx + avg_gap) * 100:.1f}%")
    print()

    # How much can we inject?
    headroom_bytes = int(avg_gap * bytes_per_sec * 0.8)  # 80% of gap to be safe
    print(f"  Safe injection window: {avg_gap * 1000 * 0.8:.0f} ms  ({headroom_bytes} bytes)")
    print()

    # Compare baud rates
    print("=== Baud rate comparison ===")
    for baud in [9600, 19200, 38400, 57600, 115200]:
        bps = baud / 10
        tx = avg_bytes / bps
        gap = 1.0 - tx  # assuming 1s cycle
        headroom = int(gap * bps * 0.8)
        print(f"  {baud:>6} baud:  burst TX {tx * 1000:>6.1f} ms,  free {gap * 1000:>6.0f} ms,  injectable ~{headroom} bytes")
