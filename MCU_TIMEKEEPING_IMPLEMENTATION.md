# MCU-Based Disciplined Timekeeping Implementation Plan

## Overview

Transform the GPSDO from displaying raw GPS time to maintaining its own disciplined time using the MCU's GPS-synchronized oscillator. This makes the device a true timing reference that continues operating during GPS outages.

## Current State Analysis

### Existing GPS PPS Infrastructure (Already Working)

- TIM2: Configured for precise 1-second intervals (70MHz clock via PSC=7000-1, ARR=10000-1)
- GPS PPS Sync: TIM2 is realigned to GPS when drift exceeds a threshold by setting TIM2->CNT = TIM2->ARR
- Disciplined Timebase: MCU pulses stay synchronized to GPS atomic time
- Flywheel Capability: Continues during GPS outages with minimal drift
- Sync Logic: PPS capture computes error and forces TIM2 realignment when needed

### Current Limitation

- Display shows raw GPS time strings parsed from NMEA
- No time display when GPS unavailable
- Device cannot function as standalone timing reference

## Proposed Architecture

### 1) MCU Time Module (`src/mcu_time.h`)

Use a simple HH:MM:SS time-of-day counter with GPS discipline status. Keep string formatting safe for ISR usage.

```c
#pragma once
#include <stdint.h>
#include <stdbool.h>

typedef struct {
    uint8_t hours;               // 0-23
    uint8_t minutes;             // 0-59
    uint8_t seconds;             // 0-59
    int8_t  timezone_offset;     // -14..+14 hours (init from existing gps_time_offset)
    bool    gps_disciplined;     // true if recently synced to GPS
    uint32_t seconds_since_gps;  // seconds since last GPS sync
} mcu_time_t;

extern volatile mcu_time_t mcu_time;

// 8 chars + NUL. Mark volatile and update via memcpy to avoid tearing.
extern volatile char mcu_time_string[9];

// Optional: dirty flag if you prefer formatting outside the ISR.
extern volatile bool mcu_time_dirty;

void mcu_time_init(int8_t initial_tz_offset);
void mcu_time_increment(void);

// Sync from a "HH:MM:SS" string (preferred) or "HHMMSS" if format==false.
void mcu_time_sync_from_string(const char* gps_formatted, bool formatted_hh_colon_mm_colon_ss);

// Manual setters (optional)
void mcu_time_set(uint8_t h, uint8_t m, uint8_t s);
void mcu_time_set_timezone(int8_t offset);
const char* mcu_time_get_status(void); // e.g. "GPS-Sync"/"GPS-Disc"/"Free-Run"
```

### 2) Core Behavior (`src/mcu_time.c`)

- mcu_time_init(): zero the clock; set timezone_offset from EEPROM-backed GPS time offset; set gps_disciplined=false; seconds_since_gps=0; build mcu_time_string ("00:00:00").
- mcu_time_increment(): increment seconds and roll over; ++seconds_since_gps; minimally update mcu_time_string. Keep it ISR-light: either
  - update `mcu_time_string` with a small local buffer and `memcpy(9)`, or
  - set `mcu_time_dirty=true` and format the string in the main loop before drawing.
- mcu_time_sync_from_string(): parse "HH:MM:SS" (preferred) or "HHMMSS" and set fields; set gps_disciplined=true; seconds_since_gps=0; refresh `mcu_time_string` atomically.

Rationale: Formatting in the ISR must be minimal. A single 9-byte memcpy is acceptable; avoid heavy `sprintf` in the ISR.

### 3) Integration Points

#### A) TIM2 Period ISR (`src/int.c`)

- Location: HAL_TIM_PeriodElapsedCallback, branch `else if (htim == &htim2)`.
- Keep existing PPS output and `device_uptime++`.
- Add the MCU time increment:

```c
#include "mcu_time.h"

// ...
else if (htim == &htim2) {
    // existing PPS OUT high pulse, LED, uptime...
    device_uptime++;

    // NEW: MCU time increment (ISR-safe and minimal)
    mcu_time_increment();
}
```

Note on resync edge: When forcing `TIM2->CNT = TIM2->ARR`, the next update event may occur quickly, causing two increments close together (time “jumps” forward to re-align). This is acceptable for a disciplined timebase.

#### B) GPS Parser Integration (`src/gps.c`)

- After computing `gps_time` (which already applies +1s compensation and timezone adjustment) and setting `gps_time[8] = '\0';`, sync MCU time using the already-formatted string (preferred over raw token):

```c
#include "mcu_time.h"

// After:
// gps_time[2] = ':';
// gps_time[5] = ':';
// gps_time[8] = '\0';

// NEW: Sync MCU time with compensated/timezone-adjusted GPS time
mcu_time_sync_from_string(gps_time, /*formatted=*/true);
```

Reason: The code intentionally adds 1s to compensate GGA latency and applies timezone via `gps_time_offset`. Re-using this formatted string ensures MCU time matches what the UI expects.

#### C) Display Integration (`src/menu.c`)

- Show `mcu_time_string` on screens intended to display “current time” even when GPS is unavailable (e.g., SCREEN_MAIN, SCREEN_DATE_TIME’s time branch).
- Keep the GPS menu showing the raw `gps_time` to retain a clear distinction between GPS data and MCU timebase.

Examples:

```c
#include "mcu_time.h"

// In SCREEN_MAIN (time view):
LCD_Puts(0, 1, (const char*)mcu_time_string);

// In SCREEN_DATE_TIME:
if (duration <= DATE_TIME_DURATION) {
    LCD_Puts(0, 1, (const char*)mcu_time_string);
} else {
    LCD_Puts(0, 1, gps_date);
}

// In SCREEN_GPS / SCREEN_GPS_TIME:
LCD_Puts(0, 1, gps_time); // keep GPS here
```

### 4) Initialization Order (IMPORTANT) (`src/main.c`)

- Initialize the MCU time module BEFORE enabling TIM2 interrupts to avoid incrementing uninitialized state.

```c
#include "mcu_time.h"

void gpsdo(void)
{
    // Initialize EEPROM, read settings first (timezone comes from ee_storage)
    EE_Init(&ee_storage, sizeof(ee_storage_t));
    EE_Read();
    // ... existing EEPROM defaults ...

    // Initialize MCU time with existing timezone setting
    int8_t tz = (int8_t)(ee_storage.gps_time_offset) + MIN_TIME_OFFSET; // reuse existing convention
    mcu_time_init(tz);

    // Only now start TIM2 periodic interrupts (1 Hz)
    HAL_TIM_Base_Start_IT(&htim2);

    // ... rest of initialization unchanged ...
}
```

### 5) EEPROM Settings (`src/eeprom.h`)

Append new fields to the end of `ee_storage_t` to preserve existing layout. Use erased defaults (0xFF/0xFFFFFFFF) in `main.c` as with other settings.

```c
typedef struct {
    // ... existing fields ...
    uint8_t  use_mcu_timekeeping;   // 1=MCU time, 0=GPS direct; default 1
    uint32_t backup_time_seconds;   // optional future use
    int8_t   mcu_timezone_offset;   // -14..+14, default from gps_time_offset
} ee_storage_t;
```

Initialization defaults in `main.c` (example):

```c
if (ee_storage.use_mcu_timekeeping == 0xff) {
    ee_storage.use_mcu_timekeeping = 1;
}
if (ee_storage.mcu_timezone_offset == (int8_t)0xff) {
    ee_storage.mcu_timezone_offset = (int8_t)(ee_storage.gps_time_offset + MIN_TIME_OFFSET);
}
```

Note: You can defer `backup_time_seconds` until you implement an RTC-like recovery; it’s included here for forward compatibility.

### 6) Build System (`CMakeLists.txt`)

Add the new module to the build:

```cmake
target_sources(${CMAKE_PROJECT_NAME} PRIVATE
    src/main.c
    src/eeprom.c
    src/frequency.c
    src/gps.c
    src/int.c
    src/menu.c
    src/mcu_time.c           # NEW
)
```

### 7) API Summary

- `mcu_time_init(initial_tz)`: initialize time module; set formatted string to "00:00:00"
- `mcu_time_increment()`: ISR-safe second step; maintains `mcu_time_string`
- `mcu_time_sync_from_string(str, formatted)`: set time from GPS; prefers formatted “HH:MM:SS”
- `mcu_time_set(h,m,s)`, `mcu_time_set_timezone(offset)`: manual controls
- `mcu_time_get_status()`: return "GPS-Sync"/"GPS-Disc"/"Free-Run"

## Implementation Phases (Adjusted)

### Phase 0: Init Order Fix (5 minutes)

- Include `mcu_time.h` in `main.c`
- Call `mcu_time_init()` before `HAL_TIM_Base_Start_IT(&htim2)`

### Phase 1: Core Time Counter (30-45 minutes)

Files: `src/mcu_time.h`, `src/mcu_time.c`

- Create time structure, ISR-light increment logic
- Implement GPS sync from formatted time ("HH:MM:SS")
- Maintain `mcu_time_string` safely

### Phase 2: System Integration (30-45 minutes)

Files: `src/int.c`, `src/gps.c`, `src/menu.c`

- Add `mcu_time_increment()` call to TIM2 period ISR
- Call `mcu_time_sync_from_string(gps_time, true)` after GPS time is formatted
- Replace `gps_time` with `mcu_time_string` on non-GPS menu time displays

### Phase 3: Settings & Initialization (30 minutes)

Files: `src/main.c`, `src/eeprom.h`

- Append new EEPROM fields (optional `backup_time_seconds`)
- Initialize defaults (use MCU timekeeping by default)
- Set `mcu_timezone_offset` from existing `gps_time_offset`

### Phase 4: Testing & Validation (30-60 minutes)

- Verify time increments and rollovers
- Verify GPS sync correctness with +1s compensation and TZ alignment
- Validate flywheel tolerance during GPS outages
- Confirm no display tearing (atomic updates or dirty-flag formatting)

Total Estimated: 2-3 hours

## Risk Assessment and Mitigations

### Low Risk ✅

- Time increment/rollover arithmetic
- Display substitutions on main/time screens
- Build system change (one source added)

### Medium Risk ⚠️

- ISR work: Keep it tiny (memcpy or dirty flag); avoid `sprintf` in ISR
- TIM2 resync causing quick double-step: acceptable for re-alignment
- Timezone/date consistency: date still comes from GPS RMC while time is MCU; acceptable in Phase 1

### Mitigations

- Prefer syncing from `gps_time` (already compensated and TZ-adjusted)
- If formatting in ISR, use local buffer + single `memcpy(mcu_time_string, buf, 9)`
- Consider formatting in main loop when `mcu_time_dirty==true` if you observe tearing
- Document that GPS menu shows GPS time; main/time screens show MCU time

## Testing Plan (Adjusted)

### Unit

1. Increment/rollover for seconds/minutes/hours; string updates match fields
2. GPS sync from "HH:MM:SS" updates fields and string atomically
3. Timezone setter adjusts next string correctly

### Integration

1. Boot: Time displays immediately, increments at 1Hz
2. GPS acquisition: MCU time snaps to GPS (+1s compensated), continues ticking
3. GPS outage: MCU time flywheels; display remains live
4. GPS recovery: MCU time re-disciplines cleanly
5. TIM2 resync moments: Accept occasional immediate extra increment

### Validation Criteria

- [ ] Time displays within 5 seconds of boot
- [ ] Time accuracy ±1 second during GPS outages < 1 hour (given disciplined oscillator)
- [ ] GPS sync occurs within 30 seconds of lock
- [ ] No noticeable tearing/glitches on time display
- [ ] Settings persist across power cycles

## Files to Create/Modify

### New Files

- `src/mcu_time.h` - Time structure and function declarations
- `src/mcu_time.c` - Time management implementation

### Modified Files

- `src/int.c` - Call `mcu_time_increment()` in TIM2 ISR
- `src/gps.c` - Call `mcu_time_sync_from_string(gps_time, true)` after formatting `gps_time`
- `src/menu.c` - Use `mcu_time_string` on non-GPS time displays; keep GPS menu on `gps_time`
- `src/eeprom.h` - Append MCU time settings (optional in Phase 1)
- `src/main.c` - Initialize MCU time before starting TIM2 interrupts

### Configuration Files

- `CMakeLists.txt` - Add `src/mcu_time.c` to target sources

## Rollback Plan

If issues arise, disable by:

1. Setting `use_mcu_timekeeping = 0` in EEPROM
2. Reverting display calls back to `gps_time`
3. Removing `mcu_time_increment()` from TIM2 ISR

Existing GPS time functionality remains as fallback.

## Appendix: Minimal Reference Implementations

These are simplified examples to illustrate ISR-safe updates.

`src/mcu_time.c` (skeleton):

```c
#include "mcu_time.h"
#include <string.h>

volatile mcu_time_t mcu_time;
volatile char mcu_time_string[9];
volatile bool mcu_time_dirty = false;

static void fmt_time_local(char* buf, uint8_t h, uint8_t m, uint8_t s) {
    buf[0] = '0' + (h/10); buf[1] = '0' + (h%10);
    buf[2] = ':';
    buf[3] = '0' + (m/10); buf[4] = '0' + (m%10);
    buf[5] = ':';
    buf[6] = '0' + (s/10); buf[7] = '0' + (s%10);
    buf[8] = '\0';
}

void mcu_time_init(int8_t initial_tz_offset) {
    mcu_time.hours = 0; mcu_time.minutes = 0; mcu_time.seconds = 0;
    mcu_time.timezone_offset = initial_tz_offset;
    mcu_time.gps_disciplined = false;
    mcu_time.seconds_since_gps = 0;
    char tmp[9]; fmt_time_local(tmp, 0, 0, 0);
    memcpy((void*)mcu_time_string, tmp, 9);
    mcu_time_dirty = false;
}

void mcu_time_increment(void) {
    uint8_t h = mcu_time.hours;
    uint8_t m = mcu_time.minutes;
    uint8_t s = mcu_time.seconds + 1;

    if (s >= 60) { s = 0; m++; if (m >= 60) { m = 0; h = (h+1) % 24; } }

    mcu_time.seconds = s; mcu_time.minutes = m; mcu_time.hours = h;
    mcu_time.seconds_since_gps++;
    char tmp[9]; fmt_time_local(tmp, h, m, s);
    memcpy((void*)mcu_time_string, tmp, 9);
}

void mcu_time_sync_from_string(const char* str, bool formatted) {
    uint8_t h, m, s;
    if (formatted) {
        h = (str[0]-'0')*10 + (str[1]-'0');
        m = (str[3]-'0')*10 + (str[4]-'0');
        s = (str[6]-'0')*10 + (str[7]-'0');
    } else {
        h = (str[0]-'0')*10 + (str[1]-'0');
        m = (str[2]-'0')*10 + (str[3]-'0');
        s = (str[4]-'0')*10 + (str[5]-'0');
    }

    mcu_time.hours = h; mcu_time.minutes = m; mcu_time.seconds = s;
    mcu_time.gps_disciplined = true;
    mcu_time.seconds_since_gps = 0;

    char tmp[9]; fmt_time_local(tmp, h, m, s);
    memcpy((void*)mcu_time_string, tmp, 9);
}

void mcu_time_set(uint8_t h, uint8_t m, uint8_t s) {
    mcu_time.hours = h%24; mcu_time.minutes = m%60; mcu_time.seconds = s%60;
    char tmp[9]; fmt_time_local(tmp, mcu_time.hours, mcu_time.minutes, mcu_time.seconds);
    memcpy((void*)mcu_time_string, tmp, 9);
}

void mcu_time_set_timezone(int8_t offset) { mcu_time.timezone_offset = offset; }

const char* mcu_time_get_status(void) {
    if (mcu_time.gps_disciplined && mcu_time.seconds_since_gps <= 10) return "GPS-Sync";
    if (mcu_time.gps_disciplined) return "GPS-Disc";
    return "Free-Run";
}
```

These examples are for guidance; adapt to your coding style and constraints.
