# Bản Tóm Tắt Yêu Cầu Thiết Kế Giao Diện (UI/UX Design Brief)
**Dự án:** Smart Plant Companion — Chậu Cây Thông Minh & Thú Cưng Để Bàn Tích Hợp AI  
**Nền tảng ưu tiên:** Ứng dụng Máy tính (Desktop App, tối ưu màn hình `1280×720` – `1920×1080`)  
**Mục đích tài liệu:** Tóm gọn phần cứng, ý tưởng sản phẩm, dữ liệu hệ thống và các màn hình cần thiết để bàn giao nhanh cho người thiết kế UI/UX.

---

## 1. Ý tưởng sản phẩm (Đọc nhanh trong 30 giây)

Biến một chậu cây IoT thành **Thú cưng để bàn (Desktop Companion Pet)** có cảm xúc, phản xạ vật lý và khả năng trò chuyện tự nhiên:

- **Cảm xúc đồng bộ 1:1:** Thông số môi trường và tác động vật lý được chuyển hóa thành biểu cảm trên **2 màn hình OLED gắn trên chậu** (đóng vai **Mắt Trái** và **Mắt Phải**) và phản chiếu sống động lên **Gương mặt tương tác trên màn hình PC**.
- **Tương tác vật lý qua cảm biến rung/nghiêng (MPU-6050):** Gõ nhẹ để xoa đầu (mắt cười), nghiêng chậu (mặt nghiêng theo góc thật), lắc mạnh (**mặt hoảng sợ / chóng mặt**).
- **Cơ chế Ngủ & Đánh thức tự nhiên:** Khi ESP32 vào chế độ ngủ (đêm khuya / trời tối / không tương tác / bấm nút Ngủ), **2 mắt tắt đen hoàn toàn**. Khi người dùng **rung/chạm vào chậu**, MPU-6050 đánh thức ESP32 và **mặt chạy hiệu ứng mở mắt tỉnh dậy**.
- **Tưới cây tự động an toàn:** Bơm theo từng xung định lượng **3 giây** → tắt bơm **1–2 giây** chờ ngấm → đo lại độ ẩm đất; khóa an toàn khi cạn bình nước hoặc tưới quá **5 chu kỳ liên tiếp** không thấy đất ẩm.
- **Trò chuyện trực tiếp bằng AI (gói `google-antigravity`):** Chat chữ hoặc nói chuyện trực tiếp ngay trên giao diện PC; AI đọc dữ liệu cảm biến thật để trả lời có cá tính (không bịa chỉ số) và có thể thực thi lệnh (tưới cây, dừng bơm, cho cây ngủ).

---

## 2. Tóm gọn Thiết bị & Phần cứng thực tế

*(Danh sách rút gọn theo mạch thật [main_bb.pdf](./main_bb.pdf) và [danh_sach_thiet_bi_moi.xlsx](./danh_sach_thiet_bi_moi.xlsx))*

| Nhóm | Thiết bị / Module | Vai trò & Tác động tới Giao diện |
| :--- | :--- | :--- |
| **Vi điều khiển** | **ESP32-S3-WROOM-1-N16R8** | Bộ xử lý trung tâm, kết nối PC/Cloud qua Wi-Fi (MQTT) hoặc cáp USB Serial. |
| **Hiển thị trên chậu** | **2× OLED 2.42" SSD1309** (`0x3C`, `0x3D`) | Mỗi màn hình hiển thị **1 mắt độc lập** (Mắt Trái & Mắt Phải), có chớp mắt tự nhiên & tắt đen khi ngủ. |
| **Cảm biến môi trường** | **AHT20 + BMP280** (`0x38`, `0x76/0x77`) | Đo **Nhiệt độ (`°C`)**, **Độ ẩm không khí (`%RH`)**, **Áp suất khí quyển (`hPa`)**. |
| | **BH1750** (`0x23`) | Đo **Cường độ ánh sáng (`Lux`)** (nhận diện trời tối / đèn phòng / nắng gắt). |
| | **Cảm biến ẩm đất điện dung v1.0** | Đo **Độ ẩm đất (`%`)** qua ADC (có hiệu chuẩn 2 mốc Khô 0% – Ướt 100%). |
| **Chuyển động & Xúc giác** | **MPU-6050 (GY-521)** (`0x68` + `INT→GPIO10`) | Đo góc nghiêng (`Roll/Pitch`), gia tốc rung (`G-Force`), bắt sự kiện gõ/lắc và **đánh thức ESP32 khi đang ngủ**. |
| **Mực nước & Tưới** | **XKC-Y25-NPN** (Digital) + **Bơm chìm 5V** | XKC-Y25 báo **Đủ nước / Cạn bình** (ngắt khóa bơm chống cháy); bơm tưới từng xung 3 giây. |
| **Âm thanh** | **Micro INMP441** + **MAX98357A & Loa 4Ω 3W** | Thu giọng nói (16kHz) và phát giọng trả lời AI / âm thanh phản hồi. |
| **Nguồn** | **Adapter DC 12V → Buck Mini560 5V** | Cấp nguồn cố định cho hệ thống. |

> **4 lưu ý phần cứng BẮT BUỘC cho người thiết kế UI (tránh vẽ sai thực tế):**
> 1. **Không có cảm biến chất lượng không khí (AQI):** Chỉ thiết kế **5 chỉ số môi trường** (`Độ ẩm đất %`, `Nhiệt độ °C`, `Độ ẩm không khí %RH`, `Ánh sáng Lux`, `Áp suất hPa`).
> 2. **Cảm biến mực nước `XKC-Y25` là dạng nhị phân (Còn nước / Cạn bình):** Không vẽ biểu đồ `%` mực nước giả theo thời gian thực. Có thể hiển thị trạng thái nổi bật `ĐỦ NƯỚC / CẠN BÌNH` kèm thanh *ước lượng số lần tưới còn lại* (có nút `[Đã châm đầy]`).
> 3. **Mặt OLED gồm 2 khung mắt tách biệt:** Không vẽ nguyên khuôn mặt lặp lại trong từng màn hình nhỏ.
> 4. **Ngưỡng độ ẩm đất hiển thị theo `%` (0–100%):** Không bắt người dùng cuối nhập số `Raw ADC` ở màn chính (ẩn `ADC / mV / X,Y,Z` sang Trang Debug).

---

## 3. Dữ liệu giao tiếp giữa ESP32 và Giao diện PC

- **ESP32 gửi lên định kỳ (`5–15s`, hoặc `1–2s` ở chế độ LIVE):**
  - Môi trường: `soil_pct`, `temp_c`, `air_hum_pct`, `light_lux`, `press_hpa` *(+ `soil_adc`, `soil_mv` cho Debug)*.
  - Chuyển động: `roll`, `pitch`, `tilt_deg`, `dyn_g`, `ax/ay/az`, `calibrated`.
  - Thủy lợi: `tank_ok` (true/false), `pump_on`, `pump_phase` (`IDLE` / `PUMPING_3S` / `SOAKING_2S`), `mode` (`AUTO`/`MANUAL`), `threshold_pct`, `consecutive_cycles` (`0..5`), `daily_cycles`.
  - Pet & Hệ thống: `emotion`, `power_state` (`ACTIVE` / `SLEEPING`), `voice_state` (`IDLE` / `LISTENING` / `THINKING` / `SPEAKING`), `mic_rms`, `sys_state` (`RUNNING` / `PENDING` / `ERROR`), `err_codes`, `rssi`, `uptime`, `backlog_count` (số gói lưu đệm khi mất mạng), `timestamp`.
- **ESP32 đẩy sự kiện tức thời (Event Push):** `TAP`, `DOUBLE_TAP`, `SHAKE_PANIC`, `CRITICAL_TILT`, `SLEEP_ENTER`, `WAKE_BY_MOTION`, `TANK_EMPTY`, `WATERING_FAULT_ERROR`, `CMD_ACK` (xác nhận hoặc báo lý do từ chối lệnh).
- **PC gửi lệnh xuống ESP32 (Downlink):** Tưới 1 xung `3s`, **Dừng khẩn cấp (E-STOP)**, Reset khóa lỗi bơm, Cho Pet ngủ ngay, Đổi chế độ `AUTO/MANUAL`, Đổi ngưỡng `%`, Hiệu chuẩn Đất Khô/Ướt & MPU-6050, Quét I2C, Nạp Firmware `.bin`.

---

## 4. Kiến trúc Giao diện Desktop (4 Trang + Thanh Trạng Thái Thông Minh)

Áp dụng nguyên lý **Progressive Disclosure**: màn hình chính ưu tiên trực quan, cảm xúc và thao tác nhanh; toàn bộ thông số kỹ thuật thô (`JSON`, `Log`, `Raw ADC`, `IMU X/Y/Z`, `Mic RMS`) được cất gọn vào Trang 4.

### 4.1. Thanh Header toàn cục (Smart Top Bar — luôn cố định trên cùng)
Giữ cực kỳ tối giản theo phong cách thanh trạng thái hiện đại, mỗi thành phần đều bấm được để điều hướng nhanh:
- **Trái:** Logo/Tên `SMART PLANT COMPANION` + Tên trang hiện tại.
- **Phải (cụm trạng thái gọn):**
  1. **Nguồn dữ liệu:** Pill `LIVE · MQTT` / `LIVE · USB` / `SLEEPING` / `OFFLINE (Đệm: N gói)` + thời điểm cập nhật gần nhất.
  2. **Đèn Trạng thái Hệ thống:** `● RUNNING` (xanh) | `◐ WATERING / PENDING` (vàng) | `❗ ERROR` (đỏ — **click nhảy thẳng sang Trang 4 Debug**).
  3. **Icon 4 vạch sóng Wi-Fi** (tự động đổi 1–4 vạch — **click mở Trang 3 Cài đặt mạng**).
  4. **Nút nhanh `[🌙 Cho Ngủ]`**, **Chuông Cảnh báo** (kèm badge số lượng lỗi chưa xem), và **Đồng hồ `HH:MM`**.
- **Banner khẩn cấp trượt xuống dưới Header** *(chỉ xuất hiện khi có lỗi nặng: Ngã chậu `>45°`, Cạn bình nước, Khóa bơm `5/5`)* kèm nút xử lý trực tiếp.

### 4.2. Sơ đồ bố cục Trang 1 — Tổng quan & Chăm sóc (`Home Dashboard`)

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ [SMART PLANT]   LIVE · MQTT    ● RUNNING   ▂▄▆█ Wi-Fi  [🌙 Ngủ]  🔔  17:45  │
├────────┬──────────────────────────┬─────────────────────────┬────────────────┤
│ SIDEBAR│ CỘT TRÁI (PET & TƯỚI)    │ CỘT GIỮA (GIÁM SÁT)     │ CỘT PHẢI       │
│ (Thu   │ ┌──────────────────────┐ │ ┌───┬───┬───┬───┬───┐   │ (Thu gọn được) │
│ gọn    │ │   LIVE FACE STAGE    │ │ │Đất│°C │%RH│Lux│hPa│   │                │
│ được)  │ │ [Mắt Trái][Mắt Phải] │ │ │62%│27°│58%│450│1008   │ TRÒ CHUYỆN AI  │
│        │ │  (Xoay/Rung theo     │ │ └───┴───┴───┴───┴───┘   │ (Antigravity)  │
│ 1.Home │ │   MPU-6050 + Ngủ)    │ │                         │                │
│ 2.DB   │ │  "Bong bóng thoại"   │ │ ┌─────────────────────┐ │ [Khung chat    │
│ 3.Cấu  │ │  [Thanh G-Force/Góc] │ │ │ BIỂU ĐỒ LỊCH SỬ LỚN │ │  streaming     │
│   hình │ └──────────────────────┘ │ │ (Click 5 ô ở trên   │ │  2 chiều]      │
│ 4.Debug│ ┌──────────────────────┐ │ │  để đổi loại chart) │ │                │
│        │ │ TRẠM TƯỚI AN TOÀN P3 │ │ │                     │ │ [Context live  │
│        │ │ Bình XKC-Y25: Đủ nước│ │ │ [LIVE][1H][24H][7D] │ │  thu gọn được] │
│        │ │ Pha: IDLE | 0/5 chu kỳ│ │ │ [⏸ Pause] [Ngưỡng]  │ │ [Gợi ý nhanh]  │
│        │ │ [AUTO/MAN] [Ngưỡng %]│ │ │                     │ │ [Nhập | 🎤Nói] │
│        │ │ [Tưới 3s]  [E-STOP]  │ │ └─────────────────────┘ │                │
│        │ └──────────────────────┘ │                         │                │
└────────┴──────────────────────────┴─────────────────────────┴────────────────┘
```

#### Chi tiết từng khối trên Trang 1:
1. **Khối `Live Face Stage` (Cột trái trên — Trung tâm thị giác):**
   - Hiển thị **2 màn hình mắt OLED Trái & Phải tách biệt** bên trong khung đầu nhân vật có thể **nghiêng/rung động vật lý theo MPU-6050** (xem bảng đặc tả ở Mục 5).
   - Bong bóng thoại câu nói gần nhất của cây + thanh nhỏ ở đáy hiển thị góc nghiêng (`Roll/Pitch`) và cường độ rung (`G-Force`) dạng trực quan (không rườm rà số thô).
2. **Khối `Trạm Tưới An toàn P3` (Cột trái dưới):**
   - Trạng thái bình chứa `XKC-Y25`: huy hiệu **`BÌNH ĐỦ NƯỚC`** (xanh) hoặc **`CẠN BÌNH — ĐÃ KHÓA BƠM`** (đỏ) + tùy chọn ước lượng số lần tưới còn lại & nút `[Đã châm đầy]`.
   - Hiển thị rõ tiến trình xung tưới: `Đang bơm 3s (đếm ngược)` → `Chờ ngấm nước 2s` → `Đo lại độ ẩm`.
   - Thanh an toàn **`Chu kỳ liên tiếp: 0 / 5`**; khi đạt `5/5` (`WATERING_FAULT_ERROR`), hiển thị nút **`[Đã kiểm tra cảm biến — Mở khóa bơm]`**.
   - **2 nút tách biệt:** `[Tưới 1 xung 3s]` (tự khóa khi cạn bình/lỗi) và **`[DỪNG KHẨN CẤP]`** (màu đỏ nổi bật, phím tắt `Esc`).
   - Công tắc `AUTO / MANUAL` + thanh trượt chỉnh ngưỡng tự tưới (`10%–80%`).
3. **5 Thẻ KPI Click-to-Chart (Cột giữa trên):**
   - `1. Độ ẩm đất (%)` *(kèm thanh Bullet so với ngưỡng tự tưới)* | `2. Nhiệt độ (°C)` | `3. Độ ẩm không khí (%RH)` | `4. Ánh sáng (Lux)` | `5. Áp suất khí quyển (hPa)` *(kèm mũi tên xu hướng tụt áp báo mưa)*.
   - Nhấn vào thẻ nào → thẻ đó highlight viền sáng và **Biểu đồ Lịch sử** bên dưới lập tức chuyển sang chỉ số đó.
4. **Biểu đồ Lịch sử tương tác (Cột giữa dưới):**
   - Dạng Streaming Area / Line Chart mượt, có thanh chọn `LIVE / 1H / 24H / 7D`, nút `Pause/Resume`, đường ngưỡng đứt nét, vùng sinh trưởng lý tưởng, chấm đánh dấu **lúc bơm chạy** và **điểm vượt ngưỡng/lỗi**.
5. **Panel Trò chuyện trực tiếp AI — `google-antigravity` (Cột phải, thu gọn được):**
   - Khung chat 2 chiều hỗ trợ **gõ phím** hoặc **giữ nút Mic / phím `Space` để nói** (nhận âm từ Mic PC hoặc `INMP441`).
   - Thanh trạng thái nhỏ cho biết pipeline đang ở bước nào (`Đang nghe` → `AI suy nghĩ` → `Đang gọi lệnh [Tưới cây]` → `Đang nói`).
   - Thẻ thu gọn **"Ngữ cảnh cảm biến gửi cho AI"** (minh bạch chỉ số thật) + nút gợi ý nhanh (*"Hôm nay mày thấy sao?"*, *"Tưới giúp 1 lần"*, *"Đi ngủ đi"*).

### 4.3. Ba trang phụ trong Sidebar
- **Trang 2 — Kho Dữ liệu Lịch sử (`SQLite Explorer`):** Bảng bản ghi `telemetry_logs` toàn màn hình, bộ lọc theo ngày & sự kiện (Tưới nước, Lỗi, Rung lắc, Sync bù Offline), biểu đồ chồng đa chỉ số để đối chiếu tương quan, nút **Xuất CSV / JSON**.
- **Trang 3 — Cấu hình, Hiệu chuẩn & Nạp Firmware:**
  - *Kết nối:* Chọn `Wi-Fi MQTT` hoặc `USB Serial (COM)`, cấu hình SSID/Pass, Broker, chu kỳ gửi.
  - *Tưới & Ngủ/Thức:* Thời gian xung bơm (`3s`), chờ ngấm (`2s`), giới hạn chu kỳ (`5`), giờ đi ngủ, điều kiện trời tối, **thanh trượt độ nhạy rung đánh thức MPU-6050**.
  - *Wizard Hiệu chuẩn cảm biến:* Đất Khô `0%` / Đất Ướt `100%` (kèm số `mV` sống); Hiệu chuẩn `MPU-6050` 1000 mẫu (đếm ngược 3s + progress) & nút `Tare 0°`.
  - *Âm thanh, OLED & AI:* Gain Mic `INMP441`, âm lượng loa, độ sáng OLED, chọn tính cách/model `google-antigravity`.
  - *Nạp Firmware:* Chọn file `.bin`, nạp qua `Wi-Fi OTA` hoặc `USB UART`, thanh tiến trình `0–100%`.
- **Trang 4 — Chẩn đoán & Kỹ thuật (`Developer Debug`):**
  - Sức khỏe Bus I2C (`BH1750`, `AHT20`, `BMP280`, `MPU-6050`, `2× OLED`) + nút `[Quét I2C]`.
  - Thông số thô: `Raw ADC/mV`, `IMU Ax/Ay/Az/Gx/Gy/Gz`, `RSSI (dBm)`, `Uptime`, `Mã lỗi Hex`, `Offline Backlog`.
  - Tích hợp bộ đo Micro **INMP441 RMS Monitor** (đồ thị RMS/Peak/dBFS, tỉ lệ rớt mẫu, nút test thu âm 5s).
  - **Live JSON Inspector** (Uplink / Event / Downlink ACK) + **System & SQL Log**.

---

## 5. Đặc tả Gương Mặt Tương Tác (MPU-6050 + 2 Mắt OLED + Ngủ/Thức)

Designer cần thiết kế bộ trạng thái cho khung **2 Mắt OLED (Trái & Phải tách biệt)** theo bảng sau:

| Nhóm trạng thái | Điều kiện kích hoạt | Biểu cảm 2 Mắt & Hiệu ứng trên PC |
| :--- | :--- | :--- |
| **1. Ngủ (Tắt mắt)** | ESP32 vào `SLEEP` (đêm/tối/idle/lệnh Ngủ) | **2 khung OLED tắt đen hoàn toàn**, hiện biểu tượng `Zzz` mờ dịu bên ngoài khung. |
| **2. Thức dậy (Mở mắt)** | Rung nhẹ MPU-6050 đánh thức từ `SLEEP` | Chuỗi animation: Đen tắt → vệt sáng `— —` → mắt hé ngái ngủ `︶ ︶` → chớp 2 nhịp → mở mắt tỉnh dậy. |
| **3. Hoảng sợ (Rung mạnh)** | MPU-6050 phát hiện `SHAKE` (`dynG > 1.2g`) | **2 mắt trợn to `O_O` với đồng tử thu nhỏ run rẩy** (hoặc xoáy chóng mặt **`@_@`**), giọt mồ hôi văng ra, khung mặt rung lắc, viền chớp cam/đỏ. |
| **4. Ngã đổ (Nghiêng nặng)** | Góc nghiêng `> 45°` | Khung mặt **nghiêng đúng góc đo**, mắt **`T_T` / `>_<`** hoảng hốt, viền đỏ cảnh báo ngã chậu. |
| **5. Nghiêng nhẹ** | Góc nghiêng `10°–45°` | Khung mặt xoay nghiêng theo góc `Roll`, đồng tử trôi theo hướng nghiêng, biểu cảm tò mò. |
| **6. Gõ nhẹ / Xoa đầu** | `TAP` / `DOUBLE_TAP` | Khung mặt nảy nhẹ đàn hồi, 2 mắt cong tít cười **`^ ‿ ^`**, má ửng nhẹ. |
| **7. Đang tương tác AI** | Đang thu Mic / AI xử lý / Loa phát | Nghe: **`o_o`** (sóng âm nhẹ) → Suy nghĩ: **`-_-`** (chấm tải) → Nói: **`^o^`** (nhịp theo giọng nói). |
| **8. Trạng thái cây** | Bình thường / Khô đất / Đang tưới / Nóng-Tối / Cạn bình | Vui vẻ **`^ ^`** (chớp mắt 3–5s) · Khát **`>_<`** · Đang tưới **`♥ ‿ ♥`** · Nóng/mệt **`-.-`** + mồ hôi · Cạn bình **`°o°`** + cảnh báo. |

*(Thứ tự ưu tiên hiển thị trên gương mặt: `Ngủ/Thức` → `Tương tác AI` → `Hoảng sợ/Ngã đổ MPU-6050` → `Cạn bình/Đất khô` → `Nhiệt độ/Ánh sáng` → `Vui vẻ mặc định`.)*

---

## 6. Định hướng Phong cách Thị giác & Danh sách Bàn giao

- **Phong cách:** **Dark Botanical IoT Dashboard** (nền tối hiện đại giúp 2 màn hình OLED phát sáng hòa quyện tự nhiên, giảm mỏi mắt khi để mở lâu trên bàn làm việc; hỗ trợ thêm Light Mode nếu cần).
- **Bảng màu gợi ý (Design Tokens):**
  - Nền chính: `#0F172A` · Thẻ (Card): `#1B2336` · Viền tinh tế: `#334155` · Khung OLED: `#05070B` (mắt phát sáng `#F8FAFC` / `#38BDF8`).
  - Chữ chính: `#F8FAFC` · Chữ phụ: `#94A3B8` (tương phản tối thiểu `≥ 4.5:1`).
  - Màu ngữ nghĩa: Khỏe/Running `#22C55E` · Nước/Tưới `#38BDF8` · Chờ/Cảnh báo `#F59E0B` · Lỗi/Hoảng sợ/E-STOP `#EF4444`.
- **Typography & Icon:**
  - Chữ giao diện: **Plus Jakarta Sans** (hỗ trợ tiếng Việt chuẩn, bo tròn thân thiện).
  - Số đo cảm biến & Log: **JetBrains Mono** bật `tabular-nums` (số cập nhật không bị nhảy bề ngang).
  - Dùng bộ **SVG Icon đồng nhất** (Lucide hoặc Phosphor), không dùng emoji cho icon điều hướng/nút điều khiển.
- **Tiêu chuẩn UX Desktop:** Cảnh báo luôn có **màu + icon + chữ**, hỗ trợ nút `Reduce Motion` (tắt rung mạnh khung mặt nếu người dùng nhạy cảm chuyển động), hỗ trợ phím tắt `Space` (nói chuyện AI) và `Esc` (dừng bơm khẩn cấp).

### Checklist sản phẩm thiết kế cần bàn giao (Deliverables)
- [ ] **4 màn hình Desktop (`1440×900`):** Trang 1 Home (có và không mở cột Chat AI), Trang 2 Database, Trang 3 Cài đặt & Hiệu chuẩn, Trang 4 Debug.
- [ ] **Bộ 8 nhóm trạng thái Gương Mặt (`Live Face Stage`):** bao gồm hoạt ảnh/frame **Ngủ tắt mắt → Mở mắt** và **Rung mạnh hoảng sợ**.
- [ ] **5 kịch bản trạng thái hoàn chỉnh cho Trang 1:** *(1) Bình thường*, *(2) Đang tưới xung 3s*, *(3) Rung mạnh hoảng sợ / Ngã chậu*, *(4) Cạn bình hoặc Lỗi khóa bơm 5/5*, *(5) ESP32 đang ngủ (tắt mắt)*.
