#pragma once

// Keep this at 0 before the exact rear label of the physical panel is verified.
// Dry-run still verifies Wi-Fi, authorization, byte count and SHA-256.
#define DEEPPULSE_WAVESHARE_7IN5_V2 0

// The current physical adapter deliberately uses full refresh only. A panel-specific
// partial/fast refresh path will be enabled after the real panel and LUT are verified.
#define DEEPPULSE_ALLOW_PARTIAL_REFRESH 0

