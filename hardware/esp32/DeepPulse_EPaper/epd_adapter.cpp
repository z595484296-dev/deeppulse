#include "epd_adapter.h"

#include <stdlib.h>
#include <string.h>

#if __has_include("hardware_config.h")
#include "hardware_config.h"
#else
#define DEEPPULSE_WAVESHARE_7IN5_V2 0
#define DEEPPULSE_ALLOW_PARTIAL_REFRESH 0
#define DEEPPULSE_ALLOW_FAST_REFRESH 0
#endif

#ifndef DEEPPULSE_ALLOW_PARTIAL_REFRESH
#define DEEPPULSE_ALLOW_PARTIAL_REFRESH 0
#endif

#ifndef DEEPPULSE_ALLOW_FAST_REFRESH
#define DEEPPULSE_ALLOW_FAST_REFRESH 0
#endif

#if DEEPPULSE_WAVESHARE_7IN5_V2
#include <DEV_Config.h>
#include <EPD.h>

static bool panelAwake = false;
#endif

static const size_t FRAME_BYTES = 800UL * 480UL / 8UL;

bool deepPulseEpdBegin() {
#if DEEPPULSE_WAVESHARE_7IN5_V2
  // The ESP32 driver-board library owns the fixed GPIO mapping and bit-banged
  // SPI setup. Panel power-up is deferred until an authenticated frame has
  // passed the length and SHA-256 checks.
  return DEV_Module_Init() == 0;
#else
  Serial.println("EPD adapter: dry-run (physical panel disabled)");
  return true;
#endif
}

DeepPulseRefreshKind deepPulseEpdDisplay(uint8_t *frame, size_t length,
                                         DeepPulseRefreshRequest request,
                                         const DeepPulseDisplayRegion &region) {
  if (frame == nullptr || length != FRAME_BYTES) {
    return DEEPPULSE_REFRESH_FAILED;
  }
#if DEEPPULSE_WAVESHARE_7IN5_V2
  if (request == DEEPPULSE_REQUEST_PARTIAL && DEEPPULSE_ALLOW_PARTIAL_REFRESH &&
      region.width && region.height && region.x + region.width <= 800 &&
      region.y + region.height <= 480 && region.x % 8 == 0 && region.width % 8 == 0) {
    const size_t rowBytes = region.width / 8;
    const size_t partialBytes = rowBytes * region.height;
    uint8_t *partial = static_cast<uint8_t *>(malloc(partialBytes));
    if (partial != nullptr) {
      for (uint16_t row = 0; row < region.height; ++row) {
        memcpy(partial + row * rowBytes,
               frame + (region.y + row) * (800 / 8) + region.x / 8,
               rowBytes);
      }
      if (EPD_7IN5_V2_Init_Part() == 0) {
        panelAwake = true;
        EPD_7IN5_V2_Display_Part(partial, region.x, region.y,
                                 region.x + region.width, region.y + region.height);
        free(partial);
        EPD_7IN5_V2_Sleep();
        panelAwake = false;
        return DEEPPULSE_REFRESH_PARTIAL;
      }
      free(partial);
      EPD_7IN5_V2_Sleep();
      panelAwake = false;
      Serial.println("partial refresh unavailable; falling back to full refresh");
    }
  }

  DeepPulseRefreshKind kind = DEEPPULSE_REFRESH_FULL;
  if (request == DEEPPULSE_REQUEST_FAST && DEEPPULSE_ALLOW_FAST_REFRESH) {
    if (EPD_7IN5_V2_Init_Fast() != 0) return DEEPPULSE_REFRESH_FAILED;
    kind = DEEPPULSE_REFRESH_FAST;
  } else if (EPD_7IN5_V2_Init() != 0) {
    return DEEPPULSE_REFRESH_FAILED;
  }
  panelAwake = true;
  EPD_7IN5_V2_Display(frame);
  // The Waveshare ESP32 routine inverts the caller's buffer in place.
  // Restore DeepPulse's canonical 1=white representation for diff tracking.
  for (size_t index = 0; index < length; ++index) frame[index] = ~frame[index];
  EPD_7IN5_V2_Sleep();
  panelAwake = false;
  return kind;
#else
  (void)request;
  (void)region;
  Serial.printf("EPD dry-run: accepted %u bytes\n", static_cast<unsigned>(length));
  return DEEPPULSE_REFRESH_DRY_RUN;
#endif
}

void deepPulseEpdSleep() {
#if DEEPPULSE_WAVESHARE_7IN5_V2
  if (panelAwake) {
    EPD_7IN5_V2_Sleep();
    panelAwake = false;
  }
#endif
}

bool deepPulseEpdIsPhysical() {
#if DEEPPULSE_WAVESHARE_7IN5_V2
  return true;
#else
  return false;
#endif
}
