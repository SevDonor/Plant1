# Test Bơm Mini 5V & Cảm Biến Mực Nước XKC-Y25-V (ESP32-S3)

Bộ firmware ESP32-S3 + giao diện điều khiển trực quan (Python Tkinter) dùng để:
- Đọc tín hiệu từ cảm biến mực nước không tiếp xúc **XKC-Y25-V** theo thời gian thực.
- Bật / tắt **bơm mini 5V**, chạy hẹn giờ (pulse), chỉnh PWM, hoặc liên động tự động theo tín hiệu XKC ngay trên màn hình UI.
- Toàn bộ sơ đồ chân khớp 100% với file Fritzing [main.fzz](file:///d:/Plant/main.fzz).

---

## 1. Sơ đồ chân đọc trực tiếp từ `D:\Plant\main.fzz`

### Cảm biến mực nước XKC-Y25-V (`M1`) — qua Optocoupler PC817 (`U5`)
| Chân trên XKC-Y25-V (`M1`) | Kết nối trong `main.fzz` | Ghi chú |
|---|---|---|
| **pin1 (`VCC`)** | **5V** (`Mini560 A2.pin9`) | Nguồn 5V |
| **pin4 (`MODE`)** | **5V** (`Mini560 A2.pin9`) | Chọn chế độ ngõ ra |
| **pin3 (`GND`)** | **GND** (`Mini560 A2.pin6`) | Mass chung |
| **pin2 (`OUT`)** | `R3 (220Ω)` → `PC817 (U5)` Anode (`Cathode → GND`) | Cách ly quang |
| **`U5` Collector** | **ESP32-S3 Pin 7 (`GPIO 7`)** + `R4 (10kΩ)` kéo lên `3V3` | **Đảo mức:** `XKC OUT = 5V` ⇒ `GPIO 7 = LOW (0V)` |
| **`U5` Emitter** | **GND** (`ESP32-S3 Pin 44`) | — |

> **Lưu ý về đảo mức tín hiệu:** Vì `PC817 (U5)` mắc kiểu cực thu hở (open-collector) kéo xuống GND kèm trở treo `R4 = 10kΩ` lên `3V3`, firmware mặc định đặt **`XKC Active-LOW = True`** (`GPIO 7 = LOW` nghĩa là có nước). Nếu module XKC-Y25-V thực tế của bạn xuất ngược cực khi nối `MODE = 5V`, chỉ cần bỏ tích **"Đảo logic PC817"** ngay trên UI mà không cần nạp lại code.

### Bơm Mini 5V (`WATER PUMP1`) — qua Optocoupler PC817 (`U4`) + MOSFET (`Q1`)
| Chân | Kết nối trong `main.fzz` | Ghi chú |
|---|---|---|
| **ESP32-S3 Pin 20 (`GPIO 14`)** | `R1 (220Ω)` → `PC817 (U4)` Anode (`Cathode → GND`) | **Active-HIGH:** `GPIO 14 = HIGH` ⇒ Bơm chạy |
| **`U4` Collector / Emitter** | `5V` (`A2.pin8`) / Gate `Q1` + `R2 (10kΩ)` kéo xuống `GND` | Đóng cắt Gate MOSFET |
| **MOSFET `Q1` Drain / Source** | `WATER PUMP1 (-)` / `GND` | Có diode `D1 (1N4001)` dập xung ngược song song bơm |
| **`WATER PUMP1 (+)`** | **5V** (`Mini560 A2.pin9`) | Nguồn 5V |

---

## 2. Nạp Firmware & Chạy Giao Diện UI

1. **Cài thư viện Python (chỉ cần `pyserial`):**
   ```powershell
   pip install -r d:\Plant\testPumpXKC\requirements.txt
   ```
2. **Nạp firmware cho ESP32-S3** (cổng Native USB OTG — mặc định):
   ```powershell
   pio run -d d:\Plant\testPumpXKC -e esp32s3_usb_cdc -t upload
   ```
   *(Nếu cắm qua cổng UART CH340/CP2102 thì dùng `-e esp32s3_uart`.)*
3. **Mở giao diện UI:** Nhấp đúp [run_ui.bat](file:///d:/Plant/testPumpXKC/run_ui.bat) hoặc chạy:
   ```powershell
   python d:\Plant\testPumpXKC\pump_xkc_ui.py
   ```

---

## 3. Tính năng chính trên UI
- **Giám sát XKC-Y25-V trực quan:** Mô phỏng bình nước + đèn cảm biến, hiển thị đồng thời mức `GPIO 7 (HIGH/LOW)` và `XKC OUT` suy ra, bộ đếm số lần chuyển trạng thái, thời gian giữ trạng thái và biểu đồ sóng thời gian thực.
- **Điều khiển bơm linh hoạt:** Nút **BẬT / TẮT**, **DỪNG KHẨN CẤP**, **Bơm hẹn giờ (1s / 3s / 5s / 10s / tùy chỉnh)**, thanh trượt **PWM (0–100%)** và tự ngắt an toàn khi chạy quá thời gian quy định.
- **4 chế độ liên động XKC ↔ Bơm:**
  1. `MANUAL`: Bật/tắt bơm hoàn toàn thủ công từ UI.
  2. `PROTECT`: Thủ công + **tự động ngắt bơm** nếu XKC báo cạn nước (chống chạy khô).
  3. `AUTO_WATER`: XKC phát hiện **CÓ nước → tự BẬT bơm**, cạn → tự TẮT.
  4. `AUTO_DRY`: XKC báo **CẠN nước → tự BẬT bơm bù**, đầy → tự TẮT.
