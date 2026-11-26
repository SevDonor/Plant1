# Firmware Hệ Thống Giám Sát Môi Trường & Chăm Sóc Cây Trồng Thông Minh (ESP32-S3 + AI Chatbot)

Firmware chính thức cho **ESP32-S3-WROOM-1-N16R8** triển khai đầy đủ **6 Phân hệ** theo **Lưu đồ Thuật toán Kỹ thuật Tổng thể** (`Lưu đồ thuật toán bản chưa góp ý.drawio.pdf`) và sơ đồ phần cứng thực tế (`main.fzz` / `Sơ đồ đấu nối.pdf`), tương thích đồng thời với cả hai giao diện Desktop Python (`mpu6050_ui.py` và `smart_plant_app.py`).

---

## 1. Sơ đồ phân bổ 2 Bus I2C & Ngoại vi trên ESP32-S3

| Nhóm / Bus | Thiết bị / Module | Địa chỉ / Giao tiếp | VCC | GND | Chân GPIO trên ESP32-S3 |
|---|---|---|---|---|---|
| **Bus I2C 0 (`Wire`)** | **MPU6050 (GY-521)** | I2C (`0x68` / `0x69`) | **3V3** | **GND** | `SDA → GPIO 11`, `SCL → GPIO 12`, `INT → GPIO 10`, `AD0 → GND` |
| **Bus I2C 0 (`Wire`)** | **OLED 1 (Mắt Trái)** | I2C (`0x3C`) | **3V3** | **GND** | `SDA → GPIO 11`, `SCL → GPIO 12` *(chung Bus I2C 0 với MPU6050)* |
| **Bus I2C 1 (`Wire1`)** | **AHT20** | I2C (`0x38`) | **3V3** | **GND** | `SDA → GPIO 1`, `SCL → GPIO 2` |
| **Bus I2C 1 (`Wire1`)** | **BMP280** | I2C (`0x76` / `0x77`) | **3V3** | **GND** | `SDA → GPIO 1`, `SCL → GPIO 2` |
| **Bus I2C 1 (`Wire1`)** | **BH1750 (GY-302)** | I2C (`0x23` / `0x5C`) | **3V3** | **GND** | `SDA → GPIO 1`, `SCL → GPIO 2`, `ADDR → GND` |
| **Bus I2C 1 (`Wire1`)** | **OLED 2 (Mắt Phải)** | I2C (`0x3C`) | **3V3** | **GND** | `SDA → GPIO 1`, `SCL → GPIO 2` *(chung Bus I2C 1, không trùng địa chỉ với OLED 1)* |
| **Micro I2S (`I2S_NUM_0`)** | **INMP441** | I2S RX (16 kHz, 24-bit) | **3V3** | **GND** | `WS → GPIO 4`, `SCK → GPIO 5`, `SD → GPIO 6`, `L/R → GND` |
| **Loa I2S (`I2S_NUM_1`)** | **MAX98357A + Loa 3W** | I2S TX (16 kHz, 16-bit) | **5V** | **GND** | `BCLK → GPIO 15`, `LRCLK → GPIO 16`, `DIN → GPIO 17`, `SD_MODE → GPIO 18` |
| **Độ ẩm đất (ADC)** | **Capacitive Soil v1.0** | Analog 12-bit (`0–4095`) | **3V3** | **GND** | `AOUT → GPIO 3 (ADC1_CH2)` |
| **Mực nước bình chứa** | **XKC-Y25-NPN (PC817)** | Digital Input | **5V** | **GND** | `OUT → GPIO 7` |
| **Bơm nước mini 5V** | **PC817 + MOSFET IRLZ44N**| Digital Output | **5V** | **GND** | `GATE/IN → GPIO 14` *(Mặc định: `OFF / LOW`)* |

---

## 2. Kiến trúc 6 Phân hệ chức năng trong Firmware (`src/main.cpp`)

1. **Phân hệ 1 — Khởi tạo Hệ thống (`System Initialization`)**:
   - Nạp cấu hình hiệu chuẩn từ bộ nhớ Flash `NVS` (`Preferences`), khóa an toàn Bơm `OFF` ngay khi cấp nguồn.
   - Khởi tạo độc lập **Bus I2C 0 (`GPIO 11/12`)** và **Bus I2C 1 (`GPIO 1/2`)** ở tốc độ `400 kHz` kèm timeout chống treo bus.
   - Thực hiện **Self-test phần cứng**, ghi nhận mã lỗi `err_codes` vào Flash, hiển thị icon cảnh báo `[!]` trên OLED và vận hành ở chế độ **Degraded Mode** (tự động kết nối lại khi cắm lại dây).
   - Kết nối Wi-Fi Station (timeout 10s), đồng bộ thời gian thực `NTP` hoặc chuyển sang chế độ **Offline tự hành 100%** (`[W:X]`).
2. **Phân hệ 2 — Thu thập & Xử lý Dữ liệu Cảm biến (`Data Acquisition & Processing`)**:
   - Lọc trung bình động (**Moving Average Filter 16 mẫu**) cho ADC độ ẩm đất; lọc bù (**Complementary Filter 100 Hz**) và khử trôi Gyro tự động cho MPU6050.
   - Tính vector gia tốc tổng hợp $a = \sqrt{X^2 + Y^2 + Z^2}$, gia tốc động `dynG`, góc nghiêng `Roll / Pitch / Yaw / Tilt`, phát hiện tương tác vật lý (`TAP`, `DOUBLE_TAP`, `SHAKE_PANIC`, `CRITICAL_TILT`).
   - Kiểm tra dải đo vật lý (**Outlier / Sanity Check**), cập nhật `SystemSensorsData`, đánh giá 6 điều kiện sinh học trong `PlantEnvironmentState` và điều phối qua `Central Data Dispatcher`.
3. **Phân hệ 3 — Thuật toán Tưới Cây Tự Động An Toàn (`Intelligent Watering Engine`)**:
   - Kiểm tra ngưỡng độ ẩm đất (`ADC > 1100` / `soil_pct >= threshold_pct`).
   - Chu kỳ tưới chuẩn định lượng: **Bật bơm đúng 3 giây (`PUMPING_3S`)** ➔ **Tắt bơm hoàn toàn (`LOW`)** ➔ **Chờ 2 giây nước ngấm rễ (`SOAKING_2S`)** ➔ **Đọc lại ADC độ ẩm đất (`RECHECK`)**.
   - **Bảo vệ cạn bình (`XKC-Y25`)**: Không kích hoạt bơm khi hết nước, đổi mắt OLED sang `THIRSTY (><)`, phát loa nhắc châm nước.
   - **Giới hạn an toàn tối đa 5 chu kỳ liên tiếp (`MAX_CYCLES = 5`)**: Tự động khóa bơm khẩn cấp (`WATERING_FAULT_ERROR`), nhấp nháy `[!]` trên OLED, phát còi cảnh báo khi cảm biến tuột khỏi đất hoặc đứt dây.
4. **Phân hệ 4 — Điều khiển Biểu cảm Hai Mắt OLED (`Dual OLED Eye Emotion Engine`)**:
   - **OLED 1 (`Bus 0 - 0x3C`)** hiển thị **Mắt Trái**, **OLED 2 (`Bus 1 - 0x3C`)** hiển thị **Mắt Phải** đồng bộ 1:1 kèm chớp mắt tự nhiên (chu kỳ 3–5s) và đồng tử liếc theo góc nghiêng MPU6050.
   - Hệ thống **4 cấp ưu tiên biểu cảm**:
     1. *Tương tác Giọng nói & AI*: Đang nghe `o_o` (`LISTENING`), Đang suy nghĩ `-_-` (`THINKING`), Đang nói `^o^` (`SPEAKING`).
     2. *Tác động vật lý & Tưới*: Rung lắc `@_@` (`DIZZY`), Nghiêng chậu `O_O` (`SURPRISED`), Gõ xoa đầu `^ ^` (`HAPPY`), Đang tưới `♥_♥` (`LOVE`).
     3. *Độ ẩm đất & Mực nước*: Cạn bình / Lỗi tưới `><` (`THIRSTY`), Đất khô `T_T` (`SAD`).
     4. *Nhiệt độ & Ánh sáng*: Quá nóng/lạnh/tối `-.-` (`SLEEPY`), Môi trường lý tưởng `NORMAL / HAPPY`.
5. **Phân hệ 5 — Tương tác Giọng nói & AI Chatbot (`Voice & AI Chatbot Interaction`)**:
   - Thu âm liên tục từ Micro I2S `INMP441` (`16 kHz`), phát hiện tiếng nói `VAD`, truyền luồng PCM 16-bit (`REC:5`) về PC cho Speech-to-Text (`STT`).
   - Bộ phân tích ngôn ngữ tự nhiên (**Intent Recognition & NLP**) phân loại câu nói vào **3 nhóm chức năng** (*Nhóm 1: Hỏi thăm tình trạng cây*, *Nhóm 2: Điều khiển thiết bị an toàn*, *Nhóm 3: Trò chuyện & Giải trí*) với câu trả lời mang tính cách thú cưng dựa trên dữ liệu cảm biến thật 100%, phát âm thanh qua `MAX98357A`.
6. **Phân hệ 6 — Kết nối Cloud, Offline Backlog & Giao diện PC/Mobile**:
   - Đóng gói bản tin JSON chuẩn IoT, tự động lưu vào **Ring Buffer Offline Backlog** khi mất Wi-Fi và đồng bộ bù (`Sync Backlog`) khi có mạng trở lại.

---

## 3. Cách biên dịch & nạp Firmware bằng PlatformIO

```powershell
pio run -e esp32s3_usb_cdc
pio run -e esp32s3_usb_cdc -t upload
```

---

## 4. Danh mục Lệnh Điều khiển qua Serial (115200 / 921600 baud)

- **Hệ thống & Quét I2C**: `PING`, `SCAN` (hoặc `s`), `TELEMETRY`, `WIFI_CFG=<ssid>|<pass>`, `SYNC_BACKLOG`
- **Tưới cây & An toàn (Phân hệ 3)**: `WATER:3`, `ESTOP`, `WATER_RESET`, `MODE:AUTO`, `MODE:MANUAL`, `THRESH=30`, `TANK_REFILL`, `TANK=EMPTY`, `SOIL_SIM=20`, `SOIL_ADC=900`
- **Hiệu chuẩn cảm biến**: `CAL` (hoặc `c`), `CLEAR_CAL`, `RESET_YAW` (hoặc `z`), `d` (Mốc đất khô 0%), `w` (Mốc đất ướt 100%), `r` (Reset mốc đất)
- **Hai mắt OLED (Phân hệ 4)**: `OLED_MODE=EYES|DASHBOARD|TEXT|TEST`, `OLED_EMO=AUTO|NORMAL|HAPPY|SURPRISED|DIZZY|LISTENING|THINKING|SPEAKING|SAD|THIRSTY|LOVE|SLEEPY`, `OLED_BLINK`, `OLED_ROT1=90`, `OLED_ROT2=270`, `OLED_SWAP=0|1`
- **Giọng nói & AI Chatbot (Phân hệ 5)**: `CHAT:Cây có khỏe không?`, `CHAT:Tưới nước cho cây`, `CHAT:Ngừng tưới nước`, `REC:5`, `MIC_THR=8`, `MIC_GAIN=1.5`
