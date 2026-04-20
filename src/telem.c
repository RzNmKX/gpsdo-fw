#include "telem.h"
#include "int.h"
#include "frequency.h"
#include "mcu_time.h"
#include "gps.h"
#include "tim.h"
#include <stdio.h>
#include <string.h>

// XOR checksum over the chars between '$' and '*' (exclusive).
static uint8_t nmea_checksum(const char *sentence)
{
    uint8_t cksum = 0;
    // skip leading '$'
    if (*sentence == '$') sentence++;
    for (; *sentence && *sentence != '*'; sentence++) {
        cksum ^= (uint8_t)*sentence;
    }
    return cksum;
}

size_t telem_format_nmea(char *buf, size_t buf_size)
{
    int32_t ppb       = frequency_get_ppb();        // ppb * 100
    uint32_t pwm      = TIM1->CCR2;
    int32_t pps_ns    = (int32_t)((int64_t)pps_error * 100 / 7);  // 70 MHz: 1 cycle = 100/7 ns
    uint32_t freq     = ppb_frequency;
    uint8_t lock      = gps_lock_status ? 1 : 0;
    uint32_t uptime   = device_uptime;
    const char *status = mcu_time_get_status();

    // $PGPSD,ppb_x100,pwm,pps_ns,freq,lock,uptime,status,temp,pressure*XX\r\n
    // temp and pressure are empty placeholders for future sensors
    int n = snprintf(buf, buf_size,
        "$PGPSD,%ld,%lu,%ld,%lu,%u,%lu,%s,,",
        (long)ppb,
        (unsigned long)pwm,
        (long)pps_ns,
        (unsigned long)freq,
        lock,
        (unsigned long)uptime,
        status);

    if (n < 0 || (size_t)n >= buf_size - 6) {
        // not enough space for checksum + \r\n
        return 0;
    }

    uint8_t cksum = nmea_checksum(buf);
    int tail = snprintf(buf + n, buf_size - n, "*%02X\r\n", cksum);
    if (tail < 0) return 0;

    return (size_t)(n + tail);
}
