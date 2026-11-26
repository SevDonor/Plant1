# Ý Tưởng Nâng Cấp Trợ Lý Chậu Cây Thông Minh (Smart Plant Desktop Pet)

Tài liệu này tổng hợp các ý tưởng kết hợp hệ thống cảm biến môi trường với AI Live Chat để biến chậu cây từ một thiết bị IoT thông thường thành một **Thú cưng để bàn (Desktop Companion Pet)** có cảm xúc, phản xạ và cá tính riêng.

---

## 1. Hệ thống "Tâm sinh lý" (Biological & Emotional State Machine)
Chuyển hóa các thông số kỹ thuật khô khan từ cảm biến thành trạng thái cảm xúc trên màn hình **OLED SSD1309** và giọng nói qua loa **MAX98357A**:

| Cảm biến            | Trạng thái môi trường         | Biểu cảm OLED                    | Phản ứng / Lời thoại mẫu                                          |
| :------------------ | :---------------------------- | :------------------------------- | :---------------------------------------------------------------- |
| **AHT20 + BH1750**  | Nóng (> 32°C) hoặc nắng gắt   | Toát mồ hôi `(；´д｀)ゞ`            | *"Nắng cháy lá rồi sen ơi, dời em vào chỗ mát với!"*              |
| **BH1750**          | Tắt đèn / Trời tối (< 10 lux) | Nhắm mắt ngủ `( - . - ) zzz`     | Thầm thì hoặc càu nhàu nhẹ nếu bị gọi dậy lúc đêm khuya.          |
| **BH1750**          | Sáng sớm (Ánh sáng tăng dần)  | Vươn vai, mắt lấp lánh `( *‿* )` | Tự động chào buổi sáng và báo nhiệt độ trong ngày.                |
| **Độ ẩm đất**       | Đất khô (< 20%)               | Khát khô, nhăn nhó `( >_< )`     | *"Cổ họng em khô queo rồi, cho em xin một ngụm nước đi mà!"*      |
| **Độ ẩm đất + Bơm** | Đang được tưới nước           | Mắt trái tim `( ♥‿♥ )`           | Phát tiếng "ực ực" sảng khoái: *"Aaa mát quá, cảm ơn sen!"*       |
| **XKC Y25 V**       | Bình trữ hết nước             | Ngạc nhiên / Báo động `( °o° )`  | *"Bình nước cạn đáy rồi kìa, nhớ châm thêm nước cho em nhé!"*     |
| **BMP280**          | Áp suất khí quyển tụt nhanh   | Lo lắng / Nhìn mây mưa           | *"Trời sắp đổ mưa dông đấy, bạn đã đóng cửa sổ hay cất đồ chưa?"* |

---

## 2. Phản xạ cơ thể vật lý qua MPU-6050 (Haptic & Motion)
Sử dụng cảm biến gia tốc & con quay hồi chuyển **MPU-6050** để tạo cảm giác xúc giác khi tương tác vật lý với chậu cây:

* **Xoa đầu / Gõ nhẹ (Tap / Double-tap):** Gõ nhẹ 2 cái vào thành chậu -> Pet cười tít mắt `( ^‿^ )`, phát tiếng rừ rừ (purr) hoặc cười khúc khích.
* **Rung lắc mạnh (Shake):** Màn hình hiện mắt xoáy ốc `( @ _ @ )` -> Kêu la: *"Ối ối chóng mặt quá, đừng lắc em nữa!"*.
* **Nhấc lên / Di chuyển (Pick-up):** Nhận diện thay đổi gia tốc đột ngột -> Hào hứng hỏi: *"Đi dổi gió hả chủ nhân?"*.
* **Bị nghiêng / Ngã đổ (Tilt > 45°):** Chuyển sang trạng thái khẩn cấp `( T_T )` -> Hét lên cầu cứu: *"Á chậu nghiêng rồi, đỡ em dậy với!"*.

---

## 3. Khởi xướng hội thoại chủ động (Proactive Companion)
Không chỉ thụ động chờ lệnh thoại (Wake-word), pet có thể chủ động bắt chuyện dựa trên thói quen của người dùng:

* **Bạn đồng hành Pomodoro (Làm việc & Nghỉ ngơi):** Khi cảm biến ánh sáng bật và micro **INMP441** nhận thấy tiếng gõ phím/hoạt động liên tục quá 60 phút, pet tự cựa quậy nhắc: *"Bạn làm việc tập trung 1 tiếng rồi đấy, uống ngụm nước và nhìn sang ngắm lá xanh của tui 30 giây để xả mắt đi!"*.
* **Nhắc ngủ muộn:** Sau 0h đêm mà đèn bàn vẫn sáng, pet ngáp dài và giục người dùng đi ngủ để bảo vệ sức khỏe.

---

## 4. Kỹ thuật "Tiêm ngữ cảnh" vào LLM (Context Injection Prompting)
Đính kèm dữ liệu cảm biến thời gian thực vào **System Prompt** trước khi gửi yêu cầu lên mô hình ngôn ngữ lớn (LLM) để câu trả lời mang đậm tính cách nhân vật:

```text
[System Context]
- Thời gian: 23:45
- Nhiệt độ: 31°C (Oi bức), Độ ẩm không khí: 55%
- Độ ẩm đất: 18% (Rất khô, đang khát)
- Tương tác vừa xảy ra: Người dùng vừa gõ nhẹ 2 cái vào chậu
- Tính cách: Chậu cây nhỏ nhõng nhẽo, hài hước, xưng "tui" và gọi người dùng là "Sen".

[Hướng dẫn]: Trả lời câu hỏi của người dùng ngắn gọn (1-2 câu), lồng ghép tự nhiên cảm giác cơ thể hiện tại.
```

* **Ví dụ:** Khi người dùng hỏi *"Hôm nay mày thấy sao?"*
* **Trả lời thông thường (nhàm chán):** *"Nhiệt độ hiện tại là 31 độ C và độ ẩm đất là 18%."*
* **Trả lời có Context Injection (thú vị):** *"Vừa nóng vừa khát muốn khô cả cọng lá đây nè Sen! Gõ đầu tui làm gì, mau tưới cho tui miếng nước rồi tâm sự tiếp!"*

---

## 5. Mini-game & Tính năng giải trí mở rộng
* **Lắc chậu bói toán / Quyết định ngẫu nhiên:** Khi phân vân (ví dụ: *"Trưa nay ăn gì?"* hoặc *"Có nên làm việc X không?"*), người dùng hỏi rồi lắc nhẹ chậu cây. MPU-6050 bắt tín hiệu lắc, màn hình OLED chạy hiệu ứng xúc xắc/vòng quay và pet phán câu trả lời.
* **Cơ chế Tiến hóa (Tamagotchi Leveling):** Tích điểm chăm sóc (duy trì độ ẩm đất, ánh sáng và nhiệt độ lý tưởng theo ngày). Khi đủ điểm, pet "lên cấp", mở khóa thêm các bộ biểu cảm OLED mới hoặc cách xưng hô thân thiết hơn.
