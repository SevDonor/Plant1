/**
 * ============================================================================
 * FIRMWARE PHAT XUNG TEST LOA I2S MAX98357A & DO CHAN GPIO (ESP32-S3)
 * ============================================================================
 * So do chan mac dinh khop 100% D:\Plant\main.fzz:
 *   - MAX98357A LRC (LRCLK)  -> ESP32-S3 Pin 8  (GPIO 15)
 *   - MAX98357A BCLK         -> ESP32-S3 Pin 9  (GPIO 16)
 *   - MAX98357A DIN          -> ESP32-S3 Pin 10 (GPIO 17)
 *   - MAX98357A SD_MODE (SD) -> ESP32-S3 Pin 11 (GPIO 18)
 *   - MAX98357A GAIN         -> GND (Do loi 12 dB)
 *   - MAX98357A VIN / GND    -> 5V / GND
 *
 * Hai che do hoat dong chuyen doi nong qua Serial (115200 baud):
 *   1. MODE:I2S   - Bo phat xung & dang song I2S thoi gian thuc:
 *        + Dang song : SINE, SQUARE, PULSE (chinh Duty 5-95%), TRIANGLE,
 *                      SAWTOOTH, CLICK (xung kim), SWEEP (quet tan so), NOISE
 *        + Che do    : CW (Lien tuc), BEEP (Bat/Tat theo chu ky ms), ONCE (1 lan)
 *        + Tuy chinh : Tan so (1..10000 Hz), Duty (5..95%), Bien do (0..100%),
 *                      Sample Rate (8k..48k), Bit depth (16/32-bit),
 *                      Chuan (I2S/MSB), I2S Port (0/1), Kenh (BOTH/LEFT/RIGHT),
 *                      SD_MODE 3 trang thai (HIGH / FLOAT / LOW), doi chan GPIO nong!
 *   2. MODE:PROBE - Che do do chan bang dong ho van nang (Multimeter Probe):
 *        + Ep muc tung chan GPIO 15/16/17/18 len HIGH (3.3V), LOW (0V), FLOAT,
 *          BLINK (nhay 1 Hz de do ap), hoac WALK (quet lan luot tung chan 1.5s).
 * ============================================================================
 */

#include <Arduino.h>
#include <driver/gpio.h>
#include <driver/i2s.h>
#include <math.h>
#include <stdint.h>
#include <string.h>

// ============================================================================
// 1. CHAN GPIO MAC DINH KHOP D:\Plant\main.fzz
// ============================================================================
#ifndef SPK_LRCLK_PIN
#define SPK_LRCLK_PIN 15
#endif
#ifndef SPK_BCLK_PIN
#define SPK_BCLK_PIN 16
#endif
#ifndef SPK_DIN_PIN
#define SPK_DIN_PIN 17
#endif
#ifndef SPK_SD_PIN
#define SPK_SD_PIN 18
#endif

static constexpr float  TWO_PI_F         = 6.28318530718f;
static constexpr size_t I2S_BLOCK_FRAMES = 128;

// ============================================================================
// 2. KIEU DU LIEU & CAU HINH HE THONG
// ============================================================================
enum class RunMode : uint8_t {
    I2S_GEN    = 0,
    GPIO_PROBE = 1,
};

enum class WaveType : uint8_t {
    SINE     = 0,
    SQUARE   = 1,
    PULSE    = 2,
    TRIANGLE = 3,
    SAWTOOTH = 4,
    CLICK    = 5,
    SWEEP    = 6,
    NOISE    = 7,
};

enum class GateMode : uint8_t {
    CW   = 0, // Phat lien tuc
    BEEP = 1, // Phat ngat quang onMs / offMs
    ONCE = 2, // Phat 1 lan dung onMs roi dung
};

enum class SdState : uint8_t {
    SD_HIGH  = 0, // OUTPUT HIGH (3.3V)
    SD_FLOAT = 1, // INPUT (High-Z tha noi, dung tro keo noi cua module)
    SD_LOW   = 2, // OUTPUT LOW (0V - Shutdown)
};

enum class SlotMode : uint8_t {
    BOTH  = 0,
    LEFT  = 1,
    RIGHT = 2,
};

enum class ProbePinMode : uint8_t {
    LOW_0V  = 0,
    HIGH_3V = 1,
    FLOAT_Z = 2,
    BLINK   = 3,
};

struct SpeakerConfig {
    RunMode  mode         = RunMode::I2S_GEN;
    bool     running      = false;

    // Cau hinh phan cung I2S (co the doi nong tu UI)
    int      pinLrclk     = SPK_LRCLK_PIN;
    int      pinBclk      = SPK_BCLK_PIN;
    int      pinDin       = SPK_DIN_PIN;
    int      pinSd        = SPK_SD_PIN;
    uint8_t  i2sPort      = 0;       // 0 = I2S_NUM_0, 1 = I2S_NUM_1
    uint32_t sampleRate   = 24000;
    uint8_t  bitsPerSamp  = 16;      // 16 hoac 32 bit
    bool     msbFormat    = false;   // false = STAND_I2S (Philips), true = STAND_MSB
    SlotMode slotMode     = SlotMode::BOTH;
    SdState  sdState      = SdState::SD_HIGH;

    // Cau hinh bo phat xung / song
    WaveType wave         = WaveType::PULSE;
    float    freqHz       = 1000.0f;
    float    sweepEndHz   = 4000.0f;
    uint32_t sweepDurMs   = 2500;
    float    dutyPct      = 50.0f;   // 5..95% cho PULSE / SQUARE
    uint8_t  ampPct       = 60;      // 0..100%
    GateMode gateMode     = GateMode::CW;
    uint32_t gateOnMs     = 400;
    uint32_t gateOffMs    = 400;

    // Cau hinh che do Do chan GPIO (Multimeter Probe)
    // Index: 0=LRC, 1=BCLK, 2=DIN, 3=SD
    ProbePinMode probeMode[4] = {
        ProbePinMode::LOW_0V,
        ProbePinMode::LOW_0V,
        ProbePinMode::LOW_0V,
        ProbePinMode::LOW_0V,
    };
    bool     probeWalk    = false;
    uint32_t probePeriodMs = 1000; // Chu ky nhay/quet (ms)
};

static SpeakerConfig     g_cfg;
static SemaphoreHandle_t g_cfgMutex      = nullptr;
static SemaphoreHandle_t g_serialTxMutex = nullptr;

// Trang thai noi bo Task Audio/Probe
static volatile bool     g_reinitHwReq    = true;
static volatile bool     g_resetGateReq   = false;
static volatile bool     g_i2sInstalled   = false;
static volatile i2s_port_t g_activePort   = I2S_NUM_0;
static volatile bool     g_gateNowOn      = false;
static volatile float    g_liveFreqHz     = 1000.0f;
static volatile float    g_livePeakPct    = 0.0f;
static volatile int8_t   g_probeLevel[4]  = {0, 0, 0, 0}; // 0=LOW, 1=HIGH, -1=FLOAT
static volatile int8_t   g_walkIndex      = -1;

// ============================================================================
// 3. HELPER TEN & XUAT JSON TRANG THAI
// ============================================================================
static const char *waveName(WaveType w) {
    switch (w) {
        case WaveType::SINE:     return "SINE";
        case WaveType::SQUARE:   return "SQUARE";
        case WaveType::PULSE:    return "PULSE";
        case WaveType::TRIANGLE: return "TRIANGLE";
        case WaveType::SAWTOOTH: return "SAWTOOTH";
        case WaveType::CLICK:    return "CLICK";
        case WaveType::SWEEP:    return "SWEEP";
        case WaveType::NOISE:    return "NOISE";
        default:                 return "SINE";
    }
}

static const char *gateName(GateMode g) {
    switch (g) {
        case GateMode::BEEP: return "BEEP";
        case GateMode::ONCE: return "ONCE";
        default:             return "CW";
    }
}

static const char *sdName(SdState s) {
    switch (s) {
        case SdState::SD_FLOAT: return "FLOAT";
        case SdState::SD_LOW:   return "LOW";
        default:                return "HIGH";
    }
}

static const char *slotName(SlotMode s) {
    switch (s) {
        case SlotMode::LEFT:  return "LEFT";
        case SlotMode::RIGHT: return "RIGHT";
        default:              return "BOTH";
    }
}

static const char *probeModeName(ProbePinMode m) {
    switch (m) {
        case ProbePinMode::HIGH_3V: return "HIGH";
        case ProbePinMode::FLOAT_Z: return "FLOAT";
        case ProbePinMode::BLINK:   return "BLINK";
        default:                    return "LOW";
    }
}

static void serialPrintlnSafe(const String &s) {
    if (g_serialTxMutex) xSemaphoreTake(g_serialTxMutex, pdMS_TO_TICKS(50));
    Serial.println(s);
    if (g_serialTxMutex) xSemaphoreGive(g_serialTxMutex);
}

static void emitStateJson(const char *ev) {
    SpeakerConfig c;
    if (g_cfgMutex) xSemaphoreTake(g_cfgMutex, pdMS_TO_TICKS(50));
    c = g_cfg;
    if (g_cfgMutex) xSemaphoreGive(g_cfgMutex);

    char buf[640];
    snprintf(
        buf, sizeof(buf),
        "{\"event\":\"%s\",\"fw\":\"ESP32S3_PULSE_TEST_v2\","
        "\"mode\":\"%s\",\"running\":%s,\"i2s_ok\":%s,\"gate_on\":%s,"
        "\"lrclk\":%d,\"bclk\":%d,\"din\":%d,\"sd_pin\":%d,"
        "\"i2s_port\":%u,\"rate\":%lu,\"bits\":%u,\"fmt\":\"%s\",\"slot\":\"%s\",\"sd\":\"%s\","
        "\"wave\":\"%s\",\"freq\":%.1f,\"sweep_end\":%.1f,\"sweep_ms\":%lu,"
        "\"duty\":%.1f,\"amp\":%u,\"gate\":\"%s\",\"on_ms\":%lu,\"off_ms\":%lu,"
        "\"live_freq\":%.1f,\"peak\":%.1f,"
        "\"probe_walk\":%s,\"probe_ms\":%lu,\"walk_idx\":%d,"
        "\"pm\":[\"%s\",\"%s\",\"%s\",\"%s\"],"
        "\"pl\":[%d,%d,%d,%d]}",
        ev,
        (c.mode == RunMode::I2S_GEN) ? "I2S" : "PROBE",
        c.running ? "true" : "false",
        g_i2sInstalled ? "true" : "false",
        g_gateNowOn ? "true" : "false",
        c.pinLrclk, c.pinBclk, c.pinDin, c.pinSd,
        static_cast<unsigned>(c.i2sPort),
        static_cast<unsigned long>(c.sampleRate),
        static_cast<unsigned>(c.bitsPerSamp),
        c.msbFormat ? "MSB" : "I2S",
        slotName(c.slotMode),
        sdName(c.sdState),
        waveName(c.wave),
        c.freqHz,
        c.sweepEndHz,
        static_cast<unsigned long>(c.sweepDurMs),
        c.dutyPct,
        static_cast<unsigned>(c.ampPct),
        gateName(c.gateMode),
        static_cast<unsigned long>(c.gateOnMs),
        static_cast<unsigned long>(c.gateOffMs),
        g_liveFreqHz,
        g_livePeakPct,
        c.probeWalk ? "true" : "false",
        static_cast<unsigned long>(c.probePeriodMs),
        static_cast<int>(g_walkIndex),
        probeModeName(c.probeMode[0]),
        probeModeName(c.probeMode[1]),
        probeModeName(c.probeMode[2]),
        probeModeName(c.probeMode[3]),
        static_cast<int>(g_probeLevel[0]),
        static_cast<int>(g_probeLevel[1]),
        static_cast<int>(g_probeLevel[2]),
        static_cast<int>(g_probeLevel[3])
    );
    serialPrintlnSafe(String(buf));
}

// ============================================================================
// 4. QUAN LY PHAN CUNG I2S & GPIO (CHI GOI TU CORE 1)
// ============================================================================
static bool isSafeGpio(int pin) {
    // Khong cho phep chon chan Flash/USB/Strapping nguy hiem
    if (pin < 1 || pin > 48) return false;
    if (pin == 19 || pin == 20) return false; // USB D-/D+
    if (pin >= 26 && pin <= 32) return false; // SPI Flash
    return true;
}

static void applySdPinState(int sdPin, SdState st) {
    if (!isSafeGpio(sdPin)) return;
    if (st == SdState::SD_FLOAT) {
        pinMode(sdPin, INPUT); // Tha noi (High-Z) de dung tro keo noi MAX98357A
    } else if (st == SdState::SD_LOW) {
        pinMode(sdPin, OUTPUT);
        digitalWrite(sdPin, LOW);
    } else {
        pinMode(sdPin, OUTPUT);
        digitalWrite(sdPin, HIGH);
    }
}

static void teardownI2s() {
    if (g_i2sInstalled) {
        i2s_zero_dma_buffer(g_activePort);
        i2s_driver_uninstall(g_activePort);
        g_i2sInstalled = false;
    }
}

static bool setupI2sFromConfig(const SpeakerConfig &c) {
    teardownI2s();

    // Reset lai 4 chan truoc khi gan I2S de xoa trang thai GPIO Probe cu
    gpio_reset_pin(static_cast<gpio_num_t>(c.pinLrclk));
    gpio_reset_pin(static_cast<gpio_num_t>(c.pinBclk));
    gpio_reset_pin(static_cast<gpio_num_t>(c.pinDin));
    gpio_reset_pin(static_cast<gpio_num_t>(c.pinSd));

    applySdPinState(c.pinSd, SdState::SD_LOW);

    i2s_port_t port = (c.i2sPort == 1) ? I2S_NUM_1 : I2S_NUM_0;
    i2s_config_t icfg = {};
    icfg.mode                 = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
    icfg.sample_rate          = c.sampleRate;
    icfg.bits_per_sample      = (c.bitsPerSamp == 32) ? I2S_BITS_PER_SAMPLE_32BIT : I2S_BITS_PER_SAMPLE_16BIT;
    icfg.channel_format       = I2S_CHANNEL_FMT_RIGHT_LEFT;
    icfg.communication_format = c.msbFormat ? I2S_COMM_FORMAT_STAND_MSB : I2S_COMM_FORMAT_STAND_I2S;
    icfg.intr_alloc_flags     = ESP_INTR_FLAG_LEVEL1;
    icfg.dma_buf_count        = 6;
    icfg.dma_buf_len          = I2S_BLOCK_FRAMES;
    icfg.use_apll             = false;
    icfg.tx_desc_auto_clear   = true;
    icfg.fixed_mclk           = 0;

    i2s_pin_config_t pins = {};
    pins.mck_io_num   = I2S_PIN_NO_CHANGE;
    pins.bck_io_num   = c.pinBclk;
    pins.ws_io_num    = c.pinLrclk;
    pins.data_out_num = c.pinDin;
    pins.data_in_num  = I2S_PIN_NO_CHANGE;

    if (i2s_driver_install(port, &icfg, 0, nullptr) != ESP_OK) {
        g_i2sInstalled = false;
        return false;
    }
    if (i2s_set_pin(port, &pins) != ESP_OK) {
        i2s_driver_uninstall(port);
        g_i2sInstalled = false;
        return false;
    }
    i2s_zero_dma_buffer(port);
    g_activePort   = port;
    g_i2sInstalled = true;

    applySdPinState(c.pinSd, c.sdState);
    return true;
}

static void setupProbePins(const SpeakerConfig &c) {
    teardownI2s();
    int pins[4] = {c.pinLrclk, c.pinBclk, c.pinDin, c.pinSd};
    for (int i = 0; i < 4; ++i) {
        if (isSafeGpio(pins[i])) {
            gpio_reset_pin(static_cast<gpio_num_t>(pins[i]));
            pinMode(pins[i], OUTPUT);
            digitalWrite(pins[i], LOW);
        }
        g_probeLevel[i] = 0;
    }
}

// ============================================================================
// 5. TASK CORE 1: BO PHAT XUNG I2S + BO DO CHAN GPIO PROBE
// ============================================================================
static void signalEngineTask(void * /*param*/) {
    int16_t  buf16[I2S_BLOCK_FRAMES * 2];
    int32_t  buf32[I2S_BLOCK_FRAMES * 2];

    float    phase       = 0.0f;
    float    envGain     = 0.0f; // Anti-pop ramp 0..1
    uint32_t rngState    = 0xA5A51234UL;
    uint32_t gateStartMs = millis();
    uint32_t sweepStartMs = millis();
    RunMode  lastMode    = RunMode::I2S_GEN;
    SdState  lastSdState = SdState::SD_HIGH;
    bool     bootBeepDone = false;

    while (true) {
        SpeakerConfig c;
        if (g_cfgMutex) xSemaphoreTake(g_cfgMutex, pdMS_TO_TICKS(20));
        c = g_cfg;
        if (g_cfgMutex) xSemaphoreGive(g_cfgMutex);

        uint32_t now = millis();

        if (g_resetGateReq) {
            g_resetGateReq = false;
            gateStartMs    = now;
            sweepStartMs   = now;
            phase          = 0.0f;
        }

        // Kiem tra yeu cau khoi tao lai phan cung
        if (g_reinitHwReq || c.mode != lastMode) {
            g_reinitHwReq = false;
            lastMode      = c.mode;
            lastSdState   = c.sdState;
            envGain       = 0.0f;
            phase         = 0.0f;
            gateStartMs   = now;
            sweepStartMs  = now;
            if (c.mode == RunMode::I2S_GEN) {
                setupI2sFromConfig(c);
            } else {
                setupProbePins(c);
            }
            emitStateJson("HW_READY");
        }

        // ---------------- CHE DO 2: DO CHAN GPIO PROBE ----------------
        if (c.mode == RunMode::GPIO_PROBE) {
            g_gateNowOn   = false;
            g_livePeakPct = 0.0f;
            int pins[4]   = {c.pinLrclk, c.pinBclk, c.pinDin, c.pinSd};
            uint32_t per  = (c.probePeriodMs < 100) ? 100 : c.probePeriodMs;

            if (c.probeWalk) {
                int activeIdx = static_cast<int>((now / per) % 4);
                g_walkIndex = static_cast<int8_t>(activeIdx);
                for (int i = 0; i < 4; ++i) {
                    if (!isSafeGpio(pins[i])) continue;
                    pinMode(pins[i], OUTPUT);
                    int lvl = (i == activeIdx) ? 1 : 0;
                    digitalWrite(pins[i], lvl ? HIGH : LOW);
                    g_probeLevel[i] = static_cast<int8_t>(lvl);
                }
            } else {
                g_walkIndex = -1;
                bool blinkHigh = ((now % per) < (per / 2));
                for (int i = 0; i < 4; ++i) {
                    if (!isSafeGpio(pins[i])) continue;
                    ProbePinMode pm = c.probeMode[i];
                    if (pm == ProbePinMode::FLOAT_Z) {
                        pinMode(pins[i], INPUT);
                        g_probeLevel[i] = -1;
                    } else {
                        pinMode(pins[i], OUTPUT);
                        int lvl = 0;
                        if (pm == ProbePinMode::HIGH_3V) lvl = 1;
                        else if (pm == ProbePinMode::BLINK) lvl = blinkHigh ? 1 : 0;
                        digitalWrite(pins[i], lvl ? HIGH : LOW);
                        g_probeLevel[i] = static_cast<int8_t>(lvl);
                    }
                }
            }
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        // ---------------- CHE DO 1: BO PHAT XUNG I2S ----------------
        if (!g_i2sInstalled) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        if (c.sdState != lastSdState) {
            lastSdState = c.sdState;
            applySdPinState(c.pinSd, c.sdState);
        }

        // Phat 1 tieng bip ngan 250ms luc vua khoi dong xong de xac nhan nhanh
        if (!bootBeepDone) {
            bootBeepDone = true;
            uint32_t beepFrames = (c.sampleRate * 180) / 1000;
            uint32_t done = 0;
            float bp = 0.0f;
            while (done < beepFrames) {
                size_t n = beepFrames - done;
                if (n > I2S_BLOCK_FRAMES) n = I2S_BLOCK_FRAMES;
                for (size_t i = 0; i < n; ++i) {
                    bp += (TWO_PI_F * 880.0f) / static_cast<float>(c.sampleRate);
                    if (bp >= TWO_PI_F) bp -= TWO_PI_F;
                    int16_t s = static_cast<int16_t>(sinf(bp) * 0.30f * 32767.0f);
                    buf16[i * 2]     = s;
                    buf16[i * 2 + 1] = s;
                }
                size_t w = 0;
                i2s_write(g_activePort, buf16, n * 2 * sizeof(int16_t), &w, pdMS_TO_TICKS(50));
                done += n;
            }
            i2s_zero_dma_buffer(g_activePort);
        }

        // Tinh trang thai cong Gate (CW / BEEP / ONCE)
        bool wantGateOn = false;
        if (c.running) {
            if (c.gateMode == GateMode::CW) {
                wantGateOn = true;
            } else if (c.gateMode == GateMode::BEEP) {
                uint32_t onMs  = (c.gateOnMs < 20) ? 20 : c.gateOnMs;
                uint32_t offMs = (c.gateOffMs < 20) ? 20 : c.gateOffMs;
                uint32_t cycle = onMs + offMs;
                wantGateOn     = ((now - gateStartMs) % cycle) < onMs;
            } else if (c.gateMode == GateMode::ONCE) {
                uint32_t onMs = (c.gateOnMs < 20) ? 20 : c.gateOnMs;
                if (now - gateStartMs < onMs) {
                    wantGateOn = true;
                } else {
                    wantGateOn = false;
                    // Tu dong dung sau khi xong 1 lan phat
                    if (g_cfgMutex) xSemaphoreTake(g_cfgMutex, pdMS_TO_TICKS(20));
                    g_cfg.running = false;
                    if (g_cfgMutex) xSemaphoreGive(g_cfgMutex);
                    emitStateJson("ONCE_DONE");
                }
            }
        }
        g_gateNowOn = wantGateOn;

        // Tinh tan so tuc thoi (cho SWEEP hoac tan so co dinh)
        float curFreq = c.freqHz;
        if (c.wave == WaveType::SWEEP) {
            uint32_t sdur = (c.sweepDurMs < 200) ? 200 : c.sweepDurMs;
            float prog = static_cast<float>((now - sweepStartMs) % sdur) / static_cast<float>(sdur);
            float f0 = (c.freqHz < 20.0f) ? 20.0f : c.freqHz;
            float f1 = (c.sweepEndHz < 20.0f) ? 20.0f : c.sweepEndHz;
            curFreq = f0 * powf(f1 / f0, prog);
        }
        g_liveFreqHz = curFreq;

        const float ampScale = static_cast<float>(c.ampPct) * 0.01f;
        const float dutyNorm = constrain(c.dutyPct, 1.0f, 99.0f) * 0.01f;
        const float srF      = static_cast<float>(c.sampleRate);
        const float phaseInc = curFreq / srF; // 0..1 moi chu ky
        const float rampStep = 1.0f / 128.0f; // Ramp muot ~5ms chong tieng tach

        float localPeak = 0.0f;

        for (size_t i = 0; i < I2S_BLOCK_FRAMES; ++i) {
            if (wantGateOn) {
                if (envGain < 1.0f) {
                    envGain += rampStep;
                    if (envGain > 1.0f) envGain = 1.0f;
                }
            } else {
                if (envGain > 0.0f) {
                    envGain -= rampStep;
                    if (envGain < 0.0f) envGain = 0.0f;
                }
            }

            float rawSample = 0.0f;
            if (envGain > 0.0001f) {
                switch (c.wave) {
                    case WaveType::SINE:
                    case WaveType::SWEEP:
                        rawSample = sinf(phase * TWO_PI_F);
                        break;
                    case WaveType::SQUARE:
                        rawSample = (phase < 0.5f) ? 0.90f : -0.90f;
                        break;
                    case WaveType::PULSE:
                        // Xung vuong co do rong duty tuy chinh (bu DC trung binh = 0)
                        rawSample = (phase < dutyNorm) ? 0.90f : -0.90f;
                        break;
                    case WaveType::TRIANGLE:
                        rawSample = (phase < 0.5f) ? (4.0f * phase - 1.0f) : (3.0f - 4.0f * phase);
                        break;
                    case WaveType::SAWTOOTH:
                        rawSample = 2.0f * phase - 1.0f;
                        break;
                    case WaveType::CLICK:
                        // Xung kim ngan 2ms moi chu ky (cuc de nghe tach-tach va nhin mang loa rung!)
                        rawSample = (phase < 0.04f) ? 0.95f : ((phase < 0.08f) ? -0.95f : 0.0f);
                        break;
                    case WaveType::NOISE: {
                        rngState = rngState * 1664525UL + 1013904223UL;
                        int16_t rv = static_cast<int16_t>(rngState >> 16);
                        rawSample = static_cast<float>(rv) / 32768.0f;
                        break;
                    }
                }
                phase += phaseInc;
                if (phase >= 1.0f) phase -= floorf(phase);
            }

            float val = rawSample * ampScale * envGain;
            if (val > 0.999f)  val = 0.999f;
            if (val < -0.999f) val = -0.999f;

            float ab = fabsf(val);
            if (ab > localPeak) localPeak = ab;

            if (c.bitsPerSamp == 32) {
                int32_t s32 = static_cast<int32_t>(val * 2147483000.0f);
                buf32[i * 2]     = (c.slotMode == SlotMode::RIGHT) ? 0 : s32;
                buf32[i * 2 + 1] = (c.slotMode == SlotMode::LEFT)  ? 0 : s32;
            } else {
                int16_t s16 = static_cast<int16_t>(val * 32767.0f);
                buf16[i * 2]     = (c.slotMode == SlotMode::RIGHT) ? 0 : s16;
                buf16[i * 2 + 1] = (c.slotMode == SlotMode::LEFT)  ? 0 : s16;
            }
        }

        g_livePeakPct = localPeak * 100.0f;

        size_t written = 0;
        if (c.bitsPerSamp == 32) {
            i2s_write(g_activePort, buf32, sizeof(buf32), &written, pdMS_TO_TICKS(100));
        } else {
            i2s_write(g_activePort, buf16, sizeof(buf16), &written, pdMS_TO_TICKS(100));
        }
    }
}

// ============================================================================
// 6. XU LY LENH TU UI / SERIAL MONITOR
// ============================================================================
static void handleCommand(String raw) {
    raw.trim();
    if (raw.length() == 0) return;

    String up = raw;
    up.toUpperCase();
    up.replace(' ', ':');
    up.replace('=', ':');

    if (up == "PING") {
        serialPrintlnSafe("{\"event\":\"PONG\",\"fw\":\"ESP32S3_PULSE_TEST_v2\"}");
        return;
    }
    if (up == "INFO" || up == "STATUS") {
        emitStateJson("STATE");
        return;
    }
    if (up == "HELP" || up == "?") {
        serialPrintlnSafe(
            "{\"event\":\"HELP\",\"cmds\":["
            "\"START\",\"STOP\",\"MODE:I2S|PROBE\","
            "\"WAVE:SINE|SQUARE|PULSE|TRIANGLE|SAWTOOTH|CLICK|SWEEP|NOISE\","
            "\"FREQ:<hz>\",\"SWEEP_END:<hz>\",\"SWEEP_MS:<ms>\",\"DUTY:<5..95>\",\"AMP:<0..100>\","
            "\"GATE:CW|BEEP|ONCE\",\"ON_MS:<ms>\",\"OFF_MS:<ms>\","
            "\"RATE:<8000..48000>\",\"BITS:16|32\",\"FMT:I2S|MSB\",\"PORT:0|1\","
            "\"SLOT:BOTH|LEFT|RIGHT\",\"SD:HIGH|FLOAT|LOW\","
            "\"PINS:<lrc>:<bclk>:<din>:<sd>\",\"SWAP_CLK\","
            "\"PROBE:<0..3>:LOW|HIGH|FLOAT|BLINK\",\"PROBE_ALL:LOW|HIGH|FLOAT|BLINK\","
            "\"PROBE_WALK:0|1\",\"PROBE_MS:<ms>\"]}"
        );
        return;
    }

    bool needReinit = false;
    bool needResetGate = false;

    if (g_cfgMutex) xSemaphoreTake(g_cfgMutex, pdMS_TO_TICKS(50));

    if (up == "START" || up == "RUN:1") {
        g_cfg.mode    = RunMode::I2S_GEN;
        g_cfg.running = true;
        needResetGate = true;
    } else if (up == "STOP" || up == "RUN:0") {
        g_cfg.running = false;
    } else if (up == "MODE:I2S") {
        g_cfg.mode = RunMode::I2S_GEN;
        needReinit = true;
    } else if (up == "MODE:PROBE") {
        g_cfg.running = false;
        g_cfg.mode    = RunMode::GPIO_PROBE;
        needReinit    = true;
    } else if (up.startsWith("WAVE:")) {
        String v = up.substring(5);
        if (v == "SINE")          g_cfg.wave = WaveType::SINE;
        else if (v == "SQUARE")   g_cfg.wave = WaveType::SQUARE;
        else if (v == "PULSE")    g_cfg.wave = WaveType::PULSE;
        else if (v == "TRIANGLE") g_cfg.wave = WaveType::TRIANGLE;
        else if (v == "SAWTOOTH") g_cfg.wave = WaveType::SAWTOOTH;
        else if (v == "CLICK")    g_cfg.wave = WaveType::CLICK;
        else if (v == "SWEEP")    { g_cfg.wave = WaveType::SWEEP; needResetGate = true; }
        else if (v == "NOISE")    g_cfg.wave = WaveType::NOISE;
    } else if (up.startsWith("FREQ:")) {
        float f = up.substring(5).toFloat();
        g_cfg.freqHz = constrain(f, 1.0f, 10000.0f);
    } else if (up.startsWith("SWEEP_END:")) {
        float f = up.substring(10).toFloat();
        g_cfg.sweepEndHz = constrain(f, 20.0f, 10000.0f);
    } else if (up.startsWith("SWEEP_MS:")) {
        uint32_t ms = static_cast<uint32_t>(up.substring(9).toInt());
        g_cfg.sweepDurMs = constrain(ms, 200UL, 20000UL);
    } else if (up.startsWith("DUTY:")) {
        float d = up.substring(5).toFloat();
        g_cfg.dutyPct = constrain(d, 1.0f, 99.0f);
    } else if (up.startsWith("AMP:") || up.startsWith("VOL:")) {
        int a = up.substring(4).toInt();
        g_cfg.ampPct = static_cast<uint8_t>(constrain(a, 0, 100));
    } else if (up.startsWith("GATE:")) {
        String v = up.substring(5);
        if (v == "BEEP")      g_cfg.gateMode = GateMode::BEEP;
        else if (v == "ONCE") g_cfg.gateMode = GateMode::ONCE;
        else                  g_cfg.gateMode = GateMode::CW;
        needResetGate = true;
    } else if (up.startsWith("ON_MS:")) {
        uint32_t ms = static_cast<uint32_t>(up.substring(6).toInt());
        g_cfg.gateOnMs = constrain(ms, 20UL, 30000UL);
        needResetGate  = true;
    } else if (up.startsWith("OFF_MS:")) {
        uint32_t ms = static_cast<uint32_t>(up.substring(7).toInt());
        g_cfg.gateOffMs = constrain(ms, 20UL, 30000UL);
        needResetGate   = true;
    } else if (up.startsWith("RATE:")) {
        uint32_t r = static_cast<uint32_t>(up.substring(5).toInt());
        if (r == 8000 || r == 16000 || r == 22050 || r == 24000 || r == 32000 || r == 44100 || r == 48000) {
            g_cfg.sampleRate = r;
            needReinit       = true;
        }
    } else if (up.startsWith("BITS:")) {
        int b = up.substring(5).toInt();
        if (b == 16 || b == 32) {
            g_cfg.bitsPerSamp = static_cast<uint8_t>(b);
            needReinit        = true;
        }
    } else if (up.startsWith("FMT:")) {
        String v = up.substring(4);
        g_cfg.msbFormat = (v == "MSB");
        needReinit      = true;
    } else if (up.startsWith("PORT:")) {
        int p = up.substring(5).toInt();
        g_cfg.i2sPort = (p == 1) ? 1 : 0;
        needReinit    = true;
    } else if (up.startsWith("SLOT:")) {
        String v = up.substring(5);
        if (v == "LEFT" || v == "L")       g_cfg.slotMode = SlotMode::LEFT;
        else if (v == "RIGHT" || v == "R") g_cfg.slotMode = SlotMode::RIGHT;
        else                               g_cfg.slotMode = SlotMode::BOTH;
    } else if (up.startsWith("SD:")) {
        String v = up.substring(3);
        if (v == "FLOAT" || v == "Z")        g_cfg.sdState = SdState::SD_FLOAT;
        else if (v == "LOW" || v == "0")     g_cfg.sdState = SdState::SD_LOW;
        else                                 g_cfg.sdState = SdState::SD_HIGH;
    } else if (up == "SWAP_CLK") {
        int tmp        = g_cfg.pinLrclk;
        g_cfg.pinLrclk = g_cfg.pinBclk;
        g_cfg.pinBclk  = tmp;
        needReinit     = true;
    } else if (up.startsWith("PINS:")) {
        // PINS:<lrc>:<bclk>:<din>:<sd>
        int p1 = up.indexOf(':', 5);
        int p2 = (p1 > 0) ? up.indexOf(':', p1 + 1) : -1;
        int p3 = (p2 > 0) ? up.indexOf(':', p2 + 1) : -1;
        if (p1 > 0 && p2 > 0 && p3 > 0) {
            int lrc  = up.substring(5, p1).toInt();
            int bclk = up.substring(p1 + 1, p2).toInt();
            int din  = up.substring(p2 + 1, p3).toInt();
            int sd   = up.substring(p3 + 1).toInt();
            if (isSafeGpio(lrc) && isSafeGpio(bclk) && isSafeGpio(din) && isSafeGpio(sd)) {
                g_cfg.pinLrclk = lrc;
                g_cfg.pinBclk  = bclk;
                g_cfg.pinDin   = din;
                g_cfg.pinSd    = sd;
                needReinit     = true;
            }
        }
    } else if (up.startsWith("PROBE_WALK:")) {
        String v = up.substring(11);
        g_cfg.mode      = RunMode::GPIO_PROBE;
        g_cfg.running   = false;
        g_cfg.probeWalk = (v == "1" || v == "ON" || v == "TRUE");
    } else if (up.startsWith("PROBE_MS:")) {
        uint32_t ms = static_cast<uint32_t>(up.substring(9).toInt());
        g_cfg.probePeriodMs = constrain(ms, 100UL, 5000UL);
    } else if (up.startsWith("PROBE_ALL:")) {
        String v = up.substring(10);
        ProbePinMode pm = ProbePinMode::LOW_0V;
        if (v == "HIGH")       pm = ProbePinMode::HIGH_3V;
        else if (v == "FLOAT") pm = ProbePinMode::FLOAT_Z;
        else if (v == "BLINK") pm = ProbePinMode::BLINK;
        g_cfg.mode      = RunMode::GPIO_PROBE;
        g_cfg.running   = false;
        g_cfg.probeWalk = false;
        for (int i = 0; i < 4; ++i) g_cfg.probeMode[i] = pm;
    } else if (up.startsWith("PROBE:")) {
        // PROBE:<idx 0..3>:<LOW|HIGH|FLOAT|BLINK>
        int p1 = up.indexOf(':', 6);
        if (p1 > 0) {
            int idx = up.substring(6, p1).toInt();
            String v = up.substring(p1 + 1);
            if (idx >= 0 && idx < 4) {
                ProbePinMode pm = ProbePinMode::LOW_0V;
                if (v == "HIGH")       pm = ProbePinMode::HIGH_3V;
                else if (v == "FLOAT") pm = ProbePinMode::FLOAT_Z;
                else if (v == "BLINK") pm = ProbePinMode::BLINK;
                g_cfg.mode           = RunMode::GPIO_PROBE;
                g_cfg.running        = false;
                g_cfg.probeWalk      = false;
                g_cfg.probeMode[idx] = pm;
            }
        }
    }

    if (g_cfgMutex) xSemaphoreGive(g_cfgMutex);

    if (needResetGate) g_resetGateReq = true;
    if (needReinit)    g_reinitHwReq  = true;

    emitStateJson("STATE");
}

// ============================================================================
// 7. SETUP & LOOP (CORE 0 DOC SERIAL + GUI TELEMETRY)
// ============================================================================
static String   g_rxLine;
static uint32_t g_lastLiveMs = 0;

void setup() {
    g_cfgMutex      = xSemaphoreCreateMutex();
    g_serialTxMutex = xSemaphoreCreateMutex();

    Serial.begin(115200);
    Serial.setTimeout(5);
#if defined(ARDUINO_USB_MODE) && (ARDUINO_USB_MODE == 1) && defined(ARDUINO_USB_CDC_ON_BOOT) && (ARDUINO_USB_CDC_ON_BOOT == 1)
    Serial.setTxTimeoutMs(5);
#endif

    uint32_t t0 = millis();
    while (!Serial && (millis() - t0 < 800)) {
        delay(10);
    }

    g_rxLine.reserve(128);

    xTaskCreatePinnedToCore(
        signalEngineTask,
        "spk_pulse_eng",
        6144,
        nullptr,
        5,
        nullptr,
        1
    );

    delay(80);
    emitStateJson("BOOT_READY");
}

void loop() {
    while (Serial.available() > 0) {
        char ch = static_cast<char>(Serial.read());
        if (ch == '\r' || ch == '\n') {
            if (g_rxLine.length() > 0) {
                handleCommand(g_rxLine);
                g_rxLine = "";
            }
        } else if (ch >= 0x20 && ch <= 0x7E) {
            if (g_rxLine.length() < 120) {
                g_rxLine += ch;
            }
        }
    }

    uint32_t now = millis();
    if (now - g_lastLiveMs >= 350) {
        g_lastLiveMs = now;
        emitStateJson("LIVE");
    }

    vTaskDelay(pdMS_TO_TICKS(5));
}
