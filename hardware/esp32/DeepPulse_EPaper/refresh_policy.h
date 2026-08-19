#pragma once

#include <stddef.h>
#include <stdint.h>

struct DeepPulseChangeRegion {
  bool changed;
  uint16_t x;
  uint16_t y;
  uint16_t width;
  uint16_t height;
  uint32_t changedPixels;
};

// The server clock/session text at x=560..799, y=0..47 is intentionally ignored.
// A clock-only update should not wake and flash the physical panel.
DeepPulseChangeRegion deepPulseAnalyzeFrameChange(const uint8_t *previous,
                                                   const uint8_t *current,
                                                   size_t length);
bool deepPulseCanUsePartial(const DeepPulseChangeRegion &region);
