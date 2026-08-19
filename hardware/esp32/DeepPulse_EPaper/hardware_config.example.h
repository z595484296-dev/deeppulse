#pragma once

// Keep this at 0 before the exact rear label of the physical panel is verified.
// Dry-run still verifies Wi-Fi, authorization, byte count and SHA-256.
#define DEEPPULSE_WAVESHARE_7IN5_V2 0

// Keep both at 0 for unknown/old panels. For the verified post-2023 7.5 V2 panel,
// copy hardware_config.v2-smart.example.h to hardware_config.h instead.
#define DEEPPULSE_ALLOW_PARTIAL_REFRESH 0
#define DEEPPULSE_ALLOW_FAST_REFRESH 0
