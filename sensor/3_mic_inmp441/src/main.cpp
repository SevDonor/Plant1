/**
 * ============================================================================
 * NHÓM 3: TEST CẢM BIẾN MICRO SỐ I2S INMP441 & THU ÂM TRUYỀN VỀ PC (STT)
 *
 * Sơ đồ chân (Khớp với file Fritzing group3_mic_inmp441.fzz & tài liệu hãng):
 *   - VDD : ESP32 3V3 (Bắt buộc 3.3V + tụ gốm lọc nhiễu nguồn 100nF sát chân VDD-GND)
 *   - GND : ESP32 GND
 *   - L/R : Nối xuống GND (Chọn kênh Trái - Left Channel)
 *   - WS  : ESP32 GPIO15 (D15) - Word Select (LRCLK)
 *   - SCK : ESP32 GPIO14 (D14) - Serial Clock (BCLK)
 *   - SD  : ESP32 GPIO32 (D32) - Serial Data (DOUT)
 *
 * Hai chế độ hoạt động tự động trên cùng 1 Firmware (Baudrate = 921600):
 *   1. Chế độ Kiểm tra trực tiếp (VU Meter / Serial Plotter trên VS Code):
 *      - Mặc định hiển thị thanh đo âm lượng VU Meter, RMS, Peak và dBFS.
 *      - Gõ 'p' để bật/tắt chế độ vẽ sóng âm trên Serial Plotter.
 *      - Gõ '+' hoặc '-' để tăng/giảm độ khuếch đại số (Digital Gain: 1x .. 10x).
 *   2. Chế độ Thu âm truyền luồng PCM về máy tính (PC Recorder & Speech-to-Text):
 *      - Khi script Python (record_and_stt.py) gửi lệnh "REC:5\n" (thu 5 giây),
 *        ESP32 tự động chuyển sang truyền luồng âm thanh số PCM 16kHz 16-bit Mono
 *        về máy tính để lưu thành file .wav và nhận diện giọng nói (Speech-to-Text).
 * ============================================================================
 */

#include <Arduino.h>
#include <driver/i2s.h>
#include <math.h>

// ======================= CẤU HÌNH CHÂN I2S INMP441 ===================
static constexpr i2s_port_t I2S_PORT = I2S_NUM_0;
static constexpr int PIN_I2S_SCK = 14; // Serial Clock (BCLK) -> GPIO14
static constexpr int PIN_I2S_WS  = 15; // Word Select (LRCLK) -> GPIO15
static constexpr int PIN_I2S_SD  = 32; // Serial Data (DOUT)  -> GPIO32

// ======================= THÔNG SỐ ÂM THANH CHUẨN STT =================
static constexpr uint32_t SAMPLE_RATE     = 16000; // 16kHz chuẩn cho Speech-to-Text
static constexpr size_t   DMA_BUF_LEN     = 512;   // Số mẫu mỗi buffer DMA
static constexpr size_t   DMA_BUF_COUNT   = 8;

// ======================= BIẾN TRẠNG THÁI =============================
int   digitalGain = 4;     // Hệ số khuếch đại biên độ số (1x .. 10x)
bool  plotterMode = false; // false = VU Meter text, true = Serial Plotter
float dcOffset    = 0.0f;  // Bộ lọc khử thành phần một chiều (DC Blocker)

static int32_t i2sRawBuf[DMA_BUF_LEN * 2];
static int16_t pcm16Buf[DMA_BUF_LEN];

// ======================= KHỞI TẠO I2S MASTER RX ======================
bool initI2SMic() {
    i2s_config_t i2s_config = {};
    i2s_config.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
    i2s_config.sample_rate = SAMPLE_RATE;
    i2s_config.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT; // INMP441 xuất khung 24-bit trong slot 32-bit
    // Dùng Stereo (RIGHT_LEFT) để tránh lỗi đảo kênh Left/Right của driver I2S ESP32 khi chạy Mono
    i2s_config.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
    i2s_config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    i2s_config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    i2s_config.dma_buf_count = DMA_BUF_COUNT;
    i2s_config.dma_buf_len = DMA_BUF_LEN;
    i2s_config.use_apll = false;
    i2s_config.tx_desc_auto_clear = false;
    i2s_config.fixed_mclk = 0;

    i2s_pin_config_t pin_config = {};
    pin_config.mck_io_num   = I2S_PIN_NO_CHANGE;
    pin_config.bck_io_num   = PIN_I2S_SCK;
    pin_config.ws_io_num    = PIN_I2S_WS;
    pin_config.data_out_num = I2S_PIN_NO_CHANGE;
    pin_config.data_in_num  = PIN_I2S_SD;

    if (i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL) != ESP_OK) {
        return false;
    }
    if (i2s_set_pin(I2S_PORT, &pin_config) != ESP_OK) {
        return false;
    }
    i2s_zero_dma_buffer(I2S_PORT);
    return true;
}

// Đọc 1 khối mẫu từ INMP441 và chuyển sang PCM 16-bit đã lọc DC
size_t readMicPCM16(int16_t *outPcm, size_t maxSamples) {
    if (maxSamples > DMA_BUF_LEN) maxSamples = DMA_BUF_LEN;
    size_t bytesRead = 0;
    size_t toReadBytes = maxSamples * 2 * sizeof(int32_t);
    esp_err_t err = i2s_read(I2S_PORT, i2sRawBuf, toReadBytes, &bytesRead, pdMS_TO_TICKS(200));
    if (err != ESP_OK || bytesRead == 0) return 0;

    size_t samplesRead = bytesRead / (2 * sizeof(int32_t));
    for (size_t i = 0; i < samplesRead; i++) {
        // Lấy kênh Left (2*i), tự fallback sang Right (2*i+1) nếu L/R nối 3V3
        int32_t rawSample = (i2sRawBuf[2 * i] != 0) ? i2sRawBuf[2 * i] : i2sRawBuf[2 * i + 1];
        // INMP441 dữ liệu 24-bit nằm ở 24 bit cao của từ 32-bit -> dịch phải 14 bit để về dải 16-bit
        float sample = (float)(rawSample >> 14);

        // Khử trôi DC (High-pass IIR nhẹ)
        dcOffset = 0.995f * dcOffset + 0.005f * sample;
        float clean = (sample - dcOffset) * (float)digitalGain;

        // Giới hạn chống tràn (Clipping)
        if (clean > 32767.0f)  clean = 32767.0f;
        if (clean < -32768.0f) clean = -32768.0f;
        outPcm[i] = (int16_t)clean;
    }
    return samplesRead;
}

// ======================= CHẾ ĐỘ THU ÂM TRUYỀN VỀ PC ==================
void streamAudioRecordingToPC(int durationSec) {
    if (durationSec < 1)  durationSec = 1;
    if (durationSec > 30) durationSec = 30;

    uint32_t totalSamples = (uint32_t)durationSec * SAMPLE_RATE;

    // Xóa các mẫu cũ trong DMA trước khi thu
    for (int i = 0; i < 4; i++) {
        readMicPCM16(pcm16Buf, DMA_BUF_LEN);
    }

    // Gửi Header đánh dấu bắt đầu luồng PCM cho script Python
    Serial.printf("\n<<<PCM_START:%lu:16:1:%lu>>>\n",
                  (unsigned long)SAMPLE_RATE,
                  (unsigned long)totalSamples);
    Serial.flush();

    uint32_t sentSamples = 0;
    while (sentSamples < totalSamples) {
        size_t need = totalSamples - sentSamples;
        if (need > DMA_BUF_LEN) need = DMA_BUF_LEN;

        size_t got = readMicPCM16(pcm16Buf, need);
        if (got > 0) {
            Serial.write((const uint8_t *)pcm16Buf, got * sizeof(int16_t));
            sentSamples += got;
        }
    }

    Serial.flush();
    Serial.println(F("\n<<<PCM_END>>>"));
    Serial.flush();
}

// ======================= CHẨN ĐOÁN RAW I2S (LEFT + RIGHT) ==============
void runRawI2SDiagnostic() {
    Serial.println(F("\n[DIAG] Đang chuyển tạm sang Stereo (LEFT + RIGHT) để đọc dữ liệu thô..."));
    i2s_driver_uninstall(I2S_PORT);

    i2s_config_t diag_cfg = {};
    diag_cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
    diag_cfg.sample_rate = SAMPLE_RATE;
    diag_cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
    diag_cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
    diag_cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    diag_cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    diag_cfg.dma_buf_count = 4;
    diag_cfg.dma_buf_len = 256;
    diag_cfg.use_apll = false;
    diag_cfg.tx_desc_auto_clear = false;
    diag_cfg.fixed_mclk = 0;

    i2s_pin_config_t pin_config = {};
    pin_config.mck_io_num   = I2S_PIN_NO_CHANGE;
    pin_config.bck_io_num   = PIN_I2S_SCK;
    pin_config.ws_io_num    = PIN_I2S_WS;
    pin_config.data_out_num = I2S_PIN_NO_CHANGE;
    pin_config.data_in_num  = PIN_I2S_SD;

    if (i2s_driver_install(I2S_PORT, &diag_cfg, 0, NULL) != ESP_OK ||
        i2s_set_pin(I2S_PORT, &pin_config) != ESP_OK) {
        Serial.println(F("[DIAG ERR] Không khởi tạo được chế độ Stereo!"));
        initI2SMic();
        return;
    }

    static int32_t stereoBuf[512]; // 256 cặp (L, R)
    size_t bytesRead = 0;
    // Bỏ qua 2 buffer đầu cho ổn định clock
    for (int k = 0; k < 3; k++) {
        i2s_read(I2S_PORT, stereoBuf, sizeof(stereoBuf), &bytesRead, pdMS_TO_TICKS(200));
    }

    size_t frames = bytesRead / (2 * sizeof(int32_t));
    int32_t lMin = INT32_MAX, lMax = INT32_MIN, rMin = INT32_MAX, rMax = INT32_MIN;
    size_t lNonZero = 0, rNonZero = 0;

    for (size_t i = 0; i < frames; i++) {
        int32_t lv = stereoBuf[2 * i] >> 8;
        int32_t rv = stereoBuf[2 * i + 1] >> 8;
        if (stereoBuf[2 * i] != 0) lNonZero++;
        if (stereoBuf[2 * i + 1] != 0) rNonZero++;
        if (lv < lMin) lMin = lv;
        if (lv > lMax) lMax = lv;
        if (rv < rMin) rMin = rv;
        if (rv > rMax) rMax = rv;
    }

    Serial.printf("[DIAG] Đọc %u cặp mẫu (32-bit raw -> 24-bit signed):\n", (unsigned)frames);
    for (size_t i = 0; i < frames && i < 8; i++) {
        uint32_t lHex = (uint32_t)stereoBuf[2 * i];
        uint32_t rHex = (uint32_t)stereoBuf[2 * i + 1];
        Serial.printf("  #%u: L=0x%08lX (%ld) | R=0x%08lX (%ld)\n",
                      (unsigned)i,
                      (unsigned long)lHex, (long)(stereoBuf[2 * i] >> 8),
                      (unsigned long)rHex, (long)(stereoBuf[2 * i + 1] >> 8));
    }
    Serial.printf("[DIAG] Thống kê L: khác 0 = %u/%u | min=%ld max=%ld\n",
                  (unsigned)lNonZero, (unsigned)frames, (long)lMin, (long)lMax);
    Serial.printf("[DIAG] Thống kê R: khác 0 = %u/%u | min=%ld max=%ld\n",
                  (unsigned)rNonZero, (unsigned)frames, (long)rMin, (long)rMax);

    i2s_driver_uninstall(I2S_PORT);
    initI2SMic();
    dcOffset = 0.0f;
    for (int i = 0; i < 10; i++) {
        readMicPCM16(pcm16Buf, DMA_BUF_LEN);
    }
    Serial.println(F("[DIAG] Đã quay lại chế độ test bình thường.\n"));
}

// ======================= XỬ LÝ LỆNH SERIAL ===========================
void checkSerialCommand() {
    if (!Serial.available()) return;

    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) return;

    if (line.startsWith("REC")) {
        int sec = 5;
        int colonIdx = line.indexOf(':');
        if (colonIdx > 0) {
            sec = line.substring(colonIdx + 1).toInt();
        }
        streamAudioRecordingToPC(sec);
    } else if (line.equalsIgnoreCase("d") || line.equalsIgnoreCase("raw")) {
        runRawI2SDiagnostic();
    } else if (line.equalsIgnoreCase("p")) {
        plotterMode = !plotterMode;
        Serial.printf("\n[MODE] Serial Plotter: %s\n\n", plotterMode ? "BẬT" : "TẮT");
    } else if (line == "+") {
        if (digitalGain < 12) digitalGain++;
        Serial.printf("\n[GAIN] Độ khuếch đại số (Digital Gain) = %dx\n\n", digitalGain);
    } else if (line == "-") {
        if (digitalGain > 1) digitalGain--;
        Serial.printf("\n[GAIN] Độ khuếch đại số (Digital Gain) = %dx\n\n", digitalGain);
    }
}

void setup() {
    // Sử dụng baudrate 921600 để truyền mượt luồng âm thanh 16kHz 16-bit (32KB/s)
    Serial.begin(921600);
    delay(800);

    Serial.println(F("\n================================================================"));
    Serial.println(F("   TEST NHÓM 3: CẢM BIẾN MICRO SỐ I2S INMP441 (16kHz / 16-bit)"));
    Serial.println(F("================================================================"));
    Serial.println(F("Sơ đồ chân: VDD=3V3, GND=GND, L/R=GND, WS=GPIO15, SCK=GPIO14, SD=GPIO32"));

    if (initI2SMic()) {
        Serial.println(F("[OK] Đã khởi tạo giao tiếp I2S Master RX cho INMP441 thành công!"));
        runRawI2SDiagnostic();
    } else {
        Serial.println(F("[ERR] Khởi tạo I2S thất bại! Kiểm tra cấu hình chân."));
    }

    // Làm nóng bộ lọc DC Offset
    for (int i = 0; i < 15; i++) {
        readMicPCM16(pcm16Buf, DMA_BUF_LEN);
    }

    Serial.println(F("Hướng dẫn sử dụng:"));
    Serial.println(F("  - Nói hoặc vỗ tay gần micro INMP441 để quan sát thanh VU Meter."));
    Serial.println(F("  - Gõ 'd' + Enter : Đọc lại dữ liệu thô I2S 2 kênh Left/Right (hex)."));
    Serial.println(F("  - Gõ 'p' + Enter : Bật/tắt chế độ vẽ sóng âm trên Serial Plotter."));
    Serial.println(F("  - Gõ '+' hoặc '-' : Tăng/giảm Digital Gain (hiện tại = 4x)."));
    Serial.println(F("  - Chạy script Python `record_and_stt.py` trên PC để thu âm .wav & chuyển Speech-to-Text!"));
    Serial.println(F("----------------------------------------------------------------\n"));
}

void loop() {
    checkSerialCommand();

    size_t n = readMicPCM16(pcm16Buf, DMA_BUF_LEN);
    if (n == 0) return;

    if (plotterMode) {
        // In mẫu đại diện để vẽ đồ thị sóng âm mượt trên Serial Plotter
        for (size_t i = 0; i < n; i += 16) {
            Serial.printf("Wave:%d,Upper:15000,Lower:-15000\n", pcm16Buf[i]);
        }
        return;
    }

    // Tính biên độ RMS, Peak và dBFS trên cửa sổ mẫu
    double sumSq = 0.0;
    int16_t peak = 0;
    for (size_t i = 0; i < n; i++) {
        int16_t s = pcm16Buf[i];
        int16_t absS = (s < 0) ? -s : s;
        if (absS > peak) peak = absS;
        sumSq += (double)s * (double)s;
    }

    float rms = sqrtf((float)(sumSq / n));
    float dbFs = (rms > 1.0f) ? (20.0f * log10f(rms / 32768.0f)) : -96.0f;

    // Tạo thanh VU Meter dài 25 ký tự
    int barLen = (int)((rms / 6000.0f) * 25.0f);
    if (barLen < 0)  barLen = 0;
    if (barLen > 25) barLen = 25;

    char vuBar[26];
    for (int i = 0; i < 25; i++) {
        vuBar[i] = (i < barLen) ? '#' : '-';
    }
    vuBar[25] = '\0';

    const char *soundState = "Im lặng";
    if (rms > 3500.0f || peak > 18000) {
        soundState = "ÂM THANH LỚN / VỖ TAY!";
    } else if (rms > 400.0f) {
        soundState = "Đang thu tiếng nói...";
    } else if (rms > 120.0f) {
        soundState = "Tiếng động nhẹ";
    }

    static uint32_t lastPrint = 0;
    if (millis() - lastPrint >= 120) {
        lastPrint = millis();
        Serial.printf("[INMP441 Gain=%dx] RMS:%6.0f | Peak:%6d | %+6.1f dBFS | [%s] %s\n",
                      digitalGain, rms, peak, dbFs, vuBar, soundState);
    }
}
