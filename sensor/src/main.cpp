/**
 * ============================================================================
 * FIRMWARE TỔNG HỢP TEST LẦN LƯỢT 3 NHÓM CẢM BIẾN TRÊN ESP32 (ALL-IN-ONE)
 *
 * Hỗ trợ chuyển đổi trực tiếp giữa 3 nhóm cảm biến ngay trên Serial Monitor
 * (Baudrate = 921600):
 *   - Nhấn '1' : Chuyển sang test NHÓM 1 (BH1750 + AHT20 + BMP280 + Độ ẩm đất)
 *   - Nhấn '2' : Chuyển sang test NHÓM 2 (MPU-6050 + Chế độ Hiệu chỉnh 'c')
 *   - Nhấn '3' : Chuyển sang test NHÓM 3 (Micro INMP441 VU Meter + Thu âm 'REC:5')
 *   - Nhấn 's' : Quét toàn bộ thiết bị trên bus I2C (I2C Scanner)
 *   - Nhấn 'c' : Hiệu chỉnh tự động MPU-6050 (1000 mẫu, lưu vào Flash NVS)
 *   - Nhấn 'd' / 'w' : Hiệu chuẩn mốc Đất Khô / Đất Ướt cho cảm biến độ ẩm đất
 *   - Lệnh "REC:5"   : Thu âm 5 giây từ INMP441 truyền về script Python STT
 * ============================================================================
 */

#include <Arduino.h>
#include <Wire.h>
#include <Preferences.h>
#include <driver/i2s.h>
#include <math.h>

// ======================= SƠ ĐỒ CHÂN (KHỚP sensor_all_groups.fzz) ======
static constexpr int PIN_I2C_SDA  = 21;
static constexpr int PIN_I2C_SCL  = 22;
static constexpr int PIN_SOIL_ADC = 34;
static constexpr int PIN_MPU_INT  = 19;
static constexpr int PIN_I2S_SCK  = 14;
static constexpr int PIN_I2S_WS   = 15;
static constexpr int PIN_I2S_SD   = 32;

// ======================= CHẾ ĐỘ TEST HIỆN TẠI ========================
enum TestGroup {
    GROUP_1_ENV = 1,
    GROUP_2_MPU = 2,
    GROUP_3_MIC = 3
};

TestGroup activeGroup = GROUP_1_ENV;
Preferences prefs;

// ----------------------- BIẾN NHÓM 1 (ENV) ---------------------------
uint8_t bh1750Addr = 0x23;
uint8_t bmp280Addr = 0x77;
bool bh1750Ok = false, aht20Ok = false, bmp280Ok = false;
int soilDryMv = 2850, soilWetMv = 1250;

struct BMP280Cal {
    uint16_t T1; int16_t T2, T3;
    uint16_t P1; int16_t P2, P3, P4, P5, P6, P7, P8, P9;
    int32_t t_fine;
} bmpCal;

// ----------------------- BIẾN NHÓM 2 (MPU-6050) ----------------------
uint8_t mpuAddr = 0x68;
bool mpuOk = false;
volatile uint32_t mpuIntCount = 0;
void IRAM_ATTR onMpuInt() { mpuIntCount++; }

struct MpuCalib {
    bool valid = false;
    float ax = 0, ay = 0, az = 0;
    float gx = 0, gy = 0, gz = 0;
    float r0 = 0, p0 = 0;
} mpuCal;

float compRoll = 0.0f, compPitch = 0.0f;
uint32_t lastMpuUs = 0;

// ----------------------- BIẾN NHÓM 3 (INMP441) -----------------------
static constexpr uint32_t MIC_SAMPLE_RATE = 16000;
static constexpr size_t   MIC_BUF_LEN     = 512;
bool i2sOk = false;
int  micGain = 4;
float micDcOffset = 0.0f;
static int32_t i2sRaw[MIC_BUF_LEN];
static int16_t pcm16[MIC_BUF_LEN];

// ======================= TIỆN ÍCH I2C ================================
bool i2cPing(uint8_t addr) {
    Wire.beginTransmission(addr);
    return (Wire.endTransmission() == 0);
}

void i2cWrite8(uint8_t addr, uint8_t reg, uint8_t val) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.write(val);
    Wire.endTransmission();
}

uint8_t i2cRead8(uint8_t addr, uint8_t reg) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.endTransmission(false);
    Wire.requestFrom(addr, (uint8_t)1);
    return Wire.available() ? Wire.read() : 0;
}

uint16_t i2cRead16LE(uint8_t addr, uint8_t reg) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.endTransmission(false);
    Wire.requestFrom(addr, (uint8_t)2);
    if (Wire.available() < 2) return 0;
    uint8_t l = Wire.read();
    uint8_t h = Wire.read();
    return (uint16_t(h) << 8) | l;
}

void scanI2C() {
    Serial.println(F("\n================ QUÉT BUS I2C (SDA=21, SCL=22) ================"));
    int found = 0;
    for (uint8_t a = 1; a < 127; a++) {
        if (i2cPing(a)) {
            Serial.printf("  [+] Địa chỉ 0x%02X : ", a);
            if (a == 0x23 || a == 0x5C) Serial.println(F("BH1750 (Ánh sáng)"));
            else if (a == 0x38)         Serial.println(F("AHT20 (Nhiệt độ & Độ ẩm)"));
            else if (a == 0x76 || a == 0x77) Serial.println(F("BMP280 (Áp suất khí quyển)"));
            else if (a == 0x68 || a == 0x69) Serial.println(F("MPU-6050 (Gia tốc & Gyro)"));
            else Serial.println(F("Thiết bị I2C khác"));
            found++;
        }
    }
    Serial.printf("  => Tổng cộng: %d thiết bị I2C.\n", found);
    Serial.println(F("===============================================================\n"));
}

// ======================= KHỞI TẠO & ĐỌC NHÓM 1 =======================
void initGroup1() {
    pinMode(PIN_SOIL_ADC, INPUT);
    analogReadResolution(12);
    analogSetPinAttenuation(PIN_SOIL_ADC, ADC_11db);

    prefs.begin("soil_cal", true);
    soilDryMv = prefs.getInt("dry_mv", 2850);
    soilWetMv = prefs.getInt("wet_mv", 1250);
    prefs.end();

    // BH1750
    if (i2cPing(0x23)) { bh1750Addr = 0x23; bh1750Ok = true; }
    else if (i2cPing(0x5C)) { bh1750Addr = 0x5C; bh1750Ok = true; }
    if (bh1750Ok) {
        Wire.beginTransmission(bh1750Addr); Wire.write(0x01); Wire.endTransmission(); delay(10);
        Wire.beginTransmission(bh1750Addr); Wire.write(0x10); Wire.endTransmission();
    }

    // AHT20
    aht20Ok = i2cPing(0x38);
    if (aht20Ok) {
        Wire.beginTransmission(0x38); Wire.write(0xBE); Wire.write(0x08); Wire.write(0x00); Wire.endTransmission();
        delay(15);
    }

    // BMP280
    if (i2cPing(0x77)) { bmp280Addr = 0x77; bmp280Ok = true; }
    else if (i2cPing(0x76)) { bmp280Addr = 0x76; bmp280Ok = true; }
    if (bmp280Ok) {
        bmpCal.T1 = i2cRead16LE(bmp280Addr, 0x88);
        bmpCal.T2 = (int16_t)i2cRead16LE(bmp280Addr, 0x8A);
        bmpCal.T3 = (int16_t)i2cRead16LE(bmp280Addr, 0x8C);
        bmpCal.P1 = i2cRead16LE(bmp280Addr, 0x8E);
        bmpCal.P2 = (int16_t)i2cRead16LE(bmp280Addr, 0x90);
        bmpCal.P3 = (int16_t)i2cRead16LE(bmp280Addr, 0x92);
        bmpCal.P4 = (int16_t)i2cRead16LE(bmp280Addr, 0x94);
        bmpCal.P5 = (int16_t)i2cRead16LE(bmp280Addr, 0x96);
        bmpCal.P6 = (int16_t)i2cRead16LE(bmp280Addr, 0x98);
        bmpCal.P7 = (int16_t)i2cRead16LE(bmp280Addr, 0x9A);
        bmpCal.P8 = (int16_t)i2cRead16LE(bmp280Addr, 0x9C);
        bmpCal.P9 = (int16_t)i2cRead16LE(bmp280Addr, 0x9E);
        i2cWrite8(bmp280Addr, 0xF5, (0x03 << 5) | (0x02 << 2));
        i2cWrite8(bmp280Addr, 0xF4, (0x02 << 5) | (0x04 << 2) | 0x03);
    }
}

void stepGroup1() {
    static uint32_t lastMs = 0;
    if (millis() - lastMs < 1500) return;
    lastMs = millis();

    // BH1750
    float lux = NAN;
    if (bh1750Ok && Wire.requestFrom(bh1750Addr, (uint8_t)2) == 2) {
        uint16_t raw = (uint16_t(Wire.read()) << 8) | Wire.read();
        lux = raw / 1.2f;
    }

    // AHT20
    float ahtT = NAN, ahtH = NAN;
    if (aht20Ok) {
        Wire.beginTransmission(0x38); Wire.write(0xAC); Wire.write(0x33); Wire.write(0x00); Wire.endTransmission();
        delay(80);
        if (Wire.requestFrom((uint8_t)0x38, (uint8_t)6) == 6) {
            uint8_t b[6];
            for (int i = 0; i < 6; i++) b[i] = Wire.read();
            if ((b[0] & 0x80) == 0) {
                uint32_t rh = (uint32_t(b[1]) << 12) | (uint32_t(b[2]) << 4) | (b[3] >> 4);
                uint32_t rt = (uint32_t(b[3] & 0x0F) << 16) | (uint32_t(b[4]) << 8) | b[5];
                ahtH = rh * 100.0f / 1048576.0f;
                ahtT = (rt * 200.0f / 1048576.0f) - 50.0f;
            }
        }
    }

    // BMP280
    float bmpP = NAN, bmpAlt = NAN;
    if (bmp280Ok) {
        Wire.beginTransmission(bmp280Addr); Wire.write(0xF7); Wire.endTransmission(false);
        if (Wire.requestFrom(bmp280Addr, (uint8_t)6) == 6) {
            uint8_t b[6];
            for (int i = 0; i < 6; i++) b[i] = Wire.read();
            int32_t adc_P = (int32_t(b[0]) << 12) | (int32_t(b[1]) << 4) | (b[2] >> 4);
            int32_t adc_T = (int32_t(b[3]) << 12) | (int32_t(b[4]) << 4) | (b[5] >> 4);
            int32_t v1 = ((((adc_T >> 3) - ((int32_t)bmpCal.T1 << 1))) * ((int32_t)bmpCal.T2)) >> 11;
            int32_t v2 = (((((adc_T >> 4) - ((int32_t)bmpCal.T1)) * ((adc_T >> 4) - ((int32_t)bmpCal.T1))) >> 12) * ((int32_t)bmpCal.T3)) >> 14;
            bmpCal.t_fine = v1 + v2;
            int64_t p1 = ((int64_t)bmpCal.t_fine) - 128000;
            int64_t p2 = p1 * p1 * (int64_t)bmpCal.P6 + ((p1 * (int64_t)bmpCal.P5) << 17) + (((int64_t)bmpCal.P4) << 35);
            p1 = ((p1 * p1 * (int64_t)bmpCal.P3) >> 8) + ((p1 * (int64_t)bmpCal.P2) << 12);
            p1 = (((((int64_t)1) << 47) + p1)) * ((int64_t)bmpCal.P1) >> 33;
            if (p1 != 0) {
                int64_t p = 1048576 - adc_P;
                p = (((p << 31) - p2) * 3125) / p1;
                p1 = (((int64_t)bmpCal.P9) * (p >> 13) * (p >> 13)) >> 25;
                p2 = (((int64_t)bmpCal.P8) * p) >> 19;
                p = ((p + p1 + p2) >> 8) + (((int64_t)bmpCal.P7) << 4);
                bmpP = (float)p / 25600.0f;
                bmpAlt = 44330.0f * (1.0f - powf(bmpP / 1013.25f, 0.1903f));
            }
        }
    }

    // Soil Moisture
    long sumMv = 0;
    for (int i = 0; i < 16; i++) sumMv += analogReadMilliVolts(PIN_SOIL_ADC);
    int mv = sumMv / 16;
    float soilPct = constrain((float)(soilDryMv - mv) * 100.0f / (float)(soilDryMv - soilWetMv), 0.0f, 100.0f);

    Serial.printf("[NHÓM 1] Ánh sáng: %6.1f Lux | AHT20: %5.2f°C, %4.1f%%RH | BMP280: %7.2f hPa (~%.1fm) | Đất: %5.1f%% (%dmV)\n",
                  lux, ahtT, ahtH, bmpP, bmpAlt, soilPct, mv);
}

// ======================= KHỞI TẠO & ĐỌC NHÓM 2 (MPU-6050) ============
bool readMpuRaw(int16_t &ax, int16_t &ay, int16_t &az, int16_t &gx, int16_t &gy, int16_t &gz) {
    Wire.beginTransmission(mpuAddr);
    Wire.write(0x3B);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom(mpuAddr, (uint8_t)14) != 14) return false;
    ax = (int16_t(Wire.read()) << 8) | Wire.read();
    ay = (int16_t(Wire.read()) << 8) | Wire.read();
    az = (int16_t(Wire.read()) << 8) | Wire.read();
    Wire.read(); Wire.read(); // Bỏ qua 2 byte temp
    gx = (int16_t(Wire.read()) << 8) | Wire.read();
    gy = (int16_t(Wire.read()) << 8) | Wire.read();
    gz = (int16_t(Wire.read()) << 8) | Wire.read();
    return true;
}

void initGroup2() {
    pinMode(PIN_MPU_INT, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_MPU_INT), onMpuInt, RISING);

    if (i2cPing(0x68)) { mpuAddr = 0x68; mpuOk = true; }
    else if (i2cPing(0x69)) { mpuAddr = 0x69; mpuOk = true; }

    if (mpuOk) {
        i2cWrite8(mpuAddr, 0x6B, 0x01); delay(30);
        i2cWrite8(mpuAddr, 0x1A, 0x03);
        i2cWrite8(mpuAddr, 0x19, 4);
        i2cWrite8(mpuAddr, 0x1B, 0x00);
        i2cWrite8(mpuAddr, 0x1C, 0x00);
        i2cWrite8(mpuAddr, 0x37, 0x10);
        i2cWrite8(mpuAddr, 0x38, 0x01);
    }

    prefs.begin("mpu_cal", true);
    mpuCal.valid = prefs.getBool("valid", false);
    if (mpuCal.valid) {
        mpuCal.ax = prefs.getFloat("ax", 0);
        mpuCal.ay = prefs.getFloat("ay", 0);
        mpuCal.az = prefs.getFloat("az", 0);
        mpuCal.gx = prefs.getFloat("gx", 0);
        mpuCal.gy = prefs.getFloat("gy", 0);
        mpuCal.gz = prefs.getFloat("gz", 0);
        mpuCal.r0 = prefs.getFloat("r0", 0);
        mpuCal.p0 = prefs.getFloat("p0", 0);
    }
    prefs.end();
    lastMpuUs = micros();
}

void calibrateMPU6050() {
    if (!mpuOk) {
        Serial.println(F("[ERR] Không tìm thấy MPU-6050 để hiệu chỉnh!"));
        return;
    }
    Serial.println(F("\n>>> BẮT ĐẦU HIỆU CHỈNH MPU-6050: Đặt mạch nằm phẳng & giữ yên trong 3 giây..."));
    delay(2000);
    double sAx = 0, sAy = 0, sAz = 0, sGx = 0, sGy = 0, sGz = 0;
    int cnt = 0;
    int16_t ax, ay, az, gx, gy, gz;
    for (int i = 0; i < 1000; i++) {
        if (readMpuRaw(ax, ay, az, gx, gy, gz)) {
            sAx += ax; sAy += ay; sAz += (az - 16384);
            sGx += gx; sGy += gy; sGz += gz;
            cnt++;
        }
        delay(2);
    }
    if (cnt > 0) {
        mpuCal.ax = sAx / cnt; mpuCal.ay = sAy / cnt; mpuCal.az = sAz / cnt;
        mpuCal.gx = sGx / cnt; mpuCal.gy = sGy / cnt; mpuCal.gz = sGz / cnt;
        mpuCal.r0 = 0; mpuCal.p0 = 0; mpuCal.valid = true;

        prefs.begin("mpu_cal", false);
        prefs.putBool("valid", true);
        prefs.putFloat("ax", mpuCal.ax); prefs.putFloat("ay", mpuCal.ay); prefs.putFloat("az", mpuCal.az);
        prefs.putFloat("gx", mpuCal.gx); prefs.putFloat("gy", mpuCal.gy); prefs.putFloat("gz", mpuCal.gz);
        prefs.putFloat("r0", 0); prefs.putFloat("p0", 0);
        prefs.end();
        Serial.printf("[OK] Hiệu chỉnh MPU-6050 thành công! Offset Acc=(%.1f, %.1f, %.1f) Gyro=(%.1f, %.1f, %.1f)\n\n",
                      mpuCal.ax, mpuCal.ay, mpuCal.az, mpuCal.gx, mpuCal.gy, mpuCal.gz);
    }
    lastMpuUs = micros();
}

void stepGroup2() {
    if (!mpuOk) return;
    int16_t rawAx, rawAy, rawAz, rawGx, rawGy, rawGz;
    if (!readMpuRaw(rawAx, rawAy, rawAz, rawGx, rawGy, rawGz)) return;

    uint32_t nowUs = micros();
    float dt = (nowUs - lastMpuUs) * 1e-6f;
    if (dt <= 0 || dt > 0.5f) dt = 0.005f;
    lastMpuUs = nowUs;

    float ax = (rawAx - mpuCal.ax) / 16384.0f;
    float ay = (rawAy - mpuCal.ay) / 16384.0f;
    float az = (rawAz - mpuCal.az) / 16384.0f;
    float gx = (rawGx - mpuCal.gx) / 131.0f;
    float gy = (rawGy - mpuCal.gy) / 131.0f;
    float gz = (rawGz - mpuCal.gz) / 131.0f;

    float accRoll  = atan2f(ay, sqrtf(ax * ax + az * az)) * 180.0f / PI;
    float accPitch = atan2f(-ax, sqrtf(ay * ay + az * az)) * 180.0f / PI;
    compRoll  = 0.98f * (compRoll + gx * dt) + 0.02f * accRoll;
    compPitch = 0.98f * (compPitch + gy * dt) + 0.02f * accPitch;

    static uint32_t lastPrint = 0;
    if (millis() - lastPrint >= 250) {
        lastPrint = millis();
        Serial.printf("[NHÓM 2 MPU-%s] Roll:%+6.1f° Pitch:%+6.1f° | Acc(g): X=%+5.2f Y=%+5.2f Z=%+5.2f | Gyro(°/s): Z=%+6.1f | INT=%lu\n",
                      mpuCal.valid ? "CAL" : "RAW",
                      compRoll - mpuCal.r0, compPitch - mpuCal.p0,
                      ax, ay, az, gz, (unsigned long)mpuIntCount);
    }
    delay(4);
}

// ======================= KHỞI TẠO & ĐỌC NHÓM 3 (INMP441) =============
void initGroup3() {
    i2s_config_t cfg = {};
    cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
    cfg.sample_rate = MIC_SAMPLE_RATE;
    cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
    cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
    cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    cfg.dma_buf_count = 8;
    cfg.dma_buf_len = MIC_BUF_LEN;

    i2s_pin_config_t pins = {};
    pins.mck_io_num   = I2S_PIN_NO_CHANGE;
    pins.bck_io_num   = PIN_I2S_SCK;
    pins.ws_io_num    = PIN_I2S_WS;
    pins.data_out_num = I2S_PIN_NO_CHANGE;
    pins.data_in_num  = PIN_I2S_SD;

    if (i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL) == ESP_OK &&
        i2s_set_pin(I2S_NUM_0, &pins) == ESP_OK) {
        i2s_zero_dma_buffer(I2S_NUM_0);
        i2sOk = true;
    }
}

size_t readMicSamples(int16_t *out, size_t maxCount) {
    if (!i2sOk) return 0;
    size_t bytesRead = 0;
    if (i2s_read(I2S_NUM_0, i2sRaw, maxCount * sizeof(int32_t), &bytesRead, pdMS_TO_TICKS(100)) != ESP_OK) {
        return 0;
    }
    size_t count = bytesRead / sizeof(int32_t);
    for (size_t i = 0; i < count; i++) {
        float s = (float)(i2sRaw[i] >> 14);
        micDcOffset = 0.995f * micDcOffset + 0.005f * s;
        float v = (s - micDcOffset) * micGain;
        if (v > 32767.0f) v = 32767.0f;
        if (v < -32768.0f) v = -32768.0f;
        out[i] = (int16_t)v;
    }
    return count;
}

void streamRecording(int sec) {
    sec = constrain(sec, 1, 30);
    uint32_t totalSamples = (uint32_t)sec * MIC_SAMPLE_RATE;
    for (int i = 0; i < 4; i++) readMicSamples(pcm16, MIC_BUF_LEN);

    Serial.printf("\n<<<PCM_START:%lu:16:1:%lu>>>\n",
                  (unsigned long)MIC_SAMPLE_RATE, (unsigned long)totalSamples);
    Serial.flush();

    uint32_t sent = 0;
    while (sent < totalSamples) {
        size_t need = min((size_t)(totalSamples - sent), MIC_BUF_LEN);
        size_t got = readMicSamples(pcm16, need);
        if (got > 0) {
            Serial.write((const uint8_t *)pcm16, got * sizeof(int16_t));
            sent += got;
        }
    }
    Serial.flush();
    Serial.println(F("\n<<<PCM_END>>>"));
    Serial.flush();
}

void stepGroup3() {
    size_t n = readMicSamples(pcm16, MIC_BUF_LEN);
    if (n == 0) return;

    double sumSq = 0;
    int16_t peak = 0;
    for (size_t i = 0; i < n; i++) {
        int16_t a = abs(pcm16[i]);
        if (a > peak) peak = a;
        sumSq += (double)pcm16[i] * pcm16[i];
    }
    float rms = sqrtf((float)(sumSq / n));
    int bars = constrain((int)(rms / 6000.0f * 20.0f), 0, 20);
    char barStr[21];
    for (int i = 0; i < 20; i++) barStr[i] = (i < bars) ? '#' : '-';
    barStr[20] = '\0';

    static uint32_t lastPrint = 0;
    if (millis() - lastPrint >= 150) {
        lastPrint = millis();
        Serial.printf("[NHÓM 3 INMP441] RMS:%6.0f | Peak:%6d | [%s]\n", rms, peak, barStr);
    }
}

// ======================= MENU ĐIỀU KHIỂN SERIAL ======================
void printMenu() {
    Serial.println(F("\n╔════════════════════════════════════════════════════════════════╗"));
    Serial.println(F("║     BỘ TEST 3 NHÓM CẢM BIẾN DỰ ÁN CHẬU CÂY THÔNG MINH         ║"));
    Serial.println(F("╠════════════════════════════════════════════════════════════════╣"));
    Serial.println(F("║ Gõ '1' : Test NHÓM 1 (BH1750 + AHT20 + BMP280 + Độ ẩm đất)     ║"));
    Serial.println(F("║ Gõ '2' : Test NHÓM 2 (MPU-6050 Gia tốc & Góc nghiêng)          ║"));
    Serial.println(F("║ Gõ '3' : Test NHÓM 3 (Micro số I2S INMP441 + Thu âm STT)       ║"));
    Serial.println(F("║ Gõ 'c' : Chạy chế độ HIỆU CHỈNH MPU-6050 (Lưu vào Flash)       ║"));
    Serial.println(F("║ Gõ 's' : Quét toàn bộ địa chỉ I2C (I2C Scanner)                ║"));
    Serial.println(F("╚════════════════════════════════════════════════════════════════╝\n"));
}

void checkCommands() {
    if (!Serial.available()) return;
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length() == 0) return;

    if (cmd.startsWith("REC")) {
        int sec = 5;
        int idx = cmd.indexOf(':');
        if (idx > 0) sec = cmd.substring(idx + 1).toInt();
        streamRecording(sec);
    } else if (cmd == "1") {
        activeGroup = GROUP_1_ENV;
        Serial.println(F("\n>>> Đã chuyển sang NHÓM 1: Cảm biến Môi trường & Độ ẩm đất <<<\n"));
    } else if (cmd == "2") {
        activeGroup = GROUP_2_MPU;
        lastMpuUs = micros();
        Serial.println(F("\n>>> Đã chuyển sang NHÓM 2: MPU-6050 (Gõ 'c' để hiệu chỉnh) <<<\n"));
    } else if (cmd == "3") {
        activeGroup = GROUP_3_MIC;
        Serial.println(F("\n>>> Đã chuyển sang NHÓM 3: Micro I2S INMP441 <<<\n"));
    } else if (cmd.equalsIgnoreCase("c")) {
        calibrateMPU6050();
    } else if (cmd.equalsIgnoreCase("s")) {
        scanI2C();
    } else if (cmd.equalsIgnoreCase("h") || cmd == "?") {
        printMenu();
    }
}

void setup() {
    Serial.begin(921600);
    delay(800);
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);

    printMenu();
    scanI2C();
    initGroup1();
    initGroup2();
    initGroup3();
}

void loop() {
    checkCommands();
    if (activeGroup == GROUP_1_ENV)      stepGroup1();
    else if (activeGroup == GROUP_2_MPU) stepGroup2();
    else if (activeGroup == GROUP_3_MIC) stepGroup3();
}
