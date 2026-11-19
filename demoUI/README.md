# Demo Bố Cục Giao Diện (UI/UX Wireframe v3) - Kiến trúc Đa Trang & Thanh Trạng Thái Thông Minh

Bản thiết kế v3 áp dụng nguyên lý **Progressive Disclosure (Ẩn chi tiết kỹ thuật sâu, ưu tiên sự tối giản trực quan)** theo đúng góp ý của bạn:

1. **Thanh Header tối giản kiểu Điện thoại (Smart Status Bar):**
   - Thay vì hiện hàng loạt chữ thô (`RSSI: -58 dBm`, `Uptime: 04:12:39`, `Error: 0x00`, `NTP: 2026-11-13...`), thanh Header góc phải giờ chỉ còn **4 biểu tượng tinh gọn**:
     - **Đèn báo trạng thái (Status LED):** Chuyển đổi giữa `● RUNNING` (Ổn định), `◐ PENDING` (Đang chờ/Đang tưới), và `❗ ERROR!` (Khi ngã chậu hoặc cạn bình nước). **Nhấn vào đèn sẽ nhảy thẳng sang Trang 4 (Debug)** để soi lỗi!
     - **Nút DB ngắn gọn (`🗄 DB: 120`):** Nhấn vào mở ngay Trang 2 (Kho dữ liệu SQLite).
     - **Icon 4 vạch sóng Wi-Fi (`▂▄▆█`):** Tự động tăng/giảm số vạch sóng theo cường độ mạng. **Nhấn vào cột sóng sẽ mở ngay Trang 3 (Cấu hình Wi-Fi)**.
     - **Đồng hồ rút gọn (`HH:MM`)**.

2. **Tách giao diện thành 4 Trang chức năng (Sidebar Navigation bên trái):**
   - **Trang 1 — `Tổng quan & Cây (Main Dashboard)`:** Giấu toàn bộ Log/JSON thô và các con số `ADC raw`, `Accel X/Y/Z` thô. Nhờ vậy khung **Biểu đồ Lịch sử** ở Trang chủ rộng thoáng gấp đôi!
   - **Trang 2 — `Kho Dữ liệu DB (SQLite Explorer)`:** Xem bảng dữ liệu `telemetry_logs` toàn màn hình và xuất file CSV.
   - **Trang 3 — `Cấu hình & Nạp FW (Wi-Fi / OTA / Calib)`:**
     - Cài đặt Wi-Fi (`SSID / Password`), địa chỉ `MQTT Broker`, cổng `COM Serial`.
     - **Trình nạp Firmware (.bin Flasher):** Chọn file `firmware.bin`, chọn nạp qua `Wi-Fi OTA` hoặc `Cáp USB UART`, có thanh tiến trình chạy `0% -> 100%`.
     - **Hiệu chuẩn cảm biến:** Nút lấy mốc Đất Khô (`2850 mV`), Đất Ướt (`1250 mV`), hiệu chuẩn điểm 0 `MPU-6050`, chỉnh âm lượng loa/độ sáng OLED.
   - **Trang 4 — `Debug & Soi Lỗi (Developer Console)`:** Nơi tập trung toàn bộ `Mã lỗi Hex`, `Wi-Fi RSSI (dBm)`, `Uptime`, `Raw ADC`, `IMU X/Y/Z`, `SQL & Event Log` và `Live JSON Packet Inspector` cho người lập trình kiểm tra lỗi.

---

## Cách chạy bản v3

- Nhấp đúp vào [`run_wireframe_app.bat`](run_wireframe_app.bat) hoặc chạy:
  ```bash
  python d:\Plant\demoUI\app_wireframe.py
  ```
- Hoặc mở [`wireframe_layout.html`](wireframe_layout.html) trên trình duyệt.
