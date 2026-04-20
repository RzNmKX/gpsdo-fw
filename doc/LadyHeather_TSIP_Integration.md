# Lady Heather TSIP Output Implementation Plan

## 1. Background and Format Landscape

- Lady Heather can speak with many receiver protocols, including Trimble TSIP (`/rxt`), Motorola binary (`/rxm`), generic NMEA (`/rxn`), Ublox UBX (`/rxu`/`/rxut`), TAIP, SCPI variants, etc., as documented in the built-in help table (`ladyheather/heather.cpp:680-744`). The Trimble TSIP binary profile is the closest match to the BH3SAP GPSDO feature set because it already models a GPSDO with oscillator control, PPS stats, and DAC reporting.
- Heather expects TSIP packets framed with `DLE` (0x10) / message ID / payload / `DLE` `ETX`, with `DLE` stuffing inside the payload (`ladyheather/heathgps.cpp:2884-2893`).
- The UI relies heavily on two recurring TSIP timing packets: 0x8F-AB (“Primary timing”) and 0x8F-AC (“Secondary timing”), which carry time-of-week/date fields plus oscillator, DAC, and survey/holdover data (`ladyheather/heathgps.cpp:12225-12380`, `12626-12900`).

## 2. Current Firmware Baseline

| Capability | Where in repo | Notes |
| ---------- | ------------- | ----- |
| GPS ingest | `src/gps.c:366-688` | UART3 receives NMEA (GGA/RMC/TXT), parses time/date, lat/lon/alt, HDOP, number of satellites, and forwards raw bytes to UART2 for pass-through. |
| Timing disciplines | `src/int.c:200-285` | PPS capture ISR computes `pps_error`, `ppb_frequency`, `ppb_error`, PWM corrections (via TIM1->CCR2), and internal lock status flags. |
| Oscillator statistics | `src/frequency.c:30-74` | Maintains cumulative frequency error, rolling PPB average, and stability checks. |
| Configuration state | `src/int.c`, `src/menu.c`, EEPROM structures | Holds PWM value, correction factor, lock thresholds, and menu toggles that can map to TSIP discipline/alarms bits. |
| Serial topology | `src/gps.c:653-688` | UART2 currently mirrors GPS UART3 (bidirectional pass-through), so Heather mode must replace this pipe with a TSIP stream. |

Takeaways:

1. All telemetry needed for TSIP timing packets (time/date, lat/lon/alt, PPS error, OCXO PWM/DAC equivalent, lock status) already exists in RAM.
2. UART2 is free once we disable the GPS pass-through path, so it can become the dedicated Heather port.

## 3. Target: Generic Trimble TSIP Output

### 3.1 Message set to support first

| TSIP packet | Why Heather needs it | Notes |
| ----------- | -------------------- | ----- |
| `0x45` Version Info | Allows auto-detect to classify the device and label screens. |
| `0x4A` Single LLA fix | Supplies lat/lon/alt for maps and survey readouts. |
| `0x8F-AB` Primary timing | Heather’s time display and week/TOW tracking use this packet (`ladyheather/heathgps.cpp:12225-12380`). |
| `0x8F-AC` Secondary timing | Carries PPS offset, oscillator error, DAC value, temperature, survey/holdover/alarms (`ladyheather/heathgps.cpp:12626-12900`). |
| Optional later: `0x47` signal levels, `0x3C/0x5C` satellite tracking, `0x38` UTC info | Nice-to-have for richer UI but not required to get core dashboards working. |

Heather polls for many packets, but if we stream AB/AC plus respond to the occasional request IDs that Heather sends at startup (`request_version`, `request_sat_list`, etc. in `ladyheather/heathgps.cpp:9825-10120`), it will stay happy even if unimplemented commands return an “unsupported” byte or do nothing. We can start by unconditionally pushing AB/AC once per second and replying to `0x1F`/`0x1C`/`0x45` requests to satisfy version queries.

### 3.2 Field mapping

| TSIP field | Meaning in Heather | Proposed source |
| ---------- | ------------------ | --------------- |
| `TOW` (dword) | GPS time-of-week in ms | Convert parsed UTC date/time to GPS week + seconds. Use existing `gps_time` + `gps_date` with a Julian conversion helper; maintain rolling week counter. |
| `GPS week` (word) | Heather’s week counter | Same helper as above. |
| `UTC offset` | Leapsecond diff | Use user-configurable constant (default 18) until we can parse $GPZDA or almanac data. |
| `Time flags` | Bits for UTC/GPS validity | Populate from current lock status and whether GPS messages are recent (`last_frame_receive_time`). |
| `Seconds` / `Minutes` / `Hours` / `Day` / `Month` / `Year` | Displayed UTC | Already held in `gps_time`/`gps_date` (`src/gps.c:366-520`). |
| `Receiver mode`, `Discipline mode`, `Discipline` | Shows “Normal”, “Holdover”, etc. | Map to internal state: GPS lock, warmup, manual hold, etc. |
| `Survey progress` | % of survey/averaging | Use number of samples we averaged before declaring lock (e.g., `num_samples` / 128) or reuse PPB stabilization progress. |
| `Holdover time` | seconds since good GPS | Track `HAL_GetTick() - last_pps` and convert to seconds. |
| `Critical`/`Minor alarms` | 16-bit bitfields | Compose from booleans (antenna open? no GPS fix? warmup?); document mapping so UI icons match. |
| `GPS status` | e.g., “0 = No fix” | Derive from `num_sats`, `gps_lock_status`. |
| `PPS offset (float)` | ns offset | Convert `pps_error` counts to nanoseconds using 70 MHz clock relationship (`src/int.c:211-260`). |
| `Osc offset (float)` | fractional frequency | Use `ppb_error`/`frequency_get_ppb()` scaled to ppb/Hz. |
| `DAC value (float)` | Control voltage or DAC percent | Convert TIM1->CCR2 (0–65535) into volts based on DAC range (document assumption). |
| `Temperature (float)` | OCXO temp | If no sensor, populate with board temp or 0 and clear “have temperature” flag until sensor is added. |
| `Lat/Lon/Alt` | Position | Already stored as doubles (`src/gps.c:435-467`). |
| `PPS quantization` | Sawtooth correction | Reuse `pps_error` remainder or just zero. |
| Spare bytes | Vendor data | Encode firmware version, current algorithm, etc. |

### 3.3 Scheduling and transport

- Use the PPS ISR (`src/int.c:200-285`) to set a flag once per second; the main loop emits AB+AC packets immediately after we confirm we have fresh GPS data to avoid reusing stale timestamps.
- UART2 should run at a Heather-friendly default (9600 8-N-1). Provide a menu item + EEPROM flag to switch between “Passthrough” and “Lady Heather (TSIP)” modes because the old serial bridge is still valuable.
- Implement a tiny TSIP serializer module that accepts a packet ID + payload bytes, escapes `DLE`, and sends `DLE ETX` to UART2. For now we only need transmit, but leave hooks for processing commands from Heather (e.g., if it sends `0x21` to request GPS time, reply with our cached AB packet).

## 4. Implementation Steps

1. **Configuration plumbing**
   - Add a persistent enum in EEPROM (e.g., `serial_mode = PASSTHROUGH | LADY_HEATHER_TSIP`), surfacing it in the existing menu so the user can pick the output behavior.
   - Update `gps_read()` so UART2 mirroring only happens in pass-through mode; in TSIP mode, UART2 writes come exclusively from the TSIP scheduler (`src/gps.c:653-688`).

2. **Shared telemetry snapshot**
   - Create a `struct heather_snapshot` filled once per second with safe copies of all fields listed in §3.2 (time, PPS error, PPB, PWM, satellites, etc.). Populate fields from `gps.c`, `int.c`, and `frequency.c` using critical sections or DMA-safe copies so ISRs are not blocked.
   - Include helpers to compute GPS week/TOW and holdover seconds so TSIP formatting stays simple.

3. **TSIP serializer module**
   - New file `src/tsip.c` (or similar) that exposes `tsip_send(uint8_t id, const uint8_t *payload, size_t len)`.
   - Handles DLE stuffing and `DLE ETX` termination exactly as Heather expects (`ladyheather/heathgps.cpp:2884-2893`).
   - Provide convenience builders for multi-byte fields (word/dword/float/double) because packets like 0x8F-AC mix several types.

4. **Packet encoders**
   - `build_primary_timing(const heather_snapshot*)` → writes AB payload in the order Heather decodes (TOW, week, UTC offset, flags, hh:mm:ss, date, year).
   - `build_secondary_timing(...)` → writes mode/alarms/disciplines/offsets/DAC/temp/LLA/spares per §3.2.
   - `build_version_info(...)` and `build_single_lla(...)` for 0x45 and 0x4A. Include firmware revision strings so Heather can show “Lady Heather-compatible BH3SAP”.

5. **Scheduler**
   - Hook into the main loop (e.g., where LCD refresh happens) to check a “new PPS” flag:
     - Every second: send 0x8F-AB immediately followed by 0x8F-AC.
     - Every 5 seconds (or on boot): send 0x45 and 0x4A so Heather has identification/location info even if it misses the very first packets.
   - If UART2 TX is busy, queue packets to avoid overlapping HAL `Transmit_IT` calls.

6. **Inbound command handling (minimal viable)**
   - Buffer bytes from UART2 in TSIP framing state machine (reuse logic from `gps_read` FIFOs).
   - Recognize a small subset of commands Heather emits on startup: `0x1F` (request version), `0x21`/`0x22` (request GPS time), `0x35` (I/O options). Reply with canned packets or `NAK`.
   - Log/ignore unknown commands for now; document that full bidirectional control is not implemented.

7. **Documentation & UX**
   - Update `README.md` to describe the new “Lady Heather mode”, default serial settings, and which TSIP packets are emitted.
   - Provide quickstart instructions (which Heather command line switch, expected baud/parity).

## 5. Validation Plan

1. **Unit-style checks**
   - Add host-side tests (e.g., using `ctest` or a simple Python script) that feed the TSIP serializer with known inputs and verify byte streams against expected hex (ensuring DLE stuffing and field ordering).
2. **Hardware-in-the-loop**
   - Flash firmware, connect UART2 to a PC, run `heather -1` (or whichever COM port) with `/rxt`. Confirm that Heather auto-detects as TSIP, shows moving time, DAC, PPS, and location.
   - Toggle GPS antenna on/off to ensure alarms and holdover transitions look sane in Heather.
3. **Regression**
   - Verify that pass-through mode still mirrors GPS as before (e.g., by running `screen /dev/ttyUSBx 9600`).
   - Ensure PPS disciplining behavior (PWM updates, lock threshold) is unaffected by the extra serialization workload.

## 6. Risks / Follow-ups

- **Leapsecond and GPS week rollover**: Without parsing $GPZDA or subframe data we must hardcode UTC offset; plan to extend the GPS parser or piggyback on Heather’s keyboard commands later.
- **Temperature field**: The BH3SAP hardware lacks a native temperature sensor in firmware; AB/AC packets may need placeholder values until we sample an ADC channel or re-use OCXO sensor data.
- **Satellite detail**: Heather’s sky plot will remain empty until we implement TSIP packets 0x5C/0x59/0x47. These can be deferred, but note the limitation in documentation.
- **Command coverage**: Heather might try to reconfigure the receiver (packet masks, survey commands). Until we implement a parser, block those commands to avoid confusing the UI.

This plan keeps the initial scope to two critical TSIP packets for Heather compatibility, while leaving clear hooks for richer telemetry once the basic stream works.

---

## Appendix: Environmental Sensor Expansion (DS18B20 / BME280)

User feedback highlighted that the OCXO is temperature and pressure sensitive, so it would be helpful to expose real environmental readings to Heather (packets 0x8F-AC spares) and to the local UI. The Bluepill still has usable GPIOs even though most high-speed peripherals are occupied (`lib/stm32/Core/Inc/main.h:35-75` lists the currently consumed pins). Below is a quick feasibility assessment.

### 1. Available pins and buses

| Function | MCU pins today | Status | Notes |
| -------- | -------------- | ------ | ----- |
| I²C1 (PB6/PB7) | LCD D6/D7 | **Busy** | Sharing with the HD44780 bus is impractical. |
| I²C2 (PB10/PB11) | GPS module UART (USART3 remap) | **Busy** | Needed for receiver comms. |
| SPI1 (PA5/PA6/PA7) | Unused | **Routed to rotary encoder** (`ROTARY_A/B`), so not free. |
| SPI2 (PB12–PB15) | Not referenced anywhere | **Free** | Ideal for an SPI BME280 (PB12=CS, PB13=SCK, PB14=MISO, PB15=MOSI). |
| PC14/PC15 | Not referenced | **Free** | Low-speed GPIO, fine for 1‑Wire sensors like DS18B20. |
| PA15 / PB0 / PB2 | Unused on this design | **Free** | Alternate choice for 1‑Wire or software I²C if desired. |

Conclusion: there isn’t a spare hardware I²C bus, but we can (a) hang a DS18B20 on any unused GPIO, and/or (b) run a BME280 over SPI2 without disturbing existing functions.

### 2. DS18B20 (board-level OCXO temp)

1. Pick a free pin—`PC14` is convenient because it already exits on the Bluepill header and is not used by the firmware. Configure it as open-drain with an internal or external pull-up (better to add a 4.7 kΩ to 3.3 V near the sensor to keep noise off the board).
2. Run a short shielded lead to the can of the OCXO or glue the TO‑92 package to its side; that will measure actual oven temperature instead of ambient.
3. Implement a simple 1‑Wire driver (bit-bang) in a background task so we do not extend the PPS ISR. Sample at 1–2 Hz max and push the temperature into the TSIP secondary timing packet (currently `x_temp` field).
4. Safety: keep the wiring short and away from the RF section of the GPS front-end.

### 3. BME280 (ambient pressure/humidity)

- The breakout supports both I²C and SPI; because both hardware I²C ports are spoken for, prefer SPI2 on PB12–PB15. Use PB12 as chip-select, PB13 for SCK, PB14 for MISO, PB15 for MOSI.
- Power from 3.3 V (the board already has a regulator feeding the MCU). Many BME280 boards include their own LDO/level shifters; if yours does, tie VIN to 5 V instead.
- Mount location: the picture in `doc/open-case.jpg` shows free space along the front panel and lid. To sense ambient pressure instead of OCXO heat, standoff the board near the front panel and, if possible, drill a small vent hole so the cavity tracks room pressure.
- Firmware: add an SPI2 driver (DMA not required). Sample every few seconds to avoid bus contention, cache values, and include them in TSIP packet spares or in a new local UI page.

### 4. Pressure sensitivity considerations

Reports that the OCXO drifts with pressure suggest two sensors might be useful:
1. **Ambient sensor (BME280)**: captures the external environment as a feed-forward term.
2. **Internal OCXO temp (DS18B20)**: confirms the oven is stable and provides data for plotting/compensation.

Using both allows us to log correlations in Heather or offline, paving the way for compensation algorithms later.

### 5. Next steps

1. Decide whether we want only temperature (simpler DS18B20) or full P/T/H (`BME280 via SPI2`).
2. Break out the chosen pins on the BH3SAP PCB via a small JST/XH header so the sensors can be serviced without soldering directly to the Bluepill.
3. Extend the firmware HAL init (`MX_GPIO_Init`, `MX_SPI2_Init`) plus new drivers (`ds18b20.c`, `bme280.c`). Feed readings into the TSIP telemetry and on-device menus.
4. Update documentation to show wiring (pin name, resistor requirements) so users can replicate the mod safely.
