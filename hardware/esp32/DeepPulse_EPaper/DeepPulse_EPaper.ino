#include <Arduino.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <mbedtls/sha256.h>

#include "epd_adapter.h"

static const char *SETUP_SSID = "DeepPulse-Setup";
static const char *SETUP_PASSWORD = "deeppulse-setup";
static const char *USER_AGENT = "DeepPulse-EPaper/1.0 ESP32";
static const size_t FRAME_BYTES = 800UL * 480UL / 8UL;

Preferences preferences;
WebServer portal(80);
String wifiSsid;
String wifiPassword;
String gatewayBase;
String deviceToken;
String lastFrameHash;
String lastMode = "";
uint8_t *frameBuffer = nullptr;
unsigned long lastPollMs = 0;
unsigned long lastDisplayMs = 0;
uint32_t pollSeconds = 30;
uint32_t displaySeconds = 180;
uint32_t partialBeforeFull = 6;
uint32_t partialCount = 0;

String htmlEscape(const String &value) {
  String out;
  out.reserve(value.length() + 12);
  for (size_t i = 0; i < value.length(); ++i) {
    const char c = value[i];
    if (c == '&') out += "&amp;";
    else if (c == '<') out += "&lt;";
    else if (c == '>') out += "&gt;";
    else if (c == '\"') out += "&quot;";
    else out += c;
  }
  return out;
}

String setupPage(const String &message = "") {
  return String("<!doctype html><meta name=viewport content='width=device-width'>") +
    "<title>DeepPulse E-Paper Setup</title><style>body{font:16px system-ui;max-width:560px;" 
    "margin:30px auto;padding:0 18px}label{display:block;margin:14px 0 5px}input{box-sizing:" 
    "border-box;width:100%;padding:10px}button{margin-top:18px;padding:11px 18px}</style>" +
    "<h1>DeepPulse E-Paper</h1><p>" + htmlEscape(message) + "</p><form method=post action=/save>" +
    "<label>Wi-Fi SSID</label><input name=ssid required value='" + htmlEscape(wifiSsid) + "'>" +
    "<label>Wi-Fi password</label><input name=password type=password value='" +
    htmlEscape(wifiPassword) + "'><label>DeepPulse gateway</label>" +
    "<input name=gateway required placeholder='http://192.168.1.20:8988' value='" +
    htmlEscape(gatewayBase) + "'><label>Device token</label><input name=token required value='" +
    htmlEscape(deviceToken) + "'><button>Save and restart</button></form>";
}

void saveProvisioning() {
  const String ssid = portal.arg("ssid");
  const String password = portal.arg("password");
  String gateway = portal.arg("gateway");
  const String token = portal.arg("token");
  gateway.trim();
  while (gateway.endsWith("/")) gateway.remove(gateway.length() - 1);
  if (ssid.isEmpty() || token.length() < 24 || !gateway.startsWith("http://")) {
    portal.send(400, "text/html; charset=utf-8",
                setupPage("SSID, http:// gateway, and a valid token are required."));
    return;
  }
  preferences.putString("ssid", ssid);
  preferences.putString("password", password);
  preferences.putString("gateway", gateway);
  preferences.putString("token", token);
  portal.send(200, "text/html; charset=utf-8",
              "<h1>Saved</h1><p>The device is restarting.</p>");
  delay(900);
  ESP.restart();
}

[[noreturn]] void runProvisioningPortal(const String &reason) {
  Serial.println("Provisioning mode: " + reason);
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(SETUP_SSID, SETUP_PASSWORD);
  Serial.print("Open http://");
  Serial.println(WiFi.softAPIP());
  portal.on("/", HTTP_GET, [reason]() {
    portal.send(200, "text/html; charset=utf-8", setupPage(reason));
  });
  portal.on("/save", HTTP_POST, saveProvisioning);
  portal.onNotFound([]() { portal.sendHeader("Location", "/", true); portal.send(302); });
  portal.begin();
  for (;;) {
    portal.handleClient();
    delay(5);
  }
}

void loadConfiguration() {
  preferences.begin("deeppulse", false);
  wifiSsid = preferences.getString("ssid", "");
  wifiPassword = preferences.getString("password", "");
  gatewayBase = preferences.getString("gateway", "");
  deviceToken = preferences.getString("token", "");
}

bool connectWiFi(uint32_t timeoutMs = 45000) {
  if (wifiSsid.isEmpty()) return false;
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);
  WiFi.begin(wifiSsid.c_str(), wifiPassword.c_str());
  const unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < timeoutMs) {
    delay(300);
    Serial.print('.');
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) return false;
  Serial.print("Wi-Fi connected: ");
  Serial.println(WiFi.localIP());
  return true;
}

String sha256Hex(const uint8_t *data, size_t length) {
  unsigned char digest[32];
  mbedtls_sha256_context context;
  mbedtls_sha256_init(&context);
  mbedtls_sha256_starts_ret(&context, 0);
  mbedtls_sha256_update_ret(&context, data, length);
  mbedtls_sha256_finish_ret(&context, digest);
  mbedtls_sha256_free(&context);
  static const char digits[] = "0123456789abcdef";
  String result;
  result.reserve(64);
  for (size_t i = 0; i < sizeof(digest); ++i) {
    result += digits[digest[i] >> 4];
    result += digits[digest[i] & 0x0f];
  }
  return result;
}

uint32_t headerUInt(HTTPClient &http, const char *name, uint32_t fallback,
                    uint32_t minimum, uint32_t maximum) {
  const long parsed = http.header(name).toInt();
  if (parsed < static_cast<long>(minimum) || parsed > static_cast<long>(maximum)) {
    return fallback;
  }
  return static_cast<uint32_t>(parsed);
}

bool fetchAndMaybeDisplay() {
  if (WiFi.status() != WL_CONNECTED && !connectWiFi(12000)) {
    Serial.println("Wi-Fi reconnect failed");
    return false;
  }
  WiFiClient client;
  HTTPClient http;
  const String url = gatewayBase + "/device/v1/frame.bin";
  if (!http.begin(client, url)) {
    Serial.println("HTTP begin failed");
    return false;
  }
  static const char *headerKeys[] = {
    "X-DeepPulse-Width", "X-DeepPulse-Height", "X-DeepPulse-Frame-SHA256",
    "X-DeepPulse-Mode", "X-DeepPulse-Poll-Seconds", "X-DeepPulse-Display-Seconds",
    "X-DeepPulse-Partial-Before-Full"
  };
  http.collectHeaders(headerKeys, sizeof(headerKeys) / sizeof(headerKeys[0]));
  http.setUserAgent(USER_AGENT);
  http.addHeader("X-DeepPulse-Device-Token", deviceToken);
  http.setConnectTimeout(8000);
  http.setTimeout(20000);
  const int status = http.GET();
  if (status != HTTP_CODE_OK) {
    Serial.printf("gateway returned HTTP %d\n", status);
    http.end();
    return false;
  }
  if (http.header("X-DeepPulse-Width") != "800" ||
      http.header("X-DeepPulse-Height") != "480" ||
      http.getSize() != static_cast<int>(FRAME_BYTES)) {
    Serial.println("frame metadata rejected");
    http.end();
    return false;
  }
  WiFiClient *stream = http.getStreamPtr();
  const size_t received = stream->readBytes(frameBuffer, FRAME_BYTES);
  const String expectedHash = http.header("X-DeepPulse-Frame-SHA256");
  const String actualHash = sha256Hex(frameBuffer, received);
  const String mode = http.header("X-DeepPulse-Mode");
  pollSeconds = headerUInt(http, "X-DeepPulse-Poll-Seconds", pollSeconds, 15, 300);
  displaySeconds = headerUInt(http, "X-DeepPulse-Display-Seconds", displaySeconds, 60, 1800);
  partialBeforeFull = headerUInt(http, "X-DeepPulse-Partial-Before-Full",
                                 partialBeforeFull, 2, 20);
  http.end();
  if (received != FRAME_BYTES || expectedHash.length() != 64 ||
      !actualHash.equalsIgnoreCase(expectedHash)) {
    Serial.println("frame SHA-256 or length rejected");
    return false;
  }
  Serial.printf("frame verified: %u bytes\n", static_cast<unsigned>(received));
  const unsigned long now = millis();
  const bool firstFrame = lastDisplayMs == 0;
  const bool enteredAlert = mode == "alert" && lastMode != "alert";
  const bool intervalDue = now - lastDisplayMs >= displaySeconds * 1000UL;
  if (!firstFrame && !enteredAlert && !intervalDue) return true;
  const bool requestPartial = partialCount > 0 && partialCount < partialBeforeFull;
  const DeepPulseRefreshKind refreshed = deepPulseEpdDisplay(frameBuffer, FRAME_BYTES,
                                                              requestPartial);
  if (refreshed == DEEPPULSE_REFRESH_FAILED) {
    Serial.println("display refresh failed");
    return false;
  }
  if (refreshed == DEEPPULSE_REFRESH_PARTIAL) ++partialCount;
  else partialCount = 0;
  lastFrameHash = actualHash;
  lastMode = mode;
  lastDisplayMs = now;
  Serial.printf("display accepted; mode=%s refresh=%d\n", mode.c_str(), refreshed);
  return true;
}

void checkSerialReset() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.equalsIgnoreCase("RESET")) {
    preferences.clear();
    Serial.println("configuration cleared; restarting");
    delay(400);
    ESP.restart();
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\nDeepPulse E-Paper 1.0");
  frameBuffer = static_cast<uint8_t *>(malloc(FRAME_BYTES));
  if (frameBuffer == nullptr) {
    Serial.println("fatal: cannot allocate 48000-byte frame");
    for (;;) delay(1000);
  }
  loadConfiguration();
  if (wifiSsid.isEmpty() || gatewayBase.isEmpty() || deviceToken.length() < 24) {
    runProvisioningPortal("First-time setup");
  }
  if (!connectWiFi()) runProvisioningPortal("Saved Wi-Fi could not connect");
  if (!deepPulseEpdBegin()) {
    Serial.println("fatal: e-paper adapter initialization failed");
    for (;;) delay(1000);
  }
  fetchAndMaybeDisplay();
  lastPollMs = millis();
}

void loop() {
  checkSerialReset();
  const unsigned long now = millis();
  if (now - lastPollMs >= pollSeconds * 1000UL) {
    lastPollMs = now;
    fetchAndMaybeDisplay();
  }
  delay(20);
}

