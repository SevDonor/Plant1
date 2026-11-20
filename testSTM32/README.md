# Test Đa Cảm Biến với ESP32-S3 (PlatformIO) + Python UI

Project PlatformIO kiểm tra **MPU6050 (GY-521)**, **AHT20 + BMP280** (môi trường: nhiệt độ, độ ẩm, áp suất, độ cao) và **BH1750** (ánh sáng Lux) trên **ESP32-S3** qua chung bus **I2C**, kèm giao diện Python (`mpu6050_ui.py`) có nút chuyển đổi qua lại giữa 2 chế độ ngay trong cùng cửa sổ.

---

## 1. Hướng dẫn đấu dây (ESP32-S3 ↔ MPU6050 + AHT20 + BMP280 + BH1750)

Tất cả 4 cảm biến dùng **chung 1 bus I2C** (`SDA = GPIO 8`, `SCL = GPIO 9`) với địa chỉ khác nhau nên không bị trùng:

| Module | Địa chỉ I2C | VCC | GND | SDA | SCL | Chân phụ |
|---|---|---|---|---|---|---|
| **MPU6050 (GY-521)** | `0x68` (`0x69`) | **3V3** | **GND** | **GPIO 8** | **GPIO 9** | `INT → GPIO 4` *(tùy chọn, dùng cho Deep Sleep)*, `AD0` để trống |
| **AHT20** *(hoặc combo AHT20+BMP280)* | `0x38` | **3V3** | **GND** | **GPIO 8** | **GPIO 9** | — |
| **BMP280** *(hoặc combo AHT20+BMP280)* | `0x77` hoặc `0x76` | **3V3** | **GND** | **GPIO 8** | **GPIO 9** | Nếu là module rời 6 chân: `CSB` để trống/3V3, `SDO` để trống/GND |
| **BH1750 (GY-30 / GY-302)** | `0x23` (`0x5C`) | **3V3** | **GND** | **GPIO 8** | **GPIO 9** | `ADDR` nối `GND` hoặc để trống → `0x23` |

```text
   ESP32-S3                       Nối song song các module I2C
  +---------+        +------------------+   +-------------------+   +----------------+
  |     3V3 |---+--->| VCC              |-->| VCC               |-->| VCC            |
  |     GND |---|-+->| GND  MPU6050     |-->| GND  AHT20+BMP280 |-->| GND   BH1750   |
  |  GPIO 8 |---|-|-+-> SDA   (0x68)    |-->| SDA   (0x38,0x77) |-->| SDA    (0x23)  |
  |  GPIO 9 |---|-|-|-+-> SCL           |-->| SCL               |-->| SCL            |
  |  GPIO 4 |<--|-|-|-|- INT (tuy chon) |   +-------------------+   +----------------+
  +---------+        +------------------+
```

> **Lưu ý nếu muốn đổi chân I2C:** Sửa `-D I2C_SDA_PIN=...` và `-D I2C_SCL_PIN=...` trong file `platformio.ini`. Tránh dùng GPIO `0, 3, 19, 20, 26–37, 45, 46`.

---

## 2. Cách nạp code PlatformIO cho ESP32-S3

1. Mở thư mục `d:\testSTM32` trong **VS Code** (đã cài extension **PlatformIO IDE**).
2. Chọn môi trường phù hợp ở thanh dưới cùng của VS Code (hoặc trong `platformio.ini`):
   - **`esp32s3_usb_cdc`** *(mặc định)*: Dùng khi cắm cáp vào cổng **USB (OTG)** trên ESP32-S3-DevKitC-1 hoặc mạch ESP32-S3 SuperMini/Zero chỉ có 1 cổng USB-C.
   - **`esp32s3_uart`**: Dùng khi cắm cáp vào cổng **UART (CH340/CP2102)** trên ESP32-S3-DevKitC-1 (nếu nạp bằng cổng UART mà không thấy dữ liệu Serial ở env mặc định, chuyển sang env này).
3. Bấm **Upload** (mũi tên `→` dưới cùng) hoặc chạy lệnh:
   ```powershell
   pio run -e esp32s3_usb_cdc -t upload
   ```
   > **Mẹo khi ESP32-S3 không tự vào chế độ nạp (USB CDC):** Giữ nút **BOOT**, bấm nhả nút **RST (EN)**, sau đó thả nút **BOOT** rồi bấm Upload lại; nạp xong bấm **RST** một lần để chạy chương trình.

---

## 3. Cách chạy Python UI xem trạng thái

Môi trường Python cần thư viện `pyserial` (`tkinter` có sẵn trong Python trên Windows):

```powershell
pip install -r requirements.txt
python mpu6050_ui.py
```

Hoặc nhấp đúp chạy trực tiếp file **`run_ui.bat`**.

### Các chức năng trên giao diện:
- **Chọn cổng COM & Kết nối:** Tự nhận diện cổng COM của ESP32-S3, hiển thị trạng thái:
  - **HOẠT ĐỘNG TỐT (`0x68` / `0x69`)**: Đọc dữ liệu accel, gyro, nhiệt độ chip và góc nghiêng bình thường.
  - **CẢNH BÁO `WHO_AM_I`**: Cảm biến phản hồi nhưng mã chip khác `0x68` (thường gặp ở module clone / MPU6500 / MPU9250).
  - **LỖI `ERR_NO_DEVICE` / `ERR_I2C_READ`**: Không tìm thấy cảm biến trên I2C → kiểm tra lại dây `SDA`, `SCL`, `VCC`, `GND`. Khi cắm lại đúng dây, firmware tự kết nối lại mà không cần reset ESP32-S3.
- **Quét I2C (`SCAN`)**: Quét toàn bộ địa chỉ I2C (`0x03..0x77`) và in ra khung Nhật ký.
- **Hiệu chuẩn 0 (`CAL`)**: Đặt mạch nằm phẳng đứng yên trên bàn rồi bấm để cân bằng gốc gia tốc (`X=0, Y=0, Z=1g`) và triệt trôi gyro.
- **Reset Yaw**: Đưa góc `Yaw` về `0°`.
- **Mô phỏng 3D + Đồ thị thời gian thực**: Xoay/nghiêng module MPU6050 để kiểm tra trực quan phản hồi các trục `X / Y / Z`.
