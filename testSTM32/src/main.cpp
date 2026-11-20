#include <Arduino.h>
#include <Preferences.h>
#include <Wire.h>
#include <driver/rtc_io.h>
#include <esp_sleep.h>
#include <math.h>

#ifndef I2C_SDA_PIN
#define I2C_SDA_PIN 8
#endif

#ifndef I2C_SCL_PIN
#define I2C_SCL_PIN 9
#endif

#ifndef MPU_INT_PIN
#define MPU_INT_PIN 4
#endif

// ============================================================================
// I2C Addresses & Registers
// ============================================================================
// MPU6050
static constexpr uint8_t MPU_ADDR_LOW  = 0x68;
static constexpr uint8_t MPU_ADDR_HIGH = 0x69;
static constexpr uint8_t REG_SMPLRT_DIV   = 0x19;
static constexpr uint8_t REG_CONFIG       = 0x1A;
static constexpr uint8_t REG_GYRO_CONFIG  = 0x1B;
static constexpr uint8_t REG_ACCEL_CONFIG = 0x1C;
static constexpr uint8_t REG_MOT_THR      = 0x1F;
static constexpr uint8_t REG_MOT_DUR      = 0x20;
static constexpr uint8_t REG_INT_PIN_CFG  = 0x37;
static constexpr uint8_t REG_INT_ENABLE   = 0x38;
static constexpr uint8_t REG_INT_STATUS   = 0x3A;
static constexpr uint8_t REG_ACCEL_XOUT_H = 0x3B;
static constexpr uint8_t REG_PWR_MGMT_1   = 0x6B;
static constexpr uint8_t REG_WHO_AM_I     = 0x75;

// AHT20
static constexpr uint8_t AHT20_ADDR = 0x38;

// BMP280
static constexpr uint8_t BMP280_ADDR_LOW  = 0x76;
static constexpr uint8_t BMP280_ADDR_HIGH = 0x77;
static constexpr uint8_t BMP_REG_CHIP_ID   = 0xD0;
static constexpr uint8_t BMP_REG_RESET     = 0xE0;
static constexpr uint8_t BMP_REG_CTRL_MEAS = 0xF4;
static constexpr uint8_t BMP_REG_CONFIG    = 0xF5;
static constexpr uint8_t BMP_REG_PRESS_MSB = 0xF7;
static constexpr uint8_t BMP_REG_CALIB_00  = 0x88;

// BH1750
static constexpr uint8_t BH1750_ADDR_LOW  = 0x23;
static constexpr uint8_t BH1750_ADDR_HIGH = 0x5C;

static constexpr float ACCEL_SCALE_2G      = 16384.0f; // LSB/g (+/-2g)
static constexpr float GYRO_SCALE_250DPS   = 131.0f;   // LSB/(deg/s) (+/-250 dps)
static constexpr float RAD_TO_DEG_F        = 57.2957795f;
static constexpr uint32_t FILTER_INTERVAL_MS = 10;     // 100 Hz MPU filter loop
static constexpr uint32_t ENV_OUTPUT_MS      = 500;    // 2 Hz Environment/Light output

enum class SleepMode : uint8_t {
    LIGHT = 0,
    DEEP  = 1,
};

enum class ActiveViewMode : uint8_t {
    MPU = 0,
    ENV = 1,
};

struct MpuSample {
    float ax, ay, az; // g
    float gx, gy, gz; // deg/s
    float tempC;      // Celsius
};

struct MpuCalibration {
    float axOff = 0.0f, ayOff = 0.0f, azOff = 0.0f;
    float gxOff = 0.0f, gyOff = 0.0f, gzOff = 0.0f;
    bool calibrated = false;
    bool savedInNvs = false;
};

struct Bmp280Calib {
    uint16_t dig_T1;
    int16_t  dig_T2, dig_T3;
    uint16_t dig_P1;
    int16_t  dig_P2, dig_P3, dig_P4, dig_P5, dig_P6, dig_P7, dig_P8, dig_P9;
};

static Preferences g_prefs;

// MPU6050 state
static uint8_t g_mpuAddr = 0x00;
static uint8_t g_whoAmI = 0x00;
static bool g_mpuReady = false;
static uint32_t g_sampleIntervalMs = 40; // 25 Hz Serial output default
static uint32_t g_lastFilterMs = 0;
static uint32_t g_lastOutputMs = 0;
static uint32_t g_lastMpuRetryMs = 0;
static uint32_t g_lastFpsMs = 0;
static uint32_t g_frameCounter = 0;
static uint32_t g_measuredHz = 0;
static uint32_t g_lastIntegrateUs = 0;

// Auto-sleep configuration & state
static bool g_autoSleepEnabled = true;
static SleepMode g_sleepMode = SleepMode::LIGHT;
static ActiveViewMode g_viewMode = ActiveViewMode::MPU;
static uint32_t g_idleSleepMs = 5000;
static uint32_t g_idleStartMs = 0;

static float g_roll = 0.0f;
static float g_pitch = 0.0f;
static float g_yaw = 0.0f;

static float g_filtAx = 0.0f, g_filtAy = 0.0f, g_filtAz = 1.0f;
static float g_outGx = 0.0f, g_outGy = 0.0f, g_outGz = 0.0f;
static float g_lastTempC = 0.0f;
static float g_prevAx = 0.0f, g_prevAy = 0.0f, g_prevAz = 1.0f;
static float g_prevRawGx = 0.0f, g_prevRawGy = 0.0f, g_prevRawGz = 0.0f;
static bool g_hasPrevSample = false;
static bool g_isStill = false;
static uint16_t g_stillTicks = 0;
static uint16_t g_biasTicks = 0;

static MpuCalibration g_cal;
static String g_cmdBuffer;
static volatile bool g_motionIrq = false;

// AHT20 state
static bool g_ahtReady = false;
static bool g_ahtWaiting = false;
static bool g_ahtValid = false;
static uint32_t g_ahtTrigMs = 0;
static float g_ahtTempC = 0.0f;
static float g_ahtHumPct = 0.0f;

// BMP280 state
static uint8_t g_bmpAddr = 0x00;
static uint8_t g_bmpChipId = 0x00;
static bool g_bmpReady = false;
static bool g_bmpValid = false;
static Bmp280Calib g_bmpCal{};
static float g_bmpTempC = 0.0f;
static float g_bmpPressHpa = 0.0f;
static float g_bmpAltM = 0.0f;

// BH1750 state
static uint8_t g_bhAddr = 0x00;
static bool g_bhReady = false;
static bool g_bhValid = false;
static float g_bhLux = 0.0f;

static uint32_t g_lastEnvOutputMs = 0;
static uint32_t g_lastEnvProbeMs = 0;

static void IRAM_ATTR onMotionPinIsr() {
    g_motionIrq = true;
}

// ============================================================================
// NVS Calibration Helpers
// ============================================================================
static void loadCalibrationFromNvs() {
    g_prefs.begin("mpu6050", true);
    bool valid = g_prefs.getBool("valid", false);
    if (valid) {
        g_cal.axOff = g_prefs.getFloat("axOff", 0.0f);
        g_cal.ayOff = g_prefs.getFloat("ayOff", 0.0f);
        g_cal.azOff = g_prefs.getFloat("azOff", 0.0f);
        g_cal.gxOff = g_prefs.getFloat("gxOff", 0.0f);
        g_cal.gyOff = g_prefs.getFloat("gyOff", 0.0f);
        g_cal.gzOff = g_prefs.getFloat("gzOff", 0.0f);
        g_cal.calibrated = true;
        g_cal.savedInNvs = true;
    }
    g_prefs.end();
}

static void saveCalibrationToNvs() {
    g_prefs.begin("mpu6050", false);
    g_prefs.putBool("valid", true);
    g_prefs.putFloat("axOff", g_cal.axOff);
    g_prefs.putFloat("ayOff", g_cal.ayOff);
    g_prefs.putFloat("azOff", g_cal.azOff);
    g_prefs.putFloat("gxOff", g_cal.gxOff);
    g_prefs.putFloat("gyOff", g_cal.gyOff);
    g_prefs.putFloat("gzOff", g_cal.gzOff);
    g_prefs.end();
    g_cal.savedInNvs = true;
}

static void clearCalibrationInNvs() {
    g_prefs.begin("mpu6050", false);
    g_prefs.clear();
    g_prefs.end();
    g_cal = MpuCalibration{};
}

// ============================================================================
// Low-level I2C Helpers
// ============================================================================
static bool i2cProbeAddr(uint8_t addr) {
    Wire.beginTransmission(addr);
    return Wire.endTransmission(true) == 0;
}

static bool i2cWriteReg(uint8_t addr, uint8_t reg, uint8_t val) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.write(val);
    return Wire.endTransmission(true) == 0;
}

static bool i2cWriteCmd(uint8_t addr, const uint8_t *bytes, size_t len) {
    Wire.beginTransmission(addr);
    Wire.write(bytes, len);
    return Wire.endTransmission(true) == 0;
}

static bool i2cReadBytes(uint8_t addr, uint8_t reg, uint8_t *buf, size_t len) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) {
        return false;
    }
    size_t received = Wire.requestFrom(static_cast<int>(addr), static_cast<int>(len), static_cast<int>(true));
    if (received != len) {
        while (Wire.available()) {
            Wire.read();
        }
        return false;
    }
    for (size_t i = 0; i < len; ++i) {
        buf[i] = static_cast<uint8_t>(Wire.read());
    }
    return true;
}

static bool i2cReadRaw(uint8_t addr, uint8_t *buf, size_t len) {
    size_t received = Wire.requestFrom(static_cast<int>(addr), static_cast<int>(len), static_cast<int>(true));
    if (received != len) {
        while (Wire.available()) {
            Wire.read();
        }
        return false;
    }
    for (size_t i = 0; i < len; ++i) {
        buf[i] = static_cast<uint8_t>(Wire.read());
    }
    return true;
}

// ============================================================================
// AHT20 Driver (0x38)
// ============================================================================
static bool probeAndInitAht20() {
    if (!i2cProbeAddr(AHT20_ADDR)) {
        g_ahtReady = false;
        g_ahtValid = false;
        g_ahtWaiting = false;
        return false;
    }
    uint8_t status = 0;
    uint8_t cmdStatus = 0x71;
    if (i2cWriteCmd(AHT20_ADDR, &cmdStatus, 1) && i2cReadRaw(AHT20_ADDR, &status, 1)) {
        if ((status & 0x08) == 0) {
            const uint8_t initCmd[3] = { 0xBE, 0x08, 0x00 };
            if (!i2cWriteCmd(AHT20_ADDR, initCmd, sizeof(initCmd))) {
                g_ahtReady = false;
                return false;
            }
            delay(15);
        }
    }
    g_ahtReady = true;
    g_ahtWaiting = false;
    g_ahtTrigMs = 0;
    return true;
}

static void pollAht20NonBlocking(uint32_t nowMs) {
    if (!g_ahtReady) return;

    if (!g_ahtWaiting) {
        if (nowMs - g_ahtTrigMs >= 450) {
            const uint8_t trigCmd[3] = { 0xAC, 0x33, 0x00 };
            if (i2cWriteCmd(AHT20_ADDR, trigCmd, sizeof(trigCmd))) {
                g_ahtWaiting = true;
                g_ahtTrigMs = nowMs;
            } else {
                g_ahtReady = false;
                g_ahtValid = false;
            }
        }
        return;
    }

    if (nowMs - g_ahtTrigMs < 90) {
        return;
    }

    uint8_t buf[6] = {0};
    if (!i2cReadRaw(AHT20_ADDR, buf, sizeof(buf))) {
        g_ahtReady = false;
        g_ahtValid = false;
        g_ahtWaiting = false;
        return;
    }

    if ((buf[0] & 0x80) == 0) {
        uint32_t rawHum = (static_cast<uint32_t>(buf[1]) << 12) |
                          (static_cast<uint32_t>(buf[2]) << 4) |
                          (static_cast<uint32_t>(buf[3]) >> 4);
        uint32_t rawTemp = ((static_cast<uint32_t>(buf[3]) & 0x0F) << 16) |
                           (static_cast<uint32_t>(buf[4]) << 8) |
                           static_cast<uint32_t>(buf[5]);

        g_ahtHumPct = (static_cast<float>(rawHum) * 100.0f) / 1048576.0f;
        g_ahtTempC  = ((static_cast<float>(rawTemp) * 200.0f) / 1048576.0f) - 50.0f;
        g_ahtValid = true;
        g_ahtWaiting = false;
    } else if (nowMs - g_ahtTrigMs >= 250) {
        g_ahtWaiting = false;
    }
}

// ============================================================================
// BMP280 Driver (0x76 / 0x77)
// ============================================================================
static bool probeAndInitBmp280() {
    const uint8_t candidates[2] = { BMP280_ADDR_LOW, BMP280_ADDR_HIGH };
    uint8_t foundAddr = 0x00;
    uint8_t chipId = 0x00;

    for (uint8_t addr : candidates) {
        if (i2cProbeAddr(addr) && i2cReadBytes(addr, BMP_REG_CHIP_ID, &chipId, 1)) {
            if (chipId == 0x58 || chipId == 0x56 || chipId == 0x57 || chipId == 0x60) {
                foundAddr = addr;
                break;
            }
        }
    }

    if (foundAddr == 0x00) {
        g_bmpAddr = 0x00;
        g_bmpChipId = 0x00;
        g_bmpReady = false;
        g_bmpValid = false;
        return false;
    }

    g_bmpAddr = foundAddr;
    g_bmpChipId = chipId;

    i2cWriteReg(g_bmpAddr, BMP_REG_RESET, 0xB6);
    delay(10);

    uint8_t cal[24] = {0};
    if (!i2cReadBytes(g_bmpAddr, BMP_REG_CALIB_00, cal, sizeof(cal))) {
        g_bmpReady = false;
        return false;
    }

    auto u16 = [&](size_t i) -> uint16_t {
        return static_cast<uint16_t>(cal[i]) | (static_cast<uint16_t>(cal[i + 1]) << 8);
    };
    auto s16 = [&](size_t i) -> int16_t {
        return static_cast<int16_t>(u16(i));
    };

    g_bmpCal.dig_T1 = u16(0);
    g_bmpCal.dig_T2 = s16(2);
    g_bmpCal.dig_T3 = s16(4);
    g_bmpCal.dig_P1 = u16(6);
    g_bmpCal.dig_P2 = s16(8);
    g_bmpCal.dig_P3 = s16(10);
    g_bmpCal.dig_P4 = s16(12);
    g_bmpCal.dig_P5 = s16(14);
    g_bmpCal.dig_P6 = s16(16);
    g_bmpCal.dig_P7 = s16(18);
    g_bmpCal.dig_P8 = s16(20);
    g_bmpCal.dig_P9 = s16(22);

    // Standby 125 ms, IIR filter x4; Temp x2, Press x16, Normal mode
    if (!i2cWriteReg(g_bmpAddr, BMP_REG_CONFIG, 0x48)) {
        g_bmpReady = false;
        return false;
    }
    if (!i2cWriteReg(g_bmpAddr, BMP_REG_CTRL_MEAS, 0x57)) {
        g_bmpReady = false;
        return false;
    }

    g_bmpReady = true;
    return true;
}

static bool readBmp280() {
    if (!g_bmpReady) return false;

    uint8_t raw[6] = {0};
    if (!i2cReadBytes(g_bmpAddr, BMP_REG_PRESS_MSB, raw, sizeof(raw))) {
        g_bmpReady = false;
        g_bmpValid = false;
        return false;
    }

    int32_t adc_P = (static_cast<int32_t>(raw[0]) << 12) |
                    (static_cast<int32_t>(raw[1]) << 4) |
                    (static_cast<int32_t>(raw[2]) >> 4);
    int32_t adc_T = (static_cast<int32_t>(raw[3]) << 12) |
                    (static_cast<int32_t>(raw[4]) << 4) |
                    (static_cast<int32_t>(raw[5]) >> 4);

    if (adc_P == 0x80000 || adc_T == 0x80000) {
        return false;
    }

    // Bosch BMP280 compensation formula
    int32_t var1 = ((((adc_T >> 3) - (static_cast<int32_t>(g_bmpCal.dig_T1) << 1))) *
                    static_cast<int32_t>(g_bmpCal.dig_T2)) >> 11;
    int32_t var2 = (((((adc_T >> 4) - static_cast<int32_t>(g_bmpCal.dig_T1)) *
                      ((adc_T >> 4) - static_cast<int32_t>(g_bmpCal.dig_T1))) >> 12) *
                    static_cast<int32_t>(g_bmpCal.dig_T3)) >> 14;
    int32_t t_fine = var1 + var2;
    int32_t tempX100 = (t_fine * 5 + 128) >> 8;

    int64_t pVar1 = static_cast<int64_t>(t_fine) - 128000;
    int64_t pVar2 = pVar1 * pVar1 * static_cast<int64_t>(g_bmpCal.dig_P6);
    pVar2 = pVar2 + ((pVar1 * static_cast<int64_t>(g_bmpCal.dig_P5)) << 17);
    pVar2 = pVar2 + (static_cast<int64_t>(g_bmpCal.dig_P4) << 35);
    pVar1 = ((pVar1 * pVar1 * static_cast<int64_t>(g_bmpCal.dig_P3)) >> 8) +
            ((pVar1 * static_cast<int64_t>(g_bmpCal.dig_P2)) << 12);
    pVar1 = ((static_cast<int64_t>(1) << 47) + pVar1) * static_cast<int64_t>(g_bmpCal.dig_P1) >> 33;
    if (pVar1 == 0) {
        return false;
    }

    int64_t p = 1048576 - adc_P;
    p = (((p << 31) - pVar2) * 3125) / pVar1;
    pVar1 = (static_cast<int64_t>(g_bmpCal.dig_P9) * (p >> 13) * (p >> 13)) >> 25;
    pVar2 = (static_cast<int64_t>(g_bmpCal.dig_P8) * p) >> 19;
    p = ((p + pVar1 + pVar2) >> 8) + (static_cast<int64_t>(g_bmpCal.dig_P7) << 4);

    float pressHpa = static_cast<float>(p) / 25600.0f;
    if (pressHpa < 300.0f || pressHpa > 1200.0f) {
        return false;
    }

    g_bmpTempC = static_cast<float>(tempX100) / 100.0f;
    g_bmpPressHpa = pressHpa;
    g_bmpAltM = 44330.0f * (1.0f - powf(pressHpa / 1013.25f, 0.1903f));
    g_bmpValid = true;
    return true;
}

// ============================================================================
// BH1750 Driver (0x23 / 0x5C)
// ============================================================================
static bool probeAndInitBh1750() {
    const uint8_t candidates[2] = { BH1750_ADDR_LOW, BH1750_ADDR_HIGH };
    uint8_t foundAddr = 0x00;

    for (uint8_t addr : candidates) {
        if (i2cProbeAddr(addr)) {
            foundAddr = addr;
            break;
        }
    }

    if (foundAddr == 0x00) {
        g_bhAddr = 0x00;
        g_bhReady = false;
        g_bhValid = false;
        return false;
    }

    const uint8_t pwrOn = 0x01;
    const uint8_t contHRes = 0x10; // Continuously H-Resolution Mode (1 lx)
    if (!i2cWriteCmd(foundAddr, &pwrOn, 1)) {
        g_bhReady = false;
        return false;
    }
    delay(5);
    if (!i2cWriteCmd(foundAddr, &contHRes, 1)) {
        g_bhReady = false;
        return false;
    }

    g_bhAddr = foundAddr;
    g_bhReady = true;
    return true;
}

static bool readBh1750() {
    if (!g_bhReady) return false;
    uint8_t raw[2] = {0};
    if (!i2cReadRaw(g_bhAddr, raw, sizeof(raw))) {
        g_bhReady = false;
        g_bhValid = false;
        return false;
    }
    uint16_t level = (static_cast<uint16_t>(raw[0]) << 8) | raw[1];
    g_bhLux = static_cast<float>(level) / 1.2f;
    g_bhValid = true;
    return true;
}

static void probeEnvSensors() {
    if (!g_ahtReady) probeAndInitAht20();
    if (!g_bmpReady) probeAndInitBmp280();
    if (!g_bhReady)  probeAndInitBh1750();
}

static void emitEnvPacket() {
    Serial.printf(
        "{\"type\":\"env\","
        "\"aht_ok\":%s,\"aht_t\":%.2f,\"aht_h\":%.1f,"
        "\"bmp_ok\":%s,\"bmp_addr\":\"0x%02X\",\"bmp_id\":\"0x%02X\",\"bmp_t\":%.2f,\"bmp_p\":%.2f,\"bmp_alt\":%.1f,"
        "\"bh_ok\":%s,\"bh_addr\":\"0x%02X\",\"lux\":%.1f}\n",
        (g_ahtReady && g_ahtValid) ? "true" : "false", g_ahtTempC, g_ahtHumPct,
        (g_bmpReady && g_bmpValid) ? "true" : "false", g_bmpAddr, g_bmpChipId, g_bmpTempC, g_bmpPressHpa, g_bmpAltM,
        (g_bhReady && g_bhValid) ? "true" : "false", g_bhAddr, g_bhLux
    );
}

// ============================================================================
// MPU6050 Driver (0x68 / 0x69)
// ============================================================================
static bool readRawSample(MpuSample &out) {
    uint8_t raw[14];
    if (!i2cReadBytes(g_mpuAddr, REG_ACCEL_XOUT_H, raw, sizeof(raw))) {
        return false;
    }
    auto toInt16 = [&](size_t idx) -> int16_t {
        return static_cast<int16_t>((static_cast<uint16_t>(raw[idx]) << 8) | raw[idx + 1]);
    };

    out.ax = static_cast<float>(toInt16(0)) / ACCEL_SCALE_2G;
    out.ay = static_cast<float>(toInt16(2)) / ACCEL_SCALE_2G;
    out.az = static_cast<float>(toInt16(4)) / ACCEL_SCALE_2G;
    out.tempC = static_cast<float>(toInt16(6)) / 340.0f + 36.53f;
    out.gx = static_cast<float>(toInt16(8)) / GYRO_SCALE_250DPS;
    out.gy = static_cast<float>(toInt16(10)) / GYRO_SCALE_250DPS;
    out.gz = static_cast<float>(toInt16(12)) / GYRO_SCALE_250DPS;
    return true;
}

static void enableMpuMotionInterrupt() {
    if (!g_mpuReady) return;
    i2cWriteReg(g_mpuAddr, REG_INT_ENABLE, 0x00);
    i2cWriteReg(g_mpuAddr, REG_ACCEL_CONFIG, 0x01); // +/-2g, ACCEL_HPF = 5 Hz
    i2cWriteReg(g_mpuAddr, REG_MOT_THR, 12);        // ~24 mg motion threshold
    i2cWriteReg(g_mpuAddr, REG_MOT_DUR, 3);         // 3 ms duration
    i2cWriteReg(g_mpuAddr, REG_INT_PIN_CFG, 0x20);  // Active-HIGH push-pull, latched until INT_STATUS read
    delay(30);
    i2cWriteReg(g_mpuAddr, REG_INT_ENABLE, 0x40);
    delay(20);
    uint8_t dummy = 0;
    i2cReadBytes(g_mpuAddr, REG_INT_STATUS, &dummy, 1);
}

static void disableMpuMotionInterrupt() {
    if (!g_mpuReady) return;
    i2cWriteReg(g_mpuAddr, REG_INT_ENABLE, 0x00);
    i2cWriteReg(g_mpuAddr, REG_ACCEL_CONFIG, 0x00);
    i2cWriteReg(g_mpuAddr, REG_CONFIG, 0x04);
    i2cWriteReg(g_mpuAddr, REG_SMPLRT_DIV, 0x01);
    uint8_t dummy = 0;
    i2cReadBytes(g_mpuAddr, REG_INT_STATUS, &dummy, 1);
}

static bool probeAndInitMpu() {
    const uint8_t candidates[2] = { MPU_ADDR_LOW, MPU_ADDR_HIGH };
    uint8_t foundAddr = 0x00;

    for (uint8_t addr : candidates) {
        if (i2cProbeAddr(addr)) {
            foundAddr = addr;
            break;
        }
    }

    if (foundAddr == 0x00) {
        g_mpuAddr = 0x00;
        g_whoAmI = 0x00;
        g_mpuReady = false;
        return false;
    }

    uint8_t who = 0x00;
    if (!i2cReadBytes(foundAddr, REG_WHO_AM_I, &who, 1)) {
        g_mpuAddr = foundAddr;
        g_whoAmI = 0x00;
        g_mpuReady = false;
        return false;
    }

    g_mpuAddr = foundAddr;
    g_whoAmI = who;

    if (!i2cWriteReg(g_mpuAddr, REG_PWR_MGMT_1, 0x01)) return false;
    delay(20);
    i2cWriteReg(g_mpuAddr, REG_INT_ENABLE, 0x00);
    if (!i2cWriteReg(g_mpuAddr, REG_CONFIG, 0x04)) return false;
    if (!i2cWriteReg(g_mpuAddr, REG_SMPLRT_DIV, 0x01)) return false;
    if (!i2cWriteReg(g_mpuAddr, REG_GYRO_CONFIG, 0x00)) return false;
    if (!i2cWriteReg(g_mpuAddr, REG_ACCEL_CONFIG, 0x00)) return false;
    delay(25);

    MpuSample s{};
    if (readRawSample(s)) {
        float ax = s.ax - g_cal.axOff;
        float ay = s.ay - g_cal.ayOff;
        float az = s.az - g_cal.azOff;
        g_filtAx = ax;
        g_filtAy = ay;
        g_filtAz = az;
        g_roll = atan2f(ay, az) * RAD_TO_DEG_F;
        g_pitch = atan2f(-ax, sqrtf(ay * ay + az * az)) * RAD_TO_DEG_F;
    }
    g_hasPrevSample = false;
    g_isStill = false;
    g_stillTicks = 0;
    g_biasTicks = 0;
    g_idleStartMs = millis();
    g_lastIntegrateUs = micros();
    g_mpuReady = true;
    return true;
}

static bool calibrateMpu(uint16_t samples, bool includeAccelLevel, bool saveNvs) {
    if (!g_mpuReady) return false;

    double sumAx = 0, sumAy = 0, sumAz = 0;
    double sumGx = 0, sumGy = 0, sumGz = 0;
    uint16_t okCount = 0;

    for (uint16_t i = 0; i < samples; ++i) {
        MpuSample s{};
        if (readRawSample(s)) {
            sumAx += s.ax;
            sumAy += s.ay;
            sumAz += s.az;
            sumGx += s.gx;
            sumGy += s.gy;
            sumGz += s.gz;
            ++okCount;
        }
        delay(3);
    }
    if (okCount < samples / 2) {
        return false;
    }

    g_cal.gxOff = static_cast<float>(sumGx / okCount);
    g_cal.gyOff = static_cast<float>(sumGy / okCount);
    g_cal.gzOff = static_cast<float>(sumGz / okCount);

    if (includeAccelLevel) {
        g_cal.axOff = static_cast<float>(sumAx / okCount);
        g_cal.ayOff = static_cast<float>(sumAy / okCount);
        g_cal.azOff = static_cast<float>(sumAz / okCount) - 1.0f;
        g_filtAx = 0.0f;
        g_filtAy = 0.0f;
        g_filtAz = 1.0f;
        g_roll = 0.0f;
        g_pitch = 0.0f;
        g_yaw = 0.0f;
    }

    g_cal.calibrated = true;
    if (saveNvs) {
        saveCalibrationToNvs();
    }

    g_hasPrevSample = false;
    g_isStill = true;
    g_stillTicks = 30;
    g_biasTicks = 0;
    g_idleStartMs = millis();
    g_lastIntegrateUs = micros();
    return true;
}

// ============================================================================
// Serial & Commands
// ============================================================================
static void printI2cScanEvent() {
    Serial.print("{\"event\":\"SCAN\",\"sda\":");
    Serial.print(I2C_SDA_PIN);
    Serial.print(",\"scl\":");
    Serial.print(I2C_SCL_PIN);
    Serial.print(",\"addrs\":[");

    bool first = true;
    for (uint8_t addr = 0x03; addr <= 0x77; ++addr) {
        if (i2cProbeAddr(addr)) {
            if (!first) Serial.print(',');
            Serial.printf("\"0x%02X\"", addr);
            first = false;
        }
    }
    Serial.println("]}");
}

static void printMpuStatusError(const char *statusCode, const char *msg) {
    Serial.printf(
        "{\"type\":\"mpu\",\"status\":\"%s\",\"addr\":\"0x%02X\",\"whoami\":\"0x%02X\",\"sda\":%d,\"scl\":%d,\"msg\":\"%s\"}\n",
        statusCode, g_mpuAddr, g_whoAmI, I2C_SDA_PIN, I2C_SCL_PIN, msg
    );
}

static void handleCommand(String cmd) {
    cmd.trim();
    if (cmd.isEmpty()) return;

    if (cmd.equalsIgnoreCase("PING")) {
        Serial.printf(
            "{\"event\":\"PONG\",\"fw\":\"esp32s3-multisensor-2.0\",\"sleep\":%s,\"sleep_mode\":\"%s\",\"sleep_delay\":%lu,\"mode\":\"%s\"}\n",
            g_autoSleepEnabled ? "true" : "false",
            g_sleepMode == SleepMode::DEEP ? "DEEP" : "LIGHT",
            static_cast<unsigned long>(g_idleSleepMs / 1000),
            g_viewMode == ActiveViewMode::ENV ? "ENV" : "MPU"
        );
    } else if (cmd.equalsIgnoreCase("SCAN")) {
        printI2cScanEvent();
        if (!g_mpuReady && probeAndInitMpu() && !g_cal.savedInNvs) {
            calibrateMpu(200, false, false);
        }
        probeEnvSensors();
    } else if (cmd.equalsIgnoreCase("CAL")) {
        if (calibrateMpu(350, true, true)) {
            Serial.printf(
                "{\"event\":\"CAL_DONE\",\"nvs\":true,\"axOff\":%.4f,\"ayOff\":%.4f,\"azOff\":%.4f,"
                "\"gxOff\":%.3f,\"gyOff\":%.3f,\"gzOff\":%.3f}\n",
                g_cal.axOff, g_cal.ayOff, g_cal.azOff,
                g_cal.gxOff, g_cal.gyOff, g_cal.gzOff
            );
        } else {
            Serial.println("{\"event\":\"CAL_ERR\",\"msg\":\"MPU6050 not connected\"}");
        }
    } else if (cmd.equalsIgnoreCase("CLEAR_CAL")) {
        clearCalibrationInNvs();
        if (g_mpuReady) {
            calibrateMpu(200, false, false);
        }
        Serial.println("{\"event\":\"CAL_CLEARED\"}");
    } else if (cmd.equalsIgnoreCase("RESET_YAW")) {
        g_yaw = 0.0f;
        g_idleStartMs = millis();
        Serial.println("{\"event\":\"YAW_RESET\"}");
    } else if (cmd.equalsIgnoreCase("WAKE")) {
        g_idleStartMs = millis();
        Serial.println("{\"event\":\"AWAKE\"}");
    } else if (cmd.equalsIgnoreCase("MODE=MPU")) {
        g_viewMode = ActiveViewMode::MPU;
        g_idleStartMs = millis();
        Serial.println("{\"event\":\"MODE_SET\",\"mode\":\"MPU\"}");
    } else if (cmd.equalsIgnoreCase("MODE=ENV")) {
        g_viewMode = ActiveViewMode::ENV;
        g_idleStartMs = millis();
        Serial.println("{\"event\":\"MODE_SET\",\"mode\":\"ENV\"}");
    } else if (cmd.equalsIgnoreCase("SLEEP=ON")) {
        g_autoSleepEnabled = true;
        g_idleStartMs = millis();
        Serial.println("{\"event\":\"SLEEP_CFG\",\"enabled\":true}");
    } else if (cmd.equalsIgnoreCase("SLEEP=OFF")) {
        g_autoSleepEnabled = false;
        Serial.println("{\"event\":\"SLEEP_CFG\",\"enabled\":false}");
    } else if (cmd.equalsIgnoreCase("SLEEP_MODE=LIGHT")) {
        g_sleepMode = SleepMode::LIGHT;
        g_idleStartMs = millis();
        Serial.println("{\"event\":\"SLEEP_CFG\",\"mode\":\"LIGHT\"}");
    } else if (cmd.equalsIgnoreCase("SLEEP_MODE=DEEP")) {
        g_sleepMode = SleepMode::DEEP;
        g_idleStartMs = millis();
        Serial.println("{\"event\":\"SLEEP_CFG\",\"mode\":\"DEEP\"}");
    } else if (cmd.startsWith("SLEEP_DELAY=") || cmd.startsWith("sleep_delay=")) {
        int sec = cmd.substring(12).toInt();
        if (sec >= 2 && sec <= 120) {
            g_idleSleepMs = static_cast<uint32_t>(sec) * 1000UL;
            g_idleStartMs = millis();
            Serial.printf("{\"event\":\"SLEEP_CFG\",\"delay_s\":%d}\n", sec);
        }
    } else if (cmd.startsWith("RATE=") || cmd.startsWith("rate=")) {
        int hz = cmd.substring(5).toInt();
        if (hz >= 1 && hz <= 100) {
            g_sampleIntervalMs = static_cast<uint32_t>(1000 / hz);
            Serial.printf("{\"event\":\"RATE_SET\",\"hz\":%d}\n", hz);
        }
    }
}

static void pollSerialCommands() {
    while (Serial.available() > 0) {
        char c = static_cast<char>(Serial.read());
        if (c == '\n' || c == '\r') {
            if (!g_cmdBuffer.isEmpty()) {
                handleCommand(g_cmdBuffer);
                g_cmdBuffer = "";
            }
        } else if (g_cmdBuffer.length() < 64) {
            g_cmdBuffer += c;
        }
    }
}

// ============================================================================
// Sleep & Motion Detection
// ============================================================================
static void enterSleepUntilMotion() {
    const char *modeStr = (g_sleepMode == SleepMode::DEEP) ? "DEEP" : "LIGHT";
    Serial.printf(
        "{\"status\":\"SLEEPING\",\"mode\":\"%s\",\"idle_s\":%lu,\"int_pin\":%d,"
        "\"msg\":\"Khong chuyen dong -> ESP32-S3 vao che do ngu (%s)\"}\n",
        modeStr,
        static_cast<unsigned long>(g_idleSleepMs / 1000),
        MPU_INT_PIN,
        modeStr
    );
    Serial.flush();
    delay(60);

    float refAx = g_filtAx;
    float refAy = g_filtAy;
    float refAz = g_filtAz;

    enableMpuMotionInterrupt();

    if (g_sleepMode == SleepMode::DEEP) {
        const gpio_num_t intGpio = static_cast<gpio_num_t>(MPU_INT_PIN);
        rtc_gpio_init(intGpio);
        rtc_gpio_set_direction(intGpio, RTC_GPIO_MODE_INPUT_ONLY);
        rtc_gpio_pullup_dis(intGpio);
        rtc_gpio_pulldown_en(intGpio);
        delay(5);

        if (rtc_gpio_get_level(intGpio) == 0) {
            esp_sleep_enable_ext0_wakeup(intGpio, 1);
            Serial.flush();
            delay(50);
            Serial.end();
            esp_deep_sleep_start();
        }
        rtc_gpio_deinit(intGpio);
    }

    setCpuFrequencyMhz(80);
    pinMode(MPU_INT_PIN, INPUT_PULLDOWN);
    g_motionIrq = false;
    attachInterrupt(digitalPinToInterrupt(MPU_INT_PIN), onMotionPinIsr, RISING);

    const char *wakeCause = "MOTION_I2C";
    while (g_autoSleepEnabled) {
        delay(50);

        if (g_motionIrq) {
            wakeCause = "GPIO4_INT";
            break;
        }

        if (Serial.available() > 0) {
            wakeCause = "SERIAL_CMD";
            break;
        }

        MpuSample s{};
        if (!readRawSample(s)) {
            wakeCause = "I2C_DISCONNECT";
            break;
        }

        float ax = s.ax - g_cal.axOff;
        float ay = s.ay - g_cal.ayOff;
        float az = s.az - g_cal.azOff;
        float gx = s.gx - g_cal.gxOff;
        float gy = s.gy - g_cal.gyOff;
        float gz = s.gz - g_cal.gzOff;

        float deltaA = fabsf(ax - refAx) + fabsf(ay - refAy) + fabsf(az - refAz);
        float maxG = fmaxf(fabsf(gx), fmaxf(fabsf(gy), fabsf(gz)));
        if (deltaA > 0.06f || maxG > 3.5f) {
            wakeCause = "MOTION_I2C";
            break;
        }
    }

    detachInterrupt(digitalPinToInterrupt(MPU_INT_PIN));
    setCpuFrequencyMhz(240);
    disableMpuMotionInterrupt();

    g_hasPrevSample = false;
    g_isStill = false;
    g_stillTicks = 0;
    g_biasTicks = 0;
    g_idleStartMs = millis();
    g_lastIntegrateUs = micros();
    g_lastFilterMs = millis();
    g_lastOutputMs = millis();

    Serial.printf(
        "{\"event\":\"WAKEUP\",\"cause\":\"%s\",\"msg\":\"Phat hien chuyen dong -> ESP32-S3 hoat dong tro lai\"}\n",
        wakeCause
    );
    pollSerialCommands();
}

static bool updateMpuFilter() {
    MpuSample s{};
    if (!readRawSample(s)) {
        return false;
    }

    float ax = s.ax - g_cal.axOff;
    float ay = s.ay - g_cal.ayOff;
    float az = s.az - g_cal.azOff;
    float gx = s.gx - g_cal.gxOff;
    float gy = s.gy - g_cal.gyOff;
    float gz = s.gz - g_cal.gzOff;

    uint32_t nowUs = micros();
    float dt = static_cast<float>(nowUs - g_lastIntegrateUs) * 1e-6f;
    g_lastIntegrateUs = nowUs;
    if (dt <= 0.0f || dt > 0.2f) {
        dt = static_cast<float>(FILTER_INTERVAL_MS) * 1e-3f;
    }

    float accDelta = 0.0f;
    float gyroDelta = 0.0f;
    if (g_hasPrevSample) {
        accDelta = fabsf(ax - g_prevAx) + fabsf(ay - g_prevAy) + fabsf(az - g_prevAz);
        gyroDelta = fabsf(s.gx - g_prevRawGx) + fabsf(s.gy - g_prevRawGy) + fabsf(s.gz - g_prevRawGz);
    }
    g_prevAx = ax;
    g_prevAy = ay;
    g_prevAz = az;
    g_prevRawGx = s.gx;
    g_prevRawGy = s.gy;
    g_prevRawGz = s.gz;
    g_hasPrevSample = true;

    bool smallGyro = (fabsf(gx) < 2.2f && fabsf(gy) < 2.2f && fabsf(gz) < 2.2f);
    bool flatDelta = (accDelta < 0.045f && gyroDelta < 1.8f);

    if (smallGyro && flatDelta) {
        if (g_stillTicks < 200) ++g_stillTicks;
    } else {
        g_stillTicks = 0;
    }

    bool steadyBiasOnly = (fabsf(gx) < 10.0f && fabsf(gy) < 10.0f && fabsf(gz) < 10.0f) &&
                          (accDelta < 0.03f && gyroDelta < 1.2f);
    if (steadyBiasOnly) {
        if (g_biasTicks < 200) ++g_biasTicks;
    } else {
        g_biasTicks = 0;
    }

    if (g_stillTicks >= 25 || g_biasTicks >= 55) {
        if (!g_isStill) {
            g_idleStartMs = millis();
        }
        g_isStill = true;
    } else if (!flatDelta || fabsf(gx) >= 2.8f || fabsf(gy) >= 2.8f || fabsf(gz) >= 2.8f) {
        if (g_biasTicks == 0) {
            g_isStill = false;
            g_idleStartMs = millis();
        }
    }

    if (g_isStill) {
        const float biasAlpha = (g_biasTicks >= 55 && !smallGyro) ? 0.05f : 0.015f;
        g_cal.gxOff += biasAlpha * gx;
        g_cal.gyOff += biasAlpha * gy;
        g_cal.gzOff += biasAlpha * gz;

        g_outGx = 0.0f;
        g_outGy = 0.0f;
        g_outGz = 0.0f;

        g_filtAx += 0.10f * (ax - g_filtAx);
        g_filtAy += 0.10f * (ay - g_filtAy);
        g_filtAz += 0.10f * (az - g_filtAz);

        float accelRoll = atan2f(g_filtAy, g_filtAz) * RAD_TO_DEG_F;
        float accelPitch = atan2f(-g_filtAx, sqrtf(g_filtAy * g_filtAy + g_filtAz * g_filtAz)) * RAD_TO_DEG_F;
        g_roll += 0.05f * (accelRoll - g_roll);
        g_pitch += 0.05f * (accelPitch - g_pitch);
    } else {
        g_idleStartMs = millis();

        auto deadband = [](float v, float thresh) -> float {
            return (fabsf(v) < thresh) ? 0.0f : v;
        };
        g_outGx = deadband(gx, 0.4f);
        g_outGy = deadband(gy, 0.4f);
        g_outGz = deadband(gz, 0.4f);

        g_filtAx += 0.35f * (ax - g_filtAx);
        g_filtAy += 0.35f * (ay - g_filtAy);
        g_filtAz += 0.35f * (az - g_filtAz);

        float accelRoll = atan2f(g_filtAy, g_filtAz) * RAD_TO_DEG_F;
        float accelPitch = atan2f(-g_filtAx, sqrtf(g_filtAy * g_filtAy + g_filtAz * g_filtAz)) * RAD_TO_DEG_F;

        g_roll = 0.96f * (g_roll + g_outGx * dt) + 0.04f * accelRoll;
        g_pitch = 0.96f * (g_pitch + g_outGy * dt) + 0.04f * accelPitch;
        g_yaw += g_outGz * dt;
    }

    g_lastTempC = s.tempC;
    return true;
}

// ============================================================================
// Setup & Main Loop
// ============================================================================
void setup() {
    Serial.begin(115200);
    uint32_t waitStart = millis();
    while (!Serial && (millis() - waitStart < 1500)) {
        delay(10);
    }

    esp_sleep_wakeup_cause_t wakeCause = esp_sleep_get_wakeup_cause();
    if (wakeCause == ESP_SLEEP_WAKEUP_EXT0) {
        rtc_gpio_deinit(static_cast<gpio_num_t>(MPU_INT_PIN));
    }

    loadCalibrationFromNvs();

    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
    Wire.setClock(400000);
    Wire.setTimeOut(50);

    Serial.printf("{\"event\":\"BOOT\",\"sda\":%d,\"scl\":%d,\"int_pin\":%d,\"nvs_cal\":%s}\n",
                  I2C_SDA_PIN, I2C_SCL_PIN, MPU_INT_PIN, g_cal.savedInNvs ? "true" : "false");
    printI2cScanEvent();

    if (probeAndInitMpu()) {
        if (!g_cal.savedInNvs) {
            calibrateMpu(200, false, false);
        }
        if (wakeCause == ESP_SLEEP_WAKEUP_EXT0) {
            Serial.println("{\"event\":\"WAKEUP\",\"cause\":\"DEEP_EXT0\",\"msg\":\"Thuc day tu Deep Sleep nho chuyen dong\"}");
        } else {
            Serial.printf("{\"event\":\"INIT_OK\",\"addr\":\"0x%02X\",\"whoami\":\"0x%02X\",\"nvs_cal\":%s}\n",
                          g_mpuAddr, g_whoAmI, g_cal.savedInNvs ? "true" : "false");
        }
    } else {
        printMpuStatusError("ERR_NO_DEVICE", "Khong tim thay MPU6050 tren I2C (kiem tra SDA/SCL/VCC/GND)");
    }

    probeEnvSensors();

    uint32_t now = millis();
    g_lastFilterMs = now;
    g_lastOutputMs = now;
    g_lastMpuRetryMs = now;
    g_lastFpsMs = now;
    g_lastEnvOutputMs = now;
    g_lastEnvProbeMs = now;
    g_idleStartMs = now;
    g_lastIntegrateUs = micros();
}

void loop() {
    pollSerialCommands();

    uint32_t nowMs = millis();

    // 1. Poll Environment & Light sensors (AHT20 + BMP280 + BH1750) non-blocking
    if (nowMs - g_lastEnvProbeMs >= 2000) {
        g_lastEnvProbeMs = nowMs;
        probeEnvSensors();
    }
    pollAht20NonBlocking(nowMs);
    if (nowMs - g_lastEnvOutputMs >= ENV_OUTPUT_MS) {
        g_lastEnvOutputMs = nowMs;
        readBmp280();
        readBh1750();
        emitEnvPacket();
    }

    // 2. Poll MPU6050
    if (!g_mpuReady) {
        if (nowMs - g_lastMpuRetryMs >= 800) {
            g_lastMpuRetryMs = nowMs;
            if (probeAndInitMpu()) {
                if (!g_cal.savedInNvs) {
                    calibrateMpu(200, false, false);
                }
                Serial.printf("{\"event\":\"RECONNECTED\",\"addr\":\"0x%02X\",\"whoami\":\"0x%02X\"}\n",
                              g_mpuAddr, g_whoAmI);
            } else {
                printMpuStatusError("ERR_NO_DEVICE", "Khong tim thay MPU6050 tren I2C (kiem tra day dau)");
            }
        }
        return;
    }

    if (nowMs - g_lastFilterMs >= FILTER_INTERVAL_MS) {
        g_lastFilterMs = nowMs;
        if (!updateMpuFilter()) {
            g_mpuReady = false;
            printMpuStatusError("ERR_I2C_READ", "Mat ket noi doc du lieu I2C tu MPU6050");
            return;
        }
    }

    // Only auto-sleep while in MPU view mode so Environment/Light monitoring doesn't stop
    if (g_viewMode == ActiveViewMode::MPU && g_autoSleepEnabled && g_isStill &&
        (nowMs - g_idleStartMs >= g_idleSleepMs)) {
        enterSleepUntilMotion();
        return;
    }

    if (nowMs - g_lastOutputMs < g_sampleIntervalMs) {
        return;
    }
    g_lastOutputMs = nowMs;

    ++g_frameCounter;
    if (nowMs - g_lastFpsMs >= 1000) {
        g_measuredHz = g_frameCounter;
        g_frameCounter = 0;
        g_lastFpsMs = nowMs;
    }

    const char *statusStr = (g_whoAmI == 0x68) ? "OK" : "WARN_WHOAMI";
    uint32_t idleSec = g_isStill ? ((nowMs - g_idleStartMs) / 1000) : 0;

    Serial.printf(
        "{\"type\":\"mpu\",\"status\":\"%s\",\"addr\":\"0x%02X\",\"whoami\":\"0x%02X\",\"cal\":%s,\"still\":%s,\"idle_s\":%lu,"
        "\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,"
        "\"gx\":%.2f,\"gy\":%.2f,\"gz\":%.2f,"
        "\"temp\":%.1f,\"roll\":%.2f,\"pitch\":%.2f,\"yaw\":%.2f,\"hz\":%lu}\n",
        statusStr,
        g_mpuAddr,
        g_whoAmI,
        g_cal.calibrated ? "true" : "false",
        g_isStill ? "true" : "false",
        static_cast<unsigned long>(idleSec),
        g_filtAx, g_filtAy, g_filtAz,
        g_outGx, g_outGy, g_outGz,
        g_lastTempC, g_roll, g_pitch, g_yaw,
        static_cast<unsigned long>(g_measuredHz)
    );
}
