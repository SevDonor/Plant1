/**
 * ============================================================================
 * NHÓM 2: TEST CẢM BIẾN GIA TỐC & CON QUAY HỒI CHUYỂN MPU-6050 (GY-521)
 *         (CÓ SẴN CHẾ ĐỘ HIỆU CHỈNH / CALIBRATION LƯU VÀO FLASH NVS)
 *
 * Sơ đồ chân (Khớp với file Fritzing group2_mpu6050.fzz):
 *   - VCC (pin1) : ESP32 3V3 (kèm tụ gốm lọc nguồn 100nF sát chân VCC-GND)
 *   - GND (pin2) : ESP32 GND
 *   - SCL (pin3) : ESP32 GPIO22 (D22) + Trở kéo lên 4.7kΩ lên 3.3V
 *   - SDA (pin4) : ESP32 GPIO21 (D21) + Trở kéo lên 4.7kΩ lên 3.3V
 *   - AD0 (pin7) : Nối xuống GND (Cố định địa chỉ I2C ở 0x68)
 *   - INT (pin8) : ESP32 GPIO19 (D19) - Chân ngắt Data Ready / Motion
 *
 * Lệnh điều khiển qua Serial Monitor (115200 baud):
 *   - 'c' hoặc 'C' : BẮT ĐẦU CHẾ ĐỘ HIỆU CHỈNH (Calibration 1000 mẫu & lưu vào Flash)
 *   - 'z' hoặc 'Z' : Đặt góc Roll/Pitch hiện tại về 0° (Zero/Tare tại vị trí đặt chậu)
 *   - 'r' hoặc 'R' : Xóa dữ liệu hiệu chỉnh trong Flash (Reset về 0)
 *   - 'p' hoặc 'P' : Bật/Tắt chế độ vẽ đồ thị trên Serial Plotter
 *   - 'i' hoặc 'I' : Xem thông tin Offset hiệu chỉnh đang áp dụng
 * ============================================================================
 */

#include <Arduino.h>
#include <Wire.h>
#include <Preferences.h>
#include <math.h>

// ======================= CẤU HÌNH CHÂN PHẦN CỨNG =====================
static constexpr int PIN_I2C_SDA = 21;
static constexpr int PIN_I2C_SCL = 22;
static constexpr int PIN_MPU_INT = 19;

// ======================= THANH GHI MPU-6050 ==========================
static constexpr uint8_t MPU_ADDR_DEFAULT = 0x68; // AD0 = GND
static constexpr uint8_t MPU_ADDR_ALT     = 0x69; // AD0 = VCC
static constexpr uint8_t REG_SMPLRT_DIV   = 0x19;
static constexpr uint8_t REG_CONFIG       = 0x1A;
static constexpr uint8_t REG_GYRO_CONFIG  = 0x1B;
static constexpr uint8_t REG_ACCEL_CONFIG = 0x1C;
static constexpr uint8_t REG_INT_PIN_CFG  = 0x37;
static constexpr uint8_t REG_INT_ENABLE   = 0x38;
static constexpr uint8_t REG_ACCEL_XOUT_H = 0x3B;
static constexpr uint8_t REG_PWR_MGMT_1   = 0x6B;
static constexpr uint8_t REG_WHO_AM_I     = 0x75;

// Hệ số quy đổi ở thang đo ±2g (16384 LSB/g) và ±250°/s (131 LSB/°/s)
static constexpr float ACCEL_SCALE_G   = 16384.0f;
static constexpr float GYRO_SCALE_DPS  = 131.0f;

// ======================= BIẾN TOÀN CỤC ===============================
Preferences prefs;
uint8_t mpuAddr = MPU_ADDR_DEFAULT;
bool mpuReady   = false;
bool plotterMode = false;

// Ngắt phần cứng từ chân INT (GPIO19)
volatile uint32_t mpuIntCount = 0;
void IRAM_ATTR onMpuInterrupt() {
    mpuIntCount++;
}

// Cấu trúc lưu Offset hiệu chỉnh
struct MpuOffsets {
    bool  calibrated = false;
    float ax_off = 0.0f;
    float ay_off = 0.0f;
    float az_off = 0.0f;
    float gx_off = 0.0f;
    float gy_off = 0.0f;
    float gz_off = 0.0f;
    float roll_zero  = 0.0f;
    float pitch_zero = 0.0f;
} offsets;

// Trạng thái góc nghiêng (Bộ lọc Complementary Filter)
float compRoll  = 0.0f;
float compPitch = 0.0f;
uint32_t lastMicros = 0;
uint32_t lastPrintMs = 0;

// Biến phát hiện tương tác vật lý (Tap / Double-Tap / Shake / Tilt)
uint32_t lastTapTimeMs = 0;
int      tapStreak     = 0;

// ======================= GIAO TIẾP I2C MPU-6050 ======================
bool mpuWriteReg(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(mpuAddr);
    Wire.write(reg);
    Wire.write(val);
    return (Wire.endTransmission() == 0);
}

uint8_t mpuReadReg(uint8_t reg) {
    Wire.beginTransmission(mpuAddr);
    Wire.write(reg);
    Wire.endTransmission(false);
    Wire.requestFrom(mpuAddr, (uint8_t)1);
    return Wire.available() ? Wire.read() : 0;
}

bool mpuReadRaw(int16_t &ax, int16_t &ay, int16_t &az,
                int16_t &rawTemp,
                int16_t &gx, int16_t &gy, int16_t &gz) {
    Wire.beginTransmission(mpuAddr);
    Wire.write(REG_ACCEL_XOUT_H);
    if (Wire.endTransmission(false) != 0) return false;

    Wire.requestFrom(mpuAddr, (uint8_t)14);
    if (Wire.available() < 14) return false;

    ax      = (int16_t(Wire.read()) << 8) | Wire.read();
    ay      = (int16_t(Wire.read()) << 8) | Wire.read();
    az      = (int16_t(Wire.read()) << 8) | Wire.read();
    rawTemp = (int16_t(Wire.read()) << 8) | Wire.read();
    gx      = (int16_t(Wire.read()) << 8) | Wire.read();
    gy      = (int16_t(Wire.read()) << 8) | Wire.read();
    gz      = (int16_t(Wire.read()) << 8) | Wire.read();
    return true;
}

bool initMPU6050() {
    Wire.beginTransmission(MPU_ADDR_DEFAULT);
    if (Wire.endTransmission() == 0) {
        mpuAddr = MPU_ADDR_DEFAULT;
    } else {
        Wire.beginTransmission(MPU_ADDR_ALT);
        if (Wire.endTransmission() == 0) {
            mpuAddr = MPU_ADDR_ALT;
        } else {
            return false;
        }
    }

    uint8_t who = mpuReadReg(REG_WHO_AM_I);
    Serial.printf("[MPU] Tìm thấy cảm biến tại 0x%02X (WHO_AM_I = 0x%02X)\n", mpuAddr, who);

    // Đánh thức MPU-6050, chọn xung nhịp PLL trục X Gyro
    mpuWriteReg(REG_PWR_MGMT_1, 0x01);
    delay(50);

    // Cấu hình bộ lọc thông thấp số DLPF = 42Hz (0x03) để khử rung cơ học
    mpuWriteReg(REG_CONFIG, 0x03);
    // Tốc độ lấy mẫu = 1kHz / (1 + 4) = 200Hz
    mpuWriteReg(REG_SMPLRT_DIV, 4);
    // Thang đo Gyro = ±250 °/s (0x00)
    mpuWriteReg(REG_GYRO_CONFIG, 0x00);
    // Thang đo Accel = ±2g (0x00)
    mpuWriteReg(REG_ACCEL_CONFIG, 0x00);
    // Cấu hình chân INT tạo xung 50us, tự xóa khi đọc bất kỳ thanh ghi nào
    mpuWriteReg(REG_INT_PIN_CFG, 0x10);
    mpuWriteReg(REG_INT_ENABLE, 0x01); // Data Ready Interrupt

    return true;
}

// ======================= QUẢN LÝ HIỆU CHỈNH (CALIBRATION) ============
void loadCalibrationFromFlash() {
    prefs.begin("mpu_cal", true);
    offsets.calibrated = prefs.getBool("valid", false);
    if (offsets.calibrated) {
        offsets.ax_off     = prefs.getFloat("ax", 0.0f);
        offsets.ay_off     = prefs.getFloat("ay", 0.0f);
        offsets.az_off     = prefs.getFloat("az", 0.0f);
        offsets.gx_off     = prefs.getFloat("gx", 0.0f);
        offsets.gy_off     = prefs.getFloat("gy", 0.0f);
        offsets.gz_off     = prefs.getFloat("gz", 0.0f);
        offsets.roll_zero  = prefs.getFloat("r0", 0.0f);
        offsets.pitch_zero = prefs.getFloat("p0", 0.0f);
    }
    prefs.end();
}

void saveCalibrationToFlash() {
    prefs.begin("mpu_cal", false);
    prefs.putBool("valid", offsets.calibrated);
    prefs.putFloat("ax", offsets.ax_off);
    prefs.putFloat("ay", offsets.ay_off);
    prefs.putFloat("az", offsets.az_off);
    prefs.putFloat("gx", offsets.gx_off);
    prefs.putFloat("gy", offsets.gy_off);
    prefs.putFloat("gz", offsets.gz_off);
    prefs.putFloat("r0", offsets.roll_zero);
    prefs.putFloat("p0", offsets.pitch_zero);
    prefs.end();
}

void printCalibrationStatus() {
    Serial.println(F("\n┌──────────────── THÔNG SỐ HIỆU CHỈNH MPU-6050 ────────────────┐"));
    Serial.printf("│ Trạng thái : %s\n",
                  offsets.calibrated ? "ĐÃ HIỆU CHỈNH (Đã lưu trong Flash NVS)" : "CHƯA HIỆU CHỈNH (Hãy nhấn 'c')");
    Serial.printf("│ Accel Bias : dAx = %+8.1f | dAy = %+8.1f | dAz = %+8.1f (LSB)\n",
                  offsets.ax_off, offsets.ay_off, offsets.az_off);
    Serial.printf("│ Gyro Bias  : dGx = %+8.1f | dGy = %+8.1f | dGz = %+8.1f (LSB)\n",
                  offsets.gx_off, offsets.gy_off, offsets.gz_off);
    Serial.printf("│ Góc Zero   : Roll_0 = %+5.2f° | Pitch_0 = %+5.2f°\n",
                  offsets.roll_zero, offsets.pitch_zero);
    Serial.println(F("└──────────────────────────────────────────────────────────────┘\n"));
}

void runCalibrationRoutine() {
    Serial.println(F("\n================================================================"));
    Serial.println(F("        CHẾ ĐỘ HIỆU CHỈNH TỰ ĐỘNG MPU-6050 (CALIBRATION)"));
    Serial.println(F("================================================================"));
    Serial.println(F("Hướng dẫn: Đặt cảm biến MPU-6050 nằm PHẲNG và ĐỨNG YÊN trên mặt bàn."));
    Serial.println(F("           Không chạm hoặc làm rung bàn trong quá trình đo!"));

    for (int sec = 3; sec >= 1; sec--) {
        Serial.printf("  -> Bắt đầu thu thập mẫu sau %d giây...\n", sec);
        delay(1000);
    }

    Serial.print(F("  -> Đang đo 1000 mẫu: ["));
    // Bỏ qua 100 mẫu đầu để ổn định bộ lọc
    int16_t ax, ay, az, temp, gx, gy, gz;
    for (int i = 0; i < 100; i++) {
        mpuReadRaw(ax, ay, az, temp, gx, gy, gz);
        delay(2);
    }

    constexpr int NUM_SAMPLES = 1000;
    double sumAx = 0, sumAy = 0, sumAz = 0;
    double sumGx = 0, sumGy = 0, sumGz = 0;
    int validCount = 0;

    for (int i = 0; i < NUM_SAMPLES; i++) {
        if (mpuReadRaw(ax, ay, az, temp, gx, gy, gz)) {
            sumAx += ax;
            sumAy += ay;
            // Khi đặt nằm phẳng, trục Z chịu trọng trường +1g (+16384 LSB)
            sumAz += (az - ACCEL_SCALE_G);
            sumGx += gx;
            sumGy += gy;
            sumGz += gz;
            validCount++;
        }
        if ((i + 1) % 50 == 0) Serial.print(F("#"));
        delay(3);
    }
    Serial.println(F("] Hoàn tất!"));

    if (validCount < 500) {
        Serial.println(F("[ERR] Lỗi đọc I2C trong quá trình hiệu chỉnh! Vui lòng kiểm tra dây nối."));
        return;
    }

    offsets.ax_off     = (float)(sumAx / validCount);
    offsets.ay_off     = (float)(sumAy / validCount);
    offsets.az_off     = (float)(sumAz / validCount);
    offsets.gx_off     = (float)(sumGx / validCount);
    offsets.gy_off     = (float)(sumGy / validCount);
    offsets.gz_off     = (float)(sumGz / validCount);
    offsets.roll_zero  = 0.0f;
    offsets.pitch_zero = 0.0f;
    offsets.calibrated = true;

    // Reset góc tích phân của bộ lọc Complementary
    compRoll  = 0.0f;
    compPitch = 0.0f;
    lastMicros = micros();

    saveCalibrationToFlash();
    Serial.println(F("[OK] Đã tính toán xong và lưu Offset vào bộ nhớ Flash NVS!"));
    printCalibrationStatus();
}

void handleSerialCommands() {
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\r' || c == '\n') continue;

        if (c == 'c' || c == 'C') {
            runCalibrationRoutine();
        } else if (c == 'z' || c == 'Z') {
            offsets.roll_zero  = compRoll;
            offsets.pitch_zero = compPitch;
            saveCalibrationToFlash();
            Serial.printf("\n[ZERO] Đã đặt góc hiện tại làm mốc 0° (Roll_0=%.2f°, Pitch_0=%.2f°)\n\n",
                          offsets.roll_zero, offsets.pitch_zero);
        } else if (c == 'r' || c == 'R') {
            offsets = MpuOffsets();
            saveCalibrationToFlash();
            Serial.println(F("\n[RESET] Đã xóa toàn bộ Offset hiệu chỉnh về 0.\n"));
            printCalibrationStatus();
        } else if (c == 'p' || c == 'P') {
            plotterMode = !plotterMode;
            Serial.printf("\n[MODE] Chế độ Serial Plotter: %s\n\n", plotterMode ? "BẬT" : "TẮT");
        } else if (c == 'i' || c == 'I') {
            printCalibrationStatus();
        }
    }
}

void setup() {
    Serial.begin(115200);
    delay(800);

    Serial.println(F("\n================================================================"));
    Serial.println(F("   TEST NHÓM 2: CẢM BIẾN GIA TỐC & GÓC NGHIÊNG MPU-6050 (GY-521)"));
    Serial.println(F("================================================================"));

    pinMode(PIN_MPU_INT, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_MPU_INT), onMpuInterrupt, RISING);

    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000); // Fast-mode I2C 400kHz cho IMU

    mpuReady = initMPU6050();
    if (!mpuReady) {
        Serial.println(F("[ERR] Không tìm thấy MPU-6050! Kiểm tra dây VCC=3V3, GND, SDA=21, SCL=22, AD0=GND."));
    }

    loadCalibrationFromFlash();
    printCalibrationStatus();

    Serial.println(F("Bảng lệnh điều khiển:"));
    Serial.println(F("  [c] Chạy chế độ HIỆU CHỈNH tự động (đặt phẳng & đứng yên)"));
    Serial.println(F("  [z] Đặt vị trí góc hiện tại về 0° (Tare/Zero)"));
    Serial.println(F("  [p] Bật/tắt chế độ vẽ đồ thị Serial Plotter"));
    Serial.println(F("  [i] Xem thông số Offset hiệu chỉnh | [r] Xóa Offset"));
    Serial.println(F("----------------------------------------------------------------\n"));

    lastMicros = micros();
}

void loop() {
    handleSerialCommands();
    if (!mpuReady) {
        delay(1000);
        mpuReady = initMPU6050();
        return;
    }

    int16_t rawAx, rawAy, rawAz, rawTemp, rawGx, rawGy, rawGz;
    if (!mpuReadRaw(rawAx, rawAy, rawAz, rawTemp, rawGx, rawGy, rawGz)) {
        return;
    }

    uint32_t nowUs = micros();
    float dt = (nowUs - lastMicros) * 1e-6f;
    if (dt <= 0.0f || dt > 0.5f) dt = 0.005f;
    lastMicros = nowUs;

    // Áp dụng Offset hiệu chỉnh
    float ax_g = (rawAx - offsets.ax_off) / ACCEL_SCALE_G;
    float ay_g = (rawAy - offsets.ay_off) / ACCEL_SCALE_G;
    float az_g = (rawAz - offsets.az_off) / ACCEL_SCALE_G;

    float gx_dps = (rawGx - offsets.gx_off) / GYRO_SCALE_DPS;
    float gy_dps = (rawGy - offsets.gy_off) / GYRO_SCALE_DPS;
    float gz_dps = (rawGz - offsets.gz_off) / GYRO_SCALE_DPS;

    float tempC = (rawTemp / 340.0f) + 36.53f;

    // Tính góc từ gia tốc kế (độ)
    float accelRoll  = atan2f(ay_g, sqrtf(ax_g * ax_g + az_g * az_g)) * 180.0f / PI;
    float accelPitch = atan2f(-ax_g, sqrtf(ay_g * ay_g + az_g * az_g)) * 180.0f / PI;

    // Bộ lọc bù (Complementary Filter: 98% Gyro + 2% Accel)
    compRoll  = 0.98f * (compRoll  + gx_dps * dt) + 0.02f * accelRoll;
    compPitch = 0.98f * (compPitch + gy_dps * dt) + 0.02f * accelPitch;

    float relRoll  = compRoll  - offsets.roll_zero;
    float relPitch = compPitch - offsets.pitch_zero;

    // Độ lớn tổng hợp gia tốc (bình thường đứng yên = ~1.0g)
    float totalG = sqrtf(ax_g * ax_g + ay_g * ay_g + az_g * az_g);
    float dynG   = fabsf(totalG - 1.0f);

    // Phát hiện tương tác vật lý (Tap / Shake / Tilt)
    uint32_t nowMs = millis();
    if (dynG > 0.35f && dynG <= 1.2f && (nowMs - lastTapTimeMs > 180)) {
        if (nowMs - lastTapTimeMs < 600) {
            tapStreak++;
        } else {
            tapStreak = 1;
        }
        lastTapTimeMs = nowMs;
        if (!plotterMode) {
            if (tapStreak >= 2) {
                Serial.println(F("  >>> [SỰ KIỆN] DOUBLE-TAP (Gõ nhẹ 2 cái vào chậu! Pet cười ^‿^ ) <<<"));
                tapStreak = 0;
            } else {
                Serial.println(F("  >>> [SỰ KIỆN] TAP (Vừa chạm/gõ nhẹ vào chậu) <<<"));
            }
        }
    }

    // In định kỳ ra Serial
    uint32_t intervalMs = plotterMode ? 40 : 250;
    if (nowMs - lastPrintMs >= intervalMs) {
        lastPrintMs = nowMs;

        if (plotterMode) {
            Serial.printf("Roll:%.2f,Pitch:%.2f,Ax:%.2f,Ay:%.2f,Az:%.2f\n",
                          relRoll, relPitch, ax_g, ay_g, az_g);
        } else {
            const char *motionState = "Đứng yên ổn định";
            if (dynG > 1.2f) {
                motionState = "RUNG LẮC MẠNH! ( @ _ @ ) Chóng mặt quá!";
            } else if (fabsf(relRoll) > 45.0f || fabsf(relPitch) > 45.0f) {
                motionState = "NGHIÊNG / NGÃ ĐỔ > 45°! ( T_T ) Đỡ em dậy!";
            } else if (fabsf(relRoll) > 15.0f || fabsf(relPitch) > 15.0f) {
                motionState = "Đang nghiêng nhẹ";
            }

            Serial.printf("[%s] Roll:%+6.1f° Pitch:%+6.1f° | Acc(g): X=%+5.2f Y=%+5.2f Z=%+5.2f | Gyro(°/s): X=%+6.1f Y=%+6.1f Z=%+6.1f | T=%.1f°C | INT=%lu | %s\n",
                          offsets.calibrated ? "CAL" : "RAW",
                          relRoll, relPitch,
                          ax_g, ay_g, az_g,
                          gx_dps, gy_dps, gz_dps,
                          tempC, (unsigned long)mpuIntCount,
                          motionState);
        }
    }

    delay(4); // Chu kỳ ~200Hz
}
