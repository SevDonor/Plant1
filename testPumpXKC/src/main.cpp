/*
 * ESP32-S3 Pump & XKC-Y25-V Liquid Level Sensor Test Firmware
 * ============================================================
 * So do chan mac dinh khop 100% voi file Fritzing D:\Plant\main.fzz:
 *
 * 1. Cam bien muc nuoc khong tiep xuc XKC-Y25-V (M1):
 *    - M1.pin1 (VCC)  -> 5V (Mini560)
 *    - M1.pin4 (MODE) -> 5V (Mini560)
 *    - M1.pin3 (GND)  -> GND
 *    - M1.pin2 (OUT)  -> R3 (220 Ohm) -> PC817 (U5) LED -> GND
 *    - U5 Collector   -> ESP32-S3 Pin 7 (GPIO 7) + R4 (10k pull-up 3V3)
 *    - U5 Emitter     -> GND
 *    => Vi PC817 U5 mac cuc thu ho keo xuong GND, tin hieu vao GPIO 7 bi DAO MUC:
 *       XKC OUT = HIGH (5V) -> U5 dan -> GPIO 7 = LOW (0V)
 *       XKC OUT = LOW  (0V) -> U5 tat -> GPIO 7 = HIGH (3.3V)
 *    => Mac dinh XKC_ACTIVE_LOW = true (co the doi nong tren UI).
 *
 * 2. Bom mini 5V (WATER PUMP1):
 *    - ESP32-S3 Pin 20 (GPIO 14) -> R1 (220 Ohm) -> PC817 (U4) LED -> GND
 *    - U4 Collector -> 5V, U4 Emitter -> MOSFET Q1 Gate + R2 (10k pull-down GND)
 *    - Q1 Drain -> WATER PUMP1 (-) + Diode 1N4001 (D1) -> WATER PUMP1 (+) 5V
 *    => Logic mac dinh: GPIO 14 = HIGH -> BOM BAT (Active-HIGH).
 *
 * Giao thuc Serial (115200 baud, ket thuc bang '\n'):
 *   PING
 *   STATUS
 *   PUMP:ON | PUMP:OFF | PUMP:TOGGLE
 *   PUMP:PULSE:<ms>
 *   PUMP:PWM:<0..100>
 *   PUMP:ACTIVE_HIGH:<0|1>
 *   MODE:<MANUAL|PROTECT|AUTO_WATER|AUTO_DRY>
 *   XKC:ACTIVE_LOW:<0|1>
 *   XKC:PULLUP:<0|1>
 *   XKC:DEBOUNCE:<ms>
 *   SAFETY:MAX_RUN:<sec>
 *   PINS:<xkc_pin>,<pump_pin>
 *   RESET_STATS
 *   STOP
 */

#include <Arduino.h>

#ifndef XKC_SENSOR_PIN
#define XKC_SENSOR_PIN 27
#endif

#ifndef PUMP_CTRL_PIN
#define PUMP_CTRL_PIN 26
#endif

static constexpr uint32_t TELEMETRY_INTERVAL_MS = 100;
static constexpr uint32_t PWM_FREQ_HZ = 500;
static constexpr uint8_t PWM_RES_BITS = 8;
static constexpr uint8_t PWM_CHANNEL = 0;

enum class ControlMode : uint8_t {
  MANUAL = 0,     // Chi bat/tat bom thu cong tu UI
  PROTECT = 1,    // Thu cong + Tu dong ngat khi XKC bao can nuoc
  AUTO_WATER = 2, // Tu dong BAT bom khi XKC bao CO nuoc, TAT khi can
  AUTO_DRY = 3    // Tu dong BAT bom khi XKC bao CAN nuoc (bom bu), TAT khi day
};

// Pin & hardware config
static int g_xkcPin = XKC_SENSOR_PIN;
static int g_pumpPin = PUMP_CTRL_PIN;
static bool g_xkcActiveLow = true;   // PC817 U5 dao muc: XKC OUT HIGH => GPIO 7 LOW
static bool g_xkcPullup = false;     // Mach da co R4 = 10k ngoai len 3V3
static bool g_pumpActiveHigh = true; // PC817 U4 + N-MOSFET Q1 => GPIO 14 HIGH = ON
static uint32_t g_debounceMs = 150;
static uint32_t g_maxRunSec = 30;
static uint8_t g_pumpPwmPct = 100;
static bool g_pwmAttached = false;

// Sensor state
static int g_xkcRawInstant = HIGH;
static int g_xkcRawStable = HIGH;
static int g_xkcRawCandidate = HIGH;
static uint32_t g_xkcCandidateSinceMs = 0;
static bool g_waterDetected = false;
static uint32_t g_xkcChanges = 0;
static uint32_t g_xkcStateSinceMs = 0;

// Pump state
static ControlMode g_mode = ControlMode::MANUAL;
static bool g_pumpOn = false;
static bool g_pulseActive = false;
static uint32_t g_pulseEndMs = 0;
static uint32_t g_pumpOnSinceMs = 0;
static uint32_t g_pumpTotalMs = 0;
static String g_lastReason = "BOOT_SAFE_OFF";

// Serial & timing
static String g_rxLine;
static uint32_t g_lastTelemetryMs = 0;
static bool g_dirtyTelemetry = true;

static const char* modeToStr(ControlMode m) {
  switch (m) {
    case ControlMode::MANUAL:     return "MANUAL";
    case ControlMode::PROTECT:    return "PROTECT";
    case ControlMode::AUTO_WATER: return "AUTO_WATER";
    case ControlMode::AUTO_DRY:   return "AUTO_DRY";
    default:                      return "MANUAL";
  }
}

static bool isValidGpio(int pin) {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
  // ESP32-S3 usable GPIOs (tranh chan flash 26..32)
  if (pin < 0 || pin > 48) return false;
  if (pin >= 26 && pin <= 32) return false;
  return true;
#else
  // ESP32 thuong 30 chan (tranh chan flash 6..11, 20, 24, 28..31)
  if (pin < 0 || pin > 39) return false;
  if (pin >= 6 && pin <= 11) return false;
  if (pin == 20 || pin == 24 || (pin >= 28 && pin <= 31)) return false;
  return true;
#endif
}

static void sendEvent(const char* level, const String& msg) {
  Serial.print("{\"type\":\"event\",\"ms\":");
  Serial.print(millis());
  Serial.print(",\"level\":\"");
  Serial.print(level);
  Serial.print("\",\"msg\":\"");
  for (size_t i = 0; i < msg.length(); ++i) {
    char c = msg[i];
    if (c == '"' || c == '\\') Serial.print('\\');
    if (c >= 0x20) Serial.print(c);
  }
  Serial.println("\"}");
}

static void detachPumpPwmIfNeeded() {
  if (!g_pwmAttached) return;
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
  ledcDetach(g_pumpPin);
#else
  ledcDetachPin(g_pumpPin);
#endif
  g_pwmAttached = false;
}

static void applyPumpHardwareOutput() {
  const bool wantActive = g_pumpOn && (g_pumpPwmPct > 0);
  if (!wantActive || g_pumpPwmPct >= 100) {
    detachPumpPwmIfNeeded();
    pinMode(g_pumpPin, OUTPUT);
    int level = wantActive ? (g_pumpActiveHigh ? HIGH : LOW)
                           : (g_pumpActiveHigh ? LOW : HIGH);
    digitalWrite(g_pumpPin, level);
    return;
  }

  // PWM 1..99% tren chan dieu khien PC817 + MOSFET
  uint8_t duty = (uint8_t)map((long)g_pumpPwmPct, 0, 100, 0, 255);
  if (!g_pumpActiveHigh) {
    duty = 255 - duty;
  }
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
  if (!g_pwmAttached) {
    ledcAttach(g_pumpPin, PWM_FREQ_HZ, PWM_RES_BITS);
    g_pwmAttached = true;
  }
  ledcWrite(g_pumpPin, duty);
#else
  if (!g_pwmAttached) {
    ledcSetup(PWM_CHANNEL, PWM_FREQ_HZ, PWM_RES_BITS);
    ledcAttachPin(g_pumpPin, PWM_CHANNEL);
    g_pwmAttached = true;
  }
  ledcWrite(PWM_CHANNEL, duty);
#endif
}

static void setPumpState(bool on, const char* reason, bool clearPulse = true) {
  const uint32_t now = millis();
  if (clearPulse && !on) {
    g_pulseActive = false;
    g_pulseEndMs = 0;
  }

  if (on != g_pumpOn) {
    if (on) {
      g_pumpOn = true;
      g_pumpOnSinceMs = now;
    } else {
      if (g_pumpOn) {
        g_pumpTotalMs += (now - g_pumpOnSinceMs);
      }
      g_pumpOn = false;
      g_pumpOnSinceMs = 0;
    }
    g_lastReason = reason;
    applyPumpHardwareOutput();
    g_dirtyTelemetry = true;
  } else {
    // Cap nhat lai phan cung trong truong hop PWM/polarity thay doi
    applyPumpHardwareOutput();
  }
}

static void configurePins(int newXkcPin, int newPumpPin) {
  // Tat bom truoc khi doi chan
  setPumpState(false, "RECONFIG_PINS", true);
  detachPumpPwmIfNeeded();

  g_xkcPin = newXkcPin;
  g_pumpPin = newPumpPin;

  pinMode(g_xkcPin, g_xkcPullup ? INPUT_PULLUP : INPUT);
  pinMode(g_pumpPin, OUTPUT);
  applyPumpHardwareOutput();

  // Doc gia tri ban dau
  const uint32_t now = millis();
  g_xkcRawInstant = digitalRead(g_xkcPin);
  g_xkcRawStable = g_xkcRawInstant;
  g_xkcRawCandidate = g_xkcRawInstant;
  g_xkcCandidateSinceMs = now;
  g_waterDetected = g_xkcActiveLow ? (g_xkcRawStable == LOW) : (g_xkcRawStable == HIGH);
  g_xkcStateSinceMs = now;
  g_dirtyTelemetry = true;
}

static void applyInterlockLogic(bool waterChanged) {
  switch (g_mode) {
    case ControlMode::MANUAL:
      break;

    case ControlMode::PROTECT:
      if (!g_waterDetected && g_pumpOn) {
        setPumpState(false, "PROTECT_DRY_CUTOFF", true);
        sendEvent("WARN", "XKC bao CAN NUOC -> Tu dong ngat bom bao ve!");
      }
      break;

    case ControlMode::AUTO_WATER:
      if (g_waterDetected && !g_pumpOn) {
        setPumpState(true, "AUTO_WATER_ON", true);
        if (waterChanged) sendEvent("INFO", "XKC phat hien CO NUOC -> Tu dong BAT bom");
      } else if (!g_waterDetected && g_pumpOn) {
        setPumpState(false, "AUTO_WATER_OFF", true);
        if (waterChanged) sendEvent("INFO", "XKC bao CAN NUOC -> Tu dong TAT bom");
      }
      break;

    case ControlMode::AUTO_DRY:
      if (!g_waterDetected && !g_pumpOn) {
        setPumpState(true, "AUTO_DRY_REFILL_ON", true);
        if (waterChanged) sendEvent("INFO", "XKC bao CAN NUOC -> Tu dong BAT bom bu");
      } else if (g_waterDetected && g_pumpOn) {
        setPumpState(false, "AUTO_DRY_FULL_OFF", true);
        if (waterChanged) sendEvent("INFO", "XKC bao DAY NUOC -> Tu dong TAT bom bu");
      }
      break;
  }
}

static void updateXkcSensor() {
  const uint32_t now = millis();
  const int raw = digitalRead(g_xkcPin);
  g_xkcRawInstant = raw;

  if (raw != g_xkcRawCandidate) {
    g_xkcRawCandidate = raw;
    g_xkcCandidateSinceMs = now;
  }

  if (g_xkcRawCandidate != g_xkcRawStable) {
    if (g_debounceMs == 0 || (now - g_xkcCandidateSinceMs) >= g_debounceMs) {
      g_xkcRawStable = g_xkcRawCandidate;
      const bool newWater = g_xkcActiveLow ? (g_xkcRawStable == LOW) : (g_xkcRawStable == HIGH);
      if (newWater != g_waterDetected) {
        g_waterDetected = newWater;
        g_xkcChanges++;
        g_xkcStateSinceMs = now;
        g_dirtyTelemetry = true;
        sendEvent("INFO", g_waterDetected ? "XKC-Y25-V: CO NUOC" : "XKC-Y25-V: CAN NUOC");
        applyInterlockLogic(true);
      }
    }
  }
}

static void updatePumpTimers() {
  const uint32_t now = millis();

  // 1. Kiem tra hen gio bom (Pulse)
  if (g_pulseActive && g_pumpOn) {
    if ((int32_t)(now - g_pulseEndMs) >= 0) {
      setPumpState(false, "PULSE_DONE", true);
      sendEvent("INFO", "Hoan thanh chu ky bom hen gio.");
    }
  }

  // 2. Kiem tra bao ve chay qua thoi gian toi da (Max Run Timeout)
  if (g_pumpOn && g_maxRunSec > 0) {
    const uint32_t runMs = now - g_pumpOnSinceMs;
    if (runMs >= (g_maxRunSec * 1000UL)) {
      if (g_mode == ControlMode::AUTO_WATER || g_mode == ControlMode::AUTO_DRY) {
        g_mode = ControlMode::MANUAL;
      }
      setPumpState(false, "MAX_RUN_TIMEOUT", true);
      sendEvent("WARN", "Bom chay vuot nguong an toan (" + String(g_maxRunSec) + "s) -> Tu dong TAT & chuyen MANUAL!");
    }
  }
}

static void sendTelemetry() {
  const uint32_t now = millis();
  const uint32_t runMs = g_pumpOn ? (now - g_pumpOnSinceMs) : 0;
  const uint32_t totalMs = g_pumpTotalMs + runMs;
  const uint32_t pulseRemMs = (g_pulseActive && g_pumpOn && (int32_t)(g_pulseEndMs - now) > 0)
                                  ? (g_pulseEndMs - now)
                                  : 0;
  // PC817 U5 dao muc: GPIO LOW => XKC OUT HIGH (5V)
  const int xkcOutEst = (g_xkcRawStable == LOW) ? 1 : 0;
  const int pumpRaw = g_pumpOn ? (g_pumpActiveHigh ? 1 : 0) : (g_pumpActiveHigh ? 0 : 1);

  Serial.print("{\"type\":\"state\",\"ms\":");
  Serial.print(now);
  Serial.print(",\"xkc_pin\":");
  Serial.print(g_xkcPin);
  Serial.print(",\"pump_pin\":");
  Serial.print(g_pumpPin);
  Serial.print(",\"xkc_raw\":");
  Serial.print(g_xkcRawStable == HIGH ? 1 : 0);
  Serial.print(",\"xkc_instant\":");
  Serial.print(g_xkcRawInstant == HIGH ? 1 : 0);
  Serial.print(",\"xkc_out_est\":");
  Serial.print(xkcOutEst);
  Serial.print(",\"water\":");
  Serial.print(g_waterDetected ? "true" : "false");
  Serial.print(",\"xkc_active_low\":");
  Serial.print(g_xkcActiveLow ? "true" : "false");
  Serial.print(",\"xkc_pullup\":");
  Serial.print(g_xkcPullup ? "true" : "false");
  Serial.print(",\"debounce_ms\":");
  Serial.print(g_debounceMs);
  Serial.print(",\"xkc_changes\":");
  Serial.print(g_xkcChanges);
  Serial.print(",\"xkc_hold_ms\":");
  Serial.print(now - g_xkcStateSinceMs);
  Serial.print(",\"pump_on\":");
  Serial.print(g_pumpOn ? "true" : "false");
  Serial.print(",\"pump_raw\":");
  Serial.print(pumpRaw);
  Serial.print(",\"pump_active_high\":");
  Serial.print(g_pumpActiveHigh ? "true" : "false");
  Serial.print(",\"pump_pwm\":");
  Serial.print(g_pumpPwmPct);
  Serial.print(",\"mode\":\"");
  Serial.print(modeToStr(g_mode));
  Serial.print("\",\"pulse_rem_ms\":");
  Serial.print(pulseRemMs);
  Serial.print(",\"pump_run_ms\":");
  Serial.print(runMs);
  Serial.print(",\"pump_total_ms\":");
  Serial.print(totalMs);
  Serial.print(",\"max_run_sec\":");
  Serial.print(g_maxRunSec);
  Serial.print(",\"last_reason\":\"");
  Serial.print(g_lastReason);
  Serial.println("\"}");

  g_lastTelemetryMs = now;
  g_dirtyTelemetry = false;
}

static void handleCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  String upper = cmd;
  upper.toUpperCase();

  if (upper == "PING") {
    Serial.print("{\"type\":\"pong\",\"ms\":");
    Serial.print(millis());
    Serial.println("}");
    return;
  }

  if (upper == "STATUS") {
    sendTelemetry();
    return;
  }

  if (upper == "STOP") {
    g_mode = ControlMode::MANUAL;
    setPumpState(false, "EMERGENCY_STOP", true);
    sendEvent("WARN", "DUNG KHAN CAP: Da tat bom va chuyen ve che do MANUAL.");
    return;
  }

  if (upper == "PUMP:ON") {
    if (g_mode == ControlMode::PROTECT && !g_waterDetected) {
      sendEvent("WARN", "Bi chan boi che do PROTECT: XKC dang bao CAN NUOC!");
      g_dirtyTelemetry = true;
      return;
    }
    if (g_mode == ControlMode::AUTO_WATER || g_mode == ControlMode::AUTO_DRY) {
      g_mode = ControlMode::MANUAL;
      sendEvent("INFO", "Chuyen sang MANUAL de bat bom thu cong.");
    }
    g_pulseActive = false;
    setPumpState(true, "UI_ON", false);
    sendEvent("INFO", "Da BAT bom (GPIO " + String(g_pumpPin) + ").");
    return;
  }

  if (upper == "PUMP:OFF") {
    if (g_mode == ControlMode::AUTO_WATER || g_mode == ControlMode::AUTO_DRY) {
      g_mode = ControlMode::MANUAL;
      sendEvent("INFO", "Chuyen sang MANUAL de tat bom thu cong.");
    }
    setPumpState(false, "UI_OFF", true);
    sendEvent("INFO", "Da TAT bom.");
    return;
  }

  if (upper == "PUMP:TOGGLE") {
    handleCommand(g_pumpOn ? "PUMP:OFF" : "PUMP:ON");
    return;
  }

  if (upper.startsWith("PUMP:PULSE:")) {
    uint32_t ms = (uint32_t)upper.substring(11).toInt();
    if (ms < 100) ms = 100;
    if (ms > 120000) ms = 120000;
    if (g_mode == ControlMode::PROTECT && !g_waterDetected) {
      sendEvent("WARN", "Bi chan boi che do PROTECT: XKC dang bao CAN NUOC!");
      g_dirtyTelemetry = true;
      return;
    }
    if (g_mode == ControlMode::AUTO_WATER || g_mode == ControlMode::AUTO_DRY) {
      g_mode = ControlMode::MANUAL;
    }
    g_pulseActive = true;
    g_pulseEndMs = millis() + ms;
    setPumpState(true, "UI_PULSE", false);
    sendEvent("INFO", "Bat bom hen gio " + String(ms) + " ms.");
    return;
  }

  if (upper.startsWith("PUMP:PWM:")) {
    int pct = upper.substring(9).toInt();
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    g_pumpPwmPct = (uint8_t)pct;
    applyPumpHardwareOutput();
    g_dirtyTelemetry = true;
    sendEvent("INFO", "Dat cong suat PWM bom = " + String(g_pumpPwmPct) + "%.");
    return;
  }

  if (upper.startsWith("PUMP:ACTIVE_HIGH:")) {
    int v = upper.substring(17).toInt();
    g_pumpActiveHigh = (v != 0);
    applyPumpHardwareOutput();
    g_dirtyTelemetry = true;
    sendEvent("INFO", g_pumpActiveHigh ? "Logic bom: Active-HIGH (GPIO HIGH = BAT)."
                                       : "Logic bom: Active-LOW (GPIO LOW = BAT).");
    return;
  }

  if (upper.startsWith("MODE:")) {
    String m = upper.substring(5);
    if (m == "MANUAL") {
      g_mode = ControlMode::MANUAL;
      sendEvent("INFO", "Che do: MANUAL (Dieu khien thu cong tu UI).");
    } else if (m == "PROTECT") {
      g_mode = ControlMode::PROTECT;
      sendEvent("INFO", "Che do: PROTECT (Tu ngat bom neu XKC bao can).");
      applyInterlockLogic(false);
    } else if (m == "AUTO_WATER") {
      g_mode = ControlMode::AUTO_WATER;
      sendEvent("INFO", "Che do: AUTO_WATER (XKC co nuoc -> BAT bom, can -> TAT).");
      applyInterlockLogic(false);
    } else if (m == "AUTO_DRY") {
      g_mode = ControlMode::AUTO_DRY;
      sendEvent("INFO", "Che do: AUTO_DRY (XKC can nuoc -> BAT bom bu, day -> TAT).");
      applyInterlockLogic(false);
    } else {
      sendEvent("ERROR", "Che do khong hop le: " + m);
      return;
    }
    g_dirtyTelemetry = true;
    return;
  }

  if (upper.startsWith("XKC:ACTIVE_LOW:")) {
    int v = upper.substring(15).toInt();
    g_xkcActiveLow = (v != 0);
    g_waterDetected = g_xkcActiveLow ? (g_xkcRawStable == LOW) : (g_xkcRawStable == HIGH);
    g_xkcStateSinceMs = millis();
    g_dirtyTelemetry = true;
    sendEvent("INFO", g_xkcActiveLow ? "Logic XKC: Active-LOW (GPIO LOW = CO NUOC, phu hop PC817 U5)."
                                     : "Logic XKC: Active-HIGH (GPIO HIGH = CO NUOC).");
    applyInterlockLogic(true);
    return;
  }

  if (upper.startsWith("XKC:PULLUP:")) {
    int v = upper.substring(11).toInt();
    g_xkcPullup = (v != 0);
    pinMode(g_xkcPin, g_xkcPullup ? INPUT_PULLUP : INPUT);
    g_dirtyTelemetry = true;
    sendEvent("INFO", g_xkcPullup ? "XKC pinMode: INPUT_PULLUP (tro keo noi)."
                                  : "XKC pinMode: INPUT (dung tro keo ngoai R4 10k).");
    return;
  }

  if (upper.startsWith("XKC:DEBOUNCE:")) {
    int ms = upper.substring(13).toInt();
    if (ms < 0) ms = 0;
    if (ms > 5000) ms = 5000;
    g_debounceMs = (uint32_t)ms;
    g_dirtyTelemetry = true;
    sendEvent("INFO", "Dat loc nhieu XKC debounce = " + String(g_debounceMs) + " ms.");
    return;
  }

  if (upper.startsWith("SAFETY:MAX_RUN:")) {
    int sec = upper.substring(15).toInt();
    if (sec < 0) sec = 0;
    if (sec > 3600) sec = 3600;
    g_maxRunSec = (uint32_t)sec;
    g_dirtyTelemetry = true;
    sendEvent("INFO", g_maxRunSec == 0
                          ? "Tat gioi han thoi gian chay bom toi da."
                          : "Gioi han chay bom toi da = " + String(g_maxRunSec) + " giay.");
    return;
  }

  if (upper.startsWith("PINS:")) {
    int comma = upper.indexOf(',', 5);
    if (comma < 0) {
      sendEvent("ERROR", "Cu phap dung: PINS:<xkc_gpio>,<pump_gpio>");
      return;
    }
    int xPin = upper.substring(5, comma).toInt();
    int pPin = upper.substring(comma + 1).toInt();
    if (!isValidGpio(xPin) || !isValidGpio(pPin) || xPin == pPin) {
      sendEvent("ERROR", "Chan GPIO khong hop le hoac trung nhau: XKC=" + String(xPin) + ", PUMP=" + String(pPin));
      return;
    }
    configurePins(xPin, pPin);
    sendEvent("INFO", "Da doi chan: XKC=GPIO " + String(g_xkcPin) + ", PUMP=GPIO " + String(g_pumpPin));
    return;
  }

  if (upper == "RESET_STATS") {
    g_xkcChanges = 0;
    g_pumpTotalMs = 0;
    g_xkcStateSinceMs = millis();
    g_dirtyTelemetry = true;
    sendEvent("INFO", "Da dat lai bo dem thong ke.");
    return;
  }

  sendEvent("ERROR", "Lenh khong ho tro: " + cmd);
}

static void pollSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      handleCommand(g_rxLine);
      g_rxLine = "";
    } else if (g_rxLine.length() < 160) {
      g_rxLine += c;
    }
  }
}

void setup() {
  // Dam bao bom TAT ngay khi khoi dong
  pinMode(g_pumpPin, OUTPUT);
  digitalWrite(g_pumpPin, g_pumpActiveHigh ? LOW : HIGH);

  Serial.begin(115200);
  delay(250);

  configurePins(g_xkcPin, g_pumpPin);
  sendEvent("INFO", "ESP32-S3 Pump & XKC-Y25-V Test Firmware san sang (XKC=GPIO " +
                        String(g_xkcPin) + ", PUMP=GPIO " + String(g_pumpPin) + ").");
  sendTelemetry();
}

void loop() {
  pollSerial();
  updateXkcSensor();
  updatePumpTimers();

  const uint32_t now = millis();
  if (g_dirtyTelemetry || (now - g_lastTelemetryMs) >= TELEMETRY_INTERVAL_MS) {
    sendTelemetry();
  }
  delay(5);
}
