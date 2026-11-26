# Firmware Phát Xung Test Loa MAX98357A & Dò Chân GPIO (ESP32-S3)

Bộ firmware + giao diện mới chuyên dụng để **phát xung test loa I2S MAX98357A** và **dò kiểm tra phần cứng bằng đồng hồ vạn năng** theo đúng sơ đồ chân trong [main.fzz](file:///d:/Plant/main.fzz).

---

## 1. Sơ đồ chân mặc định đọc từ `D:\Plant\main.fzz`

| Chân trên MAX98357A (`Part3`) | Chân trên ESP32-S3 (`A3`) / Nguồn | Ghi chú |
|---|---|---|
| **LRC (`LRCLK`)** | **GPIO 15** *(Pin 8)* | I2S Word Select (`WS`) — có thể đổi nóng trên UI |
| **BCLK** | **GPIO 16** *(Pin 9)* | I2S Bit Clock (`BCK`) — có thể đổi nóng trên UI |
| **DIN** | **GPIO 17** *(Pin 10)* | I2S Data Out (`DOUT`) |
| **SD (`SD_MODE`)** | **GPIO 18** *(Pin 11)* | Hỗ trợ 3 trạng thái: `HIGH` (3.3V), `FLOAT` (Thả nổi), `LOW` (Tắt) |
| **GAIN** | **GND** | Độ lợi **12 dB** |
| **VIN / GND** | **5V** / **GND** | Chung mass với ESP32-S3 |
| **VO+ / VO-** | **Loa 4Ω 3W (`SPKR1`)** | Ngõ ra cầu H Class-D |

---

## 2. Hai chế độ hoạt động

### Chế độ 1 — Phát Xung I2S (`MODE:I2S`)
- **8 dạng sóng / xung:** `PULSE` (chỉnh Duty `5–95%`), `SQUARE` (xung vuông), `SINE` (sin chuẩn), `TRIANGLE`, `SAWTOOTH`, `CLICK` (xung kim nhịp chậm tạch-tạch dễ nhìn màng loa rung), `SWEEP` (quét tần số liên tục), `NOISE`.
- **3 chế độ nhịp (`Gate`):** `CW` (phát liên tục để rảnh tay cầm đồng hồ đo), `BEEP` (bật/tắt theo `ON ms / OFF ms`), `ONCE` (phát 1 lần).
- **Chẩn đoán sâu MAX98357A:** đổi nóng `SD_MODE` (`HIGH / FLOAT / LOW`), `16-bit / 32-bit`, `I2S / MSB`, `I2S Port 0 / 1`, khe kênh `BOTH / LEFT / RIGHT`, đảo nhanh `LRC ↔ BCLK` hoặc đổi bất kỳ chân GPIO nào mà không cần nạp lại code.

### Chế độ 2 — Dò Chân GPIO bằng Đồng Hồ Vạn Năng (`MODE:PROBE`)
- Tạm tắt I2S và điều khiển trực tiếp 4 chân `LRC / BCLK / DIN / SD` theo các mức: **`0V (LOW)`**, **`3.3V (HIGH)`**, **`THẢ NỔI (FLOAT)`**, **`NHÁY 1Hz (BLINK)`**, hoặc **`QUÉT TỪNG CHÂN (WALKING 3.3V)`**.
- Kẹp que đen đồng hồ vào `GND` trên MAX98357A, dùng que đỏ chạm lần lượt vào 4 chân để xác định chính xác tuyệt đối dây có thông, đúng chân và không chạm chập.

---

## 3. Nạp Firmware & Chạy Giao Diện Mới

1. **Nạp firmware mới (cổng Native USB OTG — mặc định):**
   ```powershell
   pio run -d d:\Plant\testSpeaker -e esp32s3_usb_cdc -t upload
   ```
   *(Nếu cắm cổng UART CH340/CP2102 thì dùng `-e esp32s3_uart`.)*
2. **Mở giao diện mới:** Nhấp đúp **[run_pulse_ui.bat](file:///d:/Plant/testSpeaker/run_pulse_ui.bat)** hoặc chạy:
   ```powershell
   python d:\Plant\testSpeaker\speaker_pulse_ui.py
   ```
3. **Cách kiểm tra nhanh bằng đồng hồ vạn năng:**
   - **Bước A (Kiểm tra thông dây & đúng chân):** Chọn tab **`2. CHẾ ĐỘ DÒ CHÂN GPIO`** → bấm **`QUÉT TỪNG CHÂN (WALK 3.3V)`** → dùng đồng hồ thang `DC V` chạm từng chân `LRC / BCLK / DIN / SD` ngay trên board MAX98357A xem có nhảy lên `3.30V` đúng lúc màn hình báo sáng không.
   - **Bước B (Phát xung test loa):** Quay lại tab **`1. CHẾ ĐỘ PHÁT XUNG I2S`** → bấm **`Xung Vuông 1kHz`** hoặc **`Tạch-Tạch 4Hz`** → thử lần lượt `SD_MODE = HIGH` / `THA NOI (FLOAT)`, `Bits = 16 / 32`, và `Dao LRC<->BCLK` rồi đối chiếu điện áp đo được với **Bảng Điện Áp Kỳ Vọng** bên phải màn hình.
