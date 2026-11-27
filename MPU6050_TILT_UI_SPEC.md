# Đặc tả Nguyên lý Độ nghiêng MPU-6050 cho Giao diện (Figma UI Spec)

> **Dự án:** Smart Plant Companion (`A.Plant`)  
> **File Figma liên kết:** [Plant — 01 · Bố cục Tổng thể](https://www.figma.com/design/2SUUzSa6QAP2LozRFaaMAh/Plant)  
> **Mục đích:** Tài liệu ngắn gọn để designer cập nhật phần hiển thị góc nghiêng, biểu cảm 2 mắt OLED và cảnh báo an toàn bơm khi chậu bị nghiêng, lật dọc hoặc quay 180°.

---

## 1. Nguyên lý tách 2 loại nghiêng (Tóm gọn)

MPU-6050 đo 3 thành phần trọng lực `(ax, ay, az)`. Khi chậu nghiêng theo một hướng, trọng lực phân bố lại giữa `Z` và 1 trục ngang (`X` hoặc `Y`), trong khi trục còn lại gần như giữ nguyên.

Để UI hiển thị **dễ hiểu với người dùng cuối** và **không bị loạn góc khi lật quá 90°–180°**, hệ thống tách thành 3 giá trị:

1. **Nghiêng Ngang — Trái / Phải (`tilt_lr`: `-180°` đến `+180°`)**
   - `< 0°` → **Nghiêng trái `X°`**
   - `> 0°` → **Nghiêng phải `X°`**
2. **Nghiêng Dọc — Trước / Sau (`tilt_fb`: `-180°` đến `+180°`)**
   - `> 0°` → **Nghiêng trước `X°`** *(chúi mặt)*
   - `< 0°` → **Nghiêng sau `X°`** *(ngửa mặt)*
3. **Góc nghiêng tổng so với phương thẳng đứng (`tilt_total`: `0°` đến `180°`)**
   - `0°`: Đứng thẳng bình thường (`az ≈ +1g`)
   - `90°`: Lật dọc / nằm ngang hoàn toàn (`az ≈ 0`)
   - `180°`: Lật ngược / úp đầu xuống đất (`az ≈ -1g`)

> **Lưu ý về "Quay 180°":**
> - **Lật ngược 180° (`UPSIDE_DOWN`):** Chậu bị úp ngược đầu xuống đất (`az < -0.7g`, `tilt_total ≥ 135°`) → **Đo chính xác 100%, không trôi.**
> - **Xoay ngang 180° (`TURNED_180` — tùy chọn):** Chậu vẫn đứng thẳng nhưng bị xoay quay lưng lại trên bàn (`|Δyaw| ≥ 150°`) → Chỉ bắt theo sự kiện tức thời (không làm la bàn cố định vì MPU-6050 không có từ kế).

---

## 2. Bảng Trạng thái Tư thế & Quy ước hiển thị trên Figma

*(Hệ thống có vùng trễ hysteresis & lọc `300–500ms` để UI không bị nhấp nháy khi gõ nhẹ hoặc rung)*

| Trạng thái (`pose_state`) | Ngưỡng góc | Nhãn UI gợi ý (`tilt_label`) | Màu Badge | Hiệu ứng trên `Live Face Stage` (2 mắt OLED) | Tác động Trạm Tưới P3 & Banner |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`UPRIGHT`** *(Cân bằng)* | `tilt_total ≤ 15°` | `Cân bằng · 0°` | Xanh lá | Khung mặt thẳng, 2 mắt bình thường | Bơm hoạt động bình thường |
| **`TILT_LR`** *(Nghiêng Trái / Phải)* | `15° < tilt_total ≤ 50°` *(trục ngang trội hơn)* | `Nghiêng trái X°` hoặc `Nghiêng phải X°` | Vàng nhạt | Khung đầu nghiêng nhẹ theo góc thật; đồng tử liếc về phía nghiêng | Bơm bình thường |
| **`TILT_FB`** *(Nghiêng Trước / Sau)* | `15° < tilt_total ≤ 50°` *(trục dọc trội hơn)* | `Nghiêng trước X°` hoặc `Nghiêng sau X°` | Vàng nhạt | Mắt nhìn xuống (nghiêng trước) hoặc nhìn lên (nghiêng sau) | Bơm bình thường |
| **`TIPPED_VERTICAL`** *(Lật dọc / Ngã nằm)* | `50° < tilt_total < 135°` *(quanh `90°` là nằm hẳn)* | `Bị ngã [Trái/Phải/Trước/Sau] · X°` | Cam / Đỏ | Biểu cảm `SURPRISED (O_O)` hoặc `SAD (T_T)`, thoại: *"Á chậu ngã rồi, đỡ em dậy với!"* | **Khóa bơm ngay + Hiện Banner khẩn cấp** |
| **`UPSIDE_DOWN`** *(Lật ngược 180°)* | `tilt_total ≥ 135°` *(tới `180°`)* | `Lật ngược 180°` | Đỏ đậm | Biểu cảm `DIZZY (@_@)` (có thể lật ngược khung 2 mắt 180°), thoại: *"Ối lộn ngược rồi, rơi hết đất bây giờ!"* | **Khóa bơm khẩn cấp + Hiện Banner đỏ** |
| **`TURNED_180`** *(Xoay ngang 180° — tùy chọn)* | `tilt_total ≤ 20°` & `\|Δyaw\| ≥ 150°` | `Vừa xoay ngược 180°` | Tím / Xanh dương | Mắt liếc dỗi, thoại ngắn: *"Ơ sao quay lưng em lại thế?"* *(tự tắt sau 3–4s)* | Không ảnh hưởng bơm |

---

## 3. Hạng mục cần cập nhật trên Figma (`Plant`)

1. **Khối `Live Face Stage` (Trang 1 — Cột trái trên):**
   - Thanh chỉ báo ở đáy khung mặt hiển thị **2 chỉ số gọn** thay vì `Roll/Pitch` khô cứng:
     - **Badge tư thế + nhãn hướng:** ví dụ `↙ Nghiêng trái 22°` | `↑ Cân bằng 0°` | `⚠ Bị ngã phải 88°` | `🙃 Lật ngược 178°`.
     - **Thanh cân bằng 2 trục (mini bubble / 2 thanh ngang-dọc):** 1 thanh **Trái ↔ Phải**, 1 thanh **Trước ↕ Sau** (mốc `0°` ở giữa).
   - Thiết kế thêm **2 variant trạng thái khẩn cấp** cho khung `Live Face Stage`:
     - Variant `TIPPED_VERTICAL` (khung nghiêng ~90°, viền cảnh báo đỏ/cam).
     - Variant `UPSIDE_DOWN` (khung lộn ngược 180°, mắt `@_@`, viền đỏ đậm).
2. **Banner khẩn cấp dưới Header:**
   - Thêm biến thể cảnh báo tư thế:
     - `⚠ CHẬU CÂY BỊ NGÃ (88° PHẢI) — Đã tự động khóa bơm an toàn`
     - `🚨 CHẬU CÂY BỊ LẬT NGƯỢC 180° — Đã ngắt bơm khẩn cấp`
3. **Khối `Trạm Tưới An Toàn P3` (Trang 1 — Cột trái dưới):**
   - Khi `pose_state` là `TIPPED_VERTICAL` hoặc `UPSIDE_DOWN`: vô hiệu hóa nút `[Tưới 1 xung 3s]` và hiển thị lý do ngắn: **`Đã khóa bơm: Chậu không đứng thẳng`** *(chỉ mở lại sau khi dựng thẳng ổn định 3 giây)*.
4. **Trang 4 — Debug (tùy chọn):**
   - Giữ các giá trị thô `ax, ay, az`, `tilt_lr`, `tilt_fb`, `tilt_total`, `pose_state` và nút `[Tare 0° / Hiệu chuẩn MPU]` ở đây, **không** đưa số `ax/ay/az` ra Trang 1.

---

## 4. Cấu trúc dữ liệu mẫu (Để UI & Firmware khớp nhau)

```json
{
  "tilt_lr": -24.5,
  "tilt_fb": 3.2,
  "tilt_total": 24.7,
  "pose_state": "TILT_LR",
  "tilt_label": "Nghiêng trái 25°",
  "pump_locked_by_tilt": false
}
```
