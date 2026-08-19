#pragma once

#include <Arduino.h>

enum DeepPulseRefreshKind {
  DEEPPULSE_REFRESH_FAILED = 0,
  DEEPPULSE_REFRESH_DRY_RUN = 1,
  DEEPPULSE_REFRESH_FULL = 2,
  DEEPPULSE_REFRESH_PARTIAL = 3,
};

bool deepPulseEpdBegin();
// The Waveshare V2 display routine inverts the supplied frame buffer in place,
// so callers must treat the buffer as consumed after a successful refresh.
DeepPulseRefreshKind deepPulseEpdDisplay(uint8_t *frame, size_t length,
                                         bool requestPartial);
void deepPulseEpdSleep();
bool deepPulseEpdIsPhysical();
