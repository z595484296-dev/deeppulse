#pragma once

#include <Arduino.h>

enum DeepPulseRefreshKind {
  DEEPPULSE_REFRESH_FAILED = 0,
  DEEPPULSE_REFRESH_DRY_RUN = 1,
  DEEPPULSE_REFRESH_FULL = 2,
  DEEPPULSE_REFRESH_FAST = 3,
  DEEPPULSE_REFRESH_PARTIAL = 4,
};

enum DeepPulseRefreshRequest {
  DEEPPULSE_REQUEST_FULL = 0,
  DEEPPULSE_REQUEST_FAST = 1,
  DEEPPULSE_REQUEST_PARTIAL = 2,
};

struct DeepPulseDisplayRegion {
  uint16_t x;
  uint16_t y;
  uint16_t width;
  uint16_t height;
};

bool deepPulseEpdBegin();
// The adapter preserves the supplied full-frame buffer even though Waveshare's
// V2 full-display routine temporarily inverts it in place.
DeepPulseRefreshKind deepPulseEpdDisplay(uint8_t *frame, size_t length,
                                         DeepPulseRefreshRequest request,
                                         const DeepPulseDisplayRegion &region);
void deepPulseEpdSleep();
bool deepPulseEpdIsPhysical();
