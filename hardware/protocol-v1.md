# DeepPulse E-Paper Device Protocol v1

## Transport and authorization

The gateway is disabled by default and, when enabled, listens on LAN port `8988`. Every request must contain one of:

```http
X-DeepPulse-Device-Token: <pairing token>
Authorization: Bearer <pairing token>
```

Only the three read-only routes below exist on the device listener. The normal `/api/*` application routes are intentionally unavailable.

| Route | Response |
|---|---|
| `GET /device/v1/health` | model, protocol dimensions and server time |
| `GET /device/v1/state` | normalized JSON snapshot for diagnostics |
| `GET /device/v1/frame.bin` | display-ready 1-bit frame |

## Binary frame

- Width: 800 pixels
- Height: 480 pixels
- Size: exactly 48,000 bytes
- Scan: top-to-bottom rows, left-to-right pixels
- Row size: 100 bytes
- Bit order: most-significant bit first
- Bit value: `1 = white`, `0 = black`

The binary response includes:

```text
Content-Type: application/vnd.deeppulse.epaper-1bpp
Content-Length: 48000
X-DeepPulse-Width: 800
X-DeepPulse-Height: 480
X-DeepPulse-Bpp: 1
X-DeepPulse-Frame-SHA256: <lowercase hex>
X-DeepPulse-Content-SHA256: <lowercase hex, volatile clock area excluded>
X-DeepPulse-Sequence: <unix seconds>
X-DeepPulse-Mode: focus|overview|emotion|watch|hotspot|alert
X-DeepPulse-Refresh-Policy: stable|smart|fast
X-DeepPulse-Poll-Seconds: 15..300
X-DeepPulse-Display-Seconds: 60..1800
X-DeepPulse-Partial-Before-Full: 2..20
```

Consumers must reject a response if the status, byte count, dimensions or frame SHA-256 do not match. `Content-SHA256` is a refresh-decision hint, not a replacement for frame integrity: it masks only the volatile header clock/session area so a clock-only change can be skipped. A transition into `alert` mode or a display-mode change may bypass the normal display interval once; repeated alert frames should follow the configured interval.

## Security boundary

The v1 transport is intended only for a trusted private LAN. It is HTTP because the small ESP32 endpoint cannot safely validate a self-signed local TLS identity without an out-of-band certificate workflow. Do not expose port 8988 through NAT, a public reverse proxy, VPN sharing, or a guest Wi-Fi. Rotate the pairing token after loss or suspected disclosure.

The protocol is presentation-only. It has no mutation, account, position, order, cancellation or credential endpoint.
