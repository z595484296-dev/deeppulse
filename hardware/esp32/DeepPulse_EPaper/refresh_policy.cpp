#include "refresh_policy.h"

static const uint16_t PANEL_WIDTH = 800;
static const uint16_t PANEL_HEIGHT = 480;
static const size_t FRAME_BYTES = PANEL_WIDTH * PANEL_HEIGHT / 8;
static const uint16_t VOLATILE_X = 560;
static const uint16_t VOLATILE_Y_END = 48;

static uint8_t popcount8(uint8_t value) {
  uint8_t count = 0;
  while (value) {
    value &= static_cast<uint8_t>(value - 1);
    ++count;
  }
  return count;
}

DeepPulseChangeRegion deepPulseAnalyzeFrameChange(const uint8_t *previous,
                                                   const uint8_t *current,
                                                   size_t length) {
  DeepPulseChangeRegion result = {false, 0, 0, 0, 0, 0};
  if (previous == nullptr || current == nullptr || length != FRAME_BYTES) return result;

  uint16_t minX = PANEL_WIDTH;
  uint16_t minY = PANEL_HEIGHT;
  uint16_t maxX = 0;
  uint16_t maxY = 0;
  const uint16_t rowBytes = PANEL_WIDTH / 8;
  for (uint16_t y = 0; y < PANEL_HEIGHT; ++y) {
    for (uint16_t byteX = 0; byteX < rowBytes; ++byteX) {
      const uint16_t x0 = byteX * 8;
      if (y < VOLATILE_Y_END && x0 >= VOLATILE_X) continue;
      const uint8_t delta = previous[y * rowBytes + byteX] ^ current[y * rowBytes + byteX];
      if (!delta) continue;
      result.changed = true;
      result.changedPixels += popcount8(delta);
      for (uint8_t bit = 0; bit < 8; ++bit) {
        if (!(delta & (0x80 >> bit))) continue;
        const uint16_t x = x0 + bit;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (!result.changed) return result;

  // Some 7.5 V2 batches are more reliable when a partial window starts at x=0.
  // Keep that conservative boundary and add vertical padding around changed pixels.
  const uint16_t paddedY = minY > 4 ? minY - 4 : 0;
  const uint16_t paddedYEnd = maxY + 5 < PANEL_HEIGHT ? maxY + 5 : PANEL_HEIGHT;
  const uint16_t paddedXEnd = maxX + 8 < PANEL_WIDTH ? maxX + 8 : PANEL_WIDTH;
  const uint16_t alignedXEnd = static_cast<uint16_t>((paddedXEnd + 7) & ~7U);
  result.x = 0;
  result.y = paddedY;
  result.width = alignedXEnd > PANEL_WIDTH ? PANEL_WIDTH : alignedXEnd;
  result.height = paddedYEnd - paddedY;
  return result;
}

bool deepPulseCanUsePartial(const DeepPulseChangeRegion &region) {
  if (!region.changed || !region.width || !region.height) return false;
  const uint32_t totalPixels = static_cast<uint32_t>(PANEL_WIDTH) * PANEL_HEIGHT;
  const uint32_t regionPixels = static_cast<uint32_t>(region.width) * region.height;
  return regionPixels * 100UL <= totalPixels * 45UL &&
         region.changedPixels * 100UL <= totalPixels * 15UL;
}
