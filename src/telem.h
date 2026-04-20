#pragma once

#include <stddef.h>
#include <stdbool.h>

// Format a $PGPSD NMEA proprietary sentence into buf.
// Returns number of bytes written (including \r\n, excluding \0).
size_t telem_format_nmea(char *buf, size_t buf_size);
