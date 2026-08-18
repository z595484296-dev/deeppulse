#pragma once

#include <Arduino.h>

enum DeepPulseRefreshKind {
  DEEPPULSE_REFRESH_FAILED = 0,
  DEEPPULSE_REFRESH_DRY_RUN = 1,
  DEEPPULSE_REFRESH_FULL = 2,
  DEEPPULSE_REFRESH_PARTIAL = 3,
};

bool deepPulseEpdBegin();
DeepPulseRefreshKind deepPulseEpdDisplay(const uint8_t *frame, size_t length,
                                         bool requestPartial);
void deepPulseEpdSleep();
bool deepPulseEpdIsPhysical();

