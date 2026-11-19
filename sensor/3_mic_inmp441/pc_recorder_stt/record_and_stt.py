#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
CHƯƠNG TRÌNH THU ÂM TỪ MICRO INMP441 (ESP32) -> LƯU FILE .WAV & SPEECH-TO-TEXT
================================================================================
Chức năng:
  1. Tự động tìm cổng COM của ESP32 (hoặc chọn thủ công qua tham số --port).
  2. Gửi lệnh "REC:<so_giay>" xuống ESP32 để thu âm trực tiếp từ cảm biến INMP441
     ở định dạng chuẩn 16.000 Hz, 16-bit Signed PCM, Mono.
  3. Tự động cân bằng/chuẩn hóa biên độ âm thanh (Normalize) và lưu thành file
     .WAV vào thư mục `recordings/`.
  4. Chuyển đổi giọng nói thành văn bản (Speech-to-Text - hỗ trợ Tiếng Việt vi-VN
     và Tiếng Anh en-US) bằng Google Speech Recognition (hoặc Whisper nếu có),
     hiển thị kết quả lên màn hình và lưu vào `recordings/transcripts.txt`.

Cách chạy:
  pip install -r requirements.txt
  python record_and_stt.py                     # Chế độ menu tương tác tự động
  python record_and_stt.py --port COM5 -d 5    # Thu 5 giây trên cổng COM5
  python record_and_stt.py --wav file.wav      # Chuyển STT từ file .wav có sẵn
================================================================================
"""

import argparse
import array
import datetime
import math
import os
import re
import struct
import sys
import time
import wave

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None

try:
    import speech_recognition as sr
except ImportError:
    sr = None


DEFAULT_BAUD = 921600
DEFAULT_DURATION = 5
DEFAULT_LANG = "vi-VN"


def find_esp32_port():
    """Tự động quét và chọn cổng COM của mạch ESP32."""
    if serial is None:
        print("[LỖI] Chưa cài thư viện pyserial! Hãy chạy: pip install pyserial SpeechRecognition")
        return None

    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("[!] Không tìm thấy cổng COM nào đang kết nối trên máy tính.")
        return None

    print("\nDanh sách các cổng Serial (COM) hiện có:")
    esp_candidates = []
    for idx, p in enumerate(ports):
        desc = f"{p.device} - {p.description} [{p.hwid}]"
        print(f"  [{idx + 1}] {desc}")
        keywords = ("cp210", "ch340", "ch910", "usb serial", "uart", "ftdi", "esp32", "usb-enhanced")
        if any(k in (p.description or "").lower() or k in (p.hwid or "").lower() for k in keywords):
            esp_candidates.append(p.device)

    if esp_candidates:
        chosen = esp_candidates[0]
        print(f"=> Tự động chọn cổng ESP32: {chosen}")
        return chosen

    if len(ports) == 1:
        print(f"=> Sử dụng cổng duy nhất: {ports[0].device}")
        return ports[0].device

    choice = input("Nhập tên cổng COM (ví dụ COM3) hoặc số thứ tự: ").strip()
    if choice.isdigit():
        i = int(choice) - 1
        if 0 <= i < len(ports):
            return ports[i].device
    return choice


def normalize_pcm16(raw_bytes, target_peak=26000):
    """Khử độ lệch DC còn sót và chuẩn hóa âm lượng để nhận diện giọng nói rõ nhất."""
    if len(raw_bytes) < 4:
        return raw_bytes, 0.0, 0

    samples = array.array("h")
    samples.frombytes(raw_bytes[: len(raw_bytes) - (len(raw_bytes) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()

    n = len(samples)
    mean_val = sum(samples) / float(n)

    centered = []
    peak = 0
    sum_sq = 0.0
    for s in samples:
        c = int(round(s - mean_val))
        abs_c = abs(c)
        if abs_c > peak:
            peak = abs_c
        sum_sq += c * c
        centered.append(c)

    rms = math.sqrt(sum_sq / float(n))

    # Nếu biên độ quá nhỏ (nhưng không phải im lặng hoàn toàn), khuếch đại mềm lên target_peak
    gain = 1.0
    if 150 < peak < target_peak:
        gain = min(float(target_peak) / float(peak), 8.0)

    out_samples = array.array("h")
    for c in centered:
        val = int(round(c * gain))
        if val > 32767:
            val = 32767
        elif val < -32768:
            val = -32768
        out_samples.append(val)

    if sys.byteorder != "little":
        out_samples.byteswap()

    return out_samples.tobytes(), rms, peak


def save_wav_file(filepath, pcm_bytes, sample_rate=16000, channels=1, sampwidth=2):
    """Lưu dữ liệu PCM 16-bit vào file chuẩn .WAV."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)


def record_from_esp32(port, baudrate=DEFAULT_BAUD, duration_sec=DEFAULT_DURATION):
    """Gửi lệnh REC:<duration> xuống ESP32 và nhận luồng PCM 16-bit."""
    if serial is None:
        raise RuntimeError("Thiếu thư viện pyserial. Hãy chạy: pip install pyserial")

    print(f"\n[1/3] Đang mở cổng {port} ở tốc độ {baudrate} baud...")
    with serial.Serial(port, baudrate=baudrate, timeout=2.0) as ser:
        # Chờ ổn định nếu mạch vừa reset
        time.sleep(0.5)
        ser.reset_input_buffer()

        print(f"      -> Chuẩn bị thu âm trong {duration_sec} giây từ micro INMP441...")
        for countdown in (3, 2, 1):
            print(f"      -> Bắt đầu nói sau {countdown}...")
            time.sleep(0.8)

        cmd = f"REC:{duration_sec}\n".encode("ascii")
        ser.write(cmd)
        ser.flush()

        # Đợi dòng header: <<<PCM_START:16000:16:1:80000>>>
        sample_rate = 16000
        total_samples = duration_sec * sample_rate
        header_found = False
        deadline = time.time() + 5.0

        while time.time() < deadline:
            line_bytes = ser.readline()
            if not line_bytes:
                continue
            line_str = line_bytes.decode("utf-8", errors="ignore").strip()
            m = re.search(r"<<<PCM_START:(\d+):(\d+):(\d+):(\d+)>>>", line_str)
            if m:
                sample_rate = int(m.group(1))
                total_samples = int(m.group(4))
                header_found = True
                break

        if not header_found:
            raise RuntimeError(
                "Không nhận được phản hồi <<<PCM_START>>> từ ESP32! "
                "Hãy đảm bảo bạn đã nạp firmware Nhóm 3 (3_mic_inmp441) và đóng Serial Monitor của VS Code."
            )

        expected_bytes = total_samples * 2
        print(f"\n[2/3] ĐANG THU ÂM ({duration_sec} giây | {sample_rate} Hz | 16-bit Mono) - HÃY NÓI VÀO MICRO!")

        pcm_buffer = bytearray()
        start_t = time.time()
        while len(pcm_buffer) < expected_bytes:
            need = min(4096, expected_bytes - len(pcm_buffer))
            chunk = ser.read(need)
            if not chunk:
                if time.time() - start_t > duration_sec + 5.0:
                    break
                continue
            pcm_buffer.extend(chunk)

            # Hiển thị thanh tiến trình thu âm
            pct = min(100.0, len(pcm_buffer) * 100.0 / expected_bytes)
            bars = int(pct // 4)
            bar_str = "█" * bars + "░" * (25 - bars)
            sys.stdout.write(f"\r      Tiến trình: [{bar_str}] {pct:5.1f}% ({len(pcm_buffer)}/{expected_bytes} bytes)")
            sys.stdout.flush()

        print("\n      -> Đã thu xong dữ liệu âm thanh từ ESP32!")
        return bytes(pcm_buffer), sample_rate


def transcribe_wav_speech_to_text(wav_path, language=DEFAULT_LANG):
    """Chuyển đổi file âm thanh .wav thành văn bản (Speech-to-Text)."""
    print(f"\n[3/3] Đang chuyển đổi Speech-to-Text (Ngôn ngữ: {language})...")

    if sr is None:
        print("[!] Chưa cài đặt thư viện SpeechRecognition.")
        print("    Hãy chạy lệnh: pip install SpeechRecognition")
        return None

    recognizer = sr.Recognizer()
    with sr.AudioFile(wav_path) as source:
        audio_data = recognizer.record(source)

    # 1. Thử nhận diện qua Google Web Speech API (Hỗ trợ Tiếng Việt vi-VN rất tốt, không cần key)
    try:
        text = recognizer.recognize_google(audio_data, language=language)
        return text
    except sr.UnknownValueError:
        print("[STT] Không nhận diện được lời nói rõ ràng trong bản thu (âm thanh quá nhỏ hoặc chỉ có tạp âm).")
        return ""
    except sr.RequestError as e:
        print(f"[STT] Lỗi kết nối tới dịch vụ Google Speech API: {e}")
        return None


def save_transcript_log(out_dir, wav_filename, text, lang, rms, peak):
    """Lưu lịch sử nhận diện giọng nói vào file transcripts.txt."""
    log_path = os.path.join(out_dir, "transcripts.txt")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as fp:
        fp.write(
            f"[{now_str}] File: {wav_filename} | Lang: {lang} | RMS: {rms:.0f} | Peak: {peak} | "
            f"Text: {text if text else '(Không nhận diện được)'}\n"
        )
    return log_path


def process_and_save_recording(raw_pcm, sample_rate, out_dir, language):
    """Chuẩn hóa âm thanh, lưu file .wav và chạy Speech-to-Text."""
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    wav_filename = f"rec_{ts}.wav"
    wav_path = os.path.join(out_dir, wav_filename)

    norm_pcm, rms, peak = normalize_pcm16(raw_pcm)
    save_wav_file(wav_path, norm_pcm, sample_rate=sample_rate)

    print(f"      -> Đã lưu file âm thanh: {wav_path}")
    print(f"      -> Thông số tín hiệu   : RMS = {rms:.1f} | Peak = {peak} / 32767")

    text = transcribe_wav_speech_to_text(wav_path, language=language)
    log_path = save_transcript_log(out_dir, wav_filename, text, language, rms, peak)

    print("\n┌──────────────────── KẾT QUẢ SPEECH-TO-TEXT ────────────────────┐")
    print(f"│ File WAV   : {wav_path}")
    print(f"│ Ngôn ngữ   : {language}")
    if text:
        print(f"│ Văn bản    : \"{text}\"")
    else:
        print("│ Văn bản    : (Chưa nhận diện được câu nói - hãy thử nói to/gần micro hơn)")
    print(f"│ File Log   : {log_path}")
    print("└────────────────────────────────────────────────────────────────┘\n")
    return wav_path, text


def main():
    parser = argparse.ArgumentParser(
        description="Chương trình thu âm từ Micro INMP441 trên ESP32, lưu .WAV và chuyển Speech-to-Text"
    )
    parser.add_argument("-p", "--port", help="Cổng COM của ESP32 (ví dụ: COM3, COM5)")
    parser.add_argument("-b", "--baud", type=int, default=DEFAULT_BAUD, help="Tốc độ Baudrate (mặc định: 921600)")
    parser.add_argument("-d", "--duration", type=int, default=DEFAULT_DURATION, help="Thời lượng thu âm (giây)")
    parser.add_argument("-l", "--lang", default=DEFAULT_LANG, help="Ngôn ngữ STT (mặc định: vi-VN, hoặc en-US)")
    parser.add_argument("-o", "--outdir", default="recordings", help="Thư mục lưu file .wav và kết quả STT")
    parser.add_argument("-w", "--wav", help="Đường dẫn file .wav có sẵn nếu chỉ muốn chạy Speech-to-Text")
    args = parser.parse_args()

    out_dir = os.path.abspath(args.outdir)

    print("==================================================================")
    print("   ESP32 INMP441 AUDIO RECORDER (.WAV) & SPEECH-TO-TEXT (STT)")
    print("==================================================================")

    # Nếu người dùng truyền vào 1 file .wav có sẵn để test STT
    if args.wav:
        if not os.path.exists(args.wav):
            print(f"[LỖI] Không tìm thấy file: {args.wav}")
            sys.exit(1)
        text = transcribe_wav_speech_to_text(args.wav, language=args.lang)
        print(f"\n=> KẾT QUẢ STT: \"{text}\"")
        return

    port = args.port or find_esp32_port()
    if not port:
        sys.exit(1)

    while True:
        try:
            raw_pcm, sr_hz = record_from_esp32(port, baudrate=args.baud, duration_sec=args.duration)
            process_and_save_recording(raw_pcm, sr_hz, out_dir, args.lang)
        except Exception as exc:
            print(f"\n[LỖI] {exc}\n")

        ans = input("Bạn có muốn thu âm tiếp câu khác không? (Enter = Có / 'q' = Thoát): ").strip().lower()
        if ans in ("q", "n", "no", "exit"):
            print("Đã thoát chương trình.")
            break


if __name__ == "__main__":
    main()
