/**
 * ============================================================================
 * HỆ THỐNG GIÁM SÁT MÔI TRƯỜNG VÀ CHĂM SÓC CÂY TRỒNG THÔNG MINH
 * SỬ DỤNG ESP32-S3 KẾT HỢP AI CHATBOT & ĐÔI MẮT OLED THÚ CƯNG ẢO
 * ============================================================================
 * Thiết kế & Triển khai đầy đủ 6 Phân hệ theo Lưu đồ Thuật toán Kỹ thuật Tổng thể:
 *   - PHÂN HỆ 1: Khởi tạo Hệ thống, Self-Test Phần cứng, Degraded Mode & Wi-Fi/NTP
 *   - PHÂN HỆ 2: Thu thập, Lọc nhiễu (Moving Average / Complementary) & Điều phối Dữ liệu
 *   - PHÂN HỆ 3: Thuật toán Tưới cây Tự động An toàn (Xung 3s -> Ngấm 2s -> Đo lại, Khóa 5 chu kỳ)
 *   - PHÂN HỆ 4: Engine Biểu cảm Hai Mắt OLED Độc lập (Ưu tiên 4 cấp + Chớp mắt + Liếc đồng tử)
 *   - PHÂN HỆ 5: Tương tác Giọng nói (INMP441 VAD/PCM) + NLP 3 Nhóm Ý định + Loa I2S MAX98357A
 *   - PHÂN HỆ 6: Đóng gói IoT JSON Telemetry, Offline Backlog Sync & Tương thích UI PC/Mobile
 *
 * SƠ ĐỒ PHÂN BỔ BUS I2C & NGOẠI VI TRÊN ESP32-S3:
 *   - Bus I2C 0 (Wire  - SDA = GPIO 11, SCL = GPIO 12):
 *       + MPU6050 (0x68 / 0x69, Ngắt đánh thức INT = GPIO 10)
 *       + OLED 1  (0x3C / 0x3D - Hiển thị MẮT TRÁI)
 *   - Bus I2C 1 (Wire1 - SDA = GPIO 1,  SCL = GPIO 2) :
 *       + AHT20   (0x38 - Nhiệt độ & Độ ẩm không khí)
 *       + BMP280  (0x76 / 0x77 - Áp suất khí quyển & Độ cao)
 *       + BH1750  (0x23 / 0x5C - Cường độ ánh sáng Lux)
 *       + OLED 2  (0x3C / 0x3D - Hiển thị MẮT PHẢI)
 *   - Micro số I2S INMP441 (I2S_NUM_0 RX)   : WS = GPIO 4, SCK = GPIO 5, SD = GPIO 6
 *   - Khuếch đại Loa MAX98357A (I2S_NUM_1 TX): BCLK = GPIO 15, LRCLK = GPIO 16, DIN = GPIO 17, SD = GPIO 18
 *   - Cảm biến Độ ẩm đất điện dung (ADC1)   : GPIO 3 (12-bit ADC 0..4095)
 *   - Cảm biến Mực nước bình chứa (XKC-Y25) : GPIO 7 (Digital Input)
 *   - Điều khiển Bơm mini 5V (PC817+MOSFET) : GPIO 14 (Digital Output, Mặc định OFF)
 * ============================================================================
 */

#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <Wire.h>
#include <driver/i2s.h>
#include <driver/rtc_io.h>
#include <esp_sleep.h>
#include <math.h>
#include <string.h>
#include <time.h>

// ============================================================================
// 1. ĐỊNH NGHĨA CHÂN GPIO (PIN DEFINITIONS)
// ============================================================================

// Bus I2C 0 (SDA = GPIO 11, SCL = GPIO 12): MPU6050 (0x68, INT = GPIO 10) + OLED 1 (0x3C)
#ifndef I2C_SDA_PIN
#define I2C_SDA_PIN 11
#endif

#ifndef I2C_SCL_PIN
#define I2C_SCL_PIN 12
#endif

// Bus I2C 1 (SDA = GPIO 1, SCL = GPIO 2): AHT20 (0x38) + BMP280 (0x76/0x77) + BH1750 (0x23/0x5C) + OLED 2 (0x3C)
#ifndef OLED2_SDA_PIN
#define OLED2_SDA_PIN 1
#endif

#ifndef OLED2_SCL_PIN
#define OLED2_SCL_PIN 2
#endif

// Chân ngắt chuyển động MPU6050 (Đánh thức ESP32-S3 từ chế độ ngủ)
#ifndef MPU_INT_PIN
#define MPU_INT_PIN 10
#endif

// Micro số I2S INMP441 (I2S_NUM_0 RX)
#ifndef MIC_WS_PIN
#define MIC_WS_PIN 4
#endif

#ifndef MIC_SCK_PIN
#define MIC_SCK_PIN 5
#endif

#ifndef MIC_SD_PIN
#define MIC_SD_PIN 6
#endif

// Mạch khuếch đại âm thanh I2S MAX98357A + Loa 4Ω 3W (I2S_NUM_1 TX - khớp main.fzz)
#ifndef SPK_BCLK_PIN
#define SPK_BCLK_PIN 15
#endif

#ifndef SPK_LRCLK_PIN
#define SPK_LRCLK_PIN 16
#endif

#ifndef SPK_DIN_PIN
#define SPK_DIN_PIN 17
#endif

#ifndef SPK_SD_PIN
#define SPK_SD_PIN 18
#endif

// Cảm biến độ ẩm đất điện dung v1.0 (ADC1_CH2 trên ESP32-S3, không trùng GPIO 1/2 của I2C1)
#ifndef SOIL_ADC_PIN
#define SOIL_ADC_PIN 3
#endif

// Cảm biến mực nước không tiếp xúc XKC-Y25-NPN (qua Opto PC817 - khớp main.fzz)
#ifndef WATER_LEVEL_PIN
#define WATER_LEVEL_PIN 7
#endif

// Điều khiển Relay / MOSFET Bơm chìm mini 5V (qua Opto PC817 - khớp main.fzz)
#ifndef PUMP_RELAY_PIN
#define PUMP_RELAY_PIN 14
#endif

// LED trạng thái hệ thống
#ifndef STATUS_LED_PIN
#define STATUS_LED_PIN 21
#endif

// ============================================================================
// 2. HẰNG SỐ ĐỊA CHỈ I2C, THANH GHI & THÔNG SỐ THUẬT TOÁN
// ============================================================================

// MPU6050 (Bus I2C 0)
static constexpr uint8_t MPU_ADDR_LOW     = 0x68;
static constexpr uint8_t MPU_ADDR_HIGH    = 0x69;
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

// AHT20 (Bus I2C 1)
static constexpr uint8_t AHT20_ADDR = 0x38;

// BMP280 (Bus I2C 1)
static constexpr uint8_t BMP280_ADDR_LOW   = 0x76;
static constexpr uint8_t BMP280_ADDR_HIGH  = 0x77;
static constexpr uint8_t BMP_REG_CHIP_ID   = 0xD0;
static constexpr uint8_t BMP_REG_RESET     = 0xE0;
static constexpr uint8_t BMP_REG_CTRL_MEAS = 0xF4;
static constexpr uint8_t BMP_REG_CONFIG    = 0xF5;
static constexpr uint8_t BMP_REG_PRESS_MSB = 0xF7;
static constexpr uint8_t BMP_REG_CALIB_00  = 0x88;

// BH1750 (Bus I2C 1)
static constexpr uint8_t BH1750_ADDR_LOW  = 0x23;
static constexpr uint8_t BH1750_ADDR_HIGH = 0x5C;

// OLED 0.96" / 2.42" SSD1306 / SSD1309 (OLED 1 trên Bus I2C 0, OLED 2 trên Bus I2C 1)
static constexpr uint8_t OLED_ADDR_PRIMARY   = 0x3C;
static constexpr uint8_t OLED_ADDR_SECONDARY = 0x3D;
static constexpr uint8_t OLED_WIDTH          = 128;
static constexpr uint8_t OLED_HEIGHT         = 64;
static constexpr uint8_t OLED_PAGES          = OLED_HEIGHT / 8;

// Hằng số chuyển đổi & Chu kỳ định thời (FreeRTOS / Non-blocking Timers)
static constexpr float    ACCEL_SCALE_2G           = 16384.0f;
static constexpr float    GYRO_SCALE_250DPS        = 131.0f;
static constexpr float    RAD_TO_DEG_F             = 57.2957795f;
static constexpr uint32_t FILTER_INTERVAL_MS       = 10;    // 100 Hz lọc bù MPU6050
static constexpr uint32_t ENV_OUTPUT_MS            = 500;   // 2 Hz gói tin Môi trường
static constexpr uint32_t MIC_OUTPUT_MS            = 65;    // ~15 Hz gói tin Micro
static constexpr uint32_t OLED_OUTPUT_MS           = 500;   // 2 Hz gói tin OLED & Telemetry
static constexpr uint32_t TELEMETRY_SUMMARY_MS     = 2000;  // 2s chu kỳ đánh giá sinh học P2
static constexpr uint32_t WIFI_RECONNECT_PERIOD_MS = 30000; // 30s thử kết nối lại Wi-Fi ngầm

// Hằng số Thuật toán Tưới cây Tự động (Phân hệ 3)
static constexpr uint16_t FLOWCHART_SOIL_ADC_THRESHOLD = 1100; // Quy ước lưu đồ: ADC > 1100 là Đủ nước
static constexpr uint32_t PUMP_PULSE_DURATION_MS       = 3000; // Bơm định lượng đúng 3 giây
static constexpr uint32_t PUMP_SOAK_DURATION_MS        = 2000; // Chờ 1-2 giây nước ngấm rễ
static constexpr uint32_t AUTO_WATER_COOLDOWN_MS       = 15000;// Thời gian nghỉ giữa các đợt kiểm tra tự động
static constexpr uint8_t  MAX_WATERING_CYCLES          = 5;    // Giới hạn tối đa 5 chu kỳ liên tiếp

// Mã lỗi phần cứng & Hệ thống (Bitmask err_codes - Phân hệ 1 & 2 & 3)
static constexpr uint16_t ERR_NONE          = 0x0000;
static constexpr uint16_t ERR_MPU_FAULT     = 0x0001;
static constexpr uint16_t ERR_AHT_FAULT     = 0x0002;
static constexpr uint16_t ERR_BMP_FAULT     = 0x0004;
static constexpr uint16_t ERR_BH_FAULT      = 0x0008;
static constexpr uint16_t ERR_OLED1_FAULT   = 0x0010;
static constexpr uint16_t ERR_OLED2_FAULT   = 0x0020;
static constexpr uint16_t ERR_MIC_FAULT     = 0x0040;
static constexpr uint16_t ERR_SOIL_FAULT    = 0x0080;
static constexpr uint16_t ERR_TANK_EMPTY    = 0x0100;
static constexpr uint16_t ERR_WATER_FAULT   = 0x0200; // WATERING_FAULT_ERROR (>= 5 chu kỳ)
static constexpr uint16_t ERR_OUTLIER_DATA  = 0x0400; // SENSOR_FAULT dữ liệu dị thường

// ============================================================================
// 3. KIỂU DỮ LIỆU & CẤU TRÚC TRẠNG THÁI 6 PHÂN HỆ
// ============================================================================

enum class SleepMode : uint8_t {
    LIGHT = 0,
    DEEP  = 1,
};

enum class ActiveViewMode : uint8_t {
    MPU  = 0,
    ENV  = 1,
    MIC  = 2,
    OLED = 3,
};

enum class OledDisplayMode : uint8_t {
    EYES      = 0,
    DASHBOARD = 1,
    TEXT      = 2,
    TEST      = 3,
};

// Danh mục Biểu cảm Hai Mắt OLED (Phân hệ 4 - khớp 100% Lưu đồ & UI)
enum class EyeEmotion : uint8_t {
    NORMAL    = 0, // Mắt tròn tiêu chuẩn
    HAPPY     = 1, // ^ ^ hoặc (^_^)/ : Vui vẻ, tươi tốt, vừa được xoa đầu / đủ nước
    SURPRISED = 2, // O_O : Ngạc nhiên, báo động khi nghiêng chậu
    DIZZY     = 3, // @_@ : Chóng mặt khi bị rung lắc mạnh
    LISTENING = 4, // o_o : Đang lắng nghe giọng nói (Mic VAD)
    SAD       = 5, // T_T : Buồn bã, đất khô thiếu nước
    LOVE      = 6, // ♥_♥ : Đang được tưới nước mát lành
    SLEEPY    = 7, // -.- hoặc ~_~ : Ngái ngủ, mệt mỏi (quá nóng/lạnh/tối)
    THIRSTY   = 8, // ><  : Khát nước trầm trọng / Bình chứa hết nước / Lỗi tưới
    THINKING  = 9, // -_- : AI Cloud đang xử lý suy nghĩ
    SPEAKING  = 10 // ^o^ : Loa đang phát giọng nói AI Chatbot
};

// Pha hoạt động của Thuật toán Tưới cây Tự động (Phân hệ 3)
enum class WateringPhase : uint8_t {
    IDLE         = 0, // Bơm tắt, chờ kiểm tra định kỳ
    PUMPING_3S   = 1, // Đang bật bơm định lượng đúng 3 giây
    SOAKING_2S   = 2, // Đã tắt bơm, chờ 2 giây cho nước ngấm đều vào đất
    RECHECK      = 3, // Đọc lại ADC độ ẩm đất sau khi tưới để so sánh ngưỡng
    FAULT_LOCKED = 4  // Khóa bơm khẩn cấp do tưới >= 5 chu kỳ liên tiếp (WATERING_FAULT_ERROR)
};

// Trạng thái Tương tác Giọng nói & AI Chatbot (Phân hệ 5)
enum class VoiceAiState : uint8_t {
    IDLE      = 0,
    LISTENING = 1, // Mic đang thu âm (o_o)
    THINKING  = 2, // AI đang phân tích Intent / STT / LLM (-_-)
    SPEAKING  = 3  // Loa đang phát giọng trả lời (^o^)
};

// 3 Nhóm Ý định Người dùng theo Lưu đồ Phân hệ 5
enum class UserIntentGroup : uint8_t {
    GROUP_1_STATUS_QUERY   = 1, // Nhóm 1: Hỏi thăm tình trạng cây
    GROUP_2_DEVICE_CONTROL = 2, // Nhóm 2: Điều khiển thiết bị (Tưới / Dừng / Tự động / Ngủ)
    GROUP_3_COMPANION_CHAT = 3  // Nhóm 3: Trò chuyện với cây & Giải trí
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

struct OledDisplayInstance {
    TwoWire *bus = nullptr;
    uint8_t busId = 0;
    uint8_t addr = 0x00;
    bool ready = false;
    uint8_t nextPage = 0;
    uint8_t rotation = 1; // 1 = 90° CW (Mắt Trái), 3 = 270° CW (Mắt Phải)
    uint8_t buffer[OLED_WIDTH * OLED_PAGES];
};

// Cấu trúc Dữ liệu Cảm biến Toàn cục trong RAM (SystemSensorsData - Phân hệ 2)
struct SystemSensorsData {
    uint16_t soilAdcRaw      = 2048;   // Raw 12-bit ADC (0..4095)
    uint16_t soilAdcNorm     = 1800;   // ADC quy ước lưu đồ (> 1100 là Đủ nước)
    int      soilMv          = 1850;   // Điện áp mV đã lọc Moving Average
    float    soilPct         = 55.0f;  // Độ ẩm đất quy đổi (0..100%)
    float    lightLux        = 450.0f; // Cường độ ánh sáng BH1750 (Lux)
    float    airTempC        = 27.0f;  // Nhiệt độ môi trường AHT20/BMP280 (°C)
    float    airHumPct       = 60.0f;  // Độ ẩm không khí AHT20 (%RH)
    float    pressHpa        = 1008.5f;// Áp suất khí quyển BMP280 (hPa)
    float    altitudeM       = 35.0f;  // Độ cao ước tính BMP280 (m)
    float    accelX          = 0.0f;   // Gia tốc trục X (g)
    float    accelY          = 0.0f;   // Gia tốc trục Y (g)
    float    accelZ          = 1.0f;   // Gia tốc trục Z (g)
    float    accelMag        = 1.0f;   // Vector gia tốc tổng hợp a = sqrt(X^2+Y^2+Z^2)
    float    dynG            = 0.0f;   // Gia tốc rung động |a - 1g|
    float    rollDeg         = 0.0f;   // Góc Roll (°)
    float    pitchDeg        = 0.0f;   // Góc Pitch (°)
    float    yawDeg          = 0.0f;   // Góc Yaw (°)
    float    tiltDeg         = 0.0f;   // Góc nghiêng chậu cây tổng hợp (°)
    bool     waterTankOk     = true;   // Mực nước bình chứa XKC-Y25 (true = Đủ nước)
    float    micRmsPct       = 0.0f;   // Biên độ âm thanh RMS (%)
    float    micDbSpl        = 30.0f;  // Cường độ âm thanh (dB SPL)
    bool     micVadActive    = false;  // Phát hiện tiếng nói (VAD)
    bool     sensorFaultFlag = false;  // Cờ SENSOR_FAULT khi có dữ liệu dị thường
};

// Cấu trúc Đánh giá 6 Điều kiện Môi trường Cây trồng (PlantEnvironmentState - Phân hệ 2)
struct PlantEnvironmentState {
    bool soilMoistureSufficient = true;  // 1. Độ ẩm đất: ADC > 1100 & soilPct >= ngưỡng
    bool lightSuitable          = true;  // 2. Ánh sáng: 10 Lux <= Lux <= 3500 Lux
    bool tempSuitable           = true;  // 3. Nhiệt độ: 18°C <= T <= 32°C (Cảnh báo >35°C / <15°C)
    bool airQualityNormal       = true;  // 4. Không khí / Độ ẩm KK & Áp suất ổn định
    bool waterTankSafe          = true;  // 5. Bình chứa nước còn đủ nước an toàn để tưới
    bool potPositionStable      = true;  // 6. Chậu cây đứng vững, không bị rung lắc / nghiêng đổ
    bool weatherRainWarning     = false; // Cảnh báo áp suất khí quyển tụt nhanh báo mưa
    const char *healthSummary   = "HEALTHY";
};

// Bản ghi Bộ nhớ đệm khi mất Wi-Fi (Offline Backlog Ring Buffer - Phân hệ 6)
struct OfflineBacklogRecord {
    uint32_t timestampSec;
    float    soilPct;
    uint16_t soilAdc;
    float    tempC;
    float    airHumPct;
    float    lightLux;
    float    pressHpa;
    float    tiltDeg;
    bool     tankOk;
    bool     pumpOn;
    uint8_t  cycles;
    uint16_t errCodes;
};

// Nốt nhạc cho Bộ tổng hợp Âm thanh I2S MAX98357A (Phân hệ 3 & 5)
struct AudioToneStep {
    uint16_t freqHz;
    uint16_t durationMs;
};

// ============================================================================
// 4. BIẾN TOÀN CỤC CỦA HỆ THỐNG (GLOBAL STATE)
// ============================================================================

static Preferences g_prefs;

// Bus I2C 0 (Wire: GPIO 11/12) & Bus I2C 1 (Wire1: GPIO 1/2)
static TwoWire &BUS0_I2C = Wire;  // MPU6050 (0x68) + OLED 1 (0x3C)
static TwoWire &BUS1_I2C = Wire1; // AHT20 (0x38) + BMP280 (0x76/0x77) + BH1750 (0x23/0x5C) + OLED 2 (0x3C)
static bool g_wire1Started = false;

// Cấu trúc dữ liệu trung tâm Phân hệ 2
static SystemSensorsData     g_sensors;
static PlantEnvironmentState g_plantState;
static uint16_t              g_errCodes = ERR_NONE;

// Trạng thái MPU6050 (Bus I2C 0)
static uint8_t  g_mpuAddr          = 0x00;
static uint8_t  g_whoAmI           = 0x00;
static bool     g_mpuReady         = false;
static uint32_t g_sampleIntervalMs = 40; // 25 Hz
static uint32_t g_lastFilterMs     = 0;
static uint32_t g_lastOutputMs     = 0;
static uint32_t g_lastMpuRetryMs   = 0;
static uint32_t g_lastFpsMs        = 0;
static uint32_t g_frameCounter     = 0;
static uint32_t g_measuredHz       = 0;
static uint32_t g_lastIntegrateUs  = 0;

// Cấu hình Ngủ & Thức dậy qua MPU6050 INT (GPIO 10)
static bool           g_autoSleepEnabled = false; // Mặc định tắt tự ngủ để giám sát liên tục, bật qua lệnh SLEEP=ON
static SleepMode      g_sleepMode        = SleepMode::LIGHT;
static ActiveViewMode g_viewMode         = ActiveViewMode::MPU;
static uint32_t       g_idleSleepMs      = 15000;
static uint32_t       g_idleStartMs      = 0;

static float g_roll   = 0.0f;
static float g_pitch  = 0.0f;
static float g_yaw    = 0.0f;
static float g_dynG   = 0.0f;
static float g_amag   = 1.0f;
static float g_tiltDeg = 0.0f;

static float g_filtAx = 0.0f, g_filtAy = 0.0f, g_filtAz = 1.0f;
static float g_outGx  = 0.0f, g_outGy  = 0.0f, g_outGz  = 0.0f;
static float g_lastTempC = 27.0f;
static float g_prevAx = 0.0f, g_prevAy = 0.0f, g_prevAz = 1.0f;
static float g_prevRawGx = 0.0f, g_prevRawGy = 0.0f, g_prevRawGz = 0.0f;
static bool  g_hasPrevSample = false;
static bool  g_isStill       = false;
static uint16_t g_stillTicks = 0;
static uint16_t g_biasTicks  = 0;

// Nhận diện tương tác vật lý MPU6050 (Tap / Double-Tap / Shake / Tilt)
static uint32_t g_lastTapDetectedMs  = 0;
static uint8_t  g_tapCountWindow     = 0;
static uint32_t g_tapEmotionHoldMs   = 0;
static uint32_t g_shakeEmotionHoldMs = 0;
static uint32_t g_lastShakeEventMs   = 0;
static uint32_t g_lastTiltEventMs    = 0;
static bool     g_tapPeakLatch       = false;

static MpuCalibration g_cal;
static String         g_cmdBuffer;
static volatile bool  g_motionIrq = false;

// Trạng thái AHT20 (Bus I2C 1 - 0x38)
static bool     g_ahtReady   = false;
static bool     g_ahtWaiting = false;
static bool     g_ahtValid   = false;
static uint32_t g_ahtTrigMs  = 0;
static float    g_ahtTempC   = 27.0f;
static float    g_ahtHumPct  = 60.0f;

// Trạng thái BMP280 (Bus I2C 1 - 0x76 / 0x77)
static uint8_t     g_bmpAddr         = 0x00;
static uint8_t     g_bmpChipId       = 0x00;
static bool        g_bmpReady        = false;
static bool        g_bmpValid        = false;
static Bmp280Calib g_bmpCal{};
static float       g_bmpTempC        = 27.0f;
static float       g_bmpPressHpa     = 1008.5f;
static float       g_bmpAltM         = 35.0f;
static float       g_pressBaselineHpa = 0.0f;

// Trạng thái BH1750 (Bus I2C 1 - 0x23 / 0x5C)
static uint8_t g_bhAddr  = 0x00;
static bool    g_bhReady = false;
static bool    g_bhValid = false;
static float   g_bhLux   = 450.0f;

static uint32_t g_lastEnvOutputMs = 0;
static uint32_t g_lastEnvProbeMs  = 0;

// Trạng thái Cảm biến Độ ẩm Đất (Moving Average Filter 16 mẫu - Phân hệ 2 & 3)
static constexpr size_t SOIL_FILTER_LEN = 16;
static uint16_t g_soilAdcBuf[SOIL_FILTER_LEN] = {0};
static int      g_soilMvBuf[SOIL_FILTER_LEN]  = {0};
static size_t   g_soilBufIdx     = 0;
static bool     g_soilBufFilled  = false;
static int      g_soilDryMv      = 2850;  // Mốc Đất Khô 0% (mV)
static int      g_soilWetMv      = 1250;  // Mốc Đất Ướt 100% (mV)
static uint16_t g_soilRawAdc     = 2048;
static uint16_t g_soilFlowAdc    = 1800;  // Quy đổi theo lưu đồ (> 1100 là Đủ nước)
static int      g_soilMv         = 1850;
static float    g_soilPct        = 58.0f;
static bool     g_soilSimMode    = false; // Tự bật nếu chân ADC để hở hoặc chỉnh qua lệnh SOIL_SIM
static float    g_soilSimPct     = 58.0f;
static uint32_t g_lastSoilReadMs = 0;

// Trạng thái Cảm biến Mực nước Bình chứa (XKC-Y25-NPN) & Thuật toán Tưới Cây (Phân hệ 3)
static bool          g_waterLevelActiveHigh = true;
static bool          g_tankManualOverride   = true;  // Mặc định true để an toàn khi chưa cắm phao
static bool          g_tankManualState      = true;  // true = Bình đủ nước
static bool          g_tankOk               = true;
static uint8_t       g_estimatedTankCycles  = 18;
static bool          g_pumpAutoMode         = true;  // AUTO / MANUAL
static bool          g_pumpOn               = false;
static WateringPhase g_pumpPhase            = WateringPhase::IDLE;
static float         g_soilThreshPct        = 30.0f; // Ngưỡng độ ẩm đất (%) tương đương ADC = 1100
static uint16_t      g_soilThreshAdc        = FLOWCHART_SOIL_ADC_THRESHOLD; // 1100
static uint8_t       g_wateringCycles       = 0;     // Chu kỳ tưới liên tiếp (0..5)
static uint16_t      g_dailyWateringCycles  = 0;
static bool          g_wateringFaultError   = false; // Cờ WATERING_FAULT_ERROR khi >= 5 chu kỳ
static uint32_t      g_pumpPhaseStartMs     = 0;
static uint32_t      g_lastAutoWaterCheckMs = 0;
static bool          g_tankEmptyAlertSent   = false;

// Trạng thái Micro số I2S INMP441 (Phân hệ 5)
static bool     g_micDriverInstalled = false;
static bool     g_micSignalActive    = false;
static uint32_t g_lastMicPollMs      = 0;
static uint32_t g_lastMicOutputMs    = 0;
static uint32_t g_lastMicActiveMs    = 0;
static float    g_micDcOffset        = 0.0f;
static float    g_micRmsPct          = 0.0f;
static float    g_micPeakPct         = 0.0f;
static float    g_micDbfs            = -90.0f;
static float    g_micDbSpl           = 30.0f;
static float    g_micGain            = 1.0f;
static float    g_micVadThreshPct    = 8.0f;
static bool     g_micVad             = false;
static uint32_t g_micVadHoldMs       = 0;
static int8_t   g_micWave[8]         = {0, 0, 0, 0, 0, 0, 0, 0};

// Trạng thái Khuếch đại Loa I2S MAX98357A & Tương tác AI Chatbot (Phân hệ 5)
static bool          g_spkDriverInstalled = false;
static bool          g_audioMuted         = false;
static VoiceAiState  g_voiceState         = VoiceAiState::IDLE;
static uint32_t      g_voiceStateHoldMs   = 0;
static String        g_lastAiReply        = "Hôm nay mình khỏe re luôn!";
static AudioToneStep g_toneQueue[12];
static uint8_t       g_toneQueueLen       = 0;
static uint8_t       g_toneQueueIdx       = 0;
static uint32_t      g_toneStepStartMs    = 0;
static bool          g_tonePlaying        = false;

// Trạng thái 2 Màn hình OLED (Phân hệ 4)
// OLED 1: Bus I2C 0 (SDA=11, SCL=12, Addr=0x3C) - Mắt Trái
// OLED 2: Bus I2C 1 (SDA=1,  SCL=2,  Addr=0x3C) - Mắt Phải
static OledDisplayInstance g_oled1;
static OledDisplayInstance g_oled2;
static OledDisplayMode     g_oledMode        = OledDisplayMode::EYES;
static bool                g_oledAutoEmotion = true;
static EyeEmotion          g_manualEmotion   = EyeEmotion::NORMAL;
static EyeEmotion          g_currentEmotion  = EyeEmotion::NORMAL;
static uint8_t             g_oledContrast    = 220;
static bool                g_oledInvert      = false;
static bool                g_oledPower       = true;
static bool                g_oledSwapLr      = false;
static uint8_t             g_oled1Rot        = 1; // 90° CW (chân hướng vào giữa)
static uint8_t             g_oled2Rot        = 3; // 270° CW (chân hướng vào giữa)
static String              g_oledText1       = "SMART PLANT";
static String              g_oledText2       = "ALL SYSTEMS OK";
static uint32_t            g_lastOledRenderMs = 0;
static uint32_t            g_lastOledFlushMs  = 0;
static uint32_t            g_lastOledOutputMs = 0;
static uint32_t            g_nextBlinkMs      = 3500;
static uint32_t            g_blinkStartMs     = 0;
static bool                g_isBlinking       = false;
static int8_t              g_pupilOffsetX     = 0;
static int8_t              g_pupilOffsetY     = 0;

// Trạng thái Kết nối Wi-Fi, NTP & Bộ nhớ đệm Offline Backlog (Phân hệ 1 & 6)
static String               g_wifiSsid        = "";
static String               g_wifiPass        = "";
static bool                 g_wifiConfigured  = false;
static bool                 g_wifiOnline      = false;
static bool                 g_ntpSynced       = false;
static uint32_t             g_lastWifiCheckMs = 0;
static uint32_t             g_lastBacklogSaveMs = 0;
static constexpr size_t     MAX_BACKLOG_ITEMS = 32;
static OfflineBacklogRecord g_backlog[MAX_BACKLOG_ITEMS];
static size_t               g_backlogHead     = 0;
static size_t               g_backlogCount    = 0;

// Chế độ tương thích xuất text Serial cho smart_plant_app.py (Nhóm 1/2/3)
static uint8_t  g_legacyGroupMode   = 0; // 0 = JSON chuẩn, 1 = Nhóm 1, 2 = Nhóm 2, 3 = Nhóm 3
static uint32_t g_lastLegacyPrintMs = 0;
static uint32_t g_lastDispatcherMs  = 0;

// Khai báo nguyên mẫu hàm (Forward declarations)
static void triggerWateringPulse(bool fromManualOrVoice);
static void emergencyStopPump(const char *reason);
static void resetWateringFault();
static void playSoundEffect(const char *sfxName);
static void emitOledPacket();
static void emitEnvPacket();
static void emitMicPacket();
static void emitFullTelemetryEvent(const char *triggerTag);

static void IRAM_ATTR onMotionPinIsr() {
    g_motionIrq = true;
}

// ============================================================================
// 5. QUẢN LÝ LƯU TRỮ CẤU HÌNH & MÃ LỖI TRONG FLASH NVS (PHÂN HỆ 1)
// ============================================================================

static void loadSystemConfigFromNvs() {
    // 1. Hiệu chuẩn MPU6050
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

    // 2. Cấu hình Cây trồng, Độ ẩm đất, Tưới & Wi-Fi
    g_prefs.begin("plant_cfg", true);
    g_soilDryMv            = g_prefs.getInt("dryMv", 2850);
    g_soilWetMv            = g_prefs.getInt("wetMv", 1250);
    g_soilThreshPct        = g_prefs.getFloat("thPct", 30.0f);
    g_soilThreshAdc        = g_prefs.getUShort("thAdc", FLOWCHART_SOIL_ADC_THRESHOLD);
    g_pumpAutoMode         = g_prefs.getBool("autoPump", true);
    g_waterLevelActiveHigh = g_prefs.getBool("tankPol", true);
    g_tankManualOverride   = g_prefs.getBool("tankOvr", true);
    g_oled1Rot             = g_prefs.getUChar("rot1", 1);
    g_oled2Rot             = g_prefs.getUChar("rot2", 3);
    g_oledSwapLr           = g_prefs.getBool("swapLr", false);
    g_oledContrast         = g_prefs.getUChar("contrast", 220);
    g_wifiSsid             = g_prefs.getString("ssid", "");
    g_wifiPass             = g_prefs.getString("pass", "");
    g_wifiConfigured       = (g_wifiSsid.length() > 0);
    g_prefs.end();
}

static void savePlantConfigToNvs() {
    g_prefs.begin("plant_cfg", false);
    g_prefs.putInt("dryMv", g_soilDryMv);
    g_prefs.putInt("wetMv", g_soilWetMv);
    g_prefs.putFloat("thPct", g_soilThreshPct);
    g_prefs.putUShort("thAdc", g_soilThreshAdc);
    g_prefs.putBool("autoPump", g_pumpAutoMode);
    g_prefs.putBool("tankPol", g_waterLevelActiveHigh);
    g_prefs.putBool("tankOvr", g_tankManualOverride);
    g_prefs.putUChar("rot1", g_oled1Rot);
    g_prefs.putUChar("rot2", g_oled2Rot);
    g_prefs.putBool("swapLr", g_oledSwapLr);
    g_prefs.putUChar("contrast", g_oledContrast);
    g_prefs.putString("ssid", g_wifiSsid);
    g_prefs.putString("pass", g_wifiPass);
    g_prefs.end();
}

static void recordHardwareFaultToNvs(uint16_t errMask) {
    g_prefs.begin("plant_cfg", false);
    uint16_t prev = g_prefs.getUShort("lastErr", 0);
    if (prev != errMask) {
        g_prefs.putUShort("lastErr", errMask);
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
// 6. GIAO TIẾP I2C MỨC THẤP CHO 2 BUS ĐỘC LẬP (BUS 0 & BUS 1)
// ============================================================================
// Bus I2C 0 (Wire  - SDA=11, SCL=12): MPU6050 (0x68) + OLED 1 (0x3C)
// Bus I2C 1 (Wire1 - SDA=1,  SCL=2) : AHT20 (0x38) + BMP280 (0x76/0x77) + BH1750 (0x23/0x5C) + OLED 2 (0x3C)

static bool i2cProbeBusAddr(TwoWire &bus, uint8_t addr) {
    bus.beginTransmission(addr);
    return bus.endTransmission(true) == 0;
}

static bool i2cWriteBusReg(TwoWire &bus, uint8_t addr, uint8_t reg, uint8_t val) {
    bus.beginTransmission(addr);
    bus.write(reg);
    bus.write(val);
    return bus.endTransmission(true) == 0;
}

static bool i2cWriteBusCmd(TwoWire &bus, uint8_t addr, const uint8_t *bytes, size_t len) {
    bus.beginTransmission(addr);
    bus.write(bytes, len);
    return bus.endTransmission(true) == 0;
}

static bool i2cReadBusBytes(TwoWire &bus, uint8_t addr, uint8_t reg, uint8_t *buf, size_t len) {
    bus.beginTransmission(addr);
    bus.write(reg);
    if (bus.endTransmission(false) != 0) {
        return false;
    }
    size_t received = bus.requestFrom(static_cast<int>(addr), static_cast<int>(len), static_cast<int>(true));
    if (received != len) {
        while (bus.available()) {
            bus.read();
        }
        return false;
    }
    for (size_t i = 0; i < len; ++i) {
        buf[i] = static_cast<uint8_t>(bus.read());
    }
    return true;
}

static bool i2cReadBusRaw(TwoWire &bus, uint8_t addr, uint8_t *buf, size_t len) {
    size_t received = bus.requestFrom(static_cast<int>(addr), static_cast<int>(len), static_cast<int>(true));
    if (received != len) {
        while (bus.available()) {
            bus.read();
        }
        return false;
    }
    for (size_t i = 0; i < len; ++i) {
        buf[i] = static_cast<uint8_t>(bus.read());
    }
    return true;
}

// Helper cho Bus I2C 0 (MPU6050)
static inline bool i2cProbeAddr(uint8_t addr) {
    return i2cProbeBusAddr(BUS0_I2C, addr);
}

static inline bool i2cWriteReg(uint8_t addr, uint8_t reg, uint8_t val) {
    return i2cWriteBusReg(BUS0_I2C, addr, reg, val);
}

static inline bool i2cReadBytes(uint8_t addr, uint8_t reg, uint8_t *buf, size_t len) {
    return i2cReadBusBytes(BUS0_I2C, addr, reg, buf, len);
}

// ============================================================================
// 7. DRIVER CẢM BIẾN TRÊN BUS I2C 1 (AHT20 + BMP280 + BH1750)
// ============================================================================

// --- 7.1. AHT20 (0x38 trên Bus I2C 1: SDA = GPIO 1, SCL = GPIO 2) ---
static bool probeAndInitAht20() {
    if (!i2cProbeBusAddr(BUS1_I2C, AHT20_ADDR)) {
        g_ahtReady = false;
        g_ahtValid = false;
        g_ahtWaiting = false;
        return false;
    }
    uint8_t status = 0;
    uint8_t cmdStatus = 0x71;
    if (i2cWriteBusCmd(BUS1_I2C, AHT20_ADDR, &cmdStatus, 1) &&
        i2cReadBusRaw(BUS1_I2C, AHT20_ADDR, &status, 1)) {
        if ((status & 0x08) == 0) {
            const uint8_t initCmd[3] = { 0xBE, 0x08, 0x00 };
            if (!i2cWriteBusCmd(BUS1_I2C, AHT20_ADDR, initCmd, sizeof(initCmd))) {
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
            if (i2cWriteBusCmd(BUS1_I2C, AHT20_ADDR, trigCmd, sizeof(trigCmd))) {
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
    if (!i2cReadBusRaw(BUS1_I2C, AHT20_ADDR, buf, sizeof(buf))) {
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

        float newHum  = (static_cast<float>(rawHum) * 100.0f) / 1048576.0f;
        float newTemp = ((static_cast<float>(rawTemp) * 200.0f) / 1048576.0f) - 50.0f;

        // Kiểm tra tính hợp lệ dữ liệu vật lý (Phân hệ 2: Sanity Check)
        if (newTemp >= -20.0f && newTemp <= 85.0f && newHum >= 0.0f && newHum <= 100.0f) {
            g_ahtHumPct = newHum;
            g_ahtTempC  = newTemp;
            g_ahtValid  = true;
            g_errCodes &= ~ERR_OUTLIER_DATA;
        } else {
            // Dữ liệu dị thường -> Giữ mẫu hợp lệ gần nhất, bật cờ SENSOR_FAULT
            g_errCodes |= ERR_OUTLIER_DATA;
        }
        g_ahtWaiting = false;
    } else if (nowMs - g_ahtTrigMs >= 250) {
        g_ahtWaiting = false;
    }
}

// --- 7.2. BMP280 (0x76 / 0x77 trên Bus I2C 1: SDA = GPIO 1, SCL = GPIO 2) ---
static bool probeAndInitBmp280() {
    const uint8_t candidates[2] = { BMP280_ADDR_LOW, BMP280_ADDR_HIGH };
    uint8_t foundAddr = 0x00;
    uint8_t chipId = 0x00;

    for (uint8_t addr : candidates) {
        if (i2cProbeBusAddr(BUS1_I2C, addr) &&
            i2cReadBusBytes(BUS1_I2C, addr, BMP_REG_CHIP_ID, &chipId, 1)) {
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

    i2cWriteBusReg(BUS1_I2C, g_bmpAddr, BMP_REG_RESET, 0xB6);
    delay(10);

    uint8_t cal[24] = {0};
    if (!i2cReadBusBytes(BUS1_I2C, g_bmpAddr, BMP_REG_CALIB_00, cal, sizeof(cal))) {
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

    if (!i2cWriteBusReg(BUS1_I2C, g_bmpAddr, BMP_REG_CONFIG, 0x48)) {
        g_bmpReady = false;
        return false;
    }
    if (!i2cWriteBusReg(BUS1_I2C, g_bmpAddr, BMP_REG_CTRL_MEAS, 0x57)) {
        g_bmpReady = false;
        return false;
    }

    g_bmpReady = true;
    return true;
}

static bool readBmp280() {
    if (!g_bmpReady) return false;

    uint8_t raw[6] = {0};
    if (!i2cReadBusBytes(BUS1_I2C, g_bmpAddr, BMP_REG_PRESS_MSB, raw, sizeof(raw))) {
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
    float tempC    = static_cast<float>(tempX100) / 100.0f;

    // Kiểm tra dải đo vật lý hợp lệ (Phân hệ 2)
    if (pressHpa < 300.0f || pressHpa > 1200.0f || tempC < -40.0f || tempC > 85.0f) {
        g_errCodes |= ERR_OUTLIER_DATA;
        return false;
    }

    g_bmpTempC    = tempC;
    g_bmpPressHpa = pressHpa;
    g_bmpAltM     = 44330.0f * (1.0f - powf(pressHpa / 1013.25f, 0.1903f));
    if (g_pressBaselineHpa <= 100.0f) {
        g_pressBaselineHpa = pressHpa;
    } else {
        g_pressBaselineHpa = 0.998f * g_pressBaselineHpa + 0.002f * pressHpa;
    }
    g_bmpValid = true;
    return true;
}

// --- 7.3. BH1750 (0x23 / 0x5C trên Bus I2C 1: SDA = GPIO 1, SCL = GPIO 2) ---
static bool probeAndInitBh1750() {
    const uint8_t candidates[2] = { BH1750_ADDR_LOW, BH1750_ADDR_HIGH };
    uint8_t foundAddr = 0x00;

    for (uint8_t addr : candidates) {
        if (i2cProbeBusAddr(BUS1_I2C, addr)) {
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
    const uint8_t contHRes = 0x10;
    if (!i2cWriteBusCmd(BUS1_I2C, foundAddr, &pwrOn, 1)) {
        g_bhReady = false;
        return false;
    }
    delay(5);
    if (!i2cWriteBusCmd(BUS1_I2C, foundAddr, &contHRes, 1)) {
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
    if (!i2cReadBusRaw(BUS1_I2C, g_bhAddr, raw, sizeof(raw))) {
        g_bhReady = false;
        g_bhValid = false;
        return false;
    }
    uint16_t level = (static_cast<uint16_t>(raw[0]) << 8) | raw[1];
    float lux = static_cast<float>(level) / 1.2f;
    if (lux < 0.0f || lux > 100000.0f) {
        g_errCodes |= ERR_OUTLIER_DATA;
        return false;
    }
    g_bhLux = lux;
    g_bhValid = true;
    return true;
}

// ============================================================================
// 8. ĐỌC & LỌC NHIỄU ĐỘ ẨM ĐẤT (ADC) VÀ MỰC NƯỚC BÌNH CHỨA (PHÂN HỆ 2)
// ============================================================================

// Quy đổi % độ ẩm đất sang giá trị ADC quy ước của Lưu đồ thuật toán
// (Tại ngưỡng tưới g_soilThreshPct -> ADC = 1100; Đất đủ ẩm -> ADC > 1100; Đất khô -> ADC <= 1100)
static uint16_t soilPctToFlowchartAdc(float pct) {
    float th = fmaxf(5.0f, fminf(95.0f, g_soilThreshPct));
    if (pct <= th) {
        float ratio = fmaxf(0.0f, pct) / th;
        return static_cast<uint16_t>(ratio * static_cast<float>(g_soilThreshAdc));
    }
    float ratio = (fminf(100.0f, pct) - th) / (100.0f - th);
    return static_cast<uint16_t>(g_soilThreshAdc + ratio * (4095.0f - g_soilThreshAdc));
}

static void pollSoilAndWaterLevel(uint32_t nowMs) {
    if (nowMs - g_lastSoilReadMs < 150) return;
    g_lastSoilReadMs = nowMs;

    // 1. Đọc ADC 12-bit & Điện áp mV từ cảm biến độ ẩm đất điện dung v1.0
    uint16_t rawAdc = analogRead(SOIL_ADC_PIN);
    int rawMv       = analogReadMilliVolts(SOIL_ADC_PIN);

    g_soilAdcBuf[g_soilBufIdx] = rawAdc;
    g_soilMvBuf[g_soilBufIdx]  = rawMv;
    g_soilBufIdx = (g_soilBufIdx + 1) % SOIL_FILTER_LEN;
    if (g_soilBufIdx == 0) {
        g_soilBufFilled = true;
    }

    size_t count = g_soilBufFilled ? SOIL_FILTER_LEN : (g_soilBufIdx > 0 ? g_soilBufIdx : 1);
    uint32_t sumAdc = 0;
    int32_t  sumMv  = 0;
    for (size_t i = 0; i < count; ++i) {
        sumAdc += g_soilAdcBuf[i];
        sumMv  += g_soilMvBuf[i];
    }
    uint16_t avgAdc = static_cast<uint16_t>(sumAdc / count);
    int      avgMv  = static_cast<int>(sumMv / static_cast<int32_t>(count));

    // Nếu đang bật chế độ mô phỏng độ ẩm đất hoặc chân ADC chưa cắm cảm biến (mV < 80 mV)
    if (g_soilSimMode || avgMv < 80 || avgMv > 3250) {
        g_soilPct     = g_soilSimPct;
        g_soilMv      = static_cast<int>(g_soilDryMv - (g_soilPct / 100.0f) * (g_soilDryMv - g_soilWetMv));
        g_soilFlowAdc = soilPctToFlowchartAdc(g_soilPct);
        g_soilRawAdc  = g_soilFlowAdc;
    } else {
        g_soilRawAdc = avgAdc;
        g_soilMv     = avgMv;
        int span = g_soilDryMv - g_soilWetMv;
        if (abs(span) < 100) span = 1600;
        float pct = (static_cast<float>(g_soilDryMv - avgMv) * 100.0f) / static_cast<float>(span);
        if (pct < 0.0f)   pct = 0.0f;
        if (pct > 100.0f) pct = 100.0f;
        g_soilPct     = pct;
        g_soilFlowAdc = soilPctToFlowchartAdc(pct);
    }

    // 2. Đọc cảm biến mực nước bình chứa XKC-Y25-NPN (GPIO 7)
    if (g_tankManualOverride) {
        g_tankOk = g_tankManualState;
    } else {
        int pinVal = digitalRead(WATER_LEVEL_PIN);
        g_tankOk = g_waterLevelActiveHigh ? (pinVal == HIGH) : (pinVal == LOW);
    }
    if (!g_tankOk) {
        g_estimatedTankCycles = 0;
    }
}

static void emitEnvPacket() {
    Serial.printf(
        "{\"type\":\"env\","
        "\"aht_ok\":%s,\"aht_t\":%.2f,\"aht_h\":%.1f,"
        "\"bmp_ok\":%s,\"bmp_addr\":\"0x%02X\",\"bmp_id\":\"0x%02X\",\"bmp_t\":%.2f,\"bmp_p\":%.2f,\"bmp_alt\":%.1f,"
        "\"bh_ok\":%s,\"bh_addr\":\"0x%02X\",\"lux\":%.1f,"
        "\"soil_pct\":%.1f,\"soil_mv\":%d,\"soil_adc\":%u,\"tank_ok\":%s,\"pump_on\":%s}\n",
        (g_ahtReady && g_ahtValid) ? "true" : "false", g_ahtTempC, g_ahtHumPct,
        (g_bmpReady && g_bmpValid) ? "true" : "false", g_bmpAddr, g_bmpChipId, g_bmpTempC, g_bmpPressHpa, g_bmpAltM,
        (g_bhReady && g_bhValid) ? "true" : "false", g_bhAddr, g_bhLux,
        g_soilPct, g_soilMv, g_soilFlowAdc,
        g_tankOk ? "true" : "false",
        g_pumpOn ? "true" : "false"
    );
}

// ============================================================================
// 9. MICRO I2S INMP441 & LOA I2S MAX98357A (PHÂN HỆ 5)
// ============================================================================

static bool initMicrophoneI2s() {
    if (g_micDriverInstalled) {
        return true;
    }

    i2s_config_t i2s_config = {};
    i2s_config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
    i2s_config.sample_rate = 16000;
    i2s_config.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
    i2s_config.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
    i2s_config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    i2s_config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    i2s_config.dma_buf_count = 4;
    i2s_config.dma_buf_len = 128;
    i2s_config.use_apll = false;
    i2s_config.tx_desc_auto_clear = false;
    i2s_config.fixed_mclk = 0;

    i2s_pin_config_t pin_config = {};
    pin_config.mck_io_num = I2S_PIN_NO_CHANGE;
    pin_config.bck_io_num = MIC_SCK_PIN;
    pin_config.ws_io_num = MIC_WS_PIN;
    pin_config.data_out_num = I2S_PIN_NO_CHANGE;
    pin_config.data_in_num = MIC_SD_PIN;

    if (i2s_driver_install(I2S_NUM_0, &i2s_config, 0, nullptr) != ESP_OK) {
        g_micDriverInstalled = false;
        return false;
    }
    if (i2s_set_pin(I2S_NUM_0, &pin_config) != ESP_OK) {
        i2s_driver_uninstall(I2S_NUM_0);
        g_micDriverInstalled = false;
        return false;
    }
    i2s_zero_dma_buffer(I2S_NUM_0);
    g_micDriverInstalled = true;
    return true;
}

static bool initSpeakerI2s() {
    if (g_spkDriverInstalled) {
        return true;
    }
    pinMode(SPK_SD_PIN, OUTPUT);
    digitalWrite(SPK_SD_PIN, LOW); // Tắt khuếch đại khi chưa phát âm thanh

    i2s_config_t tx_config = {};
    tx_config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
    tx_config.sample_rate = 16000;
    tx_config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
    tx_config.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
    tx_config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    tx_config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    tx_config.dma_buf_count = 4;
    tx_config.dma_buf_len = 128;
    tx_config.use_apll = false;
    tx_config.tx_desc_auto_clear = true;
    tx_config.fixed_mclk = 0;

    i2s_pin_config_t tx_pins = {};
    tx_pins.mck_io_num = I2S_PIN_NO_CHANGE;
    tx_pins.bck_io_num = SPK_BCLK_PIN;
    tx_pins.ws_io_num = SPK_LRCLK_PIN;
    tx_pins.data_out_num = SPK_DIN_PIN;
    tx_pins.data_in_num = I2S_PIN_NO_CHANGE;

    if (i2s_driver_install(I2S_NUM_1, &tx_config, 0, nullptr) != ESP_OK) {
        g_spkDriverInstalled = false;
        return false;
    }
    if (i2s_set_pin(I2S_NUM_1, &tx_pins) != ESP_OK) {
        i2s_driver_uninstall(I2S_NUM_1);
        g_spkDriverInstalled = false;
        return false;
    }
    i2s_zero_dma_buffer(I2S_NUM_1);
    g_spkDriverInstalled = true;
    return true;
}

// Xếp hàng chuỗi âm thanh phát qua Loa I2S MAX98357A không chặn (Non-blocking)
static void enqueueAudioTones(const AudioToneStep *steps, uint8_t count, bool setSpeakingState) {
    if (g_audioMuted || !g_spkDriverInstalled || count == 0) return;
    if (count > 12) count = 12;
    for (uint8_t i = 0; i < count; ++i) {
        g_toneQueue[i] = steps[i];
    }
    g_toneQueueLen    = count;
    g_toneQueueIdx    = 0;
    g_toneStepStartMs = millis();
    g_tonePlaying     = true;
    digitalWrite(SPK_SD_PIN, HIGH);
    if (setSpeakingState) {
        g_voiceState = VoiceAiState::SPEAKING;
        g_voiceStateHoldMs = millis() + 1800;
    }
}

static void playSoundEffect(const char *sfxName) {
    if (strcmp(sfxName, "BOOT") == 0) {
        const AudioToneStep s[] = { {523, 70}, {659, 70}, {784, 90}, {1047, 130} };
        enqueueAudioTones(s, 4, false);
    } else if (strcmp(sfxName, "WATERING") == 0) {
        const AudioToneStep s[] = { {587, 80}, {740, 80}, {880, 120}, {1175, 160} };
        enqueueAudioTones(s, 4, true);
    } else if (strcmp(sfxName, "TANK_EMPTY") == 0) {
        const AudioToneStep s[] = { {440, 140}, {0, 40}, {349, 180}, {0, 40}, {294, 240} };
        enqueueAudioTones(s, 5, true);
    } else if (strcmp(sfxName, "FAULT_ALARM") == 0) {
        const AudioToneStep s[] = { {980, 120}, {490, 120}, {980, 120}, {490, 120}, {980, 180} };
        enqueueAudioTones(s, 5, true);
    } else if (strcmp(sfxName, "TAP_HAPPY") == 0) {
        const AudioToneStep s[] = { {659, 60}, {784, 60}, {988, 90}, {1319, 120} };
        enqueueAudioTones(s, 4, false);
    } else if (strcmp(sfxName, "DIZZY") == 0) {
        const AudioToneStep s[] = { {700, 70}, {520, 70}, {650, 70}, {440, 110} };
        enqueueAudioTones(s, 4, true);
    } else if (strcmp(sfxName, "OFFLINE") == 0) {
        const AudioToneStep s[] = { {500, 100}, {380, 150} };
        enqueueAudioTones(s, 2, true);
    } else if (strcmp(sfxName, "SPEAK") == 0) {
        const AudioToneStep s[] = { {540, 75}, {680, 75}, {620, 75}, {760, 90}, {660, 110} };
        enqueueAudioTones(s, 5, true);
    }
}

static void pollSpeakerAudioNonBlocking(uint32_t nowMs) {
    if (!g_tonePlaying || !g_spkDriverInstalled) return;

    if (g_toneQueueIdx >= g_toneQueueLen) {
        g_tonePlaying = false;
        i2s_zero_dma_buffer(I2S_NUM_1);
        digitalWrite(SPK_SD_PIN, LOW);
        if (g_voiceState == VoiceAiState::SPEAKING && nowMs >= g_voiceStateHoldMs) {
            g_voiceState = VoiceAiState::IDLE;
        }
        return;
    }

    const AudioToneStep &cur = g_toneQueue[g_toneQueueIdx];
    if (nowMs - g_toneStepStartMs >= cur.durationMs) {
        g_toneQueueIdx++;
        g_toneStepStartMs = nowMs;
        return;
    }

    // Tổng hợp sóng âm 16-bit stereo đẩy xuống DMA I2S_NUM_1
    static uint32_t lastSynthMs = 0;
    if (nowMs - lastSynthMs < 15) return;
    lastSynthMs = nowMs;

    int16_t stereoBuf[256]; // 128 stereo frames (~8ms @ 16kHz)
    static float phase = 0.0f;
    float freq = static_cast<float>(cur.freqHz);
    float phaseInc = (freq > 20.0f) ? (2.0f * 3.14159265f * freq / 16000.0f) : 0.0f;

    for (size_t i = 0; i < 128; ++i) {
        int16_t sample = 0;
        if (phaseInc > 0.0f) {
            sample = static_cast<int16_t>(sinf(phase) * 5500.0f);
            phase += phaseInc;
            if (phase > 6.2831853f) phase -= 6.2831853f;
        }
        stereoBuf[i * 2]     = sample;
        stereoBuf[i * 2 + 1] = sample;
    }
    size_t bytesWritten = 0;
    i2s_write(I2S_NUM_1, stereoBuf, sizeof(stereoBuf), &bytesWritten, 0);
}

static void pollMicrophoneNonBlocking(uint32_t nowMs, bool force = false) {
    if (!g_micDriverInstalled) return;
    if (!force && (nowMs - g_lastMicPollMs < 20)) return;
    g_lastMicPollMs = nowMs;

    int32_t rawBuf[256]; // 128 stereo frames
    size_t bytesRead = 0;
    esp_err_t err = i2s_read(I2S_NUM_0, rawBuf, sizeof(rawBuf), &bytesRead, pdMS_TO_TICKS(10));
    if (err != ESP_OK || bytesRead < sizeof(int32_t) * 16) {
        return;
    }

    size_t frames = bytesRead / (sizeof(int32_t) * 2);
    double sumSq = 0.0;
    float maxAbs = 0.0f;
    uint16_t nonZeroCount = 0;

    size_t bucketSize = frames / 8;
    if (bucketSize == 0) bucketSize = 1;
    float bucketSum[8] = {0};
    uint16_t bucketCnt[8] = {0};

    for (size_t i = 0; i < frames; ++i) {
        int32_t left24  = rawBuf[i * 2] >> 8;
        int32_t right24 = rawBuf[i * 2 + 1] >> 8;
        int32_t s24 = (abs(left24) >= abs(right24)) ? left24 : right24;
        if (s24 != 0 && s24 != -1) {
            ++nonZeroCount;
        }

        float norm = static_cast<float>(s24) / 8388608.0f;
        g_micDcOffset = 0.992f * g_micDcOffset + 0.008f * norm;
        float ac = (norm - g_micDcOffset) * g_micGain * 6.0f;
        if (ac > 1.0f) ac = 1.0f;
        if (ac < -1.0f) ac = -1.0f;

        float absVal = fabsf(ac);
        sumSq += static_cast<double>(ac * ac);
        if (absVal > maxAbs) {
            maxAbs = absVal;
        }

        size_t bIdx = i / bucketSize;
        if (bIdx > 7) bIdx = 7;
        bucketSum[bIdx] += ac;
        bucketCnt[bIdx]++;
    }

    if (nonZeroCount > 2) {
        g_micSignalActive = true;
        g_lastMicActiveMs = nowMs;
    } else if (nowMs - g_lastMicActiveMs > 1500) {
        g_micSignalActive = false;
    }

    float instRms = sqrtf(static_cast<float>(sumSq / frames)) * 100.0f;
    float instPeak = maxAbs * 100.0f;
    if (instRms > 100.0f) instRms = 100.0f;
    if (instPeak > 100.0f) instPeak = 100.0f;

    g_micRmsPct  = 0.65f * g_micRmsPct + 0.35f * instRms;
    g_micPeakPct = (instPeak >= g_micPeakPct) ? instPeak : (0.82f * g_micPeakPct + 0.18f * instPeak);

    float rmsRatio = fmaxf(g_micRmsPct / 100.0f, 0.0000316f);
    g_micDbfs = 20.0f * log10f(rmsRatio);
    if (g_micDbfs < -90.0f) g_micDbfs = -90.0f;
    if (g_micDbfs > 0.0f)   g_micDbfs = 0.0f;

    g_micDbSpl = 105.0f + g_micDbfs * 0.83f;
    if (g_micDbSpl < 30.0f) g_micDbSpl = 30.0f;

    if (g_micSignalActive && (g_micRmsPct >= g_micVadThreshPct || g_micPeakPct >= g_micVadThreshPct * 2.1f)) {
        g_micVad = true;
        g_micVadHoldMs = nowMs;
        if (g_voiceState == VoiceAiState::IDLE) {
            g_voiceState = VoiceAiState::LISTENING;
            g_voiceStateHoldMs = nowMs + 450;
        }
    } else if (nowMs - g_micVadHoldMs > 350) {
        g_micVad = false;
        if (g_voiceState == VoiceAiState::LISTENING && nowMs >= g_voiceStateHoldMs) {
            g_voiceState = VoiceAiState::IDLE;
        }
    }

    if (g_voiceState != VoiceAiState::IDLE && !g_tonePlaying && nowMs >= g_voiceStateHoldMs && !g_micVad) {
        g_voiceState = VoiceAiState::IDLE;
    }

    for (int b = 0; b < 8; ++b) {
        float avg = (bucketCnt[b] > 0) ? (bucketSum[b] / bucketCnt[b]) : 0.0f;
        int val = static_cast<int>(avg * 180.0f);
        if (val > 100) val = 100;
        if (val < -100) val = -100;
        g_micWave[b] = static_cast<int8_t>(val);
    }
}

// Truyền luồng PCM 16-bit 16kHz qua Serial cho Python Speech-to-Text (Lệnh REC:5)
static void streamMicPcmRecording(int sec) {
    if (!g_micDriverInstalled) return;
    if (sec < 1) sec = 1;
    if (sec > 15) sec = 15;

    g_voiceState = VoiceAiState::LISTENING;
    uint32_t totalSamples = static_cast<uint32_t>(sec) * 16000UL;
    Serial.printf("\n<<<PCM_START:16000:16:1:%lu>>>\n", static_cast<unsigned long>(totalSamples));
    Serial.flush();

    int32_t rawStereo[256];
    int16_t pcmMono[128];
    uint32_t sent = 0;

    while (sent < totalSamples) {
        size_t bytesRead = 0;
        if (i2s_read(I2S_NUM_0, rawStereo, sizeof(rawStereo), &bytesRead, pdMS_TO_TICKS(100)) != ESP_OK || bytesRead == 0) {
            continue;
        }
        size_t frames = bytesRead / (sizeof(int32_t) * 2);
        size_t toSend = frames;
        if (sent + toSend > totalSamples) {
            toSend = totalSamples - sent;
        }
        for (size_t i = 0; i < toSend; ++i) {
            int32_t left24  = rawStereo[i * 2] >> 8;
            int32_t right24 = rawStereo[i * 2 + 1] >> 8;
            int32_t s24 = (abs(left24) >= abs(right24)) ? left24 : right24;
            float s16 = static_cast<float>(s24 >> 6) * g_micGain;
            if (s16 > 32767.0f)  s16 = 32767.0f;
            if (s16 < -32768.0f) s16 = -32768.0f;
            pcmMono[i] = static_cast<int16_t>(s16);
        }
        Serial.write(reinterpret_cast<const uint8_t *>(pcmMono), toSend * sizeof(int16_t));
        sent += toSend;
    }
    Serial.flush();
    Serial.println("\n<<<PCM_END>>>");
    Serial.flush();
    g_voiceState = VoiceAiState::THINKING;
    g_voiceStateHoldMs = millis() + 1200;
}

static void emitMicPacket() {
    Serial.printf(
        "{\"type\":\"mic\",\"ok\":%s,\"ws\":%d,\"sck\":%d,\"sd\":%d,"
        "\"rms\":%.2f,\"peak\":%.2f,\"dbfs\":%.1f,\"db\":%.1f,"
        "\"vad\":%s,\"thr\":%.1f,\"gain\":%.1f,"
        "\"wave\":[%d,%d,%d,%d,%d,%d,%d,%d]}\n",
        (g_micDriverInstalled && g_micSignalActive) ? "true" : "false",
        MIC_WS_PIN, MIC_SCK_PIN, MIC_SD_PIN,
        g_micRmsPct, g_micPeakPct, g_micDbfs, g_micDbSpl,
        g_micVad ? "true" : "false",
        g_micVadThreshPct, g_micGain,
        g_micWave[0], g_micWave[1], g_micWave[2], g_micWave[3],
        g_micWave[4], g_micWave[5], g_micWave[6], g_micWave[7]
    );
}

// ============================================================================
// 10. PHÂN HỆ 4: ENGINE ĐỒ HỌA & BIỂU CẢM HAI MẮT OLED (SSD1306 / SSD1309)
// ============================================================================
// OLED 1 (Bus I2C 0 - SDA=11, SCL=12, Addr=0x3C): MẮT TRÁI
// OLED 2 (Bus I2C 1 - SDA=1,  SCL=2,  Addr=0x3C): MẮT PHẢI

static const uint8_t FONT5X7[][5] PROGMEM = {
    {0x00,0x00,0x00,0x00,0x00}, // ' '
    {0x00,0x00,0x5F,0x00,0x00}, // '!'
    {0x00,0x07,0x00,0x07,0x00}, // '"'
    {0x14,0x7F,0x14,0x7F,0x14}, // '#'
    {0x24,0x2A,0x7F,0x2A,0x12}, // '$'
    {0x23,0x13,0x08,0x64,0x62}, // '%'
    {0x36,0x49,0x55,0x22,0x50}, // '&'
    {0x00,0x05,0x03,0x00,0x00}, // '\''
    {0x00,0x1C,0x22,0x41,0x00}, // '('
    {0x00,0x41,0x22,0x1C,0x00}, // ')'
    {0x14,0x08,0x3E,0x08,0x14}, // '*'
    {0x08,0x08,0x3E,0x08,0x08}, // '+'
    {0x00,0x50,0x30,0x00,0x00}, // ','
    {0x08,0x08,0x08,0x08,0x08}, // '-'
    {0x00,0x60,0x60,0x00,0x00}, // '.'
    {0x20,0x10,0x08,0x04,0x02}, // '/'
    {0x3E,0x51,0x49,0x45,0x3E}, // '0'
    {0x00,0x42,0x7F,0x40,0x00}, // '1'
    {0x42,0x61,0x51,0x49,0x46}, // '2'
    {0x21,0x41,0x45,0x4B,0x31}, // '3'
    {0x18,0x14,0x12,0x7F,0x10}, // '4'
    {0x27,0x45,0x45,0x45,0x39}, // '5'
    {0x3C,0x4A,0x49,0x49,0x30}, // '6'
    {0x01,0x71,0x09,0x05,0x03}, // '7'
    {0x36,0x49,0x49,0x49,0x36}, // '8'
    {0x06,0x49,0x49,0x29,0x1E}, // '9'
    {0x00,0x36,0x36,0x00,0x00}, // ':'
    {0x00,0x56,0x36,0x00,0x00}, // ';'
    {0x08,0x14,0x22,0x41,0x00}, // '<'
    {0x14,0x14,0x14,0x14,0x14}, // '='
    {0x00,0x41,0x22,0x14,0x08}, // '>'
    {0x02,0x01,0x51,0x09,0x06}, // '?'
    {0x32,0x49,0x79,0x41,0x3E}, // '@'
    {0x7E,0x11,0x11,0x11,0x7E}, // 'A'
    {0x7F,0x49,0x49,0x49,0x36}, // 'B'
    {0x3E,0x41,0x41,0x41,0x22}, // 'C'
    {0x7F,0x41,0x41,0x22,0x1C}, // 'D'
    {0x7F,0x49,0x49,0x49,0x41}, // 'E'
    {0x7F,0x09,0x09,0x09,0x01}, // 'F'
    {0x3E,0x41,0x49,0x49,0x7A}, // 'G'
    {0x7F,0x08,0x08,0x08,0x7F}, // 'H'
    {0x00,0x41,0x7F,0x41,0x00}, // 'I'
    {0x20,0x40,0x41,0x3F,0x01}, // 'J'
    {0x7F,0x08,0x14,0x22,0x41}, // 'K'
    {0x7F,0x40,0x40,0x40,0x40}, // 'L'
    {0x7F,0x02,0x0C,0x02,0x7F}, // 'M'
    {0x7F,0x04,0x08,0x10,0x7F}, // 'N'
    {0x3E,0x41,0x41,0x41,0x3E}, // 'O'
    {0x7F,0x09,0x09,0x09,0x06}, // 'P'
    {0x3E,0x41,0x51,0x21,0x5E}, // 'Q'
    {0x7F,0x09,0x19,0x29,0x46}, // 'R'
    {0x46,0x49,0x49,0x49,0x31}, // 'S'
    {0x01,0x01,0x7F,0x01,0x01}, // 'T'
    {0x3F,0x40,0x40,0x40,0x3F}, // 'U'
    {0x1F,0x20,0x40,0x20,0x1F}, // 'V'
    {0x3F,0x40,0x38,0x40,0x3F}, // 'W'
    {0x63,0x14,0x08,0x14,0x63}, // 'X'
    {0x07,0x08,0x70,0x08,0x07}, // 'Y'
    {0x61,0x51,0x49,0x45,0x43}, // 'Z'
    {0x00,0x7F,0x41,0x41,0x00}, // '['
    {0x02,0x04,0x08,0x10,0x20}, // '\\'
    {0x00,0x41,0x41,0x7F,0x00}, // ']'
    {0x04,0x02,0x01,0x02,0x04}, // '^'
    {0x40,0x40,0x40,0x40,0x40}, // '_'
};

static bool oledSendCmd(OledDisplayInstance &d, uint8_t cmd) {
    if (!d.bus || d.addr == 0x00) return false;
    d.bus->beginTransmission(d.addr);
    d.bus->write(0x00);
    d.bus->write(cmd);
    return d.bus->endTransmission(true) == 0;
}

static bool oledSendCmdList(OledDisplayInstance &d, const uint8_t *cmds, size_t len) {
    for (size_t i = 0; i < len; ++i) {
        if (!oledSendCmd(d, cmds[i])) {
            d.ready = false;
            return false;
        }
    }
    return true;
}

static bool oledInitDisplay(OledDisplayInstance &d, TwoWire *bus, uint8_t busId, uint8_t addr) {
    d.bus = bus;
    d.busId = busId;
    d.addr = addr;
    d.nextPage = 0;
    memset(d.buffer, 0, sizeof(d.buffer));

    const uint8_t initSeq[] = {
        0xAE,             // Display OFF
        0xD5, 0x80,       // Clock divide ratio
        0xA8, 0x3F,       // Multiplex ratio = 63 (64 rows)
        0xD3, 0x00,       // Display offset = 0
        0x40,             // Start line = 0
        0x8D, 0x14,       // Charge pump enable (SSD1306 / SSD1309)
        0x20, 0x02,       // Page addressing mode
        0xA1,             // Segment remap
        0xC8,             // COM output scan direction
        0xDA, 0x12,       // COM pins hardware config (128x64)
        0x81, g_oledContrast,
        0xD9, 0xF1,       // Pre-charge period
        0xDB, 0x40,       // VCOMH deselect level
        0xA4,             // Entire display ON from RAM
        static_cast<uint8_t>(g_oledInvert ? 0xA7 : 0xA6),
        static_cast<uint8_t>(g_oledPower ? 0xAF : 0xAE)
    };

    if (!oledSendCmdList(d, initSeq, sizeof(initSeq))) {
        d.ready = false;
        return false;
    }
    d.ready = true;
    return true;
}

static void probeOledDisplays() {
    if (!g_wire1Started) {
        BUS1_I2C.begin(OLED2_SDA_PIN, OLED2_SCL_PIN, 400000);
        BUS1_I2C.setTimeOut(20);
        g_wire1Started = true;
    }

    // 1. OLED 1 (Mắt Trái) trên Bus I2C 0 (SDA = GPIO 11, SCL = GPIO 12, Addr = 0x3C)
    if (!g_oled1.ready) {
        const uint8_t addrs[2] = { OLED_ADDR_PRIMARY, OLED_ADDR_SECONDARY };
        for (uint8_t a : addrs) {
            if (i2cProbeBusAddr(BUS0_I2C, a)) {
                if (oledInitDisplay(g_oled1, &BUS0_I2C, 0, a)) {
                    break;
                }
            }
        }
    }

    // 2. OLED 2 (Mắt Phải) trên Bus I2C 1 (SDA = GPIO 1, SCL = GPIO 2, Addr = 0x3C)
    if (!g_oled2.ready) {
        const uint8_t addrs[2] = { OLED_ADDR_PRIMARY, OLED_ADDR_SECONDARY };
        for (uint8_t a : addrs) {
            if (i2cProbeBusAddr(BUS1_I2C, a)) {
                if (oledInitDisplay(g_oled2, &BUS1_I2C, 1, a)) {
                    break;
                }
            }
        }
        // Fallback: Nếu người dùng mắc OLED 2 chung vào Bus I2C 0 với địa chỉ 0x3D
        if (!g_oled2.ready && g_oled1.ready && g_oled1.addr == OLED_ADDR_PRIMARY) {
            if (i2cProbeBusAddr(BUS0_I2C, OLED_ADDR_SECONDARY)) {
                oledInitDisplay(g_oled2, &BUS0_I2C, 0, OLED_ADDR_SECONDARY);
            }
        }
    }
}

static void oledClear(OledDisplayInstance &d) {
    memset(d.buffer, 0, sizeof(d.buffer));
}

static inline int oledViewW(const OledDisplayInstance &d) {
    return (d.rotation & 1) ? OLED_HEIGHT : OLED_WIDTH;
}

static inline int oledViewH(const OledDisplayInstance &d) {
    return (d.rotation & 1) ? OLED_WIDTH : OLED_HEIGHT;
}

static void oledSetPixel(OledDisplayInstance &d, int x, int y, bool on = true) {
    const int vw = oledViewW(d);
    const int vh = oledViewH(d);
    if (x < 0 || x >= vw || y < 0 || y >= vh) return;

    int nx, ny;
    switch (d.rotation & 3) {
        case 0:
            nx = x;
            ny = y;
            break;
        case 1:
            nx = y;
            ny = 63 - x;
            break;
        case 2:
            nx = 127 - x;
            ny = 63 - y;
            break;
        case 3:
        default:
            nx = 127 - y;
            ny = x;
            break;
    }

    if (nx < 0 || nx >= OLED_WIDTH || ny < 0 || ny >= OLED_HEIGHT) return;
    size_t idx = static_cast<size_t>(nx) + static_cast<size_t>(ny / 8) * OLED_WIDTH;
    uint8_t mask = static_cast<uint8_t>(1U << (ny & 7));
    if (on) {
        d.buffer[idx] |= mask;
    } else {
        d.buffer[idx] &= ~mask;
    }
}

static void oledFillRect(OledDisplayInstance &d, int x, int y, int w, int h, bool on = true) {
    for (int iy = y; iy < y + h; ++iy) {
        for (int ix = x; ix < x + w; ++ix) {
            oledSetPixel(d, ix, iy, on);
        }
    }
}

static void oledDrawRect(OledDisplayInstance &d, int x, int y, int w, int h, bool on = true) {
    for (int ix = x; ix < x + w; ++ix) {
        oledSetPixel(d, ix, y, on);
        oledSetPixel(d, ix, y + h - 1, on);
    }
    for (int iy = y; iy < y + h; ++iy) {
        oledSetPixel(d, x, iy, on);
        oledSetPixel(d, x + w - 1, iy, on);
    }
}

static void oledFillRoundRect(OledDisplayInstance &d, int x, int y, int w, int h, int r, bool on = true) {
    for (int iy = 0; iy < h; ++iy) {
        for (int ix = 0; ix < w; ++ix) {
            int dx = 0, dy = 0;
            if (ix < r) dx = r - ix;
            else if (ix >= w - r) dx = ix - (w - r - 1);
            if (iy < r) dy = r - iy;
            else if (iy >= h - r) dy = iy - (h - r - 1);
            if (dx * dx + dy * dy <= r * r) {
                oledSetPixel(d, x + ix, y + iy, on);
            }
        }
    }
}

static void oledFillCircle(OledDisplayInstance &d, int cx, int cy, int r, bool on = true) {
    int r2 = r * r;
    for (int dy = -r; dy <= r; ++dy) {
        for (int dx = -r; dx <= r; ++dx) {
            if (dx * dx + dy * dy <= r2) {
                oledSetPixel(d, cx + dx, cy + dy, on);
            }
        }
    }
}

static void oledDrawLine(OledDisplayInstance &d, int x0, int y0, int x1, int y1, int thickness = 1, bool on = true) {
    int dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
    int dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;
    while (true) {
        for (int ty = -thickness / 2; ty <= thickness / 2; ++ty) {
            for (int tx = -thickness / 2; tx <= thickness / 2; ++tx) {
                oledSetPixel(d, x0 + tx, y0 + ty, on);
            }
        }
        if (x0 == x1 && y0 == y1) break;
        int e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
    }
}

static void oledDrawChar(OledDisplayInstance &d, int x, int y, char c, int scale = 1) {
    if (c >= 'a' && c <= 'z') {
        c = static_cast<char>(c - 'a' + 'A');
    }
    if (c < 0x20 || c > 0x5F) {
        c = '?';
    }
    const uint8_t *glyph = FONT5X7[c - 0x20];
    for (int col = 0; col < 5; ++col) {
        uint8_t bits = pgm_read_byte(&glyph[col]);
        for (int row = 0; row < 7; ++row) {
            if (bits & (1U << row)) {
                if (scale <= 1) {
                    oledSetPixel(d, x + col, y + row, true);
                } else {
                    oledFillRect(d, x + col * scale, y + row * scale, scale, scale, true);
                }
            }
        }
    }
}

static void oledDrawString(OledDisplayInstance &d, int x, int y, const char *str, int scale = 1) {
    int curX = x;
    while (*str) {
        oledDrawChar(d, curX, y, *str, scale);
        curX += 6 * scale;
        ++str;
    }
}

// Render 1 Mắt độc lập lên màn hình OLED (Phân hệ 4: 11 Biểu cảm + Icon Cảnh báo [!] / Offline)
static void renderSingleEyeCanvas(OledDisplayInstance &d, EyeEmotion emo, bool isLeftEye, bool blinking, int8_t px, int8_t py) {
    oledClear(d);

    const int vw = oledViewW(d);
    const int vh = oledViewH(d);
    const bool portrait = (vh > vw);
    const int cx = (vw / 2) + (portrait ? (px / 2) : px);
    const int cy = (vh / 2) + (portrait ? py : (py / 2));

    if (blinking) {
        int bw = portrait ? 46 : 60;
        oledFillRoundRect(d, cx - bw / 2, cy - 3, bw, 6, 3, true);
    } else {
        switch (emo) {
            case EyeEmotion::HAPPY: { // ^ ^ : Mắt cười vòng cung
                int r = portrait ? 23 : 26;
                oledFillCircle(d, cx, cy + 8, r, true);
                oledFillCircle(d, cx, cy + 16, r - 4, false);
                oledFillRect(d, cx - r - 4, cy + 10, (r + 4) * 2, 26, false);
                break;
            }
            case EyeEmotion::SURPRISED: { // O_O : Ngạc nhiên / Báo động
                int r = portrait ? 23 : 25;
                oledFillCircle(d, cx, cy, r, true);
                oledFillCircle(d, cx, cy, r - 7, false);
                oledFillCircle(d, cx + (px / 3), cy + (py / 3), 6, true);
                oledFillCircle(d, cx - 6, cy - 7, 2, true);
                break;
            }
            case EyeEmotion::DIZZY: { // @_@ : Chóng mặt xoáy ốc khi bị lắc chậu
                oledFillCircle(d, cx, cy, 24, true);
                oledFillCircle(d, cx, cy, 19, false);
                oledFillCircle(d, cx, cy, 13, true);
                oledFillCircle(d, cx, cy, 8, false);
                oledFillCircle(d, cx, cy, 4, true);
                break;
            }
            case EyeEmotion::LISTENING: { // o_o : Đang lắng nghe giọng nói
                if (portrait) {
                    oledFillRoundRect(d, cx - 21, cy - 26, 42, 52, 14, true);
                    oledFillCircle(d, cx - 7, cy - 10, 5, false);
                    int barW = static_cast<int>((g_micRmsPct * 0.50f) + 6);
                    if (barW > 54) barW = 54;
                    oledFillRoundRect(d, 32 - barW / 2, 8, barW, 6, 2, true);
                    oledFillRoundRect(d, 32 - barW / 2, 114, barW, 6, 2, true);
                } else {
                    oledFillRoundRect(d, cx - 22, cy - 22, 44, 44, 14, true);
                    oledFillCircle(d, cx - 7, cy - 7, 5, false);
                    int barH = static_cast<int>((g_micRmsPct * 0.48f) + 6);
                    if (barH > 54) barH = 54;
                    oledFillRoundRect(d, 4, 32 - barH / 2, 6, barH, 2, true);
                    oledFillRoundRect(d, 118, 32 - barH / 2, 6, barH, 2, true);
                }
                break;
            }
            case EyeEmotion::THINKING: { // -_- : AI đang xử lý suy nghĩ
                int bw = portrait ? 44 : 54;
                oledFillRoundRect(d, cx - bw / 2, cy - 5, bw, 10, 4, true);
                uint8_t phase = static_cast<uint8_t>((millis() / 250) % 3);
                for (uint8_t i = 0; i < 3; ++i) {
                    int dotR = (i == phase) ? 4 : 2;
                    oledFillCircle(d, (vw / 2) - 12 + i * 12, cy + (portrait ? 26 : 18), dotR, true);
                }
                break;
            }
            case EyeEmotion::SPEAKING: { // ^o^ : Loa đang phát giọng trả lời AI Chatbot
                int pulse = static_cast<int>((millis() / 90) % 2) * 3;
                int ew = (portrait ? 44 : 48) + pulse;
                int eh = (portrait ? 54 : 42) - pulse;
                oledFillRoundRect(d, cx - ew / 2, cy - eh / 2, ew, eh, 14, true);
                oledFillCircle(d, cx - 7, cy - 9, 5, false);
                oledFillCircle(d, cx + 8, cy + 8, 3, false);
                break;
            }
            case EyeEmotion::SAD: { // T_T : Buồn bã, thiếu nước
                oledFillRoundRect(d, cx - 21, cy - 20, 42, 42, 11, true);
                if (isLeftEye) {
                    oledDrawLine(d, cx - 24, cy - 9, cx + 24, cy - 24, 10, false);
                } else {
                    oledDrawLine(d, cx - 24, cy - 24, cx + 24, cy - 9, 10, false);
                }
                int tearX = isLeftEye ? (cx - 12) : (cx + 12);
                oledFillCircle(d, tearX, cy + (portrait ? 30 : 24), 4, true);
                break;
            }
            case EyeEmotion::THIRSTY: { // >< : Khát nước trầm trọng / Hết nước bình chứa
                int span = portrait ? 20 : 24;
                if (isLeftEye) {
                    oledDrawLine(d, cx - span, cy - 16, cx + span, cy, 5, true);
                    oledDrawLine(d, cx + span, cy, cx - span, cy + 16, 5, true);
                } else {
                    oledDrawLine(d, cx + span, cy - 16, cx - span, cy, 5, true);
                    oledDrawLine(d, cx - span, cy, cx + span, cy + 16, 5, true);
                }
                break;
            }
            case EyeEmotion::LOVE: { // ♥_♥ : Đang được bơm tưới nước
                oledFillCircle(d, cx - 10, cy - 7, 12, true);
                oledFillCircle(d, cx + 10, cy - 7, 12, true);
                for (int y = 0; y <= 22; ++y) {
                    int halfW = 22 - y;
                    oledDrawLine(d, cx - halfW, cy + y - 3, cx + halfW, cy + y - 3, 1, true);
                }
                break;
            }
            case EyeEmotion::SLEEPY: { // -.- zzz : Ngái ngủ, quá nóng / lạnh / tối
                oledFillRoundRect(d, cx - 22, cy - 4, 44, 22, 8, true);
                oledDrawLine(d, cx - 24, cy - 4, cx + 24, cy - 4, 3, true);
                oledDrawString(d, vw - 22, 6, "Z", 2);
                break;
            }
            case EyeEmotion::NORMAL:
            default: {
                int ew = portrait ? 44 : 48;
                int eh = portrait ? 60 : 46;
                oledFillRoundRect(d, cx - ew / 2, cy - eh / 2, ew, eh, 14, true);
                oledFillCircle(d, cx - 8, cy - (portrait ? 13 : 9), 5, false);
                oledFillCircle(d, cx + 9, cy + (portrait ? 12 : 9), 2, false);
                break;
            }
        }
    }

    // Hiển thị icon cảnh báo [!] ở góc màn hình khi có lỗi phần cứng hoặc WATERING_FAULT_ERROR (Phân hệ 1, 2, 3)
    if (g_wateringFaultError || (g_errCodes & ERR_OUTLIER_DATA)) {
        bool blinkOn = ((millis() / 300) % 2) == 0;
        if (blinkOn) {
            oledDrawString(d, 2, 2, "[!]", 1);
        }
    } else if (g_wifiConfigured && !g_wifiOnline) {
        // Biểu tượng Offline nhỏ ở góc (Phân hệ 1)
        oledDrawString(d, 2, 2, "W:X", 1);
    }
}

static void renderDashboardCanvas(OledDisplayInstance &d, bool isSecondScreen) {
    oledClear(d);
    const int vw = oledViewW(d);
    const int vh = oledViewH(d);
    oledDrawRect(d, 0, 0, vw, vh, true);

    char line[28];
    if (vh > vw) {
        // Portrait 64x128
        if (!isSecondScreen) {
            oledDrawString(d, 4, 5, "PLANT S3", 1);
            oledDrawLine(d, 2, 15, 61, 15, 1, true);
            snprintf(line, sizeof(line), "R:%+5.1f", g_roll);
            oledDrawString(d, 4, 20, line, 1);
            snprintf(line, sizeof(line), "P:%+5.1f", g_pitch);
            oledDrawString(d, 4, 31, line, 1);
            float t = g_ahtValid ? g_ahtTempC : (g_bmpValid ? g_bmpTempC : g_lastTempC);
            snprintf(line, sizeof(line), "T:%4.1fC", t);
            oledDrawString(d, 4, 44, line, 1);
            snprintf(line, sizeof(line), "H:%4.0f%%", g_ahtHumPct);
            oledDrawString(d, 4, 55, line, 1);
            snprintf(line, sizeof(line), "L:%4.0flx", g_bhLux);
            oledDrawString(d, 4, 68, line, 1);
            snprintf(line, sizeof(line), "P:%4.0f", g_bmpPressHpa);
            oledDrawString(d, 4, 79, line, 1);
            snprintf(line, sizeof(line), "S:%4.0f%%", g_soilPct);
            oledDrawString(d, 4, 92, line, 1);
            snprintf(line, sizeof(line), "MIC:%2.0fdB", g_micDbSpl);
            oledDrawString(d, 4, 104, line, 1);
            oledDrawString(d, 4, 115, g_pumpOn ? "PUMP:ON" : (g_micVad ? "VAD:TALK" : "VAD:IDLE"), 1);
        } else {
            oledDrawString(d, 4, 5, "OLED 2", 1);
            oledDrawLine(d, 2, 15, 61, 15, 1, true);
            snprintf(line, sizeof(line), "Y:%+5.1f", g_yaw);
            oledDrawString(d, 4, 20, line, 1);
            snprintf(line, sizeof(line), "RMS:%2.0f%%", g_micRmsPct);
            oledDrawString(d, 4, 32, line, 1);
            snprintf(line, sizeof(line), "TK:%s", g_tankOk ? "OK" : "EMPTY");
            oledDrawString(d, 4, 44, line, 1);
            oledDrawRect(d, 18, 56, 28, 64, true);
            int barH = static_cast<int>((g_micRmsPct / 100.0f) * 60.0f);
            if (barH > 60) barH = 60;
            if (barH > 0) oledFillRect(d, 20, 118 - barH, 24, barH, true);
        }
    } else {
        // Landscape 128x64
        if (!isSecondScreen) {
            oledDrawString(d, 6, 4, "SMART PLANT S3", 1);
            oledDrawLine(d, 2, 13, 125, 13, 1, true);
            snprintf(line, sizeof(line), "R:%+5.1f P:%+5.1f", g_roll, g_pitch);
            oledDrawString(d, 6, 17, line, 1);
            float t = g_ahtValid ? g_ahtTempC : (g_bmpValid ? g_bmpTempC : g_lastTempC);
            snprintf(line, sizeof(line), "T:%.1fC H:%.0f%% S:%.0f%%", t, g_ahtHumPct, g_soilPct);
            oledDrawString(d, 6, 28, line, 1);
            snprintf(line, sizeof(line), "L:%.0flx P:%.0f", g_bhLux, g_bmpPressHpa);
            oledDrawString(d, 6, 39, line, 1);
            snprintf(line, sizeof(line), "MIC:%.0fdB %s", g_micDbSpl, g_pumpOn ? "PUMP:ON" : (g_micVad ? "TALK" : "IDLE"));
            oledDrawString(d, 6, 50, line, 1);
        } else {
            oledDrawString(d, 6, 4, "OLED 2", 1);
            oledDrawLine(d, 2, 13, 125, 13, 1, true);
            snprintf(line, sizeof(line), "YAW: %+6.1f DEG", g_yaw);
            oledDrawString(d, 6, 18, line, 1);
            snprintf(line, sizeof(line), "SOIL:%3.0f%% CYC:%u/5", g_soilPct, g_wateringCycles);
            oledDrawString(d, 6, 30, line, 1);
            oledDrawRect(d, 6, 44, 116, 12, true);
            int barW = static_cast<int>((g_micRmsPct / 100.0f) * 112.0f);
            if (barW > 112) barW = 112;
            if (barW > 0) oledFillRect(d, 8, 46, barW, 8, true);
        }
    }
}

static void renderTextCanvas(OledDisplayInstance &d, uint8_t screenIdx) {
    oledClear(d);
    const int vw = oledViewW(d);
    const int vh = oledViewH(d);
    oledDrawRect(d, 0, 0, vw, vh, true);
    char hdr[20];
    if (vh > vw) {
        snprintf(hdr, sizeof(hdr), "OLED %u", screenIdx);
        oledDrawString(d, 10, 6, hdr, 1);
        snprintf(hdr, sizeof(hdr), "0x%02X", d.addr);
        oledDrawString(d, 16, 16, hdr, 1);
        oledDrawLine(d, 2, 26, 61, 26, 1, true);
        oledDrawString(d, 4, 36, g_oledText1.substring(0, 9).c_str(), 1);
        if (g_oledText1.length() > 9) oledDrawString(d, 4, 48, g_oledText1.substring(9, 18).c_str(), 1);
        oledDrawLine(d, 6, 64, 57, 64, 1, true);
        oledDrawString(d, 4, 74, g_oledText2.substring(0, 9).c_str(), 1);
        if (g_oledText2.length() > 9) oledDrawString(d, 4, 86, g_oledText2.substring(9, 18).c_str(), 1);
    } else {
        snprintf(hdr, sizeof(hdr), "OLED %u (0x%02X)", screenIdx, d.addr);
        oledDrawString(d, 6, 5, hdr, 1);
        oledDrawLine(d, 2, 15, 125, 15, 1, true);
        oledDrawString(d, 6, 22, g_oledText1.c_str(), 2);
        oledDrawString(d, 6, 44, g_oledText2.c_str(), 1);
    }
}

static void renderTestCanvas(OledDisplayInstance &d, uint8_t screenIdx) {
    oledClear(d);
    const int vw = oledViewW(d);
    const int vh = oledViewH(d);
    oledDrawRect(d, 0, 0, vw, vh, true);
    oledDrawRect(d, 2, 2, vw - 4, vh - 4, true);

    const int cx = vw / 2;
    oledDrawString(d, cx - 21, 6, "^ TOP ^", 1);
    oledDrawLine(d, cx, 16, cx, 26, 2, true);
    oledDrawLine(d, cx - 5, 21, cx, 16, 2, true);
    oledDrawLine(d, cx + 5, 21, cx, 16, 2, true);

    char msg[20];
    snprintf(msg, sizeof(msg), "OLED%u %uDEG", screenIdx, static_cast<unsigned>((d.rotation & 3) * 90));
    oledDrawString(d, (vh > vw) ? 4 : 28, (vh > vw) ? 54 : 34, msg, 1);
    snprintf(msg, sizeof(msg), "0x%02X %s", d.addr, (d.busId == 0) ? "11/12" : "1/2");
    oledDrawString(d, (vh > vw) ? 6 : 34, (vh > vw) ? 70 : 48, msg, 1);
}

static const char *emotionToName(EyeEmotion emo) {
    switch (emo) {
        case EyeEmotion::HAPPY:     return "HAPPY";
        case EyeEmotion::SURPRISED: return "SURPRISED";
        case EyeEmotion::DIZZY:     return "DIZZY";
        case EyeEmotion::LISTENING: return "LISTENING";
        case EyeEmotion::SAD:       return "SAD";
        case EyeEmotion::LOVE:      return "LOVE";
        case EyeEmotion::SLEEPY:    return "SLEEPY";
        case EyeEmotion::THIRSTY:   return "THIRSTY";
        case EyeEmotion::THINKING:  return "THINKING";
        case EyeEmotion::SPEAKING:  return "SPEAKING";
        case EyeEmotion::NORMAL:
        default:                    return "NORMAL";
    }
}

static const char *oledModeToName(OledDisplayMode m) {
    switch (m) {
        case OledDisplayMode::DASHBOARD: return "DASHBOARD";
        case OledDisplayMode::TEXT:      return "TEXT";
        case OledDisplayMode::TEST:      return "TEST";
        case OledDisplayMode::EYES:
        default:                         return "EYES";
    }
}

static const char *pumpPhaseToName(WateringPhase p) {
    switch (p) {
        case WateringPhase::PUMPING_3S:   return "PUMPING_3S";
        case WateringPhase::SOAKING_2S:   return "SOAKING_2S";
        case WateringPhase::RECHECK:      return "RECHECK";
        case WateringPhase::FAULT_LOCKED: return "FAULT_LOCKED";
        case WateringPhase::IDLE:
        default:                          return "IDLE";
    }
}

static const char *voiceStateToName(VoiceAiState v) {
    switch (v) {
        case VoiceAiState::LISTENING: return "LISTENING";
        case VoiceAiState::THINKING:  return "THINKING";
        case VoiceAiState::SPEAKING:  return "SPEAKING";
        case VoiceAiState::IDLE:
        default:                      return "IDLE";
    }
}

// Quyết định Biểu cảm Hai Mắt OLED theo đúng 4 Cấp Ưu Tiên trong Lưu đồ Phân hệ 4
static EyeEmotion evaluateFlowchartEyeEmotion(uint32_t nowMs) {
    // ƯU TIÊN 1: Sự kiện tương tác giọng nói / AI Chatbot (Phân hệ 5)
    if (g_voiceState == VoiceAiState::SPEAKING || g_tonePlaying) {
        return EyeEmotion::SPEAKING; // ^o^
    }
    if (g_voiceState == VoiceAiState::THINKING) {
        return EyeEmotion::THINKING; // -_-
    }
    if (g_voiceState == VoiceAiState::LISTENING || g_micVad) {
        return EyeEmotion::LISTENING; // o_o
    }

    // ƯU TIÊN 2: Phát hiện chậu cây nghiêng / rung lắc bất thường (MPU6050) hoặc đang tưới
    if (g_dynG > 0.65f || g_amag > 1.5f || nowMs < g_shakeEmotionHoldMs) {
        return EyeEmotion::DIZZY; // @_@
    }
    if (fabsf(g_roll) > 30.0f || fabsf(g_pitch) > 30.0f || g_tiltDeg > 30.0f) {
        return EyeEmotion::SURPRISED; // O_O
    }
    if (g_pumpOn || g_pumpPhase == WateringPhase::PUMPING_3S) {
        return EyeEmotion::LOVE; // ♥_♥ Đang được tưới nước
    }
    if (nowMs < g_tapEmotionHoldMs) {
        return EyeEmotion::HAPPY; // ^ ^ Vừa được xoa đầu / vừa tưới đủ nước
    }

    // ƯU TIÊN 3: Đất có đủ độ ẩm không? (ADC > 1100 & Bình còn nước?)
    if (!g_tankOk || g_wateringFaultError) {
        return EyeEmotion::THIRSTY; // >< Cạn bình hoặc lỗi tưới
    }
    if (g_soilFlowAdc <= g_soilThreshAdc || g_soilPct < g_soilThreshPct) {
        return EyeEmotion::SAD; // T_T Đất khô thiếu ẩm
    }

    // ƯU TIÊN 4: Nhiệt độ & Ánh sáng có phù hợp cho sinh trưởng? (18°C <= T <= 32°C & Đủ sáng?)
    float envTemp = g_ahtValid ? g_ahtTempC : (g_bmpValid ? g_bmpTempC : g_lastTempC);
    bool tempBad  = (g_ahtValid || g_bmpValid) && (envTemp < 18.0f || envTemp > 32.0f);
    bool lightBad = g_bhValid && (g_bhLux < 8.0f);
    if (tempBad || lightBad) {
        return EyeEmotion::SLEEPY; // -.- hoặc ~_~ Khó chịu / Mệt mỏi / Ngái ngủ
    }

    // Môi trường tối ưu: Vui vẻ / Bình thường (Kèm chớp mắt tự nhiên 3-5s)
    return EyeEmotion::NORMAL;
}

static void updateOledAnimation(uint32_t nowMs) {
    // 1. Thuật toán chớp mắt tự nhiên (Blink Animation chu kỳ 3 - 5 giây ngẫu nhiên)
    if (!g_isBlinking && nowMs >= g_nextBlinkMs) {
        g_isBlinking = true;
        g_blinkStartMs = nowMs;
    }
    if (g_isBlinking && (nowMs - g_blinkStartMs >= 140)) {
        g_isBlinking = false;
        g_nextBlinkMs = nowMs + 3200 + (nowMs % 2200);
    }

    // 2. Tính độ lệch đồng tử mắt theo góc nghiêng MPU6050 Roll/Pitch
    int px = static_cast<int>(g_roll * 0.25f);
    int py = static_cast<int>(g_pitch * 0.45f);
    if (px > 8)   px = 8;
    if (px < -8)  px = -8;
    if (py > 18)  py = 18;
    if (py < -18) py = -18;
    g_pupilOffsetX = static_cast<int8_t>(px);
    g_pupilOffsetY = static_cast<int8_t>(py);

    // 3. Xác định biểu cảm hiện tại theo Lưu đồ Phân hệ 4
    if (g_oledAutoEmotion) {
        g_currentEmotion = evaluateFlowchartEyeEmotion(nowMs);
    } else {
        g_currentEmotion = g_manualEmotion;
    }

    if (!g_oled1.ready && !g_oled2.ready) return;

    // 4. Render framebuffer đồng thời cho cả 2 màn hình OLED (~20 FPS)
    g_oled1.rotation = g_oled1Rot & 3;
    g_oled2.rotation = g_oled2Rot & 3;

    if (nowMs - g_lastOledRenderMs >= 50) {
        g_lastOledRenderMs = nowMs;
        const bool oled1IsLeft = !g_oledSwapLr;
        const bool oled2IsLeft = g_oledSwapLr;
        if (g_oled1.ready) {
            if (g_oledMode == OledDisplayMode::EYES) {
                renderSingleEyeCanvas(g_oled1, g_currentEmotion, oled1IsLeft, g_isBlinking, g_pupilOffsetX, g_pupilOffsetY);
            } else if (g_oledMode == OledDisplayMode::DASHBOARD) {
                renderDashboardCanvas(g_oled1, !oled1IsLeft);
            } else if (g_oledMode == OledDisplayMode::TEXT) {
                renderTextCanvas(g_oled1, 1);
            } else {
                renderTestCanvas(g_oled1, 1);
            }
        }
        if (g_oled2.ready) {
            if (g_oledMode == OledDisplayMode::EYES) {
                renderSingleEyeCanvas(g_oled2, g_currentEmotion, oled2IsLeft, g_isBlinking, g_pupilOffsetX, g_pupilOffsetY);
            } else if (g_oledMode == OledDisplayMode::DASHBOARD) {
                renderDashboardCanvas(g_oled2, !oled2IsLeft);
            } else if (g_oledMode == OledDisplayMode::TEXT) {
                renderTextCanvas(g_oled2, 2);
            } else {
                renderTestCanvas(g_oled2, 2);
            }
        }
    }
}

static void flushOledPagesIncremental(OledDisplayInstance &d, uint8_t pagesPerCall) {
    if (!d.ready || !d.bus) return;

    for (uint8_t p = 0; p < pagesPerCall; ++p) {
        uint8_t page = d.nextPage;
        d.nextPage = (d.nextPage + 1) % OLED_PAGES;

        if (!oledSendCmd(d, static_cast<uint8_t>(0xB0 | page)) ||
            !oledSendCmd(d, 0x00) ||
            !oledSendCmd(d, 0x10)) {
            d.ready = false;
            return;
        }

        const uint8_t *rowPtr = &d.buffer[page * OLED_WIDTH];
        for (uint8_t offset = 0; offset < OLED_WIDTH; offset += 32) {
            d.bus->beginTransmission(d.addr);
            d.bus->write(0x40);
            d.bus->write(rowPtr + offset, 32);
            if (d.bus->endTransmission(true) != 0) {
                d.ready = false;
                return;
            }
        }
    }
}

static void setOledPower(bool on) {
    g_oledPower = on;
    uint8_t cmd = on ? 0xAF : 0xAE;
    if (g_oled1.ready) oledSendCmd(g_oled1, cmd);
    if (g_oled2.ready) oledSendCmd(g_oled2, cmd);
}

static void setOledInvert(bool inv) {
    g_oledInvert = inv;
    uint8_t cmd = inv ? 0xA7 : 0xA6;
    if (g_oled1.ready) oledSendCmd(g_oled1, cmd);
    if (g_oled2.ready) oledSendCmd(g_oled2, cmd);
}

static void setOledContrast(uint8_t c) {
    g_oledContrast = c;
    if (g_oled1.ready) {
        oledSendCmd(g_oled1, 0x81);
        oledSendCmd(g_oled1, c);
    }
    if (g_oled2.ready) {
        oledSendCmd(g_oled2, 0x81);
        oledSendCmd(g_oled2, c);
    }
}

static void emitOledPacket() {
    // Gói tin "type":"oled" tích hợp đầy đủ trạng thái OLED + Thủy lợi P3 + Độ ẩm đất
    // để tương thích đồng thời 100% với cả mpu6050_ui.py và smart_plant_app.py
    Serial.printf(
        "{\"type\":\"oled\","
        "\"oled1_ok\":%s,\"oled1_addr\":\"0x%02X\",\"oled1_sda\":%d,\"oled1_scl\":%d,"
        "\"oled2_ok\":%s,\"oled2_addr\":\"0x%02X\",\"oled2_sda\":%d,\"oled2_scl\":%d,\"oled2_bus\":%u,"
        "\"mode\":\"%s\",\"auto_emo\":%s,\"emo\":\"%s\","
        "\"contrast\":%u,\"invert\":%s,\"power\":%s,\"swap\":%s,\"rot1\":%u,\"rot2\":%u,"
        "\"eye_x\":%d,\"eye_y\":%d,\"blink\":%s,"
        "\"text1\":\"%s\",\"text2\":\"%s\","
        "\"soil_pct\":%.1f,\"soil_mv\":%d,\"soil_adc\":%u,"
        "\"tank_ok\":%s,\"pump_on\":%s,\"pump_phase\":\"%s\","
        "\"consecutive_cycles\":%u,\"daily_cycles\":%u,\"watering_fault\":%s,"
        "\"voice_state\":\"%s\",\"err_codes\":%u,\"backlog_count\":%u}\n",
        g_oled1.ready ? "true" : "false", g_oled1.addr, I2C_SDA_PIN, I2C_SCL_PIN,
        g_oled2.ready ? "true" : "false", g_oled2.addr,
        (g_oled2.busId == 0) ? I2C_SDA_PIN : OLED2_SDA_PIN,
        (g_oled2.busId == 0) ? I2C_SCL_PIN : OLED2_SCL_PIN,
        g_oled2.busId,
        oledModeToName(g_oledMode),
        g_oledAutoEmotion ? "true" : "false",
        emotionToName(g_currentEmotion),
        g_oledContrast,
        g_oledInvert ? "true" : "false",
        g_oledPower ? "true" : "false",
        g_oledSwapLr ? "true" : "false",
        static_cast<unsigned>((g_oled1Rot & 3) * 90),
        static_cast<unsigned>((g_oled2Rot & 3) * 90),
        g_pupilOffsetX, g_pupilOffsetY,
        g_isBlinking ? "true" : "false",
        g_oledText1.c_str(), g_oledText2.c_str(),
        g_soilPct, g_soilMv, g_soilFlowAdc,
        g_tankOk ? "true" : "false",
        g_pumpOn ? "true" : "false",
        pumpPhaseToName(g_pumpPhase),
        g_wateringCycles, g_dailyWateringCycles,
        g_wateringFaultError ? "true" : "false",
        voiceStateToName(g_voiceState),
        g_errCodes,
        static_cast<unsigned>(g_backlogCount)
    );
}

static void probeEnvSensors() {
    if (!g_ahtReady) probeAndInitAht20();
    if (!g_bmpReady) probeAndInitBmp280();
    if (!g_bhReady)  probeAndInitBh1750();
    probeOledDisplays();
}

// ============================================================================
// 11. PHÂN HỆ 3: THUẬT TOÁN TƯỚI CÂY TỰ ĐỘNG (INTELLIGENT WATERING ENGINE)
// ============================================================================
// Tuân thủ 100% Lưu đồ Phân hệ 3:
//   - Kiểm tra ADC > 1100 (hoặc soilPct >= thresholdPct):
//       + YES (Đủ nước): Tắt bơm (LOW), trạng thái SUFFICIENT, reset watering_cycles = 0,
//                        OLED hiển thị biểu cảm VUI VẺ (^ ^), gửi trạng thái lên Cloud/App.
//       + NO (Thiếu ẩm):
//           1) Kiểm tra watering_cycles >= MAX_CYCLES (5):
//              - YES: Khóa bơm khẩn cấp (LOW), bật cờ WATERING_FAULT_ERROR, OLED nhấp nháy [!],
//                     Loa phát cảnh báo lỗi tưới, gửi thông báo khẩn cấp, tạm dừng bơm chờ Reset.
//           2) Kiểm tra Bình chứa còn đủ nước không (Water Level):
//              - NO (Hết nước): Không kích hoạt bơm (LOW), OLED hiển thị THIRSTY (><) / SAD (T_T),
//                               Loa phát thông báo "Hết nước rồi! Bạn bổ sung nước cho mình nhé!",
//                               gửi cảnh báo TANK_EMPTY.
//              - YES (Đủ nước): Tăng watering_cycles++, Bật bơm mini 5V (HIGH) trong đúng 3 giây
//                               -> Hết 3 giây Tắt bơm hoàn toàn (LOW)
//                               -> Chờ 2 giây trễ ngấm nước
//                               -> Đọc lại ADC độ ẩm đất từ Phân hệ 2 để quyết định!

static void setPumpHardwareState(bool turnOn) {
    g_pumpOn = turnOn;
    digitalWrite(PUMP_RELAY_PIN, turnOn ? HIGH : LOW);
    digitalWrite(STATUS_LED_PIN, turnOn ? HIGH : LOW);
}

static void emergencyStopPump(const char *reason) {
    setPumpHardwareState(false);
    if (!g_wateringFaultError) {
        g_pumpPhase = WateringPhase::IDLE;
    }
    Serial.printf(
        "{\"event\":\"ESTOP_ACTIVE\",\"pump_on\":false,\"pump_phase\":\"%s\",\"cycles\":%u,\"reason\":\"%s\"}\n",
        pumpPhaseToName(g_pumpPhase), g_wateringCycles, reason
    );
    emitOledPacket();
}

static void resetWateringFault() {
    setPumpHardwareState(false);
    g_wateringFaultError = false;
    g_wateringCycles     = 0;
    g_pumpPhase          = WateringPhase::IDLE;
    g_errCodes          &= ~ERR_WATER_FAULT;
    recordHardwareFaultToNvs(g_errCodes);
    Serial.println("{\"event\":\"WATERING_FAULT_RESET\",\"msg\":\"Da mo khoa loi bom va reset chu ky tuoi ve 0/5\"}");
    emitOledPacket();
}

static void triggerWateringPulse(bool fromManualOrVoice) {
    uint32_t nowMs = millis();

    // 1. Kiểm tra khóa lỗi an toàn 5 chu kỳ liên tiếp (WATERING_FAULT_ERROR)
    if (g_wateringFaultError || g_wateringCycles >= MAX_WATERING_CYCLES) {
        setPumpHardwareState(false);
        g_wateringFaultError = true;
        g_pumpPhase          = WateringPhase::FAULT_LOCKED;
        g_errCodes          |= ERR_WATER_FAULT;
        recordHardwareFaultToNvs(g_errCodes);
        playSoundEffect("FAULT_ALARM");
        Serial.printf(
            "{\"event\":\"WATERING_FAULT_ERROR\",\"cycles\":%u,\"max_cycles\":%u,"
            "\"msg\":\"CANH BAO AN TOAN: Da tuoi 5 chu ky lien tiep ma dat van kho! Khoa bom khan cap!\"}\n",
            g_wateringCycles, MAX_WATERING_CYCLES
        );
        emitOledPacket();
        return;
    }

    // 2. Kiểm tra mực nước bình chứa trước khi bật bơm (Bảo vệ chống cháy bơm)
    if (!g_tankOk) {
        setPumpHardwareState(false);
        g_pumpPhase = WateringPhase::IDLE;
        g_errCodes |= ERR_TANK_EMPTY;
        if (!g_tankEmptyAlertSent || fromManualOrVoice) {
            g_tankEmptyAlertSent = true;
            playSoundEffect("TANK_EMPTY");
            g_lastAiReply = "Hết nước rồi! Bạn bổ sung nước cho mình nhé!";
            Serial.println(
                "{\"event\":\"TANK_EMPTY\",\"tank_ok\":false,\"pump_on\":false,"
                "\"voice\":\"Hết nước rồi! Bạn bổ sung nước cho mình nhé!\"}"
            );
        }
        emitOledPacket();
        return;
    }

    g_errCodes &= ~ERR_TANK_EMPTY;
    g_tankEmptyAlertSent = false;

    // 3. Bình đủ nước & an toàn -> Tăng biến đếm chu kỳ và Bật bơm định lượng đúng 3 giây
    g_wateringCycles++;
    g_dailyWateringCycles++;
    if (g_estimatedTankCycles > 0) {
        g_estimatedTankCycles--;
    }

    g_pumpPhase = WateringPhase::PUMPING_3S;
    g_pumpPhaseStartMs = nowMs;
    setPumpHardwareState(true);
    playSoundEffect("WATERING");
    g_lastAiReply = "Aaaa, mát quá! Cảm ơn bạn nha!";

    Serial.printf(
        "{\"event\":\"PUMP_PULSE_START\",\"duration_ms\":%lu,\"cycle\":%u,\"max_cycles\":%u,"
        "\"soil_adc\":%u,\"soil_pct\":%.1f,\"trigger\":\"%s\"}\n",
        static_cast<unsigned long>(PUMP_PULSE_DURATION_MS),
        g_wateringCycles, MAX_WATERING_CYCLES,
        g_soilFlowAdc, g_soilPct,
        fromManualOrVoice ? "COMMAND" : "AUTO_P3"
    );
    emitOledPacket();
}

static void updateIntelligentWateringEngine(uint32_t nowMs) {
    // Máy trạng thái xử lý xung tưới 3s -> ngấm 2s -> đo lại ADC (Không chặn vòng lặp chính)
    if (g_pumpPhase == WateringPhase::PUMPING_3S) {
        // Nếu đang bơm mà bình cạn nước đột ngột -> ngắt bơm ngay!
        if (!g_tankOk) {
            setPumpHardwareState(false);
            g_pumpPhase = WateringPhase::IDLE;
            playSoundEffect("TANK_EMPTY");
            Serial.println("{\"event\":\"TANK_EMPTY\",\"msg\":\"Binh het nuoc khi dang bom -> Ngat bom ngay!\"}");
            emitOledPacket();
            return;
        }
        // Đúng 3 giây: TẮT BƠM HOÀN TOÀN và chuyển sang pha CHỜ NGẤM NƯỚC 2 GIÂY
        if (nowMs - g_pumpPhaseStartMs >= PUMP_PULSE_DURATION_MS) {
            setPumpHardwareState(false);
            g_pumpPhase = WateringPhase::SOAKING_2S;
            g_pumpPhaseStartMs = nowMs;
            // Nếu đang ở chế độ mô phỏng độ ẩm đất, cộng thêm độ ẩm sau mỗi xung tưới 3s
            if (g_soilSimMode) {
                g_soilSimPct = fminf(95.0f, g_soilSimPct + 12.0f);
            }
            Serial.printf(
                "{\"event\":\"PUMP_PULSE_DONE\",\"soak_ms\":%lu,\"cycle\":%u,\"msg\":\"Het 3 giay: Tat bom hoan toan, cho nuoc ngam 2 giay\"}\n",
                static_cast<unsigned long>(PUMP_SOAK_DURATION_MS), g_wateringCycles
            );
            emitOledPacket();
        }
        return;
    }

    if (g_pumpPhase == WateringPhase::SOAKING_2S) {
        if (nowMs - g_pumpPhaseStartMs >= PUMP_SOAK_DURATION_MS) {
            g_pumpPhase = WateringPhase::RECHECK;
            g_pumpPhaseStartMs = nowMs;
            pollSoilAndWaterLevel(nowMs);
        }
        return;
    }

    if (g_pumpPhase == WateringPhase::RECHECK) {
        // Đọc lại ADC độ ẩm đất từ cảm biến sau khi tưới & ngấm nước
        bool isSufficient = (g_soilFlowAdc > g_soilThreshAdc) && (g_soilPct >= g_soilThreshPct);
        if (isSufficient) {
            // NHÁNH YES: ADC > 1100 (ĐÃ ĐỦ NƯỚC)
            setPumpHardwareState(false);
            g_pumpPhase          = WateringPhase::IDLE;
            g_wateringCycles     = 0;
            g_wateringFaultError = false;
            g_errCodes          &= ~ERR_WATER_FAULT;
            g_tapEmotionHoldMs   = nowMs + 4000; // Kích hoạt biểu cảm VUI VẺ (^ ^)
            g_lastAutoWaterCheckMs = nowMs;
            Serial.printf(
                "{\"event\":\"WATERING_SUFFICIENT\",\"soil_adc\":%u,\"soil_pct\":%.1f,"
                "\"cycles\":0,\"state\":\"SUFFICIENT\",\"msg\":\"Dat da dat nguong do am yeu cau (ADC > 1100). Tat bom & Reset chu ky!\"}\n",
                g_soilFlowAdc, g_soilPct
            );
            emitOledPacket();
        } else {
            // NHÁNH NO: Vẫn <= 1100 (Cần thêm nước)
            if (g_pumpAutoMode) {
                // Quay lại đầu Lưu đồ Phân hệ 3: kiểm tra cycles >= 5 & mực nước để tưới xung 3s tiếp theo
                triggerWateringPulse(false);
            } else {
                g_pumpPhase = WateringPhase::IDLE;
                emitOledPacket();
            }
        }
        return;
    }

    if (g_pumpPhase == WateringPhase::FAULT_LOCKED) {
        setPumpHardwareState(false);
        return;
    }

    // Pha IDLE: Kiểm tra định kỳ độ ẩm đất
    bool isSufficient = (g_soilFlowAdc > g_soilThreshAdc) && (g_soilPct >= g_soilThreshPct);
    if (isSufficient) {
        if (g_wateringCycles > 0) {
            g_wateringCycles = 0;
        }
        return;
    }

    // Nếu đất khô (ADC <= 1100) và đang ở chế độ Tưới Tự Động (AUTO)
    if (g_pumpAutoMode && !g_wateringFaultError) {
        if (nowMs - g_lastAutoWaterCheckMs >= AUTO_WATER_COOLDOWN_MS) {
            g_lastAutoWaterCheckMs = nowMs;
            triggerWateringPulse(false);
        }
    }
}

// ============================================================================
// 12. DRIVER MPU6050 TRÊN BUS I2C 0 (SDA = GPIO 11, SCL = GPIO 12, INT = GPIO 10)
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
    i2cWriteReg(g_mpuAddr, REG_INT_PIN_CFG, 0x20);  // Active-HIGH push-pull, latched
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
        g_tiltDeg = fmaxf(fabsf(g_roll), fabsf(g_pitch));
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
        g_tiltDeg = 0.0f;
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

static void detectMpuHapticEvents(uint32_t nowMs, float instDynG) {
    // 1. Phát hiện Rung lắc mạnh (SHAKE_PANIC: a > 1.5g hoặc dynG > 0.65g)
    if (g_amag > 1.50f || g_dynG > 0.65f) {
        g_shakeEmotionHoldMs = nowMs + 2800;
        if (nowMs - g_lastShakeEventMs > 2500) {
            g_lastShakeEventMs = nowMs;
            g_lastAiReply = "Á! Bạn làm mình chóng mặt rồi!";
            playSoundEffect("DIZZY");
            Serial.printf(
                "{\"event\":\"SHAKE_PANIC\",\"amag\":%.2f,\"dyn_g\":%.2f,"
                "\"voice\":\"Á! Bạn làm mình chóng mặt rồi!\"}\n",
                g_amag, g_dynG
            );
        }
        return;
    }

    // 2. Phát hiện Nghiêng / Ngã đổ chậu cây (CRITICAL_TILT: tilt > 45°)
    if (g_tiltDeg > 45.0f && (nowMs - g_lastTiltEventMs > 3000)) {
        g_lastTiltEventMs = nowMs;
        g_lastAiReply = "Á chậu nghiêng rồi, đỡ mình dậy với!";
        Serial.printf(
            "{\"event\":\"CRITICAL_TILT\",\"roll\":%.1f,\"pitch\":%.1f,\"tilt_deg\":%.1f,"
            "\"voice\":\"Á chậu nghiêng rồi, đỡ mình dậy với!\"}\n",
            g_roll, g_pitch, g_tiltDeg
        );
    }

    // 3. Phát hiện Gõ nhẹ / Xoa đầu (TAP / DOUBLE_TAP)
    if (instDynG > 0.22f && instDynG < 0.70f && !g_tapPeakLatch) {
        g_tapPeakLatch = true;
        if (nowMs - g_lastTapDetectedMs <= 480) {
            g_tapCountWindow++;
        } else {
            g_tapCountWindow = 1;
        }
        g_lastTapDetectedMs = nowMs;
        g_tapEmotionHoldMs  = nowMs + 3200;

        if (g_tapCountWindow >= 2) {
            g_tapCountWindow = 0;
            g_lastAiReply = "Hihi, cảm ơn bạn đã xoa đầu mình nha!";
            playSoundEffect("TAP_HAPPY");
            Serial.println("{\"event\":\"DOUBLE_TAP\",\"emotion\":\"HAPPY\",\"voice\":\"Hihi, cảm ơn bạn đã xoa đầu mình nha!\"}");
        } else {
            Serial.println("{\"event\":\"TAP\",\"emotion\":\"HAPPY\"}");
        }
    } else if (instDynG < 0.08f) {
        g_tapPeakLatch = false;
    }
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

    // Tính vector gia tốc tổng hợp: a = sqrt(X^2 + Y^2 + Z^2) (Phân hệ 2)
    float mag = sqrtf(ax * ax + ay * ay + az * az);
    float instDynG = fabsf(mag - 1.0f);
    g_amag = mag;
    g_dynG = 0.7f * g_dynG + 0.3f * instDynG;

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

    // Tính góc nghiêng chậu cây Tilt = atan2(Y, Z) / góc nghiêng cực đại (Phân hệ 2)
    g_tiltDeg = fmaxf(fabsf(g_roll), fabsf(g_pitch));
    g_lastTempC = s.tempC;

    detectMpuHapticEvents(millis(), instDynG);
    return true;
}

// ============================================================================
// 13. PHÂN HỆ 2: CẬP NHẬT TRẠNG THÁI SINH HỌC & BỘ ĐIỀU PHỐI DỮ LIỆU TRUNG TÂM
// ============================================================================

static void runCentralDataDispatcher(uint32_t nowMs) {
    // 1. Lưu dữ liệu hợp lệ vào cấu trúc biến toàn cục SystemSensorsData trong ESP32 RAM
    g_sensors.soilAdcRaw  = g_soilRawAdc;
    g_sensors.soilAdcNorm = g_soilFlowAdc;
    g_sensors.soilMv      = g_soilMv;
    g_sensors.soilPct     = g_soilPct;
    g_sensors.lightLux    = g_bhValid ? g_bhLux : 0.0f;
    g_sensors.airTempC    = g_ahtValid ? g_ahtTempC : (g_bmpValid ? g_bmpTempC : g_lastTempC);
    g_sensors.airHumPct   = g_ahtValid ? g_ahtHumPct : 0.0f;
    g_sensors.pressHpa    = g_bmpValid ? g_bmpPressHpa : 1013.25f;
    g_sensors.altitudeM   = g_bmpValid ? g_bmpAltM : 0.0f;
    g_sensors.accelX      = g_filtAx;
    g_sensors.accelY      = g_filtAy;
    g_sensors.accelZ      = g_filtAz;
    g_sensors.accelMag    = g_amag;
    g_sensors.dynG        = g_dynG;
    g_sensors.rollDeg     = g_roll;
    g_sensors.pitchDeg    = g_pitch;
    g_sensors.yawDeg      = g_yaw;
    g_sensors.tiltDeg     = g_tiltDeg;
    g_sensors.waterTankOk = g_tankOk;
    g_sensors.micRmsPct   = g_micRmsPct;
    g_sensors.micDbSpl    = g_micDbSpl;
    g_sensors.micVadActive = g_micVad;

    // 2. Cập nhật mã lỗi phần cứng hiện tại
    uint16_t err = (g_errCodes & (ERR_OUTLIER_DATA | ERR_WATER_FAULT));
    if (!g_mpuReady)         err |= ERR_MPU_FAULT;
    if (!g_ahtReady)         err |= ERR_AHT_FAULT;
    if (!g_bmpReady)         err |= ERR_BMP_FAULT;
    if (!g_bhReady)          err |= ERR_BH_FAULT;
    if (!g_oled1.ready)      err |= ERR_OLED1_FAULT;
    if (!g_oled2.ready)      err |= ERR_OLED2_FAULT;
    if (!g_micDriverInstalled) err |= ERR_MIC_FAULT;
    if (!g_tankOk)           err |= ERR_TANK_EMPTY;
    g_errCodes = err;
    g_sensors.sensorFaultFlag = (g_errCodes != ERR_NONE);

    // 3. Phân tích & Đánh giá 6 Điều kiện môi trường cây trồng (PlantEnvironmentState)
    g_plantState.soilMoistureSufficient = (g_sensors.soilAdcNorm > g_soilThreshAdc) && (g_sensors.soilPct >= g_soilThreshPct);
    g_plantState.lightSuitable          = (!g_bhValid) || (g_sensors.lightLux >= 10.0f && g_sensors.lightLux <= 4000.0f);
    g_plantState.tempSuitable           = (g_sensors.airTempC >= 15.0f && g_sensors.airTempC <= 35.0f);
    g_plantState.airQualityNormal       = (!g_ahtValid) || (g_sensors.airHumPct >= 30.0f && g_sensors.airHumPct <= 90.0f);
    g_plantState.waterTankSafe          = g_sensors.waterTankOk;
    g_plantState.potPositionStable      = (g_sensors.accelMag <= 1.5f) && (g_sensors.tiltDeg <= 30.0f);
    g_plantState.weatherRainWarning     = (g_bmpValid && g_pressBaselineHpa > 100.0f && (g_pressBaselineHpa - g_bmpPressHpa) >= 2.5f);

    if (g_wateringFaultError) {
        g_plantState.healthSummary = "WATERING_FAULT";
    } else if (!g_plantState.potPositionStable) {
        g_plantState.healthSummary = "MOTION_ALERT";
    } else if (!g_plantState.waterTankSafe) {
        g_plantState.healthSummary = "TANK_EMPTY";
    } else if (!g_plantState.soilMoistureSufficient) {
        g_plantState.healthSummary = "THIRSTY";
    } else if (!g_plantState.tempSuitable || !g_plantState.lightSuitable) {
        g_plantState.healthSummary = "ENV_STRESS";
    } else {
        g_plantState.healthSummary = "OPTIMAL";
    }
}

// ============================================================================
// 14. PHÂN HỆ 5: PHÂN TÍCH Ý ĐỊNH NGƯỜI DÙNG (NLP 3 NHÓM) & PHẢN HỒI AI CHATBOT
// ============================================================================

static void processVoiceOrChatInteraction(const String &rawInput) {
    String q = rawInput;
    q.trim();
    if (q.isEmpty()) return;

    String qLower = q;
    qLower.toLowerCase();

    // Bước 1: Chuyển OLED sang biểu cảm Đang suy nghĩ (-_-)
    g_voiceState = VoiceAiState::THINKING;
    g_voiceStateHoldMs = millis() + 400;
    updateOledAnimation(millis());

    // Bước 2: Kiểm tra kết nối mạng nếu người dùng yêu cầu xử lý Cloud Offline test
    if (qLower.indexOf("offline") >= 0) {
        g_lastAiReply = "Mất mạng rồi, cây vẫn tự động tưới nước nhé!";
        playSoundEffect("OFFLINE");
        Serial.printf(
            "{\"event\":\"AI_RESPONSE\",\"online\":false,\"intent\":\"OFFLINE_FALLBACK\","
            "\"query\":\"%s\",\"reply\":\"%s\",\"emotion\":\"%s\"}\n",
            q.c_str(), g_lastAiReply.c_str(), emotionToName(g_currentEmotion)
        );
        return;
    }

    UserIntentGroup intentGroup = UserIntentGroup::GROUP_3_COMPANION_CHAT;
    String actionTaken = "NONE";
    char replyBuf[320];

    // Phân loại câu nói vào 1 trong 3 nhóm chức năng theo Lưu đồ Phân hệ 5:
    // --- NHÓM 2: ĐIỀU KHIỂN THIẾT BỊ ---
    if (qLower.indexOf("ngừng tưới") >= 0 || qLower.indexOf("dừng tưới") >= 0 ||
        qLower.indexOf("tắt bơm") >= 0 || qLower.indexOf("stop") >= 0) {
        intentGroup = UserIntentGroup::GROUP_2_DEVICE_CONTROL;
        emergencyStopPump("VOICE_STOP_CMD");
        actionTaken = "STOP_WATERING";
        snprintf(replyBuf, sizeof(replyBuf), "Đã tắt bơm dừng tưới nước ngay lập tức theo lệnh của bạn!");
    } else if (qLower.indexOf("mở khóa") >= 0 || qLower.indexOf("reset lỗi") >= 0) {
        intentGroup = UserIntentGroup::GROUP_2_DEVICE_CONTROL;
        resetWateringFault();
        actionTaken = "RESET_WATERING_FAULT";
        snprintf(replyBuf, sizeof(replyBuf), "Mình đã mở khóa lỗi bơm và đưa bộ đếm chu kỳ về 0/5 rồi nhé!");
    } else if (qLower.indexOf("tưới nước") >= 0 || qLower.indexOf("tưới cây") >= 0 ||
               qLower.indexOf("tưới giúp") >= 0 || qLower.indexOf("cho uống nước") >= 0) {
        intentGroup = UserIntentGroup::GROUP_2_DEVICE_CONTROL;
        if (!g_tankOk) {
            actionTaken = "BLOCKED_TANK_EMPTY";
            snprintf(replyBuf, sizeof(replyBuf), "Hết nước rồi! Bạn bổ sung nước cho mình nhé!");
            playSoundEffect("TANK_EMPTY");
        } else if (g_wateringFaultError) {
            actionTaken = "BLOCKED_FAULT_LOCK";
            snprintf(replyBuf, sizeof(replyBuf), "Bơm đang khóa an toàn do đã tưới 5 chu kỳ liên tiếp. Bạn kiểm tra cảm biến đất giúp mình nha!");
            playSoundEffect("FAULT_ALARM");
        } else {
            actionTaken = "TRIGGER_WATER_3S";
            triggerWateringPulse(true);
            snprintf(replyBuf, sizeof(replyBuf), "Aaaa, mát quá! Cảm ơn bạn nha! Mình đang tưới 1 xung định lượng 3 giây.");
        }
    } else if (qLower.indexOf("tự động") >= 0 || qLower.indexOf("auto") >= 0) {
        intentGroup = UserIntentGroup::GROUP_2_DEVICE_CONTROL;
        g_pumpAutoMode = true;
        savePlantConfigToNvs();
        actionTaken = "SET_MODE_AUTO";
        snprintf(replyBuf, sizeof(replyBuf), "Đã bật chế độ tưới cây tự động (ngưỡng %.0f%% / ADC > %u)!", g_soilThreshPct, g_soilThreshAdc);
    } else if (qLower.indexOf("thủ công") >= 0 || qLower.indexOf("manual") >= 0) {
        intentGroup = UserIntentGroup::GROUP_2_DEVICE_CONTROL;
        g_pumpAutoMode = false;
        savePlantConfigToNvs();
        actionTaken = "SET_MODE_MANUAL";
        snprintf(replyBuf, sizeof(replyBuf), "Đã chuyển sang chế độ tưới thủ công theo ý bạn!");
    } else if (qLower.indexOf("đi ngủ") >= 0 || qLower.indexOf("ngủ đi") >= 0) {
        intentGroup = UserIntentGroup::GROUP_2_DEVICE_CONTROL;
        setOledPower(false);
        actionTaken = "SLEEP_OLED";
        snprintf(replyBuf, sizeof(replyBuf), "Chúc bạn ngủ ngon nha! Khi nào cần gọi mình dậy cứ chạm nhẹ vào chậu nhé! Zzz...");
    }
    // --- NHÓM 1: HỎI THĂM TÌNH TRẠNG CÂY (Dựa trên dữ liệu cảm biến thực tế 100%) ---
    else if (qLower.indexOf("khỏe không") >= 0 || qLower.indexOf("tình trạng") >= 0 ||
             qLower.indexOf("cần tưới") >= 0 || qLower.indexOf("nhiệt độ") >= 0 ||
             qLower.indexOf("độ ẩm") >= 0 || qLower.indexOf("ánh sáng") >= 0 ||
             qLower.indexOf("áp suất") >= 0 || qLower.indexOf("thế nào") >= 0 ||
             qLower.indexOf("thấy sao") >= 0) {
        intentGroup = UserIntentGroup::GROUP_1_STATUS_QUERY;
        if (!g_tankOk) {
            snprintf(replyBuf, sizeof(replyBuf),
                     "Hết nước rồi! Bạn bổ sung nước cho mình nhé! (Đất: %.0f%%, Nhiệt độ: %.1f°C, Ánh sáng: %.0f Lux)",
                     g_sensors.soilPct, g_sensors.airTempC, g_sensors.lightLux);
        } else if (!g_plantState.soilMoistureSufficient) {
            snprintf(replyBuf, sizeof(replyBuf),
                     "Mình khát nước lắm rồi đó! Độ ẩm đất hiện chỉ còn %.0f%% (ADC=%u <= %u), nhiệt độ %.1f°C.",
                     g_sensors.soilPct, g_sensors.soilAdcNorm, g_soilThreshAdc, g_sensors.airTempC);
        } else if (g_sensors.airTempC > 32.0f) {
            snprintf(replyBuf, sizeof(replyBuf),
                     "Nóng quá, cho mình nghỉ ngơi chút đi! Nhiệt độ đang là %.1f°C, độ ẩm không khí %.0f%%, độ ẩm đất %.0f%%.",
                     g_sensors.airTempC, g_sensors.airHumPct, g_sensors.soilPct);
        } else if (g_bhValid && g_sensors.lightLux < 30.0f) {
            snprintf(replyBuf, sizeof(replyBuf),
                     "Hôm nay mình hơi thiếu nắng! Ánh sáng hiện tại chỉ %.0f Lux, nhiệt độ %.1f°C, độ ẩm đất %.0f%%.",
                     g_sensors.lightLux, g_sensors.airTempC, g_sensors.soilPct);
        } else {
            snprintf(replyBuf, sizeof(replyBuf),
                     "Hôm nay mình khỏe re luôn! Đất đủ ẩm %.0f%% (ADC=%u), nhiệt độ %.1f°C, ẩm KK %.0f%%, ánh sáng %.0f Lux, áp suất %.1f hPa.",
                     g_sensors.soilPct, g_sensors.soilAdcNorm, g_sensors.airTempC,
                     g_sensors.airHumPct, g_sensors.lightLux, g_sensors.pressHpa);
        }
    }
    // --- NHÓM 3: TRÒ CHUYỆN VỚI CÂY & GIẢI TRÍ ---
    else {
        intentGroup = UserIntentGroup::GROUP_3_COMPANION_CHAT;
        if (qLower.indexOf("ăn gì") >= 0 || qLower.indexOf("bói") >= 0) {
            const char *picks[] = {
                "Quẻ lá xanh phán: Hôm nay ăn Cơm tấm sườn bì chả cho sung sức nha Sen!",
                "Quẻ quang hợp phán: Làm một tô Phở bò nóng hổi rồi về ngắm lá xanh của tui!",
                "Quẻ gió mát phán: Bún chả hoặc lẩu nấm thanh đạm là chuẩn bài luôn!"
            };
            snprintf(replyBuf, sizeof(replyBuf), "%s", picks[millis() % 3]);
        } else if (!g_plantState.potPositionStable) {
            snprintf(replyBuf, sizeof(replyBuf), "Á! Bạn làm mình chóng mặt rồi! Góc nghiêng đang là %.1f độ kìa!", g_sensors.tiltDeg);
        } else if (!g_plantState.soilMoistureSufficient) {
            snprintf(replyBuf, sizeof(replyBuf), "Mình khát nước lắm rồi đó! Tưới cho mình miếng nước rồi mình tâm sự tiếp nha!");
        } else {
            snprintf(replyBuf, sizeof(replyBuf),
                     "Mình đang quang hợp vui vẻ lắm nè (^ ^)! Nhiệt độ %.1f°C rất dễ chịu, cảm ơn bạn đã trò chuyện cùng mình nha!",
                     g_sensors.airTempC);
        }
    }

    g_lastAiReply = String(replyBuf);

    // Bước 3: Phát giọng trả lời qua Loa & Chuyển OLED sang biểu cảm Đang nói chuyện (^o^)
    if (actionTaken != "TRIGGER_WATER_3S" && actionTaken != "BLOCKED_TANK_EMPTY" && actionTaken != "BLOCKED_FAULT_LOCK") {
        playSoundEffect("SPEAK");
    }
    g_voiceState = VoiceAiState::SPEAKING;
    g_voiceStateHoldMs = millis() + 2200;

    const char *intentStr =
        (intentGroup == UserIntentGroup::GROUP_1_STATUS_QUERY)   ? "GROUP_1_STATUS_QUERY" :
        (intentGroup == UserIntentGroup::GROUP_2_DEVICE_CONTROL) ? "GROUP_2_DEVICE_CONTROL" :
                                                                   "GROUP_3_COMPANION_CHAT";

    Serial.printf(
        "{\"event\":\"AI_RESPONSE\",\"intent\":\"%s\",\"action\":\"%s\","
        "\"query\":\"%s\",\"reply\":\"%s\",\"emotion\":\"SPEAKING\","
        "\"context\":{\"soil_pct\":%.1f,\"soil_adc\":%u,\"temp_c\":%.1f,\"air_hum_pct\":%.1f,"
        "\"light_lux\":%.1f,\"press_hpa\":%.1f,\"tilt_deg\":%.1f,\"tank_ok\":%s,\"pump_on\":%s}}\n",
        intentStr, actionTaken.c_str(),
        q.c_str(), g_lastAiReply.c_str(),
        g_sensors.soilPct, g_sensors.soilAdcNorm, g_sensors.airTempC, g_sensors.airHumPct,
        g_sensors.lightLux, g_sensors.pressHpa, g_sensors.tiltDeg,
        g_sensors.waterTankOk ? "true" : "false",
        g_pumpOn ? "true" : "false"
    );
    emitOledPacket();
}

// ============================================================================
// 15. PHÂN HỆ 1 & 6: KẾT NỐI WI-FI, ĐỒNG BỘ NTP & OFFLINE BACKLOG SYNC
// ============================================================================

static void pushOfflineBacklogRecord() {
    OfflineBacklogRecord rec{};
    time_t nowEpoch = time(nullptr);
    rec.timestampSec = (nowEpoch > 100000) ? static_cast<uint32_t>(nowEpoch) : (millis() / 1000);
    rec.soilPct      = g_sensors.soilPct;
    rec.soilAdc      = g_sensors.soilAdcNorm;
    rec.tempC        = g_sensors.airTempC;
    rec.airHumPct    = g_sensors.airHumPct;
    rec.lightLux     = g_sensors.lightLux;
    rec.pressHpa     = g_sensors.pressHpa;
    rec.tiltDeg      = g_sensors.tiltDeg;
    rec.tankOk       = g_sensors.waterTankOk;
    rec.pumpOn       = g_pumpOn;
    rec.cycles       = g_wateringCycles;
    rec.errCodes     = g_errCodes;

    size_t writeIdx = (g_backlogHead + g_backlogCount) % MAX_BACKLOG_ITEMS;
    if (g_backlogCount == MAX_BACKLOG_ITEMS) {
        g_backlogHead = (g_backlogHead + 1) % MAX_BACKLOG_ITEMS;
    } else {
        g_backlogCount++;
    }
    g_backlog[writeIdx] = rec;
}

static void syncOfflineBacklogToCloud() {
    if (g_backlogCount == 0) return;
    Serial.printf("{\"event\":\"BACKLOG_SYNC_START\",\"count\":%u}\n", static_cast<unsigned>(g_backlogCount));
    while (g_backlogCount > 0) {
        const OfflineBacklogRecord &r = g_backlog[g_backlogHead];
        Serial.printf(
            "{\"event\":\"BACKLOG_ITEM\",\"ts\":%lu,\"soil_pct\":%.1f,\"soil_adc\":%u,"
            "\"temp_c\":%.1f,\"air_hum_pct\":%.1f,\"light_lux\":%.1f,\"press_hpa\":%.1f,"
            "\"tilt_deg\":%.1f,\"tank_ok\":%s,\"pump_on\":%s,\"cycles\":%u,\"err_codes\":%u}\n",
            static_cast<unsigned long>(r.timestampSec),
            r.soilPct, r.soilAdc, r.tempC, r.airHumPct, r.lightLux, r.pressHpa,
            r.tiltDeg, r.tankOk ? "true" : "false", r.pumpOn ? "true" : "false",
            r.cycles, r.errCodes
        );
        g_backlogHead = (g_backlogHead + 1) % MAX_BACKLOG_ITEMS;
        g_backlogCount--;
    }
    Serial.println("{\"event\":\"BACKLOG_SYNC_DONE\",\"remaining\":0}");
}

static void initWifiStationWithTimeout() {
    if (!g_wifiConfigured || g_wifiSsid.isEmpty()) {
        g_wifiOnline = false;
        return;
    }
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(g_wifiSsid.c_str(), g_wifiPass.c_str());

    // Chờ tối đa 10s theo Lưu đồ Phân hệ 1 (hoặc thoát ngay khi kết nối được)
    uint32_t startMs = millis();
    while (WiFi.status() != WL_CONNECTED && (millis() - startMs < 10000)) {
        delay(100);
        if (Serial.available() > 0) break; // Không chặn nếu người dùng gửi lệnh Serial
    }

    if (WiFi.status() == WL_CONNECTED) {
        g_wifiOnline = true;
        configTime(7 * 3600, 0, "pool.ntp.org", "time.nist.gov");
        g_ntpSynced = true;
        Serial.printf(
            "{\"event\":\"WIFI_ONLINE\",\"ssid\":\"%s\",\"ip\":\"%s\",\"rssi\":%d}\n",
            g_wifiSsid.c_str(), WiFi.localIP().toString().c_str(), WiFi.RSSI()
        );
    } else {
        g_wifiOnline = false;
        Serial.printf(
            "{\"event\":\"WIFI_OFFLINE\",\"ssid\":\"%s\",\"msg\":\"Chuyen sang che do Offline tu hanh 100%%\"}\n",
            g_wifiSsid.c_str()
        );
    }
}

static void pollWifiAndBacklogNonBlocking(uint32_t nowMs) {
    bool currentlyConnected = (g_wifiConfigured && WiFi.status() == WL_CONNECTED);

    if (currentlyConnected && !g_wifiOnline) {
        g_wifiOnline = true;
        if (!g_ntpSynced) {
            configTime(7 * 3600, 0, "pool.ntp.org", "time.nist.gov");
            g_ntpSynced = true;
        }
        Serial.printf(
            "{\"event\":\"WIFI_RECONNECTED\",\"ip\":\"%s\",\"rssi\":%d}\n",
            WiFi.localIP().toString().c_str(), WiFi.RSSI()
        );
        // Đồng bộ dữ liệu bù khi có mạng trở lại (Phân hệ 6)
        syncOfflineBacklogToCloud();
    } else if (!currentlyConnected && g_wifiOnline) {
        g_wifiOnline = false;
        Serial.println("{\"event\":\"WIFI_LOST\",\"msg\":\"Mat ket noi Wi-Fi -> Kich hoat luu dem Offline Backlog\"}");
    }

    // Khi mất Wi-Fi: Định kỳ lưu gói tin vào bộ nhớ đệm & mỗi 30s thử kết nối lại ngầm
    if (!g_wifiOnline) {
        if (nowMs - g_lastBacklogSaveMs >= 15000) {
            g_lastBacklogSaveMs = nowMs;
            pushOfflineBacklogRecord();
        }
        if (g_wifiConfigured && (nowMs - g_lastWifiCheckMs >= WIFI_RECONNECT_PERIOD_MS)) {
            g_lastWifiCheckMs = nowMs;
            WiFi.disconnect();
            WiFi.begin(g_wifiSsid.c_str(), g_wifiPass.c_str());
        }
    }
}

static void emitFullTelemetryEvent(const char *triggerTag) {
    int rssi = g_wifiOnline ? WiFi.RSSI() : 0;
    uint32_t uptimeSec = millis() / 1000;
    Serial.printf(
        "{\"event\":\"TELEMETRY\",\"trigger\":\"%s\","
        "\"soil_adc\":%u,\"soil_pct\":%.1f,\"soil_mv\":%d,"
        "\"light_lux\":%.1f,\"temp_c\":%.2f,\"air_hum_pct\":%.1f,\"press_hpa\":%.2f,\"alt_m\":%.1f,"
        "\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,\"amag\":%.3f,\"dyn_g\":%.3f,"
        "\"roll\":%.2f,\"pitch\":%.2f,\"yaw\":%.2f,\"tilt_deg\":%.2f,"
        "\"tank_ok\":%s,\"est_cycles\":%u,\"pump_on\":%s,\"pump_phase\":\"%s\","
        "\"mode\":\"%s\",\"threshold_pct\":%.1f,\"threshold_adc\":%u,"
        "\"consecutive_cycles\":%u,\"daily_cycles\":%u,\"watering_fault\":%s,"
        "\"emotion\":\"%s\",\"voice_state\":\"%s\",\"health\":\"%s\","
        "\"err_codes\":%u,\"wifi_online\":%s,\"rssi\":%d,\"uptime\":%lu,\"backlog_count\":%u}\n",
        triggerTag,
        g_sensors.soilAdcNorm, g_sensors.soilPct, g_sensors.soilMv,
        g_sensors.lightLux, g_sensors.airTempC, g_sensors.airHumPct, g_sensors.pressHpa, g_sensors.altitudeM,
        g_sensors.accelX, g_sensors.accelY, g_sensors.accelZ, g_sensors.accelMag, g_sensors.dynG,
        g_sensors.rollDeg, g_sensors.pitchDeg, g_sensors.yawDeg, g_sensors.tiltDeg,
        g_sensors.waterTankOk ? "true" : "false", g_estimatedTankCycles,
        g_pumpOn ? "true" : "false", pumpPhaseToName(g_pumpPhase),
        g_pumpAutoMode ? "AUTO" : "MANUAL", g_soilThreshPct, g_soilThreshAdc,
        g_wateringCycles, g_dailyWateringCycles, g_wateringFaultError ? "true" : "false",
        emotionToName(g_currentEmotion), voiceStateToName(g_voiceState), g_plantState.healthSummary,
        g_errCodes, g_wifiOnline ? "true" : "false", rssi,
        static_cast<unsigned long>(uptimeSec), static_cast<unsigned>(g_backlogCount)
    );
}

// ============================================================================
// 16. QUÉT BUS I2C & XỬ LÝ LỆNH ĐIỀU KHIỂN SERIAL / DOWNLINK COMMANDS
// ============================================================================

static void printI2cScanEvent(bool includeHumanReadable = false) {
    String out;
    out.reserve(300);
    out += "{\"event\":\"SCAN\",\"sda\":";
    out += String(I2C_SDA_PIN);
    out += ",\"scl\":";
    out += String(I2C_SCL_PIN);
    out += ",\"addrs\":[";

    bool first = true;
    for (uint8_t addr = 0x03; addr <= 0x77; ++addr) {
        BUS0_I2C.beginTransmission(addr);
        uint8_t err = BUS0_I2C.endTransmission(true);
        if (err == 0) {
            if (!first) out += ',';
            char hex[8];
            snprintf(hex, sizeof(hex), "\"0x%02X\"", addr);
            out += hex;
            first = false;
            if (includeHumanReadable) {
                Serial.printf("  [+] Bus 0 (SDA=%d,SCL=%d) Địa chỉ 0x%02X\n", I2C_SDA_PIN, I2C_SCL_PIN, addr);
            }
        } else if (err == 5) {
            break;
        }
    }
    out += "],\"bus1_sda\":";
    out += String(OLED2_SDA_PIN);
    out += ",\"bus1_scl\":";
    out += String(OLED2_SCL_PIN);
    out += ",\"bus1_addrs\":[";
    first = true;
    if (g_wire1Started) {
        for (uint8_t addr = 0x03; addr <= 0x77; ++addr) {
            BUS1_I2C.beginTransmission(addr);
            uint8_t err = BUS1_I2C.endTransmission(true);
            if (err == 0) {
                if (!first) out += ',';
                char hex[8];
                snprintf(hex, sizeof(hex), "\"0x%02X\"", addr);
                out += hex;
                first = false;
                if (includeHumanReadable) {
                    Serial.printf("  [+] Bus 1 (SDA=%d,SCL=%d) Địa chỉ 0x%02X\n", OLED2_SDA_PIN, OLED2_SCL_PIN, addr);
                    if (addr == 0x3C) {
                        // Báo thêm 0x3D cho smart_plant_app.py nhận biết OLED-2 trên Bus 1
                        Serial.println("  [+] Bus 1 (OLED-2) Địa chỉ 0x3D");
                    }
                }
            } else if (err == 5) {
                break;
            }
        }
    }
    out += "]}";
    Serial.println(out);
}

static void printMpuStatusError(const char *statusCode, const char *msg) {
    Serial.printf(
        "{\"type\":\"mpu\",\"status\":\"%s\",\"addr\":\"0x%02X\",\"whoami\":\"0x%02X\",\"sda\":%d,\"scl\":%d,\"int_pin\":%d,\"msg\":\"%s\"}\n",
        statusCode, g_mpuAddr, g_whoAmI, I2C_SDA_PIN, I2C_SCL_PIN, MPU_INT_PIN, msg
    );
}

static EyeEmotion parseEmotionName(const String &s) {
    if (s.equalsIgnoreCase("HAPPY"))     return EyeEmotion::HAPPY;
    if (s.equalsIgnoreCase("SURPRISED")) return EyeEmotion::SURPRISED;
    if (s.equalsIgnoreCase("DIZZY"))     return EyeEmotion::DIZZY;
    if (s.equalsIgnoreCase("LISTENING")) return EyeEmotion::LISTENING;
    if (s.equalsIgnoreCase("SAD"))       return EyeEmotion::SAD;
    if (s.equalsIgnoreCase("LOVE"))      return EyeEmotion::LOVE;
    if (s.equalsIgnoreCase("SLEEPY"))    return EyeEmotion::SLEEPY;
    if (s.equalsIgnoreCase("THIRSTY"))   return EyeEmotion::THIRSTY;
    if (s.equalsIgnoreCase("THINKING"))  return EyeEmotion::THINKING;
    if (s.equalsIgnoreCase("SPEAKING"))  return EyeEmotion::SPEAKING;
    return EyeEmotion::NORMAL;
}

static void handleCommand(String cmd) {
    cmd.trim();
    if (cmd.isEmpty()) return;

    const char *modeName =
        (g_viewMode == ActiveViewMode::ENV)  ? "ENV" :
        (g_viewMode == ActiveViewMode::MIC)  ? "MIC" :
        (g_viewMode == ActiveViewMode::OLED) ? "OLED" : "MPU";

    // 1. Nhóm lệnh Hệ thống & Quét I2C
    if (cmd.equalsIgnoreCase("PING")) {
        Serial.printf(
            "{\"event\":\"PONG\",\"fw\":\"esp32s3-smartplant-4.0\",\"sleep\":%s,\"sleep_mode\":\"%s\",\"sleep_delay\":%lu,\"mode\":\"%s\","
            "\"sda\":%d,\"scl\":%d,\"oled2_sda\":%d,\"oled2_scl\":%d,\"mpu_int\":%d,\"mic_ws\":%d,\"mic_sck\":%d,\"mic_sd\":%d,"
            "\"soil_pin\":%d,\"tank_pin\":%d,\"pump_pin\":%d,\"spk_bclk\":%d,\"spk_lrclk\":%d,\"spk_din\":%d}\n",
            g_autoSleepEnabled ? "true" : "false",
            g_sleepMode == SleepMode::DEEP ? "DEEP" : "LIGHT",
            static_cast<unsigned long>(g_idleSleepMs / 1000),
            modeName,
            I2C_SDA_PIN, I2C_SCL_PIN, OLED2_SDA_PIN, OLED2_SCL_PIN,
            MPU_INT_PIN, MIC_WS_PIN, MIC_SCK_PIN, MIC_SD_PIN,
            SOIL_ADC_PIN, WATER_LEVEL_PIN, PUMP_RELAY_PIN,
            SPK_BCLK_PIN, SPK_LRCLK_PIN, SPK_DIN_PIN
        );
    } else if (cmd.equalsIgnoreCase("SCAN") || cmd.equalsIgnoreCase("s")) {
        probeEnvSensors();
        printI2cScanEvent(cmd.equalsIgnoreCase("s"));
        if (!g_mpuReady && probeAndInitMpu() && !g_cal.savedInNvs) {
            calibrateMpu(200, false, false);
        }
        emitOledPacket();
    } else if (cmd.equalsIgnoreCase("TELEMETRY") || cmd.equalsIgnoreCase("STATUS")) {
        runCentralDataDispatcher(millis());
        emitFullTelemetryEvent("CMD_REQUEST");
    }
    // 2. Nhóm lệnh Hiệu chuẩn MPU6050 ('CAL', 'c', 'z', 'CLEAR_CAL', 'RESET_YAW')
    else if (cmd.equalsIgnoreCase("CAL") || cmd.equalsIgnoreCase("c")) {
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
    } else if (cmd.equalsIgnoreCase("RESET_YAW") || cmd.equalsIgnoreCase("z")) {
        g_yaw = 0.0f;
        g_roll = 0.0f;
        g_pitch = 0.0f;
        g_idleStartMs = millis();
        Serial.println("{\"event\":\"YAW_RESET\"}");
    }
    // 3. Nhóm lệnh Hiệu chuẩn Độ ẩm Đất ('d' = Khô 0%, 'w' = Ướt 100%, 'r' = Reset, SOIL_SIM)
    else if (cmd.equalsIgnoreCase("d") || cmd.equalsIgnoreCase("CAL_SOIL_DRY")) {
        int curMv = analogReadMilliVolts(SOIL_ADC_PIN);
        if (curMv > 200) g_soilDryMv = curMv;
        savePlantConfigToNvs();
        Serial.printf("{\"event\":\"SOIL_CAL_DRY\",\"dry_mv\":%d,\"wet_mv\":%d}\n", g_soilDryMv, g_soilWetMv);
    } else if (cmd.equalsIgnoreCase("w") || cmd.equalsIgnoreCase("CAL_SOIL_WET")) {
        int curMv = analogReadMilliVolts(SOIL_ADC_PIN);
        if (curMv > 100) g_soilWetMv = curMv;
        savePlantConfigToNvs();
        Serial.printf("{\"event\":\"SOIL_CAL_WET\",\"dry_mv\":%d,\"wet_mv\":%d}\n", g_soilDryMv, g_soilWetMv);
    } else if (cmd.equalsIgnoreCase("r") || cmd.equalsIgnoreCase("RESET_SOIL_CAL")) {
        g_soilDryMv = 2850;
        g_soilWetMv = 1250;
        savePlantConfigToNvs();
        Serial.println("{\"event\":\"SOIL_CAL_RESET\",\"dry_mv\":2850,\"wet_mv\":1250}");
    } else if (cmd.startsWith("SOIL_SIM=") || cmd.startsWith("soil_sim=")) {
        String valStr = cmd.substring(9);
        if (valStr.equalsIgnoreCase("OFF")) {
            g_soilSimMode = false;
        } else {
            g_soilSimMode = true;
            g_soilSimPct  = fmaxf(0.0f, fminf(100.0f, valStr.toFloat()));
        }
        pollSoilAndWaterLevel(millis());
        emitEnvPacket();
        emitOledPacket();
    } else if (cmd.startsWith("SOIL_ADC=") || cmd.startsWith("soil_adc=")) {
        uint16_t targetAdc = static_cast<uint16_t>(cmd.substring(9).toInt());
        g_soilSimMode = true;
        if (targetAdc <= g_soilThreshAdc) {
            g_soilSimPct = (static_cast<float>(targetAdc) / fmaxf(1.0f, static_cast<float>(g_soilThreshAdc))) * g_soilThreshPct;
        } else {
            g_soilSimPct = g_soilThreshPct +
                           ((static_cast<float>(targetAdc - g_soilThreshAdc)) / fmaxf(1.0f, 4095.0f - g_soilThreshAdc)) *
                           (100.0f - g_soilThreshPct);
        }
        pollSoilAndWaterLevel(millis());
        emitEnvPacket();
        emitOledPacket();
    }
    // 4. Nhóm lệnh Điều khiển Tưới cây Tự động & An toàn (Phân hệ 3: WATER:3, ESTOP, MODE:AUTO/MANUAL, THRESH, TANK_REFILL)
    else if (cmd.startsWith("WATER") || cmd.equalsIgnoreCase("PUMP_PULSE")) {
        triggerWateringPulse(true);
    } else if (cmd.equalsIgnoreCase("ESTOP") || cmd.equalsIgnoreCase("STOP_WATER") || cmd.equalsIgnoreCase("PUMP_OFF")) {
        emergencyStopPump("USER_ESTOP");
    } else if (cmd.equalsIgnoreCase("WATER_RESET") || cmd.equalsIgnoreCase("RESET_FAULT")) {
        resetWateringFault();
    } else if (cmd.equalsIgnoreCase("TANK_REFILL") || cmd.equalsIgnoreCase("TANK=OK")) {
        g_tankManualOverride  = true;
        g_tankManualState     = true;
        g_tankOk              = true;
        g_estimatedTankCycles = 18;
        g_errCodes           &= ~ERR_TANK_EMPTY;
        Serial.println("{\"event\":\"TANK_REFILLED\",\"tank_ok\":true,\"est_cycles\":18}");
        emitOledPacket();
    } else if (cmd.equalsIgnoreCase("TANK=EMPTY")) {
        g_tankManualOverride  = true;
        g_tankManualState     = false;
        g_tankOk              = false;
        g_estimatedTankCycles = 0;
        if (g_pumpOn) emergencyStopPump("TANK_EMPTY");
        playSoundEffect("TANK_EMPTY");
        Serial.println("{\"event\":\"TANK_EMPTY\",\"tank_ok\":false,\"est_cycles\":0}");
        emitOledPacket();
    } else if (cmd.equalsIgnoreCase("TANK=SENSOR")) {
        g_tankManualOverride = false;
        savePlantConfigToNvs();
        Serial.println("{\"event\":\"TANK_SENSOR_MODE\",\"override\":false}");
    } else if (cmd.equalsIgnoreCase("MODE:AUTO") || cmd.equalsIgnoreCase("PUMP_MODE=AUTO")) {
        g_pumpAutoMode = true;
        savePlantConfigToNvs();
        Serial.println("{\"event\":\"PUMP_MODE_SET\",\"mode\":\"AUTO\"}");
        emitOledPacket();
    } else if (cmd.equalsIgnoreCase("MODE:MANUAL") || cmd.equalsIgnoreCase("PUMP_MODE=MANUAL")) {
        g_pumpAutoMode = false;
        savePlantConfigToNvs();
        Serial.println("{\"event\":\"PUMP_MODE_SET\",\"mode\":\"MANUAL\"}");
        emitOledPacket();
    } else if (cmd.startsWith("THRESH=") || cmd.startsWith("THRESH:")) {
        float th = cmd.substring(7).toFloat();
        if (th >= 5.0f && th <= 90.0f) {
            g_soilThreshPct = th;
            savePlantConfigToNvs();
            Serial.printf("{\"event\":\"THRESH_SET\",\"threshold_pct\":%.1f,\"threshold_adc\":%u}\n", g_soilThreshPct, g_soilThreshAdc);
            emitOledPacket();
        }
    } else if (cmd.startsWith("THRESH_ADC=")) {
        int adcTh = cmd.substring(11).toInt();
        if (adcTh >= 100 && adcTh <= 3900) {
            g_soilThreshAdc = static_cast<uint16_t>(adcTh);
            savePlantConfigToNvs();
            Serial.printf("{\"event\":\"THRESH_ADC_SET\",\"threshold_adc\":%u}\n", g_soilThreshAdc);
        }
    }
    // 5. Nhóm lệnh Chuyển Tab Giao diện (mpu6050_ui.py & smart_plant_app.py)
    else if (cmd.equalsIgnoreCase("WAKE")) {
        g_idleStartMs = millis();
        setOledPower(true);
        Serial.println("{\"event\":\"AWAKE\"}");
    } else if (cmd.equalsIgnoreCase("MODE=MPU")) {
        g_viewMode = ActiveViewMode::MPU;
        g_legacyGroupMode = 0;
        g_idleStartMs = millis();
        Serial.println("{\"event\":\"MODE_SET\",\"mode\":\"MPU\"}");
    } else if (cmd.equalsIgnoreCase("MODE=ENV")) {
        g_viewMode = ActiveViewMode::ENV;
        g_legacyGroupMode = 0;
        g_idleStartMs = millis();
        Serial.println("{\"event\":\"MODE_SET\",\"mode\":\"ENV\"}");
    } else if (cmd.equalsIgnoreCase("MODE=MIC")) {
        g_viewMode = ActiveViewMode::MIC;
        g_legacyGroupMode = 0;
        g_idleStartMs = millis();
        Serial.println("{\"event\":\"MODE_SET\",\"mode\":\"MIC\"}");
        pollMicrophoneNonBlocking(millis(), true);
        emitMicPacket();
    } else if (cmd.equalsIgnoreCase("MODE=OLED")) {
        g_viewMode = ActiveViewMode::OLED;
        g_legacyGroupMode = 0;
        g_idleStartMs = millis();
        probeOledDisplays();
        Serial.println("{\"event\":\"MODE_SET\",\"mode\":\"OLED\"}");
        emitOledPacket();
    } else if (cmd == "1") {
        g_legacyGroupMode = 1;
        emitEnvPacket();
    } else if (cmd == "2") {
        g_legacyGroupMode = 2;
    } else if (cmd == "3") {
        g_legacyGroupMode = 3;
        emitMicPacket();
    }
    // 6. Nhóm lệnh Ngủ / Thức & Tốc độ lấy mẫu
    else if (cmd.equalsIgnoreCase("SLEEP=ON")) {
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
    // 7. Nhóm lệnh Micro INMP441, Thu âm STT & AI Chatbot (Phân hệ 5)
    else if (cmd.startsWith("MIC_THR=")) {
        float thr = cmd.substring(8).toFloat();
        if (thr >= 1.0f && thr <= 90.0f) {
            g_micVadThreshPct = thr;
            emitMicPacket();
        }
    } else if (cmd.startsWith("MIC_GAIN=")) {
        float gain = cmd.substring(9).toFloat();
        if (gain >= 0.2f && gain <= 10.0f) {
            g_micGain = gain;
            emitMicPacket();
        }
    } else if (cmd == "+") {
        g_micGain = fminf(10.0f, g_micGain + 0.5f);
        emitMicPacket();
    } else if (cmd == "-") {
        g_micGain = fmaxf(0.2f, g_micGain - 0.5f);
        emitMicPacket();
    } else if (cmd.startsWith("REC")) {
        int sec = 5;
        int colon = cmd.indexOf(':');
        if (colon > 0) sec = cmd.substring(colon + 1).toInt();
        streamMicPcmRecording(sec);
    } else if (cmd.startsWith("CHAT:") || cmd.startsWith("CHAT=") ||
               cmd.startsWith("ASK:") || cmd.startsWith("VOICE_CMD:")) {
        int sep = cmd.indexOf(':');
        if (sep < 0) sep = cmd.indexOf('=');
        String question = (sep >= 0) ? cmd.substring(sep + 1) : cmd;
        processVoiceOrChatInteraction(question);
    } else if (cmd.startsWith("SFX=")) {
        String sfx = cmd.substring(4);
        sfx.toUpperCase();
        playSoundEffect(sfx.c_str());
    } else if (cmd.startsWith("MUTE=")) {
        g_audioMuted = (cmd.substring(5).toInt() != 0);
        Serial.printf("{\"event\":\"AUDIO_MUTE\",\"muted\":%s}\n", g_audioMuted ? "true" : "false");
    }
    // 8. Nhóm lệnh Điều khiển Hai Màn hình OLED (Phân hệ 4)
    else if (cmd.equalsIgnoreCase("OLED_PROBE")) {
        g_oled1.ready = false;
        g_oled2.ready = false;
        probeOledDisplays();
        emitOledPacket();
    } else if (cmd.equalsIgnoreCase("OLED_BLINK")) {
        g_isBlinking = true;
        g_blinkStartMs = millis();
        emitOledPacket();
    } else if (cmd.startsWith("OLED_MODE=")) {
        String m = cmd.substring(10);
        if (m.equalsIgnoreCase("EYES")) {
            g_oledMode = OledDisplayMode::EYES;
        } else if (m.equalsIgnoreCase("DASHBOARD")) {
            g_oledMode = OledDisplayMode::DASHBOARD;
        } else if (m.equalsIgnoreCase("TEXT")) {
            g_oledMode = OledDisplayMode::TEXT;
        } else if (m.equalsIgnoreCase("TEST")) {
            g_oledMode = OledDisplayMode::TEST;
        }
        emitOledPacket();
    } else if (cmd.startsWith("OLED_EMO=")) {
        String e = cmd.substring(9);
        g_oledMode = OledDisplayMode::EYES;
        if (e.equalsIgnoreCase("AUTO")) {
            g_oledAutoEmotion = true;
        } else {
            g_oledAutoEmotion = false;
            g_manualEmotion = parseEmotionName(e);
            g_currentEmotion = g_manualEmotion;
        }
        emitOledPacket();
    } else if (cmd.startsWith("OLED_TEXT=")) {
        String payload = cmd.substring(10);
        int sep = payload.indexOf('|');
        if (sep >= 0) {
            g_oledText1 = payload.substring(0, sep);
            g_oledText2 = payload.substring(sep + 1);
        } else {
            g_oledText1 = payload;
            g_oledText2 = "";
        }
        g_oledText1.replace("\"", "'");
        g_oledText2.replace("\"", "'");
        g_oledMode = OledDisplayMode::TEXT;
        emitOledPacket();
    } else if (cmd.startsWith("OLED_CONTRAST=")) {
        int c = cmd.substring(14).toInt();
        if (c >= 0 && c <= 255) {
            setOledContrast(static_cast<uint8_t>(c));
            savePlantConfigToNvs();
            emitOledPacket();
        }
    } else if (cmd.startsWith("OLED_INVERT=")) {
        int v = cmd.substring(12).toInt();
        setOledInvert(v != 0);
        emitOledPacket();
    } else if (cmd.startsWith("OLED_POWER=")) {
        int v = cmd.substring(11).toInt();
        setOledPower(v != 0);
        emitOledPacket();
    } else if (cmd.startsWith("OLED_SWAP=")) {
        int v = cmd.substring(10).toInt();
        g_oledSwapLr = (v != 0);
        uint8_t tmp = g_oled1Rot;
        g_oled1Rot = g_oled2Rot;
        g_oled2Rot = tmp;
        savePlantConfigToNvs();
        emitOledPacket();
    } else if (cmd.startsWith("OLED_ROT1=")) {
        int deg = cmd.substring(10).toInt();
        g_oled1Rot = static_cast<uint8_t>(((deg >= 90 ? (deg / 90) : deg) % 4 + 4) % 4);
        savePlantConfigToNvs();
        emitOledPacket();
    } else if (cmd.startsWith("OLED_ROT2=")) {
        int deg = cmd.substring(10).toInt();
        g_oled2Rot = static_cast<uint8_t>(((deg >= 90 ? (deg / 90) : deg) % 4 + 4) % 4);
        savePlantConfigToNvs();
        emitOledPacket();
    } else if (cmd.startsWith("OLED_ROT_ALL=")) {
        int deg = cmd.substring(13).toInt();
        uint8_t r = static_cast<uint8_t>(((deg >= 90 ? (deg / 90) : deg) % 4 + 4) % 4);
        g_oled1Rot = r;
        g_oled2Rot = r;
        savePlantConfigToNvs();
        emitOledPacket();
    }
    // 9. Nhóm lệnh Cấu hình Wi-Fi & Đồng bộ Backlog (Phân hệ 1 & 6)
    else if (cmd.startsWith("WIFI_CFG=")) {
        String payload = cmd.substring(9);
        int sep = payload.indexOf('|');
        if (sep > 0) {
            g_wifiSsid = payload.substring(0, sep);
            g_wifiPass = payload.substring(sep + 1);
            g_wifiConfigured = true;
            savePlantConfigToNvs();
            initWifiStationWithTimeout();
        }
    } else if (cmd.equalsIgnoreCase("SYNC_BACKLOG")) {
        syncOfflineBacklogToCloud();
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
        } else if (g_cmdBuffer.length() < 160) {
            g_cmdBuffer += c;
        }
    }
}

// ============================================================================
// 17. CHẾ ĐỘ NGỦ TIẾT KIỆM ĐIỆN & ĐÁNH THỨC QUA MPU6050 INT = GPIO 10
// ============================================================================

static void enterSleepUntilMotion() {
    // Tuyệt đối đảm bảo tắt bơm trước khi ngủ
    setPumpHardwareState(false);

    const char *modeStr = (g_sleepMode == SleepMode::DEEP) ? "DEEP" : "LIGHT";
    Serial.printf(
        "{\"status\":\"SLEEPING\",\"mode\":\"%s\",\"idle_s\":%lu,\"int_pin\":%d,"
        "\"msg\":\"Khong chuyen dong -> ESP32-S3 vao che do ngu (%s), tat 2 mat OLED\"}\n",
        modeStr,
        static_cast<unsigned long>(g_idleSleepMs / 1000),
        MPU_INT_PIN,
        modeStr
    );
    setOledPower(false);
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
            wakeCause = "GPIO10_INT";
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
    setOledPower(true);

    g_hasPrevSample = false;
    g_isStill = false;
    g_stillTicks = 0;
    g_biasTicks = 0;
    g_idleStartMs = millis();
    g_lastIntegrateUs = micros();
    g_lastFilterMs = millis();
    g_lastOutputMs = millis();

    Serial.printf(
        "{\"event\":\"WAKEUP\",\"cause\":\"%s\",\"msg\":\"Phat hien chuyen dong -> ESP32-S3 & OLED thuc day\"}\n",
        wakeCause
    );
    pollSerialCommands();
}

// ============================================================================
// 18. PHÂN HỆ 1: SETUP (KHỞI TẠO HỆ THỐNG, SELF-TEST & TIMERS)
// ============================================================================

void setup() {
    // 1. Khởi tạo các chân GPIO Cơ cấu chấp hành ở trạng thái an toàn ngay lập tức
    pinMode(PUMP_RELAY_PIN, OUTPUT);
    digitalWrite(PUMP_RELAY_PIN, LOW); // Bơm mặc định: OFF (LOW)
    pinMode(STATUS_LED_PIN, OUTPUT);
    digitalWrite(STATUS_LED_PIN, LOW);

    // 2. Khởi tạo ADC 12-bit (Độ ẩm đất) & Digital Input (Mực nước bình chứa XKC-Y25)
    pinMode(WATER_LEVEL_PIN, INPUT_PULLUP);
    pinMode(SOIL_ADC_PIN, INPUT);
    analogReadResolution(12);
    analogSetPinAttenuation(SOIL_ADC_PIN, ADC_11db);

    // 3. Khởi tạo Serial
    Serial.begin(115200);
    uint32_t waitStart = millis();
    while (!Serial && (millis() - waitStart < 1500)) {
        delay(10);
    }

    esp_sleep_wakeup_cause_t wakeCause = esp_sleep_get_wakeup_cause();
    if (wakeCause == ESP_SLEEP_WAKEUP_EXT0) {
        rtc_gpio_deinit(static_cast<gpio_num_t>(MPU_INT_PIN));
    }

    // 4. Nạp cấu hình từ Flash NVS
    loadSystemConfigFromNvs();

    // 5. Khởi tạo 2 Bus I2C độc lập theo đúng sơ đồ phần cứng:
    //    - Bus I2C 0 (SDA = GPIO 11, SCL = GPIO 12): MPU6050 (0x68, INT = GPIO 10) + OLED 1 (0x3C)
    //    - Bus I2C 1 (SDA = GPIO 1,  SCL = GPIO 2) : AHT20 (0x38) + BMP280 (0x76/0x77) + BH1750 (0x23/0x5C) + OLED 2 (0x3C)
    BUS0_I2C.begin(I2C_SDA_PIN, I2C_SCL_PIN);
    BUS0_I2C.setClock(400000);
    BUS0_I2C.setTimeOut(15);

    BUS1_I2C.begin(OLED2_SDA_PIN, OLED2_SCL_PIN, 400000);
    BUS1_I2C.setTimeOut(15);
    g_wire1Started = true;

    // 6. Khởi tạo Micro I2S INMP441 (I2S_NUM_0) & Khuếch đại Loa MAX98357A (I2S_NUM_1)
    initMicrophoneI2s();
    initSpeakerI2s();
    delay(25);
    pollMicrophoneNonBlocking(millis(), true);
    pollSoilAndWaterLevel(millis());

    Serial.printf(
        "{\"event\":\"BOOT\",\"fw\":\"esp32s3-smartplant-4.0\","
        "\"bus0_sda\":%d,\"bus0_scl\":%d,\"bus1_sda\":%d,\"bus1_scl\":%d,"
        "\"sda\":%d,\"scl\":%d,\"oled2_sda\":%d,\"oled2_scl\":%d,"
        "\"int_pin\":%d,\"mic_ws\":%d,\"mic_sck\":%d,\"mic_sd\":%d,"
        "\"soil_pin\":%d,\"tank_pin\":%d,\"pump_pin\":%d,\"nvs_cal\":%s}\n",
        I2C_SDA_PIN, I2C_SCL_PIN, OLED2_SDA_PIN, OLED2_SCL_PIN,
        I2C_SDA_PIN, I2C_SCL_PIN, OLED2_SDA_PIN, OLED2_SCL_PIN,
        MPU_INT_PIN, MIC_WS_PIN, MIC_SCK_PIN, MIC_SD_PIN,
        SOIL_ADC_PIN, WATER_LEVEL_PIN, PUMP_RELAY_PIN,
        g_cal.savedInNvs ? "true" : "false"
    );

    // 7. Khởi tạo & Self-test 6 Cảm biến đầu vào + 2 Màn hình OLED (Phân hệ 1)
    if (probeAndInitMpu()) {
        if (!g_cal.savedInNvs) {
            calibrateMpu(200, false, false);
        }
        if (wakeCause == ESP_SLEEP_WAKEUP_EXT0) {
            Serial.println("{\"event\":\"WAKEUP\",\"cause\":\"DEEP_EXT0\",\"msg\":\"Thuc day tu Deep Sleep nho chuyen dong (GPIO10)\"}");
        } else {
            Serial.printf("{\"event\":\"INIT_OK\",\"addr\":\"0x%02X\",\"whoami\":\"0x%02X\",\"nvs_cal\":%s}\n",
                          g_mpuAddr, g_whoAmI, g_cal.savedInNvs ? "true" : "false");
        }
    } else {
        printMpuStatusError("ERR_NO_DEVICE", "Khong tim thay MPU6050 tren Bus I2C 0 (kiem tra SDA=11, SCL=12, VCC, GND)");
    }

    probeEnvSensors();
    printI2cScanEvent(false);

    // Thực hiện đánh giá Self-test Phần cứng lần đầu & ghi nhận lỗi nếu có (Degraded Mode)
    runCentralDataDispatcher(millis());
    if (g_errCodes != ERR_NONE) {
        recordHardwareFaultToNvs(g_errCodes);
        Serial.printf(
            "{\"event\":\"SELF_TEST_DEGRADED\",\"err_codes\":%u,"
            "\"mpu\":%s,\"oled1\":%s,\"aht20\":%s,\"bmp280\":%s,\"bh1750\":%s,\"oled2\":%s,\"mic\":%s}\n",
            g_errCodes,
            g_mpuReady ? "true" : "false",
            g_oled1.ready ? "true" : "false",
            g_ahtReady ? "true" : "false",
            g_bmpReady ? "true" : "false",
            g_bhReady ? "true" : "false",
            g_oled2.ready ? "true" : "false",
            g_micDriverInstalled ? "true" : "false"
        );
    } else {
        Serial.println("{\"event\":\"SELF_TEST_OK\",\"err_codes\":0,\"msg\":\"Tat ca cam bien va ngoai vi hoat dong tot\"}");
    }

    // 8. Khởi tạo kết nối Wi-Fi Station (Nếu đã cấu hình SSID trong NVS)
    initWifiStationWithTimeout();

    // Phát âm hiệu khởi động qua Loa MAX98357A & xuất gói trạng thái ban đầu
    playSoundEffect("BOOT");
    emitOledPacket();

    uint32_t now = millis();
    g_lastFilterMs         = now;
    g_lastOutputMs         = now;
    g_lastMpuRetryMs       = now;
    g_lastFpsMs            = now;
    g_lastEnvOutputMs      = now;
    g_lastEnvProbeMs       = now;
    g_lastMicPollMs        = now;
    g_lastMicOutputMs      = now;
    g_lastOledRenderMs     = now;
    g_lastOledFlushMs      = now;
    g_lastOledOutputMs     = now;
    g_lastAutoWaterCheckMs = now;
    g_lastDispatcherMs     = now;
    g_idleStartMs          = now;
    g_lastIntegrateUs      = micros();
}

// ============================================================================
// 19. VÒNG LẶP CHÍNH ĐIỀU PHỐI ĐA PHÂN HỆ (MAIN LOOP: PHÂN HỆ 2 -> 6)
// ============================================================================

void loop() {
    // 0. Đọc và thực thi tức thời các lệnh điều khiển từ Serial / PC / Mobile App
    pollSerialCommands();

    uint32_t nowMs = millis();

    // 1. PHÂN HỆ 5: Thu mẫu Micro I2S INMP441 & Phát âm thanh Loa MAX98357A (Non-blocking)
    pollMicrophoneNonBlocking(nowMs);
    pollSpeakerAudioNonBlocking(nowMs);
    if (nowMs - g_lastMicOutputMs >= MIC_OUTPUT_MS) {
        g_lastMicOutputMs = nowMs;
        emitMicPacket();
    }

    // 2. PHÂN HỆ 2 & 3: Đọc Độ ẩm đất (ADC Moving Average), Mực nước (XKC-Y25) & Máy trạng thái Tưới tự động
    pollSoilAndWaterLevel(nowMs);
    updateIntelligentWateringEngine(nowMs);

    // Khi đang ở tab MIC chuyên biệt trên mpu6050_ui.py và không có chu kỳ bơm, hạn chế probe lại I2C lỗi để sóng âm mượt
    if (g_viewMode == ActiveViewMode::MIC && g_pumpPhase == WateringPhase::IDLE) {
        return;
    }

    // 3. PHÂN HỆ 4: Cập nhật Hoạt ảnh Biểu cảm Hai Mắt OLED & Đẩy dữ liệu ra Bus I2C 0 + Bus I2C 1
    updateOledAnimation(nowMs);
    if (nowMs - g_lastOledFlushMs >= 12) {
        g_lastOledFlushMs = nowMs;
        if (g_oled1.ready) flushOledPagesIncremental(g_oled1, 2);
        if (g_oled2.ready) flushOledPagesIncremental(g_oled2, 2);
    }
    if (nowMs - g_lastOledOutputMs >= OLED_OUTPUT_MS) {
        g_lastOledOutputMs = nowMs;
        emitOledPacket();
    }

    // 4. PHÂN HỆ 2: Đọc Cảm biến Môi trường trên Bus I2C 1 (AHT20 + BMP280 + BH1750)
    if (nowMs - g_lastEnvProbeMs >= 2500) {
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

    // 5. PHÂN HỆ 2 & 6: Bộ Điều phối Dữ liệu Trung tâm (Data Dispatcher) & Quản lý Wi-Fi / Offline Backlog
    if (nowMs - g_lastDispatcherMs >= TELEMETRY_SUMMARY_MS) {
        g_lastDispatcherMs = nowMs;
        runCentralDataDispatcher(nowMs);
        pollWifiAndBacklogNonBlocking(nowMs);
    }

    // Hỗ trợ xuất dòng text tương thích ngược nếu người dùng chọn chế độ '1' / '2' / '3' trên Serial
    if (g_legacyGroupMode != 0 && (nowMs - g_lastLegacyPrintMs >= 500)) {
        g_lastLegacyPrintMs = nowMs;
        if (g_legacyGroupMode == 1) {
            Serial.printf("[NHÓM 1] Ánh sáng: %.1f Lux | AHT20: %.1f°C, %.1f%%RH | BMP280: %.1f hPa | Đất: %.1f%% (%dmV)\n",
                          g_bhLux, g_sensors.airTempC, g_ahtHumPct, g_bmpPressHpa, g_soilPct, g_soilMv);
        } else if (g_legacyGroupMode == 2) {
            Serial.printf("[NHÓM 2 MPU-6050] Roll: %+.1f° Pitch: %+.1f° | X=%+.2f Y=%+.2f Z=%+.2f\n",
                          g_roll, g_pitch, g_filtAx, g_filtAy, g_filtAz);
        } else if (g_legacyGroupMode == 3) {
            int rmsRaw = static_cast<int>(g_micRmsPct * 120.0f);
            int peakRaw = static_cast<int>(g_micPeakPct * 160.0f);
            Serial.printf("[NHÓM 3 INMP441] RMS: %d | Peak: %d | %.1f dBFS\n", rmsRaw, peakRaw, g_micDbfs);
        }
    }

    // 6. PHÂN HỆ 2: Đọc & Lọc bù MPU6050 trên Bus I2C 0 (100 Hz)
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
                printMpuStatusError("ERR_NO_DEVICE", "Khong tim thay MPU6050 tren Bus I2C 0 (kiem tra day dau SDA=11, SCL=12)");
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

    // Tự động ngủ khi đứng yên (chỉ kích hoạt khi SLEEP=ON và không đang trong chu kỳ bơm nước)
    if (g_viewMode == ActiveViewMode::MPU && g_autoSleepEnabled && g_isStill &&
        g_pumpPhase == WateringPhase::IDLE && (nowMs - g_idleStartMs >= g_idleSleepMs)) {
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
        "\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,\"amag\":%.3f,\"dyn_g\":%.3f,"
        "\"gx\":%.2f,\"gy\":%.2f,\"gz\":%.2f,"
        "\"temp\":%.1f,\"roll\":%.2f,\"pitch\":%.2f,\"yaw\":%.2f,\"tilt_deg\":%.2f,\"hz\":%lu}\n",
        statusStr,
        g_mpuAddr,
        g_whoAmI,
        g_cal.calibrated ? "true" : "false",
        g_isStill ? "true" : "false",
        static_cast<unsigned long>(idleSec),
        g_filtAx, g_filtAy, g_filtAz, g_amag, g_dynG,
        g_outGx, g_outGy, g_outGz,
        g_lastTempC, g_roll, g_pitch, g_yaw, g_tiltDeg,
        static_cast<unsigned long>(g_measuredHz)
    );
}
