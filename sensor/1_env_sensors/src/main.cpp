/**
 * ============================================================================
 * NHÓM 1: TEST CẢM BIẾN MÔI TRƯỜNG & ĐẤT
 *   1. BH1750 (GY-302)        - Cảm biến cường độ ánh sáng (Lux) [I2C: 0x23 / 0x5C]
 *   2. AHT20                  - Cảm biến nhiệt độ & độ ẩm không khí [I2C: 0x38]
 *   3. BMP280                 - Cảm biến áp suất khí quyển & độ cao [I2C: 0x77 / 0x76]
 *   4. Capacitive Soil Sensor - Cảm biến độ ẩm đất điện dung [Analog ADC1: GPIO34]
 *
 * Sơ đồ chân (Khớp với file Fritzing group1_env_sensors.fzz):
 *   - Nguồn chung : ESP32 3V3 -> VCC/VDD các cảm biến (kèm tụ gốm lọc nhiễu 100nF)
 *   - Mass chung  : ESP32 GND -> GND các cảm biến + chân ADDR của BH1750
 *   - I2C SDA     : ESP32 GPIO21 (D21) -> SDA (BH1750 + AHT20-BMP280) + Trở kéo lên 4.7kΩ lên 3.3V
 *   - I2C SCL     : ESP32 GPIO22 (D22) -> SCL (BH1750 + AHT20-BMP280) + Trở kéo lên 4.7kΩ lên 3.3V
 *   - Soil Analog : ESP32 GPIO34 (D34) -> Chân A (Analog Out) cảm biến độ ẩm đất
 *
 * Lệnh tương tác qua Serial Monitor (115200 baud):
 *   - 's' : Quét lại toàn bộ các địa chỉ trên bus I2C (I2C Scanner)
 *   - 'd' : Lưu mốc hiệu chuẩn ĐẤT KHÔ (để cảm biến ngoài không khí khô rồi bấm 'd')
 *   - 'w' : Lưu mốc hiệu chuẩn ĐẤT ƯỚT (nhúng cảm biến vào cốc nước/đất bão hòa rồi bấm 'w')
 *   - 'r' : Khôi phục mốc hiệu chuẩn độ ẩm đất mặc định
 * ============================================================================
 */

#include <Arduino.h>
#include <Wire.h>
#include <Preferences.h>
#include <math.h>

// ======================= CẤU HÌNH CHÂN KẾT NỐI =======================
static constexpr int PIN_I2C_SDA  = 21;
static constexpr int PIN_I2C_SCL  = 22;
static constexpr int PIN_SOIL_ADC = 34; // ADC1_CH6 (Input-only, an toàn khi bật Wi-Fi)

// ======================= ĐỊA CHỈ I2C CẢM BIẾN ========================
static constexpr uint8_t ADDR_BH1750_LOW  = 0x23; // Khi ADDR nối GND
static constexpr uint8_t ADDR_BH1750_HIGH = 0x5C; // Khi ADDR nối VCC
static constexpr uint8_t ADDR_AHT20       = 0x38;
static constexpr uint8_t ADDR_BMP280_ALT  = 0x76;
static constexpr uint8_t ADDR_BMP280_DEF  = 0x77; // Mặc định trên module AHT20+BMP280

// ======================= BIẾN TRẠNG THÁI THIẾT BỊ ====================
Preferences prefs;
uint8_t bh1750Addr = 0;
uint8_t bmp280Addr = 0;
bool aht20Ready    = false;

// Hệ số hiệu chuẩn gốc trong ROM của BMP280
struct BMP280CalibData {
    uint16_t dig_T1;
    int16_t  dig_T2;
    int16_t  dig_T3;
    uint16_t dig_P1;
    int16_t  dig_P2;
    int16_t  dig_P3;
    int16_t  dig_P4;
    int16_t  dig_P5;
    int16_t  dig_P6;
    int16_t  dig_P7;
    int16_t  dig_P8;
    int16_t  dig_P9;
    int32_t  t_fine;
} bmpCalib;

// Ngưỡng hiệu chuẩn cảm biến độ ẩm đất điện dung (mV)
int soilDryMv = 2850; // Điện áp khi để khô ngoài không khí (~2.85V)
int soilWetMv = 1250; // Điện áp khi nhúng trong nước (~1.25V)

// ======================= HÀM TIỆN ÍCH I2C ============================
bool i2cDevicePresent(uint8_t addr) {
    Wire.beginTransmission(addr);
    return (Wire.endTransmission() == 0);
}

void writeReg8(uint8_t addr, uint8_t reg, uint8_t val) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.write(val);
    Wire.endTransmission();
}

uint8_t readReg8(uint8_t addr, uint8_t reg) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.endTransmission(false);
    Wire.requestFrom(addr, (uint8_t)1);
    return Wire.available() ? Wire.read() : 0;
}

uint16_t readReg16LE(uint8_t addr, uint8_t reg) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.endTransmission(false);
    Wire.requestFrom(addr, (uint8_t)2);
    if (Wire.available() < 2) return 0;
    uint8_t lsb = Wire.read();
    uint8_t msb = Wire.read();
    return (uint16_t(msb) << 8) | lsb;
}

int16_t readRegS16LE(uint8_t addr, uint8_t reg) {
    return (int16_t)readReg16LE(addr, reg);
}

void scanI2CBus() {
    Serial.println(F("\n================ QUÉT BUS I2C (SDA=21, SCL=22) ================"));
    int count = 0;
    for (uint8_t addr = 1; addr < 127; addr++) {
        if (i2cDevicePresent(addr)) {
            Serial.printf("  [+] Tìm thấy thiết bị tại 0x%02X -> ", addr);
            if (addr == 0x23 || addr == 0x5C) Serial.println(F("BH1750 (Cảm biến ánh sáng)"));
            else if (addr == 0x38)            Serial.println(F("AHT20 (Nhiệt độ & Độ ẩm)"));
            else if (addr == 0x76 || addr == 0x77) Serial.println(F("BMP280 (Áp suất khí quyển)"));
            else if (addr == 0x68 || addr == 0x69) Serial.println(F("MPU-6050 (Gia tốc & Gyro)"));
            else if (addr == 0x3C || addr == 0x3D) Serial.println(F("OLED SSD1309 / SSD1306"));
            else Serial.println(F("Thiết bị I2C khác"));
            count++;
        }
    }
    if (count == 0) {
        Serial.println(F("  [!] KHÔNG tìm thấy thiết bị I2C nào! Kiểm tra nguồn 3.3V, GND và 2 trở kéo lên 4.7kΩ."));
    } else {
        Serial.printf("  => Tổng cộng: %d thiết bị I2C sẵn sàng.\n", count);
    }
    Serial.println(F("===============================================================\n"));
}

// ======================= 1. DRIVER BH1750 ============================
bool initBH1750() {
    if (i2cDevicePresent(ADDR_BH1750_LOW)) {
        bh1750Addr = ADDR_BH1750_LOW;
    } else if (i2cDevicePresent(ADDR_BH1750_HIGH)) {
        bh1750Addr = ADDR_BH1750_HIGH;
    } else {
        bh1750Addr = 0;
        return false;
    }

    // Power ON (0x01) -> Continuous High-Resolution Mode 1 lux (0x10)
    Wire.beginTransmission(bh1750Addr);
    Wire.write(0x01);
    Wire.endTransmission();
    delay(10);

    Wire.beginTransmission(bh1750Addr);
    Wire.write(0x10);
    Wire.endTransmission();
    delay(180);
    return true;
}

float readBH1750Lux() {
    if (bh1750Addr == 0) return NAN;
    Wire.requestFrom(bh1750Addr, (uint8_t)2);
    if (Wire.available() < 2) return NAN;
    uint16_t raw = (uint16_t(Wire.read()) << 8) | Wire.read();
    return raw / 1.2f;
}

// ======================= 2. DRIVER AHT20 =============================
bool initAHT20() {
    if (!i2cDevicePresent(ADDR_AHT20)) {
        aht20Ready = false;
        return false;
    }
    // Soft reset (0xBA)
    Wire.beginTransmission(ADDR_AHT20);
    Wire.write(0xBA);
    Wire.endTransmission();
    delay(25);

    // Kiểm tra bit Calibrated (bit 3 của thanh ghi trạng thái)
    Wire.requestFrom(ADDR_AHT20, (uint8_t)1);
    uint8_t status = Wire.available() ? Wire.read() : 0;
    if ((status & 0x08) == 0) {
        Wire.beginTransmission(ADDR_AHT20);
        Wire.write(0xBE);
        Wire.write(0x08);
        Wire.write(0x00);
        Wire.endTransmission();
        delay(15);
    }
    aht20Ready = true;
    return true;
}

bool readAHT20(float &tempC, float &humPct) {
    if (!aht20Ready) return false;

    // Gửi lệnh đo: 0xAC, 0x33, 0x00
    Wire.beginTransmission(ADDR_AHT20);
    Wire.write(0xAC);
    Wire.write(0x33);
    Wire.write(0x00);
    if (Wire.endTransmission() != 0) return false;

    delay(85); // Chờ AHT20 hoàn tất chuyển đổi (~80ms)

    Wire.requestFrom(ADDR_AHT20, (uint8_t)6);
    if (Wire.available() < 6) return false;

    uint8_t data[6];
    for (int i = 0; i < 6; i++) data[i] = Wire.read();

    if (data[0] & 0x80) {
        // Đang bận
        return false;
    }

    uint32_t rawHum = (uint32_t(data[1]) << 12) | (uint32_t(data[2]) << 4) | (data[3] >> 4);
    uint32_t rawTmp = (uint32_t(data[3] & 0x0F) << 16) | (uint32_t(data[4]) << 8) | data[5];

    humPct = (rawHum * 100.0f) / 1048576.0f;
    tempC  = ((rawTmp * 200.0f) / 1048576.0f) - 50.0f;
    return true;
}

// ======================= 3. DRIVER BMP280 ============================
bool initBMP280() {
    if (i2cDevicePresent(ADDR_BMP280_DEF)) {
        bmp280Addr = ADDR_BMP280_DEF;
    } else if (i2cDevicePresent(ADDR_BMP280_ALT)) {
        bmp280Addr = ADDR_BMP280_ALT;
    } else {
        bmp280Addr = 0;
        return false;
    }

    uint8_t chipId = readReg8(bmp280Addr, 0xD0);
    if (chipId != 0x58 && chipId != 0x60) {
        Serial.printf("  [!] Cảnh báo: Chip ID tại 0x%02X = 0x%02X (khác 0x58/0x60)\n", bmp280Addr, chipId);
    }

    // Đọc bảng hệ số hiệu chuẩn nhà máy từ thanh ghi 0x88..0x9F
    bmpCalib.dig_T1 = readReg16LE(bmp280Addr, 0x88);
    bmpCalib.dig_T2 = readRegS16LE(bmp280Addr, 0x8A);
    bmpCalib.dig_T3 = readRegS16LE(bmp280Addr, 0x8C);
    bmpCalib.dig_P1 = readReg16LE(bmp280Addr, 0x8E);
    bmpCalib.dig_P2 = readRegS16LE(bmp280Addr, 0x90);
    bmpCalib.dig_P3 = readRegS16LE(bmp280Addr, 0x92);
    bmpCalib.dig_P4 = readRegS16LE(bmp280Addr, 0x94);
    bmpCalib.dig_P5 = readRegS16LE(bmp280Addr, 0x96);
    bmpCalib.dig_P6 = readRegS16LE(bmp280Addr, 0x98);
    bmpCalib.dig_P7 = readRegS16LE(bmp280Addr, 0x9A);
    bmpCalib.dig_P8 = readRegS16LE(bmp280Addr, 0x9C);
    bmpCalib.dig_P9 = readRegS16LE(bmp280Addr, 0x9E);

    // Cấu hình bộ lọc IIR (0xF5): standby 250ms, filter x4
    writeReg8(bmp280Addr, 0xF5, (0x03 << 5) | (0x02 << 2));
    // Cấu hình đo (0xF4): Temp oversampling x2, Press oversampling x8, Normal mode (0x03)
    writeReg8(bmp280Addr, 0xF4, (0x02 << 5) | (0x04 << 2) | 0x03);
    delay(50);
    return true;
}

bool readBMP280(float &tempC, float &pressHpa, float &altitudeM) {
    if (bmp280Addr == 0) return false;

    Wire.beginTransmission(bmp280Addr);
    Wire.write(0xF7);
    if (Wire.endTransmission(false) != 0) return false;

    Wire.requestFrom(bmp280Addr, (uint8_t)6);
    if (Wire.available() < 6) return false;

    uint8_t buf[6];
    for (int i = 0; i < 6; i++) buf[i] = Wire.read();

    int32_t adc_P = (int32_t(buf[0]) << 12) | (int32_t(buf[1]) << 4) | (buf[2] >> 4);
    int32_t adc_T = (int32_t(buf[3]) << 12) | (int32_t(buf[4]) << 4) | (buf[5] >> 4);

    // Bù nhiệt độ theo công thức chuẩn Bosch BMP280 datasheet
    int32_t var1 = ((((adc_T >> 3) - ((int32_t)bmpCalib.dig_T1 << 1))) * ((int32_t)bmpCalib.dig_T2)) >> 11;
    int32_t var2 = (((((adc_T >> 4) - ((int32_t)bmpCalib.dig_T1)) * ((adc_T >> 4) - ((int32_t)bmpCalib.dig_T1))) >> 12) *
                    ((int32_t)bmpCalib.dig_T3)) >> 14;
    bmpCalib.t_fine = var1 + var2;
    tempC = ((bmpCalib.t_fine * 5 + 128) >> 8) / 100.0f;

    // Bù áp suất 64-bit
    int64_t p_var1 = ((int64_t)bmpCalib.t_fine) - 128000;
    int64_t p_var2 = p_var1 * p_var1 * (int64_t)bmpCalib.dig_P6;
    p_var2 = p_var2 + ((p_var1 * (int64_t)bmpCalib.dig_P5) << 17);
    p_var2 = p_var2 + (((int64_t)bmpCalib.dig_P4) << 35);
    p_var1 = ((p_var1 * p_var1 * (int64_t)bmpCalib.dig_P3) >> 8) + ((p_var1 * (int64_t)bmpCalib.dig_P2) << 12);
    p_var1 = (((((int64_t)1) << 47) + p_var1)) * ((int64_t)bmpCalib.dig_P1) >> 33;

    if (p_var1 == 0) return false;

    int64_t p = 1048576 - adc_P;
    p = (((p << 31) - p_var2) * 3125) / p_var1;
    p_var1 = (((int64_t)bmpCalib.dig_P9) * (p >> 13) * (p >> 13)) >> 25;
    p_var2 = (((int64_t)bmpCalib.dig_P8) * p) >> 19;
    p = ((p + p_var1 + p_var2) >> 8) + (((int64_t)bmpCalib.dig_P7) << 4);

    float pressPa = (float)p / 256.0f;
    pressHpa = pressPa / 100.0f;
    altitudeM = 44330.0f * (1.0f - powf(pressHpa / 1013.25f, 0.1903f));
    return true;
}

// ======================= 4. ĐỌC ĐỘ ẨM ĐẤT ANALOG =====================
void readSoilMoisture(int &rawAdc, int &milliVolts, float &moisturePct) {
    long sumRaw = 0;
    long sumMv  = 0;
    constexpr int SAMPLES = 32;
    for (int i = 0; i < SAMPLES; i++) {
        sumRaw += analogRead(PIN_SOIL_ADC);
        sumMv  += analogReadMilliVolts(PIN_SOIL_ADC);
        delayMicroseconds(250);
    }
    rawAdc     = sumRaw / SAMPLES;
    milliVolts = sumMv / SAMPLES;

    if (soilDryMv <= soilWetMv) {
        moisturePct = 0.0f;
        return;
    }
    float pct = (float)(soilDryMv - milliVolts) * 100.0f / (float)(soilDryMv - soilWetMv);
    if (pct < 0.0f)   pct = 0.0f;
    if (pct > 100.0f) pct = 100.0f;
    moisturePct = pct;
}

// ======================= XỬ LÝ LỆNH SERIAL ===========================
void handleSerialCommands() {
    while (Serial.available()) {
        char cmd = Serial.read();
        if (cmd == '\r' || cmd == '\n') continue;

        int rawAdc, mv;
        float pct;
        readSoilMoisture(rawAdc, mv, pct);

        if (cmd == 's' || cmd == 'S') {
            scanI2CBus();
        } else if (cmd == 'd' || cmd == 'D') {
            soilDryMv = mv;
            prefs.putInt("dry_mv", soilDryMv);
            Serial.printf("\n[CALIB] Đã lưu mốc ĐẤT KHÔ (Dry Air) = %d mV (Raw ADC = %d)\n\n", soilDryMv, rawAdc);
        } else if (cmd == 'w' || cmd == 'W') {
            soilWetMv = mv;
            prefs.putInt("wet_mv", soilWetMv);
            Serial.printf("\n[CALIB] Đã lưu mốc ĐẤT ƯỚT (Water) = %d mV (Raw ADC = %d)\n\n", soilWetMv, rawAdc);
        } else if (cmd == 'r' || cmd == 'R') {
            soilDryMv = 2850;
            soilWetMv = 1250;
            prefs.putInt("dry_mv", soilDryMv);
            prefs.putInt("wet_mv", soilWetMv);
            Serial.println(F("\n[CALIB] Đã khôi phục mốc độ ẩm đất mặc định (Dry=2850mV, Wet=1250mV).\n"));
        }
    }
}

void setup() {
    Serial.begin(115200);
    delay(800);

    Serial.println(F("\n================================================================"));
    Serial.println(F("   TEST NHÓM 1: ÁNH SÁNG (BH1750) + NHIỆT ĐỘ/ÁP SUẤT (AHT20/BMP280)"));
    Serial.println(F("                + ĐỘ ẨM ĐẤT ĐIỆN DUNG (GPIO34)"));
    Serial.println(F("================================================================"));

    // Khởi tạo ADC cho cảm biến độ ẩm đất
    pinMode(PIN_SOIL_ADC, INPUT);
    analogReadResolution(12);
    analogSetPinAttenuation(PIN_SOIL_ADC, ADC_11db);

    // Đọc mốc hiệu chuẩn đất từ Flash NVS
    prefs.begin("soil_cal", false);
    soilDryMv = prefs.getInt("dry_mv", 2850);
    soilWetMv = prefs.getInt("wet_mv", 1250);
    Serial.printf("[INFO] Mốc hiệu chuẩn Độ ẩm đất: Khô = %d mV | Ướt = %d mV\n", soilDryMv, soilWetMv);
    Serial.println(F("[INFO] Lệnh Serial: 's'=Quét I2C | 'd'=Hiệu chuẩn Khô | 'w'=Hiệu chuẩn Ướt | 'r'=Reset"));

    // Khởi tạo I2C
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 100000);
    scanI2CBus();

    if (initBH1750()) {
        Serial.printf("[OK] BH1750 khởi tạo thành công tại địa chỉ 0x%02X\n", bh1750Addr);
    } else {
        Serial.println(F("[ERR] Không tìm thấy BH1750 (Kiểm tra dây SDA=21, SCL=22, ADDR=GND)"));
    }

    if (initAHT20()) {
        Serial.println(F("[OK] AHT20 khởi tạo thành công tại địa chỉ 0x38"));
    } else {
        Serial.println(F("[ERR] Không tìm thấy AHT20 tại địa chỉ 0x38"));
    }

    if (initBMP280()) {
        Serial.printf("[OK] BMP280 khởi tạo thành công tại địa chỉ 0x%02X\n", bmp280Addr);
    } else {
        Serial.println(F("[ERR] Không tìm thấy BMP280 tại địa chỉ 0x77 / 0x76"));
    }
    Serial.println(F("----------------------------------------------------------------\n"));
}

void loop() {
    handleSerialCommands();

    // 1. Đọc cường độ ánh sáng BH1750
    float lux = readBH1750Lux();
    const char *lightState = "Bình thường";
    if (!isnan(lux)) {
        if (lux < 10.0f)        lightState = "Trời tối / Tắt đèn ( - . - ) zzz";
        else if (lux < 200.0f)  lightState = "Ánh sáng phòng dịu ( ‿ )";
        else if (lux < 2000.0f) lightState = "Sáng rõ ( *‿* )";
        else                    lightState = "Nắng gắt (；´д｀)ゞ";
    }

    // 2. Đọc nhiệt độ & độ ẩm AHT20
    float ahtTemp = NAN, ahtHum = NAN;
    bool okAht = readAHT20(ahtTemp, ahtHum);

    // 3. Đọc áp suất & nhiệt độ BMP280
    float bmpTemp = NAN, bmpPress = NAN, bmpAlt = NAN;
    bool okBmp = readBMP280(bmpTemp, bmpPress, bmpAlt);

    // 4. Đọc độ ẩm đất
    int soilRaw = 0, soilMv = 0;
    float soilPct = 0.0f;
    readSoilMoisture(soilRaw, soilMv, soilPct);
    const char *soilState = "Đủ ẩm ( ♥‿♥ )";
    if (soilPct < 20.0f)      soilState = "Đất khô - Cần tưới nước! ( >_< )";
    else if (soilPct > 85.0f) soilState = "Đất rất ướt / Đang tưới";

    // In báo cáo tổng hợp
    Serial.println(F("┌──────────────────── KẾT QUẢ ĐO NHÓM 1 ────────────────────┐"));
    if (!isnan(lux)) {
        Serial.printf("│ [BH1750] Ánh sáng   : %8.1f Lux   | %s\n", lux, lightState);
    } else {
        Serial.println(F("│ [BH1750] Ánh sáng   : LỖI ĐỌC I2C (Kiểm tra dây nối)"));
    }

    if (okAht) {
        Serial.printf("│ [AHT20]  Không khí  : %6.2f °C     | Độ ẩm KK: %5.1f %%RH\n", ahtTemp, ahtHum);
    } else {
        Serial.println(F("│ [AHT20]  Không khí  : LỖI ĐỌC I2C"));
    }

    if (okBmp) {
        Serial.printf("│ [BMP280] Khí quyển  : %7.2f hPa   | Nhiệt độ: %5.2f °C | Cao độ: ~%.1f m\n",
                      bmpPress, bmpTemp, bmpAlt);
    } else {
        Serial.println(F("│ [BMP280] Khí quyển  : LỖI ĐỌC I2C"));
    }

    Serial.printf("│ [SOIL]   Độ ẩm đất  : %6.1f %%      | Điện áp : %4d mV (ADC=%4d) | %s\n",
                  soilPct, soilMv, soilRaw, soilState);
    Serial.println(F("└───────────────────────────────────────────────────────────┘"));

    delay(1500);
}
