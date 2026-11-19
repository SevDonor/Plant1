#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[WIREFRAME PROTOTYPE v3 - MULTI-PAGE ARCHITECTURE & SMART STATUS BAR]
Smart Plant Desktop App - Bố cục UI/UX Đa trang trên Máy tính

Cải tiến UX trọng tâm theo góp ý:
  1. Thanh Header tối giản kiểu Smartphone/OS hiện đại:
     - Đèn trạng thái (Status LED): RUNNING / PENDING / ERROR (click vào nhảy thẳng tới trang Debug).
     - Cột sóng Wi-Fi đồ họa 4 vạch (Signal Bars) thay vì hiện chữ RSSI thô (di chuột hoặc bấm để xem chi tiết).
  2. Tách biệt thành 4 Trang chức năng (Multi-page Navigation) để màn hình chính thoáng đãng:
     - Trang 1 [Tổng quan & Biểu đồ]: Dành cho người dùng cuối (OLED, Bơm, 4 Ô Click-to-Chart, Biểu đồ lớn).
     - Trang 2 [Kho Dữ liệu SQLite]: Xem bảng bản ghi toàn màn hình, lọc dữ liệu, xuất CSV.
     - Trang 3 [Cấu hình Wi-Fi & Nạp Firmware]: Cài đặt SSID/Pass, hiệu chuẩn ADC/IMU, Nạp Firmware (.bin OTA/Serial).
     - Trang 4 [Debug & Soi Gói Tin]: Giấu toàn bộ JSON thô, Event/SQL Log, mã lỗi Hex, Uptime, RSSI chi tiết vào đây.
"""

import datetime
import json
import math
import os
import random
import sqlite3
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plant_telemetry.db")

METRICS_CONFIG = {
    "soil_pct": {
        "title": "1. ĐỘ ẨM ĐẤT (SOIL MOISTURE %)",
        "unit": "%",
        "y_min": 0,
        "y_max": 100,
        "threshold": 30,
        "threshold_label": "Ngưỡng tự tưới (30%)",
        "db_col": "soil_pct",
        "sensor_name": "ADC GPIO34",
    },
    "light_lux": {
        "title": "2. CƯỜNG ĐỘ ÁNH SÁNG (LIGHT INTENSITY)",
        "unit": "Lux",
        "y_min": 0,
        "y_max": 1200,
        "threshold": 800,
        "threshold_label": "Ngưỡng nắng gắt (800 Lux)",
        "db_col": "light_lux",
        "sensor_name": "BH1750 (I2C)",
    },
    "temp_c": {
        "title": "3. NHIỆT ĐỘ MÔI TRƯỜNG (TEMPERATURE)",
        "unit": "°C",
        "y_min": 15,
        "y_max": 40,
        "threshold": 32,
        "threshold_label": "Ngưỡng cảnh báo nóng (32°C)",
        "db_col": "temp_c",
        "sensor_name": "AHT20 / BMP280",
    },
    "press_hpa": {
        "title": "4. ÁP SUẤT KHÍ QUYỂN (PRESSURE)",
        "unit": "hPa",
        "y_min": 990,
        "y_max": 1025,
        "threshold": 1000,
        "threshold_label": "Ngưỡng áp thấp báo mưa (1000 hPa)",
        "db_col": "press_hpa",
        "sensor_name": "BMP280 (I2C)",
    },
    "water_pct": {
        "title": "5. MỨC NƯỚC TRONG BÌNH (WATER TANK LEVEL)",
        "unit": "%",
        "y_min": 0,
        "y_max": 100,
        "threshold": 15,
        "threshold_label": "Ngưỡng cạn bình khóa bơm (15%)",
        "db_col": "water_pct",
        "sensor_name": "XKC-Y25-V",
    },
    "tilt": {
        "title": "6. GÓC NGHIÊNG CHẬU CÂY (MPU-6050 TILT)",
        "unit": "°",
        "y_min": 0,
        "y_max": 70,
        "threshold": 45,
        "threshold_label": "Ngưỡng cảnh báo ngã đổ (45°)",
        "db_col": "tilt_deg",
        "sensor_name": "MPU-6050",
    },
}

SCENARIOS = {
    "normal": {
        "name": "1. Bình thường (Running)",
        "sys_state": "RUNNING",      # RUNNING | PENDING | ERROR
        "wifi_bars": 4,              # 0..4 vạch sóng
        "soil_adc": 1840,
        "soil_pct": 62.0,
        "soil_status": "Đủ ẩm",
        "light_lux": 450.0,
        "light_status": "Ánh sáng phòng",
        "temp_c": 27.4,
        "press_hpa": 1008.5,
        "ax": 0.02,
        "ay": -0.01,
        "az": 0.99,
        "tilt": 1.4,
        "water_pct": 78.0,
        "pump_on": 0,
        "pump_cycle": 4,
        "emotion_code": "HAPPY_IDLE",
        "oled_eyes": "( ^ ‿ ^ )",
        "speech": '"Đất đủ ẩm, nắng dịu nhẹ, tui đang rất yêu đời nha Sen ơi!"',
        "err_code": "0x00 (NO_ERROR)",
        "rssi": -54,
        "uptime": "04:12:39",
    },
    "dry": {
        "name": "2. Đất khô (Pending)",
        "sys_state": "PENDING",
        "wifi_bars": 3,
        "soil_adc": 2690,
        "soil_pct": 16.0,
        "soil_status": "RẤT KHÔ (Chờ tưới)",
        "light_lux": 920.0,
        "light_status": "Nắng gắt",
        "temp_c": 33.2,
        "press_hpa": 1004.2,
        "ax": 0.00,
        "ay": 0.01,
        "az": 1.00,
        "tilt": 0.8,
        "water_pct": 65.0,
        "pump_on": 0,
        "pump_cycle": 4,
        "emotion_code": "THIRSTY_HOT",
        "oled_eyes": "( > _ < )",
        "speech": '"Cổ họng khô queo (16%) lại còn nóng 33.2°C, chuẩn bị kích hoạt bơm..."',
        "err_code": "0x01 (WARN_SOIL_DRY)",
        "rssi": -66,
        "uptime": "04:15:10",
    },
    "watering": {
        "name": "3. Đang tưới (Pending/Busy)",
        "sys_state": "PENDING",
        "wifi_bars": 4,
        "soil_adc": 1520,
        "soil_pct": 74.0,
        "soil_status": "Đang thấm nước",
        "light_lux": 460.0,
        "light_status": "Ánh sáng phòng",
        "temp_c": 26.9,
        "press_hpa": 1008.0,
        "ax": 0.01,
        "ay": 0.02,
        "az": 0.99,
        "tilt": 1.1,
        "water_pct": 52.0,
        "pump_on": 1,
        "pump_cycle": 5,
        "emotion_code": "WATERING_LOVE",
        "oled_eyes": "( ♥ ‿ ♥ )",
        "speech": '"Ực ực... Aaa mát quá, cảm ơn Sen nhiều nha!"',
        "err_code": "0x00 (PUMP_ACTIVE)",
        "rssi": -56,
        "uptime": "04:16:05",
    },
    "tilt": {
        "name": "4. Ngã chậu (Error!)",
        "sys_state": "ERROR",
        "wifi_bars": 2,
        "soil_adc": 1890,
        "soil_pct": 58.0,
        "soil_status": "Đủ ẩm",
        "light_lux": 390.0,
        "light_status": "Ánh sáng phòng",
        "temp_c": 27.5,
        "press_hpa": 1007.8,
        "ax": 0.72,
        "ay": -0.45,
        "az": 0.51,
        "tilt": 54.6,
        "water_pct": 50.0,
        "pump_on": 0,
        "pump_cycle": 5,
        "emotion_code": "DIZZY_TILT",
        "oled_eyes": "( @ _ @ )",
        "speech": '"Á á chậu cây nghiêng 54.6° rồi! Đỡ tui dậy kẻo đổ đất Sen ơi!"',
        "err_code": "0x04 (CRITICAL_TILT)",
        "rssi": -76,
        "uptime": "04:18:22",
    },
    "empty_tank": {
        "name": "5. Cạn bình & Sóng yếu (Error!)",
        "sys_state": "ERROR",
        "wifi_bars": 1,
        "soil_adc": 2610,
        "soil_pct": 20.0,
        "soil_status": "Đất khô",
        "light_lux": 25.0,
        "light_status": "Trời tối",
        "temp_c": 25.2,
        "press_hpa": 996.4,
        "ax": 0.00,
        "ay": 0.00,
        "az": 1.00,
        "tilt": 0.5,
        "water_pct": 0.0,
        "pump_on": 0,
        "pump_cycle": 5,
        "emotion_code": "TANK_EMPTY_PANIC",
        "oled_eyes": "( ° o ° )",
        "speech": '"Bình nước cạn đáy (0%) rồi! Bơm đã tự khóa an toàn để chống cháy!"',
        "err_code": "0x02 (ERR_EMPTY_TANK)",
        "rssi": -86,
        "uptime": "08:45:00",
    },
}


class TelemetryDatabase:
    """Quản lý cơ sở dữ liệu SQLite lưu trữ lịch sử gói tin JSON từ ESP32."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self._init_schema()
        self._seed_if_empty()

    def _init_schema(self):
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                soil_adc INTEGER,
                soil_pct REAL,
                light_lux REAL,
                temp_c REAL,
                press_hpa REAL,
                ax REAL,
                ay REAL,
                az REAL,
                tilt_deg REAL,
                water_pct REAL,
                pump_on INTEGER,
                pump_cycle INTEGER,
                emotion_code TEXT,
                err_code TEXT,
                rssi INTEGER
            )
            """
        )
        self.conn.commit()

    def _seed_if_empty(self):
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM telemetry_logs")
        if cur.fetchone()[0] > 0:
            return

        now = datetime.datetime.now()
        rows = []
        random.seed(42)
        water = 95.0
        soil = 68.0
        cycle = 1

        for i in range(120, 0, -1):
            ts = now - datetime.timedelta(hours=i * 1.4)
            hour = ts.hour
            if 6 <= hour <= 18:
                lux = round(250 + 650 * math.sin((hour - 6) / 12.0 * math.pi) + random.uniform(-40, 40), 1)
            else:
                lux = round(random.uniform(5, 25), 1)

            temp = round(24.5 + (4.5 if 10 <= hour <= 16 else 1.0) + random.uniform(-0.8, 0.8), 1)
            press = round(1007.0 + 4.0 * math.cos(i / 10.0) + random.uniform(-0.6, 0.6), 1)

            soil -= random.uniform(1.8, 3.5)
            pump_on = 0
            emotion = "HAPPY_IDLE"
            if soil < 28.0:
                pump_on = 1
                soil = round(random.uniform(68.0, 76.0), 1)
                water = max(10.0, round(water - random.uniform(6.0, 10.0), 1))
                cycle += 1
                emotion = "WATERING_LOVE"
                if water < 20.0:
                    water = 95.0

            soil = round(max(12.0, min(85.0, soil)), 1)
            adc = int(2850 - (soil / 100.0) * 1600)
            tilt = round(random.uniform(0.5, 2.2), 1)

            rows.append(
                (
                    ts.strftime("%Y-%m-%d %H:%M:%S"),
                    adc,
                    soil,
                    max(0.0, lux),
                    temp,
                    press,
                    0.01,
                    -0.01,
                    0.99,
                    tilt,
                    water,
                    pump_on,
                    cycle,
                    emotion,
                    "0x00",
                    random.randint(-65, -52),
                )
            )

        cur.executemany(
            """
            INSERT INTO telemetry_logs (
                timestamp, soil_adc, soil_pct, light_lux, temp_c, press_hpa,
                ax, ay, az, tilt_deg, water_pct, pump_on, pump_cycle,
                emotion_code, err_code, rssi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()

    def insert_record(self, d: dict, ts_str: str):
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO telemetry_logs (
                timestamp, soil_adc, soil_pct, light_lux, temp_c, press_hpa,
                ax, ay, az, tilt_deg, water_pct, pump_on, pump_cycle,
                emotion_code, err_code, rssi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts_str,
                d["soil_adc"],
                d["soil_pct"],
                d["light_lux"],
                d["temp_c"],
                d["press_hpa"],
                d["ax"],
                d["ay"],
                d["az"],
                d["tilt"],
                d["water_pct"],
                d["pump_on"],
                d["pump_cycle"],
                d["emotion_code"],
                d["err_code"].split()[0],
                d["rssi"],
            ),
        )
        self.conn.commit()

    def query_metric_series(self, col_name: str, range_mode: str):
        cur = self.conn.cursor()
        limit_map = {"LIVE": 12, "1H": 24, "24H": 48, "7D": 90}
        limit_n = limit_map.get(range_mode, 12)

        cur.execute(
            f"""
            SELECT id, timestamp, {col_name}, pump_on, emotion_code
            FROM (
                SELECT id, timestamp, {col_name}, pump_on, emotion_code
                FROM telemetry_logs
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """,
            (limit_n,),
        )
        rows = cur.fetchall()
        cur.execute(f"SELECT COUNT(*), MIN({col_name}), MAX({col_name}), AVG({col_name}) FROM telemetry_logs")
        stats = cur.fetchone()
        return rows, stats

    def query_recent_rows(self, limit_n: int = 80):
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id, timestamp, soil_pct, soil_adc, light_lux, temp_c, press_hpa,
                   water_pct, tilt_deg, pump_on, emotion_code, err_code, rssi
            FROM telemetry_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit_n,),
        )
        return cur.fetchall()


class SmartPlantMultiPageApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("[WIREFRAME v3] Smart Plant Desktop App - Đa trang & Thanh trạng thái thông minh")
        self.root.geometry("1320x820")
        self.root.minsize(1120, 700)
        self.root.configure(bg="#EFEFF2")

        self.db = TelemetryDatabase(DB_PATH)
        self.current_scenario = "normal"
        self.active_metric = "soil_pct"
        self.range_mode = "LIVE"
        self.current_page = "dashboard"

        self.mode_var = tk.StringVar(value="AUTO (Tự động theo ngưỡng)")
        self.thresh_var = tk.IntVar(value=30)

        self.kpi_cards = {}
        self.range_buttons = {}
        self.nav_buttons = {}
        self.pages = {}

        self._setup_styles()
        self._build_top_bar()
        self._build_simulator_bar()
        self._build_body_with_sidebar()

        self.load_scenario("normal", record_to_db=False)
        self.select_metric("soil_pct")
        self.switch_page("dashboard")

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#EFEFF2")
        style.configure("Card.TLabelframe", background="#FFFFFF", borderwidth=2, relief="solid")
        style.configure(
            "Card.TLabelframe.Label",
            background="#222222",
            foreground="#FFFFFF",
            font=("Consolas", 9, "bold"),
            padding=(6, 2),
        )
        style.configure("TLabel", background="#FFFFFF", foreground="#111111", font=("Segoe UI", 10))
        style.configure("Mono.TLabel", background="#FFFFFF", foreground="#111111", font=("Consolas", 10))
        style.configure("Sub.TLabel", background="#FFFFFF", foreground="#555555", font=("Consolas", 9))

    # =========================================================================
    # 1. TOP BAR TỐI GIẢN KIỂU ĐIỆN THOẠI (ĐÈN TRẠNG THÁI + ICON SÓNG WI-FI + GIỜ)
    # =========================================================================
    def _build_top_bar(self):
        top = tk.Frame(self.root, bg="#FFFFFF", bd=2, relief="solid", height=50)
        top.pack(fill="x", padx=10, pady=(8, 4))

        # Trái: Tên ứng dụng
        left = tk.Frame(top, bg="#FFFFFF")
        left.pack(side="left", padx=10, pady=6)
        tk.Label(
            left,
            text="SMART PLANT COMPANION",
            bg="#222222",
            fg="#FFFFFF",
            font=("Consolas", 10, "bold"),
            padx=8,
            pady=3,
        ).pack(side="left", padx=(0, 8))
        self.lbl_page_breadcrumb = tk.Label(
            left,
            text="Trang 1: Tổng quan & Chăm sóc (Dashboard)",
            bg="#FFFFFF",
            fg="#333333",
            font=("Segoe UI", 10, "bold"),
        )
        self.lbl_page_breadcrumb.pack(side="left")

        # Phải: Cụm Icon trạng thái gọn như điện thoại (Click để nhảy sang trang tương ứng)
        right = tk.Frame(top, bg="#FFFFFF")
        right.pack(side="right", padx=10, pady=4)

        # (D) Đồng hồ tối giản (HH:MM)
        self.lbl_clock = tk.Label(
            right,
            text="15:09",
            bg="#FFFFFF",
            fg="#111111",
            font=("Consolas", 12, "bold"),
            padx=8,
        )
        self.lbl_clock.pack(side="right")

        # (C) Icon Cột sóng Wi-Fi như điện thoại (Canvas vẽ 4 vạch sóng + Click mở cài đặt Wi-Fi)
        wifi_box = tk.Frame(right, bg="#F4F4F6", bd=1, relief="solid", padx=6, pady=2, cursor="hand2")
        wifi_box.pack(side="right", padx=6)
        self.canvas_wifi = tk.Canvas(wifi_box, width=28, height=20, bg="#F4F4F6", highlightthickness=0, cursor="hand2")
        self.canvas_wifi.pack(side="left")
        self.lbl_wifi_short = tk.Label(wifi_box, text="Wi-Fi", bg="#F4F4F6", fg="#111111", font=("Consolas", 8, "bold"), cursor="hand2")
        self.lbl_wifi_short.pack(side="left", padx=(4, 2))
        for w in (wifi_box, self.canvas_wifi, self.lbl_wifi_short):
            w.bind("<Button-1>", lambda e: self.switch_page("settings"))

        # (B) Pill báo trạng thái DB ngắn gọn (Click mở trang Database)
        self.btn_db_pill = tk.Button(
            right,
            text="🗄 DB: 120",
            font=("Consolas", 9, "bold"),
            bg="#F4F4F6",
            fg="#111111",
            relief="solid",
            bd=1,
            padx=8,
            pady=2,
            cursor="hand2",
            command=lambda: self.switch_page("database"),
        )
        self.btn_db_pill.pack(side="right", padx=6)

        # (A) Đèn báo Trạng thái Hệ thống (LED Indicator: RUNNING / PENDING / ERROR)
        # Click vào đèn sẽ mở ngay trang Debug để xem mã lỗi chi tiết!
        self.led_frame = tk.Frame(right, bg="#F4F4F6", bd=1, relief="solid", padx=8, pady=2, cursor="hand2")
        self.led_frame.pack(side="right", padx=6)
        self.canvas_led = tk.Canvas(self.led_frame, width=16, height=16, bg="#F4F4F6", highlightthickness=0, cursor="hand2")
        self.canvas_led.pack(side="left", padx=(0, 5))
        self.lbl_led_text = tk.Label(
            self.led_frame,
            text="RUNNING",
            bg="#F4F4F6",
            fg="#111111",
            font=("Consolas", 9, "bold"),
            cursor="hand2",
        )
        self.lbl_led_text.pack(side="left")
        for w in (self.led_frame, self.canvas_led, self.lbl_led_text):
            w.bind("<Button-1>", lambda e: self.switch_page("debug"))

    # =========================================================================
    # THANH GIẢ LẬP KỊCH BẢN (UX SCENARIO BAR)
    # =========================================================================
    def _build_simulator_bar(self):
        sim = tk.Frame(self.root, bg="#DCDCE2", bd=1, relief="solid")
        sim.pack(fill="x", padx=10, pady=2)

        tk.Label(
            sim,
            text="[UX SIMULATOR] Chọn kịch bản để xem Đèn trạng thái, Vạch sóng Wi-Fi & Giao diện phản ứng:",
            bg="#DCDCE2",
            fg="#111111",
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=8, pady=3)

        btn_frame = tk.Frame(sim, bg="#DCDCE2")
        btn_frame.pack(side="right", padx=8, pady=2)

        for key, item in SCENARIOS.items():
            b = tk.Button(
                btn_frame,
                text=item["name"],
                font=("Segoe UI", 8, "bold"),
                bg="#FFFFFF",
                fg="#111111",
                relief="solid",
                bd=1,
                padx=7,
                pady=2,
                cursor="hand2",
                command=lambda k=key: self.load_scenario(k, record_to_db=True),
            )
            b.pack(side="left", padx=2)

    # =========================================================================
    # BỐ CỤC CHÍNH: THANH ĐIỀU HƯỚNG TRÁI (SIDEBAR 4 TRANG) + KHUNG NỘI DUNG
    # =========================================================================
    def _build_body_with_sidebar(self):
        body = tk.Frame(self.root, bg="#EFEFF2")
        body.pack(fill="both", expand=True, padx=10, pady=6)

        # SIDEBAR ĐIỀU HƯỚNG 4 TRANG (Rộng 200px)
        sidebar = tk.Frame(body, bg="#FFFFFF", bd=2, relief="solid", width=205)
        sidebar.pack(side="left", fill="y", padx=(0, 10))
        sidebar.pack_propagate(False)

        tk.Label(
            sidebar,
            text="ĐIỀU HƯỚNG (MENU)",
            bg="#222222",
            fg="#FFFFFF",
            font=("Consolas", 9, "bold"),
            pady=6,
        ).pack(fill="x")

        nav_items = [
            ("dashboard", "1. Tổng quan & Cây\n   (Main Dashboard)"),
            ("database", "2. Kho Dữ liệu DB\n   (SQLite Explorer)"),
            ("settings", "3. Cấu hình & Nạp FW\n   (Wi-Fi / OTA / Calib)"),
            ("debug", "4. Debug & Soi Lỗi\n   (JSON / Logs / Diag)"),
        ]

        for page_key, label in nav_items:
            btn = tk.Button(
                sidebar,
                text=label,
                font=("Consolas", 9, "bold"),
                bg="#FFFFFF",
                fg="#111111",
                anchor="w",
                justify="left",
                relief="solid",
                bd=1,
                padx=10,
                pady=10,
                cursor="hand2",
                command=lambda pk=page_key: self.switch_page(pk),
            )
            btn.pack(fill="x", padx=8, pady=(8, 0))
            self.nav_buttons[page_key] = btn

        # Gợi ý UX ở đáy Sidebar
        hint = tk.Label(
            sidebar,
            text=(
                "💡 MẸO UX:\n"
                "• Bấm vào Đèn trạng thái trên Header để nhảy nhanh sang trang Debug.\n\n"
                "• Bấm vào Icon cột sóng Wi-Fi để mở trang Cấu hình mạng.\n\n"
                "• Trang chủ giờ đã sạch bóng Log/JSON thô!"
            ),
            bg="#F4F4F6",
            fg="#444444",
            font=("Segoe UI", 8),
            wraplength=175,
            justify="left",
            bd=1,
            relief="solid",
            padx=6,
            pady=8,
        )
        hint.pack(side="bottom", fill="x", padx=8, pady=8)

        # CONTAINER CHỨA 4 TRANG
        self.page_container = tk.Frame(body, bg="#EFEFF2")
        self.page_container.pack(side="left", fill="both", expand=True)

        self.pages["dashboard"] = tk.Frame(self.page_container, bg="#EFEFF2")
        self.pages["database"] = tk.Frame(self.page_container, bg="#EFEFF2")
        self.pages["settings"] = tk.Frame(self.page_container, bg="#EFEFF2")
        self.pages["debug"] = tk.Frame(self.page_container, bg="#EFEFF2")

        self._build_page_1_dashboard(self.pages["dashboard"])
        self._build_page_2_database(self.pages["database"])
        self._build_page_3_settings(self.pages["settings"])
        self._build_page_4_debug(self.pages["debug"])

    def switch_page(self, page_key: str):
        self.current_page = page_key
        titles = {
            "dashboard": "Trang 1: Tổng quan & Chăm sóc (Main Dashboard)",
            "database": "Trang 2: Kho Dữ liệu Lịch sử (SQLite Database Explorer)",
            "settings": "Trang 3: Cấu hình Hệ thống, Wi-Fi & Nạp Firmware (OTA / Serial)",
            "debug": "Trang 4: Chẩn đoán Kỹ thuật, Mã lỗi & Soi gói tin JSON (Developer Debug)",
        }
        self.lbl_page_breadcrumb.configure(text=titles.get(page_key, ""))

        for k, btn in self.nav_buttons.items():
            if k == page_key:
                btn.configure(bg="#222222", fg="#FFFFFF")
            else:
                btn.configure(bg="#FFFFFF", fg="#111111")

        for k, frame in self.pages.items():
            frame.pack_forget()
        self.pages[page_key].pack(fill="both", expand=True)

        if page_key == "dashboard":
            self.refresh_chart_and_db_view()
        elif page_key == "database":
            self.refresh_full_db_table()

    # =========================================================================
    # TRANG 1: TỔNG QUAN & CHĂM SÓC (SẠCH SẼ, KHÔNG CÒN LOG/JSON THÔ)
    # =========================================================================
    def _build_page_1_dashboard(self, parent):
        parent.columnconfigure(0, weight=0, minsize=340)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)

        # Cột trái: OLED + Bình nước/Bơm + Góc nghiêng
        col_left = tk.Frame(parent, bg="#EFEFF2", width=340)
        col_left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        col_left.grid_propagate(False)

        # 1. OLED
        c_oled = ttk.LabelFrame(col_left, text="BIỂU CẢM CHẬU CÂY (OLED MIRROR)", style="Card.TLabelframe")
        c_oled.pack(fill="x", pady=(0, 8), ipady=4)

        oled_box = tk.Frame(c_oled, bg="#111111", bd=3, relief="ridge", height=105)
        oled_box.pack(fill="x", padx=10, pady=(8, 6))
        oled_box.pack_propagate(False)

        self.lbl_oled_eyes = tk.Label(oled_box, text="( ^ ‿ ^ )", bg="#111111", fg="#FFFFFF", font=("Consolas", 24, "bold"))
        self.lbl_oled_eyes.pack(expand=True, pady=(8, 0))
        self.lbl_emotion_code = tk.Label(oled_box, text="STATE: HAPPY_IDLE", bg="#111111", fg="#BBBBBB", font=("Consolas", 9))
        self.lbl_emotion_code.pack(pady=(0, 6))

        self.lbl_speech = tk.Label(
            c_oled,
            text="",
            bg="#E9E9ED",
            fg="#111111",
            font=("Segoe UI", 9, "italic"),
            wraplength=305,
            justify="left",
            bd=1,
            relief="solid",
            padx=8,
            pady=6,
        )
        self.lbl_speech.pack(fill="x", padx=10, pady=(0, 4))

        # 2. Bình nước & Bơm
        c_pump = ttk.LabelFrame(col_left, text="TRẠM THỦY LỢI (BÌNH NƯỚC & BƠM)", style="Card.TLabelframe")
        c_pump.pack(fill="x", pady=(0, 8), ipady=4)

        body_p = tk.Frame(c_pump, bg="#FFFFFF")
        body_p.pack(fill="x", padx=10, pady=6)

        tank_wrap = tk.Frame(body_p, bg="#FFFFFF")
        tank_wrap.pack(side="left", padx=(0, 10))
        self.canvas_tank = tk.Canvas(
            tank_wrap, width=80, height=130, bg="#E9E9ED", bd=1, relief="solid", highlightthickness=2, highlightbackground="#111111", cursor="hand2"
        )
        self.canvas_tank.pack()
        self.canvas_tank.bind("<Button-1>", lambda e: self.select_metric("water_pct"))
        tk.Button(
            tank_wrap, text="[Xem Chart]", font=("Consolas", 7, "bold"), bg="#E6E6EB", relief="solid", bd=1, cursor="hand2", command=lambda: self.select_metric("water_pct")
        ).pack(fill="x", pady=(3, 0))

        ctrl = tk.Frame(body_p, bg="#FFFFFF")
        ctrl.pack(side="left", fill="both", expand=True)
        self.lbl_pump_state = ttk.Label(ctrl, text="Bơm: OFF", style="Mono.TLabel")
        self.lbl_pump_state.pack(anchor="w", pady=2)
        self.lbl_pump_cycle = ttk.Label(ctrl, text="Chu kỳ: #04", style="Mono.TLabel")
        self.lbl_pump_cycle.pack(anchor="w", pady=2)

        ttk.Label(ctrl, text="Chế độ tưới:", style="Sub.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Combobox(
            ctrl, textvariable=self.mode_var, values=["AUTO (Tự động)", "MANUAL (Thủ công)"], state="readonly", font=("Consolas", 9)
        ).pack(fill="x", pady=2)

        self.lbl_thresh = ttk.Label(ctrl, text="Ngưỡng tự tưới: 30%", style="Sub.TLabel")
        self.lbl_thresh.pack(anchor="w", pady=(4, 0))
        tk.Scale(
            ctrl, from_=10, to=80, orient="horizontal", variable=self.thresh_var, showvalue=False, bg="#FFFFFF", highlightthickness=0, command=self._on_threshold_change
        ).pack(fill="x")

        tk.Button(
            ctrl, text="[TƯỚI NGAY / DỪNG]", font=("Consolas", 9, "bold"), bg="#222222", fg="#FFFFFF", relief="solid", bd=1, pady=5, cursor="hand2", command=self.toggle_manual_pump
        ).pack(fill="x", pady=(6, 0))

        # 3. Góc nghiêng & Tương tác vật lý (Chỉ hiện gọn trạng thái, giấu số X,Y,Z thô sang trang Debug hoặc hiện tối giản)
        c_imu = ttk.LabelFrame(col_left, text="CÂN BẰNG VẬT LÝ (TILT SENSOR)", style="Card.TLabelframe")
        c_imu.pack(fill="both", expand=True, ipady=4)

        body_i = tk.Frame(c_imu, bg="#FFFFFF")
        body_i.pack(fill="both", expand=True, padx=10, pady=6)

        self.canvas_radar = tk.Canvas(body_i, width=86, height=86, bg="#E9E9ED", highlightthickness=1, highlightbackground="#111111", cursor="hand2")
        self.canvas_radar.pack(side="left", padx=(0, 12))
        self.canvas_radar.bind("<Button-1>", lambda e: self.select_metric("tilt"))

        info_i = tk.Frame(body_i, bg="#FFFFFF")
        info_i.pack(side="left", fill="both", expand=True)
        self.lbl_tilt_main = ttk.Label(info_i, text="Góc nghiêng: 1.4°", font=("Consolas", 11, "bold"))
        self.lbl_tilt_main.pack(anchor="w", pady=(6, 2))
        self.lbl_tilt_state = ttk.Label(info_i, text="Trạng thái: Đứng vững", style="Sub.TLabel")
        self.lbl_tilt_state.pack(anchor="w", pady=2)
        tk.Button(
            info_i, text="[Xem Biểu đồ Góc nghiêng]", font=("Consolas", 8, "bold"), bg="#E6E6EB", relief="solid", bd=1, cursor="hand2", command=lambda: self.select_metric("tilt")
        ).pack(anchor="w", pady=(8, 0))

        # Cột phải: Chỉ gồm 4 Ô KPI Clickable + Biểu đồ lớn toàn phần dưới (Rất thoáng!)
        col_right = tk.Frame(parent, bg="#EFEFF2")
        col_right.grid(row=0, column=1, sticky="nsew")
        col_right.rowconfigure(0, weight=0)
        col_right.rowconfigure(1, weight=1)
        col_right.columnconfigure(0, weight=1)

        # Hàng 1: 4 Ô KPI
        kpi_wrap = tk.Frame(col_right, bg="#EFEFF2")
        kpi_wrap.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        tk.Label(
            kpi_wrap,
            text="NHẤN VÀO TỪNG Ô CHỈ SỐ ĐỂ XEM BIỂU ĐỒ LỊCH SỬ TƯƠNG ỨNG BÊN DƯỚI ▼",
            bg="#222222",
            fg="#FFFFFF",
            font=("Consolas", 9, "bold"),
            padx=8,
            pady=2,
        ).pack(anchor="w", pady=(0, 4))

        row = tk.Frame(kpi_wrap, bg="#EFEFF2")
        row.pack(fill="x")
        for i in range(4):
            row.columnconfigure(i, weight=1)

        cards_spec = [
            ("soil_pct", "1. ĐỘ ẨM ĐẤT", "62%", "Đủ ẩm"),
            ("light_lux", "2. ÁNH SÁNG", "450 Lux", "Ánh sáng phòng"),
            ("temp_c", "3. NHIỆT ĐỘ", "27.4 °C", "Mát mẻ"),
            ("press_hpa", "4. ÁP SUẤT KQ", "1008.5 hPa", "Khí quyển ổn định"),
        ]

        for idx, (m_key, title, init_val, init_sub) in enumerate(cards_spec):
            pad_x = (0, 5) if idx == 0 else ((5, 0) if idx == 3 else (3, 3))
            card_outer = tk.Frame(row, bg="#111111", bd=2, relief="solid", cursor="hand2")
            card_outer.grid(row=0, column=idx, sticky="nsew", padx=pad_x)

            header = tk.Frame(card_outer, bg="#E6E6EB", padx=8, pady=4)
            header.pack(fill="x")
            lbl_title = tk.Label(header, text=title, bg="#E6E6EB", fg="#111111", font=("Consolas", 9, "bold"))
            lbl_title.pack(side="left")
            lbl_active_tag = tk.Label(header, text="[XEM CHART]", bg="#E6E6EB", fg="#555555", font=("Consolas", 8, "bold"))
            lbl_active_tag.pack(side="right")

            body_c = tk.Frame(card_outer, bg="#FFFFFF", padx=10, pady=8)
            body_c.pack(fill="both", expand=True)
            lbl_val = tk.Label(body_c, text=init_val, bg="#FFFFFF", fg="#111111", font=("Consolas", 20, "bold"), anchor="w")
            lbl_val.pack(fill="x")
            lbl_sub = tk.Label(body_c, text=init_sub, bg="#FFFFFF", fg="#555555", font=("Consolas", 9), anchor="w")
            lbl_sub.pack(fill="x", pady=(3, 0))

            for widget in (card_outer, header, lbl_title, lbl_active_tag, body_c, lbl_val, lbl_sub):
                widget.bind("<Button-1>", lambda e, k=m_key: self.select_metric(k))

            self.kpi_cards[m_key] = {
                "outer": card_outer,
                "header": header,
                "title": lbl_title,
                "tag": lbl_active_tag,
                "val": lbl_val,
                "sub": lbl_sub,
            }

        # Hàng 2: Biểu đồ Lớn (Chiếm toàn bộ không gian còn lại nhờ đã cất Log/JSON sang trang 4)
        chart_card = ttk.LabelFrame(
            col_right,
            text="BIỂU ĐỒ PHÂN TÍCH LỊCH SỬ TỪ DATABASE (CLICK Ô Ở TRÊN ĐỂ ĐỔI TRƯỜNG)",
            style="Card.TLabelframe",
        )
        chart_card.grid(row=1, column=0, sticky="nsew")

        toolbar = tk.Frame(chart_card, bg="#E9E9ED", bd=1, relief="solid", padx=8, pady=6)
        toolbar.pack(fill="x", padx=10, pady=(8, 4))

        self.lbl_chart_metric_title = tk.Label(
            toolbar, text="ĐANG HIỂN THỊ: 1. ĐỘ ẨM ĐẤT", bg="#E9E9ED", fg="#111111", font=("Consolas", 10, "bold")
        )
        self.lbl_chart_metric_title.pack(side="left")

        right_tools = tk.Frame(toolbar, bg="#E9E9ED")
        right_tools.pack(side="right")

        for r_key, r_label in [("7D", "7 Ngày"), ("24H", "24 Giờ"), ("1H", "1 Giờ"), ("LIVE", "LIVE (12 mẫu)")]:
            b = tk.Button(
                right_tools,
                text=r_label,
                font=("Consolas", 8, "bold"),
                bg="#FFFFFF",
                fg="#111111",
                relief="solid",
                bd=1,
                padx=8,
                pady=2,
                cursor="hand2",
                command=lambda rk=r_key: self.set_range_mode(rk),
            )
            b.pack(side="right", padx=2)
            self.range_buttons[r_key] = b

        tk.Label(right_tools, text="Khung thời gian DB:", bg="#E9E9ED", font=("Consolas", 8, "bold")).pack(side="right", padx=4)

        stats_strip = tk.Frame(chart_card, bg="#F4F4F6", bd=1, relief="solid", padx=8, pady=4)
        stats_strip.pack(fill="x", padx=10, pady=(0, 6))
        self.lbl_sql_stats = tk.Label(stats_strip, text="", bg="#F4F4F6", fg="#111111", font=("Consolas", 9), anchor="w")
        self.lbl_sql_stats.pack(side="left", fill="x", expand=True)

        tk.Button(
            stats_strip,
            text="[Mở Trang Bảng Database Chi Tiết ->]",
            font=("Consolas", 8, "bold"),
            bg="#FFFFFF",
            fg="#111111",
            relief="solid",
            bd=1,
            padx=6,
            cursor="hand2",
            command=lambda: self.switch_page("database"),
        ).pack(side="right")

        self.canvas_chart = tk.Canvas(chart_card, bg="#FAFAFC", highlightthickness=1, highlightbackground="#888888")
        self.canvas_chart.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.canvas_chart.bind("<Configure>", lambda e: self.refresh_chart_and_db_view())

    # =========================================================================
    # TRANG 2: KHO DỮ LIỆU SQLITE (DATABASE EXPLORER TOÀN MÀN HÌNH)
    # =========================================================================
    def _build_page_2_database(self, parent):
        card = ttk.LabelFrame(parent, text="TRANG 2: TRÌNH QUẢN LÝ CƠ SỞ DỮ LIỆU SQLITE (plant_telemetry.db)", style="Card.TLabelframe")
        card.pack(fill="both", expand=True)

        top_bar = tk.Frame(card, bg="#E9E9ED", bd=1, relief="solid", padx=10, pady=8)
        top_bar.pack(fill="x", padx=10, pady=8)

        self.lbl_db_page_summary = tk.Label(
            top_bar, text="Đang kết nối: plant_telemetry.db | Bảng: telemetry_logs", bg="#E9E9ED", font=("Consolas", 10, "bold")
        )
        self.lbl_db_page_summary.pack(side="left")

        tk.Button(
            top_bar,
            text="[Xuất dữ liệu ra CSV]",
            font=("Consolas", 9, "bold"),
            bg="#222222",
            fg="#FFFFFF",
            padx=10,
            pady=3,
            cursor="hand2",
            command=lambda: messagebox.showinfo("Xuất CSV", "Đã trích xuất các bản ghi từ bảng telemetry_logs ra file CSV!"),
        ).pack(side="right", padx=4)

        tk.Button(
            top_bar,
            text="[+ Ghi mẫu hiện tại vào DB]",
            font=("Consolas", 9, "bold"),
            bg="#FFFFFF",
            fg="#111111",
            padx=10,
            pady=3,
            cursor="hand2",
            command=lambda: self.load_scenario(self.current_scenario, record_to_db=True),
        ).pack(side="right", padx=4)

        table_wrap = tk.Frame(card, bg="#FFFFFF")
        table_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        cols = ("id", "timestamp", "soil_pct", "soil_adc", "light_lux", "temp_c", "press_hpa", "water_pct", "tilt_deg", "pump_on", "emotion", "err_code", "rssi")
        self.tree_db = ttk.Treeview(table_wrap, columns=cols, show="headings")
        headings = {
            "id": "ID",
            "timestamp": "Thời gian (NTP)",
            "soil_pct": "Ẩm đất (%)",
            "soil_adc": "ADC Raw",
            "light_lux": "Ánh sáng (Lux)",
            "temp_c": "Nhiệt độ (°C)",
            "press_hpa": "Áp suất (hPa)",
            "water_pct": "Bình nước (%)",
            "tilt_deg": "Góc Tilt (°)",
            "pump_on": "Bơm",
            "emotion": "OLED Emotion",
            "err_code": "Mã lỗi",
            "rssi": "RSSI (dBm)",
        }
        for c in cols:
            self.tree_db.heading(c, text=headings[c])
            self.tree_db.column(c, width=75 if c != "timestamp" else 135, anchor="center")

        vsb = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree_db.yview)
        self.tree_db.configure(yscrollcommand=vsb.set)
        self.tree_db.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    # =========================================================================
    # TRANG 3: CẤU HÌNH HỆ THỐNG, WI-FI & NẠP FIRMWARE (OTA / SERIAL)
    # =========================================================================
    def _build_page_3_settings(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        # Ô 1: Cấu hình Wi-Fi & Giao thức kết nối
        c_wifi = ttk.LabelFrame(parent, text="1. CẤU HÌNH MẠNG WI-FI & GIAO THỨC (MQTT / SERIAL)", style="Card.TLabelframe")
        c_wifi.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))

        f_w = tk.Frame(c_wifi, bg="#FFFFFF", padx=14, pady=10)
        f_w.pack(fill="both", expand=True)

        self._form_row(f_w, "Tên Wi-Fi (SSID):", "DuyTruong_IoT_2.4G")
        self._form_row(f_w, "Mật khẩu Wi-Fi:", "********", is_pass=True)
        self._form_row(f_w, "MQTT Broker / IP:", "192.168.1.105:1883")
        self._form_row(f_w, "Cổng USB Serial:", "COM5 (Baudrate: 921600)")
        self._form_row(f_w, "Chu kỳ gửi JSON:", "2000 ms (2 giây/lần)")

        btn_w_row = tk.Frame(f_w, bg="#FFFFFF")
        btn_w_row.pack(fill="x", pady=(10, 0))
        tk.Button(
            btn_w_row, text="[Quét Wi-Fi quanh ESP32]", font=("Consolas", 9, "bold"), bg="#E6E6EB", padx=8, pady=4, command=lambda: messagebox.showinfo("Quét Wi-Fi", "Tìm thấy 4 mạng Wi-Fi 2.4GHz!")
        ).pack(side="left")
        tk.Button(
            btn_w_row, text="[Lưu & Gửi Cấu Hình Xuống ESP32]", font=("Consolas", 9, "bold"), bg="#222222", fg="#FFFFFF", padx=10, pady=4, command=lambda: messagebox.showinfo("Thành công", "Đã gửi cấu hình Wi-Fi xuống NVS Flash của ESP32!")
        ).pack(side="right")

        # Ô 2: Nạp Firmware (OTA / USB Flasher)
        c_fw = ttk.LabelFrame(parent, text="2. NẠP FIRMWARE & CẬP NHẬT OTA (FIRMWARE UPDATE)", style="Card.TLabelframe")
        c_fw.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))

        f_fw = tk.Frame(c_fw, bg="#FFFFFF", padx=14, pady=10)
        f_fw.pack(fill="both", expand=True)

        tk.Label(f_fw, text="Phiên bản hiện tại trên ESP32: v1.4.2-all-in-one (Build: 2026-11-13)", font=("Consolas", 9, "bold")).pack(anchor="w", pady=(0, 8))

        file_row = tk.Frame(f_fw, bg="#FFFFFF")
        file_row.pack(fill="x", pady=4)
        self.ent_fw_path = tk.Entry(file_row, font=("Consolas", 9), bg="#F4F4F6", bd=1, relief="solid")
        self.ent_fw_path.insert(0, "d:/Plant/sensor/.pio/build/all_in_one/firmware.bin")
        self.ent_fw_path.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
        tk.Button(
            file_row, text="[Chọn file .bin]", font=("Consolas", 8, "bold"), bg="#E6E6EB", command=self._browse_firmware
        ).pack(side="right")

        method_row = tk.Frame(f_fw, bg="#FFFFFF")
        method_row.pack(fill="x", pady=6)
        tk.Label(method_row, text="Phương thức nạp:", font=("Segoe UI", 9)).pack(side="left", padx=(0, 8))
        self.fw_method = tk.StringVar(value="OTA")
        tk.Radiobutton(method_row, text="Nạp không dây (Wi-Fi OTA)", variable=self.fw_method, value="OTA", bg="#FFFFFF").pack(side="left", padx=4)
        tk.Radiobutton(method_row, text="Qua cáp USB (UART esptool)", variable=self.fw_method, value="USB", bg="#FFFFFF").pack(side="left", padx=4)

        tk.Label(f_fw, text="Tiến trình nạp Firmware:", font=("Consolas", 9)).pack(anchor="w", pady=(6, 2))
        self.fw_progress = ttk.Progressbar(f_fw, orient="horizontal", mode="determinate", value=0)
        self.fw_progress.pack(fill="x", pady=4)
        self.lbl_fw_status = tk.Label(f_fw, text="Trạng thái: Sẵn sàng nạp.", font=("Consolas", 9), fg="#555555")
        self.lbl_fw_status.pack(anchor="w")

        tk.Button(
            f_fw, text="[⚡ BẮT ĐẦU NẠP FIRMWARE XUỐNG ESP32]", font=("Consolas", 9, "bold"), bg="#222222", fg="#FFFFFF", pady=6, cursor="hand2", command=self._simulate_flash_fw
        ).pack(fill="x", pady=(10, 0))

        # Ô 3: Hiệu chuẩn cảm biến (Sensor Calibration)
        c_cal = ttk.LabelFrame(parent, text="3. HIỆU CHUẨN CẢM BIẾN (ADC ĐẤT & MPU-6050)", style="Card.TLabelframe")
        c_cal.grid(row=1, column=0, sticky="nsew", padx=(0, 6))

        f_cal = tk.Frame(c_cal, bg="#FFFFFF", padx=14, pady=10)
        f_cal.pack(fill="both", expand=True)

        self._form_row(f_cal, "Mốc ADC Đất Khô (0%):", "2850 mV")
        self._form_row(f_cal, "Mốc ADC Đất Ướt (100%):", "1250 mV")
        self._form_row(f_cal, "Góc nghiêng cảnh báo:", "45.0 °")

        cal_btns = tk.Frame(f_cal, bg="#FFFFFF")
        cal_btns.pack(fill="x", pady=(12, 0))
        tk.Button(cal_btns, text="[Lấy mẫu Đất Khô ('d')]", font=("Consolas", 8, "bold"), bg="#E6E6EB", padx=6, pady=4).pack(side="left", padx=(0, 4))
        tk.Button(cal_btns, text="[Lấy mẫu Đất Ướt ('w')]", font=("Consolas", 8, "bold"), bg="#E6E6EB", padx=6, pady=4).pack(side="left", padx=4)
        tk.Button(cal_btns, text="[Zero-Calib MPU6050 ('c')]", font=("Consolas", 8, "bold"), bg="#222222", fg="#FFFFFF", padx=6, pady=4).pack(side="right")

        # Ô 4: Cấu hình Âm thanh & Biểu cảm Pet (OLED + MAX98357A + INMP441)
        c_pet = ttk.LabelFrame(parent, text="4. TUỲ CHỈNH TÍNH CÁCH PET, LOA & MÀN HÌNH OLED", style="Card.TLabelframe")
        c_pet.grid(row=1, column=1, sticky="nsew", padx=(6, 0))

        f_pet = tk.Frame(c_pet, bg="#FFFFFF", padx=14, pady=10)
        f_pet.pack(fill="both", expand=True)

        self._form_row(f_pet, "Độ sáng màn OLED:", "80 %")
        self._form_row(f_pet, "Âm lượng Loa I2S:", "65 %")
        self._form_row(f_pet, "Độ nhạy Mic INMP441:", "Gain 4x")
        self._form_row(f_pet, "Giờ đi ngủ (Tắt đèn):", "23:30 - 06:30")

    def _form_row(self, parent, label_txt, default_val, is_pass=False):
        r = tk.Frame(parent, bg="#FFFFFF")
        r.pack(fill="x", pady=4)
        tk.Label(r, text=label_txt, width=22, anchor="w", font=("Segoe UI", 9, "bold")).pack(side="left")
        e = tk.Entry(r, font=("Consolas", 9), bg="#F4F4F6", bd=1, relief="solid", show="*" if is_pass else "")
        e.insert(0, default_val)
        e.pack(side="left", fill="x", expand=True, ipady=3)

    def _browse_firmware(self):
        path = filedialog.askopenfilename(filetypes=[("ESP32 Firmware Binary", "*.bin"), ("All Files", "*.*")])
        if path:
            self.ent_fw_path.delete(0, "end")
            self.ent_fw_path.insert(0, path)

    def _simulate_flash_fw(self):
        self.lbl_fw_status.configure(text="Đang kết nối ESP32 và nạp các phân vùng Flash...")
        self.fw_progress["value"] = 0
        self._step_flash(0)

    def _step_flash(self, val):
        if val <= 100:
            self.fw_progress["value"] = val
            self.lbl_fw_status.configure(text=f"Đang nạp Firmware ({self.fw_method.get()})... {val}%")
            self.root.after(80, lambda: self._step_flash(val + 10))
        else:
            self.lbl_fw_status.configure(text="✔ Hoàn tất nạp Firmware! ESP32 đang khởi động lại...")
            messagebox.showinfo("Nạp Firmware", "Nạp Firmware thành công (100%)!")

    # =========================================================================
    # TRANG 4: DEBUG & SOI GÓI TIN (GIẤU LOG, JSON THÔ, MÃ LỖI VÀO ĐÂY)
    # =========================================================================
    def _build_page_4_debug(self, parent):
        parent.rowconfigure(0, weight=0)
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)

        # Hàng 1: Bảng thông số chẩn đoán phần cứng chi tiết (từng bị nhồi trên Header cũ)
        diag_card = ttk.LabelFrame(
            parent, text="1. CHẨN ĐOÁN HỆ THỐNG & CẢM BIẾN THÔ (HARDWARE DIAGNOSTICS)", style="Card.TLabelframe"
        )
        diag_card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        diag_grid = tk.Frame(diag_card, bg="#FFFFFF", padx=12, pady=10)
        diag_grid.pack(fill="x")
        for i in range(4):
            diag_grid.columnconfigure(i, weight=1)

        self.lbl_dbg_state = tk.Label(diag_grid, text="Trạng thái cờ: RUNNING", font=("Consolas", 10, "bold"), anchor="w")
        self.lbl_dbg_state.grid(row=0, column=0, sticky="w", pady=3)
        self.lbl_dbg_err = tk.Label(diag_grid, text="Mã lỗi Hex: 0x00", font=("Consolas", 10, "bold"), anchor="w")
        self.lbl_dbg_err.grid(row=0, column=1, sticky="w", pady=3)
        self.lbl_dbg_rssi = tk.Label(diag_grid, text="Wi-Fi RSSI: -54 dBm", font=("Consolas", 10, "bold"), anchor="w")
        self.lbl_dbg_rssi.grid(row=0, column=2, sticky="w", pady=3)
        self.lbl_dbg_uptime = tk.Label(diag_grid, text="Uptime: 04:12:39", font=("Consolas", 10, "bold"), anchor="w")
        self.lbl_dbg_uptime.grid(row=0, column=3, sticky="w", pady=3)

        self.lbl_dbg_adc = tk.Label(diag_grid, text="Soil Raw ADC: 1840", font=("Consolas", 10), anchor="w")
        self.lbl_dbg_adc.grid(row=1, column=0, sticky="w", pady=3)
        self.lbl_dbg_imu = tk.Label(diag_grid, text="IMU X,Y,Z: 0.02, -0.01, 0.99g", font=("Consolas", 10), anchor="w")
        self.lbl_dbg_imu.grid(row=1, column=1, sticky="w", pady=3)
        self.lbl_dbg_ntp = tk.Label(diag_grid, text="NTP Timestamp: --", font=("Consolas", 10), anchor="w")
        self.lbl_dbg_ntp.grid(row=1, column=2, columnspan=2, sticky="w", pady=3)

        # Hàng 2: Cột Trái = Event & SQL Log | Cột Phải = Live Raw JSON Inspector
        log_card = ttk.LabelFrame(parent, text="2. NHẬT KÝ SỰ KIỆN, LỖI & TRUY VẤN SQL (SYSTEM & DEBUG LOG)", style="Card.TLabelframe")
        log_card.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.txt_log = tk.Text(log_card, bg="#F4F4F6", fg="#111111", font=("Consolas", 9), bd=1, relief="solid")
        self.txt_log.pack(fill="both", expand=True, padx=8, pady=8)

        json_card = ttk.LabelFrame(parent, text="3. SOI GÓI TIN JSON THÔ TỪ ESP32 (RAW JSON PACKET INSPECTOR)", style="Card.TLabelframe")
        json_card.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        self.txt_json = tk.Text(json_card, bg="#F4F4F6", fg="#111111", font=("Consolas", 9), bd=1, relief="solid")
        self.txt_json.pack(fill="both", expand=True, padx=8, pady=8)

    # =========================================================================
    # CẬP NHẬT TRẠNG THÁI, ĐÈN LED, CỘT SÓNG WI-FI & BIỂU ĐỒ
    # =========================================================================
    def _draw_status_led_and_wifi(self, sys_state: str, wifi_bars: int, rssi: int):
        """Vẽ Đèn trạng thái (RUNNING / PENDING / ERROR) và Icon 4 vạch sóng Wi-Fi như điện thoại."""
        # 1. Đèn LED trạng thái
        self.canvas_led.delete("all")
        if sys_state == "RUNNING":
            # Đèn sáng trắng viền đậm + dấu chấm tâm
            self.canvas_led.create_oval(2, 2, 14, 14, fill="#FFFFFF", outline="#111111", width=2)
            self.canvas_led.create_oval(5, 5, 11, 11, fill="#111111", outline="")
            self.lbl_led_text.configure(text="RUNNING", fg="#111111", bg="#F4F4F6")
            self.led_frame.configure(bg="#F4F4F6")
        elif sys_state == "PENDING":
            # Đèn nửa sáng nửa tối (Pending / Busy)
            self.canvas_led.create_oval(2, 2, 14, 14, fill="#CCCCCC", outline="#111111", width=2)
            self.canvas_led.create_arc(2, 2, 14, 14, start=90, extent=180, fill="#111111", outline="")
            self.lbl_led_text.configure(text="PENDING", fg="#111111", bg="#DCDCE2")
            self.led_frame.configure(bg="#DCDCE2")
        else:  # ERROR
            # Đèn cảnh báo nền đen chữ trắng nổi bật
            self.canvas_led.configure(bg="#111111")
            self.canvas_led.create_oval(2, 2, 14, 14, fill="#FFFFFF", outline="#FFFFFF", width=1)
            self.canvas_led.create_text(8, 8, text="!", font=("Consolas", 9, "bold"), fill="#111111")
            self.lbl_led_text.configure(text="ERROR!", fg="#FFFFFF", bg="#111111")
            self.led_frame.configure(bg="#111111")

        # 2. Vẽ 4 cột sóng Wi-Fi tăng dần chiều cao (giống thanh trạng thái điện thoại)
        self.canvas_wifi.delete("all")
        bar_heights = [5, 9, 13, 17]
        for i in range(4):
            x0 = 2 + i * 6
            x1 = x0 + 4
            y1 = 18
            y0 = y1 - bar_heights[i]
            if i < wifi_bars:
                self.canvas_wifi.create_rectangle(x0, y0, x1, y1, fill="#111111", outline="#111111")
            else:
                self.canvas_wifi.create_rectangle(x0, y0, x1, y1, fill="#FFFFFF", outline="#888888")

    def select_metric(self, metric_key: str):
        self.active_metric = metric_key
        for k, widgets in self.kpi_cards.items():
            is_selected = k == metric_key
            if is_selected:
                widgets["header"].configure(bg="#111111")
                widgets["title"].configure(bg="#111111", fg="#FFFFFF")
                widgets["tag"].configure(text="[▼ ĐANG HIỆN]", bg="#111111", fg="#FFFFFF")
                widgets["outer"].configure(bd=3)
            else:
                widgets["header"].configure(bg="#E6E6EB")
                widgets["title"].configure(bg="#E6E6EB", fg="#111111")
                widgets["tag"].configure(text="[XEM CHART]", bg="#E6E6EB", fg="#555555")
                widgets["outer"].configure(bd=1)

        cfg = METRICS_CONFIG[metric_key]
        self.lbl_chart_metric_title.configure(text=f"ĐANG HIỂN THỊ: {cfg['title']} ({cfg['sensor_name']})")
        self._append_log(f"UI CLICK: Chuyển biểu đồ sang [{cfg['db_col']}]")
        self.refresh_chart_and_db_view()

    def set_range_mode(self, range_key: str):
        self.range_mode = range_key
        for k, btn in self.range_buttons.items():
            btn.configure(bg="#111111" if k == range_key else "#FFFFFF", fg="#FFFFFF" if k == range_key else "#111111")
        self.refresh_chart_and_db_view()

    def _on_threshold_change(self, val):
        v = int(float(val))
        self.lbl_thresh.configure(text=f"Ngưỡng tự tưới: {v}%")
        METRICS_CONFIG["soil_pct"]["threshold"] = v
        METRICS_CONFIG["soil_pct"]["threshold_label"] = f"Ngưỡng tự tưới ({v}%)"
        if self.active_metric == "soil_pct":
            self.refresh_chart_and_db_view()

    def load_scenario(self, key: str, record_to_db: bool = True):
        self.current_scenario = key
        d = SCENARIOS[key]
        now_dt = datetime.datetime.now()
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        if record_to_db:
            self.db.insert_record(d, now_str)
            self._append_log(f"SQLite INSERT: Lưu mẫu ({d['name']}) | State={d['sys_state']}")

        # Cập nhật Top Bar thông minh (Đèn LED + Cột sóng Wi-Fi + Giờ HH:MM)
        self.lbl_clock.configure(text=now_dt.strftime("%H:%M"))
        self._draw_status_led_and_wifi(d["sys_state"], d["wifi_bars"], d["rssi"])

        # Cập nhật Trang 1 (Dashboard)
        self.lbl_oled_eyes.configure(text=d["oled_eyes"])
        self.lbl_emotion_code.configure(text=f"STATE: {d['emotion_code']}")
        self.lbl_speech.configure(text=d["speech"])

        self._draw_tank(int(d["water_pct"]))
        pump_txt = "[ ON - ĐANG TƯỚI ]" if d["pump_on"] else "[ OFF - ĐANG NGHỈ ]"
        self.lbl_pump_state.configure(text=f"Bơm   : {pump_txt}")
        self.lbl_pump_cycle.configure(text=f"Chu kỳ: #{d['pump_cycle']:02d} (Hôm nay)")

        self.lbl_tilt_main.configure(text=f"Góc nghiêng: {d['tilt']:.1f}°")
        self.lbl_tilt_state.configure(text="CẢNH BÁO NGÃ!" if d["tilt"] > 45 else "Trạng thái: Đứng vững")
        self._draw_radar(d["ax"], d["ay"])

        self.kpi_cards["soil_pct"]["val"].configure(text=f"{int(d['soil_pct'])}%")
        self.kpi_cards["soil_pct"]["sub"].configure(text=d["soil_status"])
        self.kpi_cards["light_lux"]["val"].configure(text=f"{int(d['light_lux'])} Lux")
        self.kpi_cards["light_lux"]["sub"].configure(text=d["light_status"])
        self.kpi_cards["temp_c"]["val"].configure(text=f"{d['temp_c']:.1f} °C")
        self.kpi_cards["press_hpa"]["val"].configure(text=f"{d['press_hpa']:.1f} hPa")

        # Cập nhật Trang 4 (Debug Console)
        self.lbl_dbg_state.configure(text=f"Trạng thái cờ: {d['sys_state']}")
        self.lbl_dbg_err.configure(text=f"Mã lỗi: {d['err_code']}")
        self.lbl_dbg_rssi.configure(text=f"Wi-Fi RSSI: {d['rssi']} dBm ({d['wifi_bars']}/4 vạch)")
        self.lbl_dbg_uptime.configure(text=f"Uptime: {d['uptime']}")
        self.lbl_dbg_adc.configure(text=f"Soil Raw ADC: {d['soil_adc']} (Quy đổi: {d['soil_pct']}%)")
        self.lbl_dbg_imu.configure(text=f"IMU X,Y,Z: {d['ax']:+.2f}, {d['ay']:+.2f}, {d['az']:+.2f} g")
        self.lbl_dbg_ntp.configure(text=f"NTP Timestamp: {now_str}")

        payload = {
            "timestamp": now_str,
            "soil_moisture": {"adc": d["soil_adc"], "percent": d["soil_pct"]},
            "light_intensity_lux": d["light_lux"],
            "temp_pressure": {"temp_c": d["temp_c"], "pressure_hpa": d["press_hpa"]},
            "acceleration": {"x": d["ax"], "y": d["ay"], "z": d["az"], "tilt_deg": d["tilt"]},
            "water_level_pct": d["water_pct"],
            "pump_status": {"on": bool(d["pump_on"]), "cycle": d["pump_cycle"]},
            "plant_emotion": d["emotion_code"],
            "system_status": {"state": d["sys_state"], "err": d["err_code"], "rssi": d["rssi"], "uptime": d["uptime"]},
        }
        self.txt_json.delete("1.0", "end")
        self.txt_json.insert("1.0", json.dumps(payload, indent=2, ensure_ascii=False))

        self.set_range_mode(self.range_mode)
        if self.current_page == "database":
            self.refresh_full_db_table()

    def toggle_manual_pump(self):
        if self.current_scenario == "watering":
            self.load_scenario("normal", record_to_db=True)
        else:
            self.load_scenario("watering", record_to_db=True)

    def _append_log(self, msg: str):
        now_t = datetime.datetime.now().strftime("%H:%M:%S")
        self.txt_log.insert("1.0", f"[{now_t}] {msg}\n")

    def refresh_full_db_table(self):
        for item in self.tree_db.get_children():
            self.tree_db.delete(item)
        rows = self.db.query_recent_rows(100)
        for r in rows:
            self.tree_db.insert("", "end", values=r)

    def refresh_chart_and_db_view(self):
        cfg = METRICS_CONFIG[self.active_metric]
        col = cfg["db_col"]
        unit = cfg["unit"]

        rows, stats = self.db.query_metric_series(col, self.range_mode)
        total_cnt, min_v, max_v, avg_v = stats

        self.btn_db_pill.configure(text=f"🗄 DB: {total_cnt}")
        self.lbl_sql_stats.configure(
            text=f"SQLite [{col}] -> MIN: {min_v:.1f} {unit}  |  MAX: {max_v:.1f} {unit}  |  TRUNG BÌNH (AVG): {avg_v:.1f} {unit}  |  Tổng: {total_cnt} bản ghi"
        )

        c = self.canvas_chart
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 80 or h < 80 or not rows:
            return

        pad_l, pad_r, pad_t, pad_b = 58, 24, 24, 34
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b

        y_min, y_max = cfg["y_min"], cfg["y_max"]
        span = max(1e-5, y_max - y_min)

        def val_to_y(v):
            clamped = max(y_min, min(y_max, v))
            return pad_t + plot_h - int(((clamped - y_min) / span) * plot_h)

        for step in range(5):
            val_tick = y_min + (span * step / 4.0)
            y = val_to_y(val_tick)
            c.create_line(pad_l, y, w - pad_r, y, fill="#E0E0E5")
            lbl_tick = f"{val_tick:.0f} {unit}" if span >= 20 else f"{val_tick:.1f} {unit}"
            c.create_text(pad_l - 6, y, text=lbl_tick, anchor="e", font=("Consolas", 8), fill="#444444")

        th_val = cfg["threshold"]
        if y_min <= th_val <= y_max:
            y_th = val_to_y(th_val)
            c.create_line(pad_l, y_th, w - pad_r, y_th, fill="#333333", dash=(5, 3), width=1.5)
            c.create_text(pad_l + 10, y_th - 8, text=cfg["threshold_label"], anchor="w", font=("Consolas", 8, "bold"), fill="#111111")

        coords = []
        n = len(rows)
        show_val_text = n <= 16

        for idx, (rec_id, ts_str, val, pump_on, emotion) in enumerate(rows):
            x = pad_l + int((idx / max(1, n - 1)) * plot_w)
            y = val_to_y(val)
            coords.extend([x, y])
            if idx == 0 or idx == n - 1 or idx % max(1, n // 6) == 0:
                short_ts = ts_str.split()[1][:5] if " " in ts_str else ts_str
                c.create_text(x, pad_t + plot_h + 14, text=short_ts, font=("Consolas", 8), fill="#444444")

        if len(coords) >= 4:
            poly_coords = [coords[0], pad_t + plot_h] + coords + [coords[-2], pad_t + plot_h]
            c.create_polygon(*poly_coords, fill="#E4E4EA", outline="")
            c.create_line(*coords, fill="#111111", width=2.5)

        for idx, (rec_id, ts_str, val, pump_on, emotion) in enumerate(rows):
            x, y = coords[idx * 2], coords[idx * 2 + 1]
            c.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#FFFFFF", outline="#111111", width=2)
            if show_val_text:
                v_str = f"{val:.0f}" if span >= 50 else f"{val:.1f}"
                c.create_text(x, y - 11, text=v_str, font=("Consolas", 8, "bold"), fill="#111111")

    def _draw_tank(self, pct: int):
        c = self.canvas_tank
        c.delete("all")
        w, h = 80, 130
        fill_h = int((pct / 100.0) * h)
        c.create_rectangle(0, h - fill_h, w, h, fill="#A8A8B2", outline="#111111", width=2)
        c.create_rectangle(8, 45, 72, 82, fill="#FFFFFF", outline="#111111")
        c.create_text(40, 58, text=f"{pct}%", font=("Consolas", 12, "bold"), fill="#111111")
        c.create_text(40, 72, text="NƯỚC", font=("Consolas", 7, "bold"), fill="#333333")

    def _draw_radar(self, ax: float, ay: float):
        c = self.canvas_radar
        c.delete("all")
        cx, cy, r = 43, 43, 36
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#111111", width=2, fill="#E9E9ED")
        c.create_line(cx - r, cy, cx + r, cy, fill="#888888", dash=(2, 2))
        c.create_line(cx, cy - r, cx, cy + r, fill="#888888", dash=(2, 2))
        dx = max(-25, min(25, int(ax * 32)))
        dy = max(-25, min(25, int(-ay * 32)))
        c.create_oval(cx + dx - 6, cy + dy - 6, cx + dx + 6, cy + dy + 6, fill="#111111")


if __name__ == "__main__":
    root = tk.Tk()
    app = SmartPlantMultiPageApp(root)
    root.mainloop()
