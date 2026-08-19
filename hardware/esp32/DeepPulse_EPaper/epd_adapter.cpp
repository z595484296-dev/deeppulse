#include "epd_adapter.h"

#if __has_include("hardware_config.h")
#include "hardware_config.h"
#else
#define DEEPPULSE_WAVESHARE_7IN5_V2 0
#define DEEPPULSE_ALLOW_PARTIAL_REFRESH 0
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
                                         bool requestPartial) {
  if (frame == nullptr || length != FRAME_BYTES) {
    return DEEPPULSE_REFRESH_FAILED;
  }
#if DEEPPULSE_WAVESHARE_7IN5_V2
  // Re-initialize after sleep. Full refresh is intentionally enforced until the
  // exact panel generation and LUT have been validated on the physical unit.
  if (EPD_7IN5_V2_Init() != 0) {
    return DEEPPULSE_REFRESH_FAILED;
  }
  panelAwake = true;
  (void)requestPartial;
  EPD_7IN5_V2_Display(frame);
  EPD_7IN5_V2_Sleep();
  panelAwake = false;
  return DEEPPULSE_REFRESH_FULL;
#else
  (void)requestPartial;
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
