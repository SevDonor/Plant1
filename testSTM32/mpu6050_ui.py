"""
ESP32-S3 Multi-Sensor Status Monitor UI
Ho tro 2 che do chuyen doi trong cung giao dien:
  1. MPU6050 (Gia toc, Gyro, Goc nghieng 3D, Tu ngu/Thuc)
  2. Moi truong & Anh sang (AHT20 + BMP280 + BH1750)
"""

from __future__ import annotations

import json
import math
import queue
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk

try:
    import serial
    import serial.tools.list_ports
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Thieu thu vien 'pyserial'. Hay chay: pip install pyserial"
    ) from exc


MAX_HISTORY = 180
MAX_ENV_HISTORY = 120

CUBE_VERTICES = [
    (-1.2, -0.7, -0.35),
    (1.2, -0.7, -0.35),
    (1.2, 0.7, -0.35),
    (-1.2, 0.7, -0.35),
    (-1.2, -0.7, 0.35),
    (1.2, -0.7, 0.35),
    (1.2, 0.7, 0.35),
    (-1.2, 0.7, 0.35),
]

CUBE_EDGES = [
    (0, 1), (1, 2), (2, 3), (3, 0),
    (4, 5), (5, 6), (6, 7), (7, 4),
    (0, 4), (1, 5), (2, 6), (3, 7),
]

CUBE_FACES = [
    (4, 5, 6, 7, "#1f6feb"),  # Top (+Z)
    (0, 1, 2, 3, "#1f2937"),  # Bottom (-Z)
    (1, 2, 6, 5, "#238636"),  # Front (+X)
]

KNOWN_I2C_NAMES = {
    "0x23": "BH1750",
    "0x5C": "BH1750",
    "0x38": "AHT20",
    "0x68": "MPU6050",
    "0x69": "MPU6050",
    "0x76": "BMP280",
    "0x77": "BMP280",
}


class SerialReaderThread(threading.Thread):
    """Doc du lieu JSON theo dong tu cong Serial trong luong nen."""

    def __init__(self, ser: serial.Serial, out_queue: queue.Queue):
        super().__init__(daemon=True)
        self.ser = ser
        self.out_queue = out_queue
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                raw_line = self.ser.readline()
                if not raw_line:
                    continue
                text = raw_line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                self.out_queue.put(("line", text))
            except serial.SerialException as err:
                self.out_queue.put(("serial_err", str(err)))
                break
            except Exception as err:  # pragma: no cover
                self.out_queue.put(("serial_err", str(err)))
                break


class Mpu6050MonitorApp:
    """Ung dung Tkinter hien thi trang thai MPU6050 va Moi truong/Anh sang."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ESP32-S3 Multi-Sensor Monitor (MPU6050 + AHT20 + BMP280 + BH1750)")
        self.root.geometry("1200x800")
        self.root.minsize(1000, 680)
        self.root.configure(bg="#0d1117")

        self.ser: serial.Serial | None = None
        self.reader: SerialReaderThread | None = None
        self.rx_queue: queue.Queue = queue.Queue()
        self.port_map: dict[str, str] = {}

        self.packet_count = 0
        self.env_packet_count = 0
        self.last_rx_time = 0.0
        self.last_error_log_key = ""
        self.last_error_log_ts = 0.0
        self.device_sleeping = False
        self.waiting_wake_reconnect = False
        self.connected_device = ""

        # Active view: "MPU" or "ENV"
        self.active_view = "MPU"

        # MPU history
        self.hist_ax = deque([0.0] * MAX_HISTORY, maxlen=MAX_HISTORY)
        self.hist_ay = deque([0.0] * MAX_HISTORY, maxlen=MAX_HISTORY)
        self.hist_az = deque([1.0] * MAX_HISTORY, maxlen=MAX_HISTORY)
        self.hist_gx = deque([0.0] * MAX_HISTORY, maxlen=MAX_HISTORY)
        self.hist_gy = deque([0.0] * MAX_HISTORY, maxlen=MAX_HISTORY)
        self.hist_gz = deque([0.0] * MAX_HISTORY, maxlen=MAX_HISTORY)
        self.hist_roll = deque([0.0] * MAX_HISTORY, maxlen=MAX_HISTORY)
        self.hist_pitch = deque([0.0] * MAX_HISTORY, maxlen=MAX_HISTORY)
        self.hist_yaw = deque([0.0] * MAX_HISTORY, maxlen=MAX_HISTORY)

        # Environment & Light history
        self.hist_aht_t = deque(maxlen=MAX_ENV_HISTORY)
        self.hist_bmp_t = deque(maxlen=MAX_ENV_HISTORY)
        self.hist_hum = deque(maxlen=MAX_ENV_HISTORY)
        self.hist_press = deque(maxlen=MAX_ENV_HISTORY)
        self.hist_lux = deque(maxlen=MAX_ENV_HISTORY)

        self.graph_mode = tk.StringVar(value="accel")
        self.env_graph_mode = tk.StringVar(value="temp")
        self.pause_graph = tk.BooleanVar(value=False)
        self.pause_env_graph = tk.BooleanVar(value=False)
        self.rate_var = tk.StringVar(value="25")
        self.auto_sleep_var = tk.BooleanVar(value=True)
        self.sleep_delay_var = tk.StringVar(value="5")
        self.sleep_mode_var = tk.StringVar(value="LIGHT")
        self.baud_var = tk.StringVar(value="115200")
        self.port_var = tk.StringVar()

        self._setup_styles()
        self._build_ui()
        self.refresh_ports()
        self.switch_view("MPU", send_cmd=False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(25, self._poll_queue)
        self.root.after(250, self._check_watchdog)
        self.root.after(600, self._auto_reconnect_loop)

    def _setup_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "TCombobox",
            fieldbackground="#0d1117",
            background="#21262d",
            foreground="#e6edf3",
            arrowcolor="#e6edf3",
            bordercolor="#30363d",
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#0d1117")],
            foreground=[("readonly", "#e6edf3")],
            selectbackground=[("readonly", "#1f6feb")],
            selectforeground=[("readonly", "#ffffff")],
        )

    def _build_ui(self) -> None:
        # 1. Top connection bar
        top = tk.Frame(self.root, bg="#161b22", padx=12, pady=9, highlightthickness=1, highlightbackground="#30363d")
        top.pack(fill="x", padx=10, pady=(10, 6))

        tk.Label(top, text="Cổng COM:", bg="#161b22", fg="#e6edf3", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=36, state="readonly")
        self.port_combo.pack(side="left", padx=(6, 6))

        tk.Button(
            top, text="Làm mới", command=self.refresh_ports,
            bg="#21262d", fg="#e6edf3", relief="flat", padx=10, pady=4, cursor="hand2"
        ).pack(side="left", padx=(0, 10))

        tk.Label(top, text="Baud:", bg="#161b22", fg="#e6edf3", font=("Segoe UI", 10)).pack(side="left")
        baud_combo = ttk.Combobox(top, textvariable=self.baud_var, values=["115200", "921600", "9600"], width=8, state="readonly")
        baud_combo.pack(side="left", padx=(4, 10))

        self.connect_btn = tk.Button(
            top, text="Kết nối", command=self.toggle_connection,
            bg="#238636", fg="#ffffff", font=("Segoe UI", 10, "bold"),
            relief="flat", padx=14, pady=4, cursor="hand2"
        )
        self.connect_btn.pack(side="left", padx=(0, 10))

        self.status_badge = tk.Label(
            top, text="● CHƯA KẾT NỐI", bg="#30363d", fg="#c9d1d9",
            font=("Segoe UI", 10, "bold"), padx=12, pady=4
        )
        self.status_badge.pack(side="right")

        # 2. Mode switcher bar
        mode_bar = tk.Frame(self.root, bg="#161b22", padx=12, pady=7, highlightthickness=1, highlightbackground="#30363d")
        mode_bar.pack(fill="x", padx=10, pady=(0, 6))

        tk.Label(mode_bar, text="CHỨC NĂNG:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 8))

        self.btn_mode_mpu = tk.Button(
            mode_bar, text="MPU6050 (Chuyển động 3D)",
            command=lambda: self.switch_view("MPU"),
            font=("Segoe UI", 9, "bold"), relief="flat", padx=12, pady=4, cursor="hand2"
        )
        self.btn_mode_mpu.pack(side="left", padx=(0, 6))

        self.btn_mode_env = tk.Button(
            mode_bar, text="Môi trường & Ánh sáng (AHT20 + BMP280 + BH1750)",
            command=lambda: self.switch_view("ENV"),
            font=("Segoe UI", 9, "bold"), relief="flat", padx=12, pady=4, cursor="hand2"
        )
        self.btn_mode_env.pack(side="left", padx=(0, 10))

        self.btn_toggle_mode = tk.Button(
            mode_bar, text="⇄ Chuyển đổi nhanh",
            command=self.toggle_view_mode,
            bg="#8957e5", fg="#ffffff", font=("Segoe UI", 9, "bold"),
            relief="flat", padx=12, pady=4, cursor="hand2"
        )
        self.btn_toggle_mode.pack(side="left")

        tk.Button(
            mode_bar, text="Quét I2C (SCAN)", command=lambda: self.send_command("SCAN"),
            bg="#1f6feb", fg="#ffffff", font=("Segoe UI", 9, "bold"),
            relief="flat", padx=11, pady=4, cursor="hand2"
        ).pack(side="right")

        # 3. View container (swaps between MPU6050 view and Environment/Light view)
        self.view_container = tk.Frame(self.root, bg="#0d1117")
        self.view_container.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.mpu_view = tk.Frame(self.view_container, bg="#0d1117")
        self.env_view = tk.Frame(self.view_container, bg="#0d1117")

        self._build_mpu_view(self.mpu_view)
        self._build_env_view(self.env_view)

        # 4. Shared Log console
        log_card = tk.Frame(self.root, bg="#161b22", height=135, highlightthickness=1, highlightbackground="#30363d")
        log_card.pack(fill="x", padx=10, pady=(0, 10))
        log_card.pack_propagate(False)

        l_hdr = tk.Frame(log_card, bg="#161b22", padx=10, pady=4)
        l_hdr.pack(fill="x")
        tk.Label(l_hdr, text="NHẬT KÝ & CHẨN ĐOÁN", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Button(
            l_hdr, text="Xóa log", command=self.clear_log,
            bg="#21262d", fg="#e6edf3", relief="flat", padx=8, pady=1, font=("Segoe UI", 8)
        ).pack(side="right")

        self.log_text = tk.Text(
            log_card, bg="#0d1117", fg="#c9d1d9", font=("Consolas", 9),
            relief="flat", state="disabled", wrap="word"
        )
        self.log_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._draw_cube(0.0, 0.0, 0.0)
        self._redraw_graph()
        self._redraw_env_graph()
        self.append_log("Sẵn sàng. Chọn cổng COM của ESP32-S3 rồi bấm 'Kết nối'.")

    def _build_mpu_view(self, parent: tk.Frame) -> None:
        # Info chips
        info_bar = tk.Frame(parent, bg="#161b22", padx=12, pady=6, highlightthickness=1, highlightbackground="#30363d")
        info_bar.pack(fill="x", pady=(0, 6))

        self.lbl_addr = self._make_chip(info_bar, "I2C Addr: --")
        self.lbl_whoami = self._make_chip(info_bar, "WHO_AM_I: --")
        self.lbl_temp = self._make_chip(info_bar, "Nhiệt độ: -- °C")
        self.lbl_hz = self._make_chip(info_bar, "Tốc độ: -- Hz")
        self.lbl_cal = self._make_chip(info_bar, "Calib: --")
        self.lbl_motion = self._make_chip(info_bar, "Trạng thái: --")
        self.lbl_pkt = self._make_chip(info_bar, "Gói tin: 0")

        # Controls + Sleep bar
        ctrl_bar = tk.Frame(parent, bg="#161b22", padx=12, pady=6, highlightthickness=1, highlightbackground="#30363d")
        ctrl_bar.pack(fill="x", pady=(0, 6))

        cmd_frame = tk.Frame(ctrl_bar, bg="#161b22")
        cmd_frame.pack(side="left")

        tk.Label(cmd_frame, text="Hz:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9)).pack(side="left")
        rate_cb = ttk.Combobox(cmd_frame, textvariable=self.rate_var, values=["10", "25", "50"], width=4, state="readonly")
        rate_cb.pack(side="left", padx=(3, 8))
        rate_cb.bind("<<ComboboxSelected>>", lambda _e: self.send_command(f"RATE={self.rate_var.get()}"))

        for label, cmd, color in [
            ("Hiệu chuẩn 0 (CAL)", "CAL", "#8957e5"),
            ("Reset Yaw", "RESET_YAW", "#21262d"),
        ]:
            tk.Button(
                cmd_frame, text=label, command=lambda c=cmd: self.send_command(c),
                bg=color, fg="#ffffff", font=("Segoe UI", 9, "bold"),
                relief="flat", padx=10, pady=3, cursor="hand2"
            ).pack(side="left", padx=3)

        sleep_frame = tk.Frame(ctrl_bar, bg="#161b22")
        sleep_frame.pack(side="right")

        tk.Checkbutton(
            sleep_frame, text="Tự ngủ khi đứng yên", variable=self.auto_sleep_var,
            command=self._on_toggle_sleep,
            bg="#161b22", fg="#e6edf3", selectcolor="#0d1117",
            activebackground="#161b22", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=(0, 8))

        tk.Label(sleep_frame, text="Sau (s):", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9)).pack(side="left")
        delay_cb = ttk.Combobox(sleep_frame, textvariable=self.sleep_delay_var, values=["3", "5", "10", "15", "30"], width=4, state="readonly")
        delay_cb.pack(side="left", padx=(4, 8))
        delay_cb.bind("<<ComboboxSelected>>", lambda _e: self.send_command(f"SLEEP_DELAY={self.sleep_delay_var.get()}"))

        tk.Label(sleep_frame, text="Ngủ:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9)).pack(side="left")
        mode_cb = ttk.Combobox(sleep_frame, textvariable=self.sleep_mode_var, values=["LIGHT", "DEEP"], width=6, state="readonly")
        mode_cb.pack(side="left", padx=(4, 8))
        mode_cb.bind("<<ComboboxSelected>>", lambda _e: self.send_command(f"SLEEP_MODE={self.sleep_mode_var.get()}"))

        tk.Button(
            sleep_frame, text="Đánh thức", command=lambda: self.send_command("WAKE"),
            bg="#21262d", fg="#e6edf3", font=("Segoe UI", 9),
            relief="flat", padx=9, pady=3, cursor="hand2"
        ).pack(side="left")

        # Main split area
        main = tk.Frame(parent, bg="#0d1117")
        main.pack(fill="both", expand=True)

        left = tk.Frame(main, bg="#0d1117", width=395)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)

        cube_card = tk.Frame(left, bg="#161b22", highlightthickness=1, highlightbackground="#30363d")
        cube_card.pack(fill="x", pady=(0, 6))
        tk.Label(
            cube_card, text="MÔ PHỎNG HƯỚNG NGHIÊNG 3D (MPU6050)",
            bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold"), pady=5
        ).pack()
        self.cube_canvas = tk.Canvas(cube_card, width=375, height=205, bg="#0d1117", highlightthickness=0)
        self.cube_canvas.pack(padx=8, pady=(0, 6))

        self.val_labels: dict[str, tk.Label] = {}
        self._build_metric_card(left, "GIA TỐC (ACCEL - g)", [("ax", "AX", "#ff7b72"), ("ay", "AY", "#7ee787"), ("az", "AZ", "#79c0ff"), ("amag", "|A|", "#d2a8ff")])
        self._build_metric_card(left, "CON QUAY HỒI CHUYỂN (GYRO - °/s)", [("gx", "GX", "#ff7b72"), ("gy", "GY", "#7ee787"), ("gz", "GZ", "#79c0ff")])
        self._build_metric_card(left, "GÓC NGHIÊNG (ORIENTATION - °)", [("roll", "Roll", "#ffa657"), ("pitch", "Pitch", "#79c0ff"), ("yaw", "Yaw", "#7ee787")])

        right = tk.Frame(main, bg="#0d1117")
        right.pack(side="left", fill="both", expand=True)

        graph_card = tk.Frame(right, bg="#161b22", highlightthickness=1, highlightbackground="#30363d")
        graph_card.pack(fill="both", expand=True)

        g_hdr = tk.Frame(graph_card, bg="#161b22", padx=10, pady=6)
        g_hdr.pack(fill="x")
        tk.Label(g_hdr, text="ĐỒ THỊ:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(side="left")

        for text, mode in [("Gia tốc (g)", "accel"), ("Gyro (°/s)", "gyro"), ("Góc (°)", "angle")]:
            tk.Radiobutton(
                g_hdr, text=text, variable=self.graph_mode, value=mode,
                command=self._redraw_graph,
                bg="#161b22", fg="#e6edf3", selectcolor="#0d1117",
                activebackground="#161b22", activeforeground="#ffffff",
                font=("Segoe UI", 9)
            ).pack(side="left", padx=(6, 0))

        tk.Button(
            g_hdr, text="Xóa đồ thị", command=self.clear_history,
            bg="#21262d", fg="#e6edf3", relief="flat", padx=8, pady=2, font=("Segoe UI", 9)
        ).pack(side="right", padx=(6, 0))

        tk.Checkbutton(
            g_hdr, text="Tạm dừng", variable=self.pause_graph,
            bg="#161b22", fg="#e6edf3", selectcolor="#0d1117",
            activebackground="#161b22", activeforeground="#ffffff",
            font=("Segoe UI", 9)
        ).pack(side="right")

        self.graph_canvas = tk.Canvas(graph_card, bg="#0d1117", highlightthickness=0)
        self.graph_canvas.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.graph_canvas.bind("<Configure>", lambda _e: self._redraw_graph())

    def _build_env_view(self, parent: tk.Frame) -> None:
        # Environment sensor health bar
        env_bar = tk.Frame(parent, bg="#161b22", padx=12, pady=7, highlightthickness=1, highlightbackground="#30363d")
        env_bar.pack(fill="x", pady=(0, 6))

        self.lbl_aht_status = self._make_chip(env_bar, "AHT20 (0x38): --")
        self.lbl_bmp_status = self._make_chip(env_bar, "BMP280 (0x76/0x77): --")
        self.lbl_bh_status = self._make_chip(env_bar, "BH1750 (0x23/0x5C): --")
        self.lbl_env_pkt = self._make_chip(env_bar, "Gói Env: 0")

        tk.Label(
            env_bar,
            text="Đấu chung bus I2C: SDA=GPIO8, SCL=GPIO9, VCC=3V3, GND",
            bg="#161b22", fg="#8b949e", font=("Segoe UI", 8)
        ).pack(side="right")

        main = tk.Frame(parent, bg="#0d1117")
        main.pack(fill="both", expand=True)

        left = tk.Frame(main, bg="#0d1117", width=410)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)

        self.env_val_labels: dict[str, tk.Label] = {}

        # AHT20 card
        aht_card = tk.Frame(left, bg="#161b22", padx=12, pady=10, highlightthickness=1, highlightbackground="#30363d")
        aht_card.pack(fill="x", pady=(0, 8))
        tk.Label(aht_card, text="CẢM BIẾN NHIỆT ĐỘ & ĐỘ ẨM (AHT20 - 0x38)", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
        aht_row = tk.Frame(aht_card, bg="#161b22")
        aht_row.pack(fill="x")
        self._add_env_cell(aht_row, "aht_t", "Nhiệt độ", "-- °C", "#ff7b72")
        self._add_env_cell(aht_row, "aht_h", "Độ ẩm", "-- %", "#79c0ff")
        self.lbl_hum_level = tk.Label(aht_card, text="Mức độ ẩm: --", bg="#161b22", fg="#c9d1d9", font=("Segoe UI", 9), pady=4)
        self.lbl_hum_level.pack(anchor="w")

        # BMP280 card
        bmp_card = tk.Frame(left, bg="#161b22", padx=12, pady=10, highlightthickness=1, highlightbackground="#30363d")
        bmp_card.pack(fill="x", pady=(0, 8))
        tk.Label(bmp_card, text="CẢM BIẾN ÁP SUẤT & ĐỘ CAO (BMP280 - 0x76/0x77)", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
        bmp_row = tk.Frame(bmp_card, bg="#161b22")
        bmp_row.pack(fill="x")
        self._add_env_cell(bmp_row, "bmp_p", "Áp suất", "-- hPa", "#7ee787")
        self._add_env_cell(bmp_row, "bmp_t", "Nhiệt độ BMP", "-- °C", "#ffa657")
        self._add_env_cell(bmp_row, "bmp_alt", "Độ cao (ước tính)", "-- m", "#d2a8ff")

        # BH1750 card
        bh_card = tk.Frame(left, bg="#161b22", padx=12, pady=10, highlightthickness=1, highlightbackground="#30363d")
        bh_card.pack(fill="both", expand=True)
        tk.Label(bh_card, text="CẢM BIẾN ÁNH SÁNG (BH1750 - 0x23/0x5C)", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
        bh_row = tk.Frame(bh_card, bg="#161b22")
        bh_row.pack(fill="x")
        self._add_env_cell(bh_row, "lux", "Cường độ sáng (Lux)", "-- lx", "#f2cc60")
        self.lbl_lux_level = tk.Label(bh_card, text="Mức ánh sáng: --", bg="#161b22", fg="#c9d1d9", font=("Segoe UI", 9), pady=6)
        self.lbl_lux_level.pack(anchor="w")
        self.lux_bar_canvas = tk.Canvas(bh_card, height=18, bg="#0d1117", highlightthickness=1, highlightbackground="#30363d")
        self.lux_bar_canvas.pack(fill="x", pady=(2, 0))

        # Right: Environment & Light waveform graph
        right = tk.Frame(main, bg="#0d1117")
        right.pack(side="left", fill="both", expand=True)

        env_graph_card = tk.Frame(right, bg="#161b22", highlightthickness=1, highlightbackground="#30363d")
        env_graph_card.pack(fill="both", expand=True)

        ehdr = tk.Frame(env_graph_card, bg="#161b22", padx=10, pady=6)
        ehdr.pack(fill="x")
        tk.Label(ehdr, text="ĐỒ THỊ:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(side="left")

        for text, mode in [
            ("Nhiệt độ (°C)", "temp"),
            ("Độ ẩm (%)", "hum"),
            ("Áp suất (hPa)", "press"),
            ("Ánh sáng (Lux)", "lux"),
        ]:
            tk.Radiobutton(
                ehdr, text=text, variable=self.env_graph_mode, value=mode,
                command=self._redraw_env_graph,
                bg="#161b22", fg="#e6edf3", selectcolor="#0d1117",
                activebackground="#161b22", activeforeground="#ffffff",
                font=("Segoe UI", 9)
            ).pack(side="left", padx=(6, 0))

        tk.Button(
            ehdr, text="Xóa đồ thị", command=self.clear_env_history,
            bg="#21262d", fg="#e6edf3", relief="flat", padx=8, pady=2, font=("Segoe UI", 9)
        ).pack(side="right", padx=(6, 0))

        tk.Checkbutton(
            ehdr, text="Tạm dừng", variable=self.pause_env_graph,
            bg="#161b22", fg="#e6edf3", selectcolor="#0d1117",
            activebackground="#161b22", activeforeground="#ffffff",
            font=("Segoe UI", 9)
        ).pack(side="right")

        self.env_graph_canvas = tk.Canvas(env_graph_card, bg="#0d1117", highlightthickness=0)
        self.env_graph_canvas.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.env_graph_canvas.bind("<Configure>", lambda _e: self._redraw_env_graph())

    def _add_env_cell(self, parent: tk.Frame, key: str, caption: str, default_txt: str, color: str) -> None:
        cell = tk.Frame(parent, bg="#0d1117", padx=8, pady=8, highlightthickness=1, highlightbackground="#30363d")
        cell.pack(side="left", fill="x", expand=True, padx=2)
        tk.Label(cell, text=caption, bg="#0d1117", fg=color, font=("Segoe UI", 8, "bold")).pack()
        lbl = tk.Label(cell, text=default_txt, bg="#0d1117", fg="#f0f6fc", font=("Consolas", 13, "bold"))
        lbl.pack(pady=(2, 0))
        self.env_val_labels[key] = lbl

    def _make_chip(self, parent: tk.Frame, text: str) -> tk.Label:
        lbl = tk.Label(
            parent, text=text, bg="#0d1117", fg="#e6edf3",
            font=("Consolas", 9), padx=8, pady=3,
            highlightthickness=1, highlightbackground="#30363d"
        )
        lbl.pack(side="left", padx=(0, 5))
        return lbl

    def _build_metric_card(self, parent: tk.Frame, title: str, fields: list[tuple[str, str, str]]) -> None:
        card = tk.Frame(parent, bg="#161b22", padx=10, pady=6, highlightthickness=1, highlightbackground="#30363d")
        card.pack(fill="x", pady=(0, 6))
        tk.Label(card, text=title, bg="#161b22", fg="#8b949e", font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 4))

        row = tk.Frame(card, bg="#161b22")
        row.pack(fill="x")
        for key, caption, color in fields:
            cell = tk.Frame(row, bg="#0d1117", padx=6, pady=4, highlightthickness=1, highlightbackground="#30363d")
            cell.pack(side="left", fill="x", expand=True, padx=2)
            tk.Label(cell, text=caption, bg="#0d1117", fg=color, font=("Segoe UI", 8, "bold")).pack()
            val_lbl = tk.Label(cell, text="--", bg="#0d1117", fg="#f0f6fc", font=("Consolas", 11, "bold"))
            val_lbl.pack()
            self.val_labels[key] = val_lbl

    def switch_view(self, mode: str, send_cmd: bool = True) -> None:
        self.active_view = mode
        if mode == "MPU":
            self.env_view.pack_forget()
            self.mpu_view.pack(fill="both", expand=True)
            self.btn_mode_mpu.config(bg="#1f6feb", fg="#ffffff")
            self.btn_mode_env.config(bg="#21262d", fg="#c9d1d9")
            self.btn_toggle_mode.config(text="⇄ Sang Môi trường & Ánh sáng")
            self._redraw_graph()
        else:
            self.mpu_view.pack_forget()
            self.env_view.pack(fill="both", expand=True)
            self.btn_mode_mpu.config(bg="#21262d", fg="#c9d1d9")
            self.btn_mode_env.config(bg="#238636", fg="#ffffff")
            self.btn_toggle_mode.config(text="⇄ Sang MPU6050 (3D)")
            self._redraw_env_graph()

        if send_cmd and self.ser and self.ser.is_open:
            self.send_command(f"MODE={mode}")

    def toggle_view_mode(self) -> None:
        next_mode = "ENV" if self.active_view == "MPU" else "MPU"
        self.switch_view(next_mode, send_cmd=True)

    def refresh_ports(self) -> None:
        ports = sorted(serial.tools.list_ports.comports(), key=lambda p: p.device)
        self.port_map.clear()
        display_items = []
        preferred_idx = 0

        for idx, p in enumerate(ports):
            desc = p.description or "Serial Port"
            label = f"{p.device} - {desc}"
            self.port_map[label] = p.device
            display_items.append(label)
            if any(tag in desc.upper() for tag in ("ESP32", "JTAG", "USB SERIAL", "CP210", "CH340", "CH343", "CH910")):
                preferred_idx = idx

        self.port_combo["values"] = display_items
        if display_items:
            if self.port_var.get() not in display_items:
                self.port_combo.current(preferred_idx)
        else:
            self.port_var.set("")

    def toggle_connection(self) -> None:
        if (self.ser and self.ser.is_open) or self.waiting_wake_reconnect:
            self.disconnect()
        else:
            self.connect()

    def connect(self) -> None:
        selected = self.port_var.get()
        device = self.port_map.get(selected)
        if not device:
            messagebox.showwarning("Chưa chọn cổng COM", "Vui lòng cắm ESP32-S3 và bấm 'Làm mới' để chọn cổng COM.")
            return

        baud = int(self.baud_var.get())
        if not self._open_serial(device, baud, show_error=True):
            return

        self.packet_count = 0
        self.env_packet_count = 0
        self.device_sleeping = False
        self.waiting_wake_reconnect = False
        self.connected_device = device
        self.connect_btn.config(text="Ngắt kết nối", bg="#da3633")
        self._set_badge("● ĐANG CHỜ DỮ LIỆU...", "#9e6a03", "#ffffff")
        self.append_log(f"Đã kết nối {device} ({baud} baud). Đang gửi PING & SCAN...")
        self.root.after(200, lambda: self.send_command("PING"))
        self.root.after(320, lambda: self.send_command(f"MODE={self.active_view}"))
        self.root.after(450, lambda: self.send_command("SCAN"))

    def _open_serial(self, device: str, baud: int, show_error: bool = False) -> bool:
        try:
            self.ser = serial.Serial(port=device, baudrate=baud, timeout=0.2)
            self.ser.dtr = False
            self.ser.rts = False
        except serial.SerialException as err:
            if show_error:
                messagebox.showerror("Không mở được cổng COM", f"Không thể mở {device}:\n{err}")
            self.ser = None
            return False

        self.last_rx_time = time.monotonic()
        self.reader = SerialReaderThread(self.ser, self.rx_queue)
        self.reader.start()
        return True

    def disconnect(self) -> None:
        self.waiting_wake_reconnect = False
        self.device_sleeping = False
        if self.reader:
            self.reader.stop()
            self.reader = None
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

        self.connect_btn.config(text="Kết nối", bg="#238636")
        self._set_badge("● CHƯA KẾT NỐI", "#30363d", "#c9d1d9")
        self.lbl_motion.config(text="Trạng thái: --", fg="#e6edf3")
        self.append_log("Đã ngắt kết nối cổng Serial.")

    def _on_toggle_sleep(self) -> None:
        self.send_command("SLEEP=ON" if self.auto_sleep_var.get() else "SLEEP=OFF")

    def _auto_reconnect_loop(self) -> None:
        if self.waiting_wake_reconnect and self.connected_device:
            baud = int(self.baud_var.get())
            if self._open_serial(self.connected_device, baud, show_error=False):
                self.waiting_wake_reconnect = False
                self.device_sleeping = False
                self._set_badge("● ĐÃ THỨC & KẾT NỐI LẠI", "#238636", "#ffffff")
                self.append_log(f"ESP32-S3 đã thức dậy — tự động kết nối lại {self.connected_device}.")
                self.root.after(200, lambda: self.send_command("PING"))
        self.root.after(600, self._auto_reconnect_loop)

    def send_command(self, cmd: str) -> None:
        if not self.ser or not self.ser.is_open:
            self.append_log(f"Không thể gửi '{cmd}': chưa kết nối cổng COM.")
            return
        try:
            self.ser.write((cmd.strip() + "\n").encode("utf-8"))
            self.append_log(f">>> Gửi lệnh: {cmd}")
        except serial.SerialException as err:
            self.append_log(f"Lỗi gửi lệnh '{cmd}': {err}")

    def _set_badge(self, text: str, bg: str, fg: str) -> None:
        self.status_badge.config(text=text, bg=bg, fg=fg)

    def append_log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{stamp}] {text}\n")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 250:
            self.log_text.delete("1.0", f"{line_count - 250}.0")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def clear_log(self) -> None:
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def clear_history(self) -> None:
        for buf in (
            self.hist_ax, self.hist_ay, self.hist_az,
            self.hist_gx, self.hist_gy, self.hist_gz,
            self.hist_roll, self.hist_pitch, self.hist_yaw,
        ):
            buf.clear()
        self._redraw_graph()

    def clear_env_history(self) -> None:
        for buf in (
            self.hist_aht_t, self.hist_bmp_t,
            self.hist_hum, self.hist_press, self.hist_lux,
        ):
            buf.clear()
        self._redraw_env_graph()

    def _poll_queue(self) -> None:
        processed = 0
        while processed < 50:
            try:
                kind, payload = self.rx_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1

            if kind == "serial_err":
                if self.device_sleeping and self.connected_device:
                    if self.reader:
                        self.reader.stop()
                        self.reader = None
                    if self.ser:
                        try:
                            self.ser.close()
                        except Exception:
                            pass
                        self.ser = None
                    self.waiting_wake_reconnect = True
                    self._set_badge("● ĐANG NGỦ (CHỜ THỨC DẬY)", "#8957e5", "#ffffff")
                    self.lbl_motion.config(text="Trạng thái: ĐANG NGỦ", fg="#d2a8ff")
                    self.append_log(f"ESP32-S3 đã ngắt USB để ngủ ({self.connected_device}). Tự kết nối lại khi có chuyển động...")
                else:
                    self.append_log(f"Mất kết nối Serial: {payload}")
                    self.disconnect()
                break
            if kind == "line":
                self._handle_line(payload)

        self.root.after(20, self._poll_queue)

    def _check_watchdog(self) -> None:
        if self.ser and self.ser.is_open and not self.device_sleeping:
            if time.monotonic() - self.last_rx_time > 2.5:
                self._set_badge("● KHÔNG CÓ TÍN HIỆU TỪ ESP32", "#9e6a03", "#ffffff")
        self.root.after(400, self._check_watchdog)

    def _handle_line(self, line: str) -> None:
        self.last_rx_time = time.monotonic()
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            self.append_log(f"[RAW] {line}")
            return

        if "event" in data:
            ev = data["event"]
            if ev == "PONG":
                if "sleep" in data:
                    self.auto_sleep_var.set(bool(data["sleep"]))
                if "sleep_mode" in data:
                    self.sleep_mode_var.set(str(data["sleep_mode"]))
                if "sleep_delay" in data:
                    self.sleep_delay_var.set(str(data["sleep_delay"]))
                self.append_log(f"[EVENT] {line}")
            elif ev == "WAKEUP":
                self.device_sleeping = False
                self.waiting_wake_reconnect = False
                self._set_badge("● ĐÃ THỨC DẬY!", "#238636", "#ffffff")
                self.lbl_motion.config(text="Trạng thái: ĐANG HOẠT ĐỘNG", fg="#7ee787")
                self.append_log(f"[WAKEUP] ({data.get('cause', '')}) {data.get('msg', 'ESP32-S3 hoạt động trở lại')}")
            elif ev == "SCAN":
                addrs = data.get("addrs", [])
                if addrs:
                    formatted = [
                        f"{a} ({KNOWN_I2C_NAMES[a]})" if a in KNOWN_I2C_NAMES else a
                        for a in addrs
                    ]
                    addr_str = ", ".join(formatted)
                else:
                    addr_str = "Không có thiết bị nào"
                self.append_log(f"[I2C SCAN] SDA={data.get('sda')} SCL={data.get('scl')} -> {addr_str}")
            elif ev == "CAL_DONE":
                nvs_msg = " (đã lưu vào Flash NVS)" if data.get("nvs") else ""
                self.append_log(
                    f"[CALIB OK{nvs_msg}] gxOff={data.get('gxOff')}, gyOff={data.get('gyOff')}, gzOff={data.get('gzOff')}"
                )
            elif ev == "CAL_CLEARED":
                self.append_log("[CALIB] Đã xóa offset lưu trong Flash NVS.")
            else:
                self.append_log(f"[EVENT] {line}")
            return

        # Handle Environment & Light packet
        if data.get("type") == "env":
            self._handle_env_packet(data)
            return

        status = data.get("status", "")
        if status == "SLEEPING":
            self.device_sleeping = True
            mode = data.get("mode", "LIGHT")
            self._set_badge(f"● ĐANG NGỦ ({mode}) - CHỜ CHUYỂN ĐỘNG", "#8957e5", "#ffffff")
            self.lbl_motion.config(text=f"Trạng thái: ĐANG NGỦ ({mode})", fg="#d2a8ff")
            self.append_log(f"[SLEEP] {data.get('msg', 'ESP32-S3 đã vào chế độ ngủ')}")
            return

        self.device_sleeping = False
        addr = data.get("addr", "--")
        whoami = data.get("whoami", "--")
        self.lbl_addr.config(text=f"I2C Addr: {addr}")
        self.lbl_whoami.config(text=f"WHO_AM_I: {whoami}")

        if status.startswith("ERR_"):
            if self.active_view == "MPU":
                self._set_badge(f"● LỖI MPU: {status}", "#da3633", "#ffffff")
            now = time.monotonic()
            if status != self.last_error_log_key or (now - self.last_error_log_ts) > 5.0:
                self.last_error_log_key = status
                self.last_error_log_ts = now
                self.append_log(f"[{status}] {data.get('msg', 'Kiểm tra dây đấu I2C')}")
            return

        self.packet_count += 1
        self.last_error_log_key = ""

        if self.active_view == "MPU":
            if status == "OK":
                self._set_badge(f"● HOẠT ĐỘNG TỐT ({addr})", "#238636", "#ffffff")
            else:
                self._set_badge(f"● CẢNH BÁO WHO_AM_I ({whoami})", "#9e6a03", "#ffffff")

        ax = float(data.get("ax", 0.0))
        ay = float(data.get("ay", 0.0))
        az = float(data.get("az", 0.0))
        gx = float(data.get("gx", 0.0))
        gy = float(data.get("gy", 0.0))
        gz = float(data.get("gz", 0.0))
        roll = float(data.get("roll", 0.0))
        pitch = float(data.get("pitch", 0.0))
        yaw = float(data.get("yaw", 0.0))
        temp = float(data.get("temp", 0.0))
        hz = int(data.get("hz", 0))
        cal_ok = bool(data.get("cal", False))
        is_still = bool(data.get("still", False))

        amag = math.sqrt(ax * ax + ay * ay + az * az)

        self.val_labels["ax"].config(text=f"{ax:+.3f}")
        self.val_labels["ay"].config(text=f"{ay:+.3f}")
        self.val_labels["az"].config(text=f"{az:+.3f}")
        self.val_labels["amag"].config(text=f"{amag:.3f}")
        self.val_labels["gx"].config(text=f"{gx:+.2f}")
        self.val_labels["gy"].config(text=f"{gy:+.2f}")
        self.val_labels["gz"].config(text=f"{gz:+.2f}")
        self.val_labels["roll"].config(text=f"{roll:+.1f}°")
        self.val_labels["pitch"].config(text=f"{pitch:+.1f}°")
        self.val_labels["yaw"].config(text=f"{yaw:+.1f}°")

        self.lbl_temp.config(text=f"Nhiệt độ: {temp:.1f} °C")
        self.lbl_hz.config(text=f"Tốc độ: {hz} Hz")
        self.lbl_cal.config(text=f"Calib: {'OK' if cal_ok else 'Chưa'}")
        if is_still:
            self.lbl_motion.config(text="Trạng thái: ĐỨNG YÊN", fg="#7ee787")
        else:
            self.lbl_motion.config(text="Trạng thái: ĐANG XOAY", fg="#ffa657")
        self.lbl_pkt.config(text=f"Gói tin: {self.packet_count}")

        if self.active_view == "MPU":
            self._draw_cube(roll, pitch, yaw)

        if not self.pause_graph.get():
            self.hist_ax.append(ax)
            self.hist_ay.append(ay)
            self.hist_az.append(az)
            self.hist_gx.append(gx)
            self.hist_gy.append(gy)
            self.hist_gz.append(gz)
            self.hist_roll.append(roll)
            self.hist_pitch.append(pitch)
            self.hist_yaw.append(yaw)
            if self.active_view == "MPU":
                self._redraw_graph()

    def _handle_env_packet(self, data: dict) -> None:
        self.env_packet_count += 1
        self.lbl_env_pkt.config(text=f"Gói Env: {self.env_packet_count}")

        aht_ok = bool(data.get("aht_ok", False))
        bmp_ok = bool(data.get("bmp_ok", False))
        bh_ok = bool(data.get("bh_ok", False))

        if aht_ok:
            aht_t = float(data.get("aht_t", 0.0))
            aht_h = float(data.get("aht_h", 0.0))
            self.lbl_aht_status.config(text="AHT20 (0x38): OK", fg="#7ee787")
            self.env_val_labels["aht_t"].config(text=f"{aht_t:.2f} °C")
            self.env_val_labels["aht_h"].config(text=f"{aht_h:.1f} %")
            if aht_h < 40:
                hum_desc = "Không khí khô (< 40%)"
            elif aht_h <= 70:
                hum_desc = "Thoải mái (40–70%)"
            else:
                hum_desc = "Độ ẩm cao (> 70%)"
            self.lbl_hum_level.config(text=f"Mức độ ẩm: {hum_desc}")
        else:
            aht_t = None
            aht_h = None
            self.lbl_aht_status.config(text="AHT20 (0x38): Chưa thấy", fg="#ff7b72")
            self.env_val_labels["aht_t"].config(text="-- °C")
            self.env_val_labels["aht_h"].config(text="-- %")
            self.lbl_hum_level.config(text="Mức độ ẩm: Chưa kết nối AHT20")

        if bmp_ok:
            bmp_addr = data.get("bmp_addr", "--")
            bmp_t = float(data.get("bmp_t", 0.0))
            bmp_p = float(data.get("bmp_p", 0.0))
            bmp_alt = float(data.get("bmp_alt", 0.0))
            self.lbl_bmp_status.config(text=f"BMP280 ({bmp_addr}): OK", fg="#7ee787")
            self.env_val_labels["bmp_p"].config(text=f"{bmp_p:.2f} hPa")
            self.env_val_labels["bmp_t"].config(text=f"{bmp_t:.2f} °C")
            self.env_val_labels["bmp_alt"].config(text=f"{bmp_alt:.1f} m")
        else:
            bmp_t = None
            bmp_p = None
            self.lbl_bmp_status.config(text="BMP280: Chưa thấy", fg="#ff7b72")
            self.env_val_labels["bmp_p"].config(text="-- hPa")
            self.env_val_labels["bmp_t"].config(text="-- °C")
            self.env_val_labels["bmp_alt"].config(text="-- m")

        if bh_ok:
            bh_addr = data.get("bh_addr", "--")
            lux = float(data.get("lux", 0.0))
            self.lbl_bh_status.config(text=f"BH1750 ({bh_addr}): OK", fg="#7ee787")
            self.env_val_labels["lux"].config(text=f"{lux:.1f} lx")
            if lux < 50:
                lux_desc = "Tối / Thiếu sáng (< 50 lx)"
            elif lux < 300:
                lux_desc = "Ánh sáng trong nhà (50–300 lx)"
            elif lux < 1000:
                lux_desc = "Đủ sáng / Làm việc (300–1000 lx)"
            else:
                lux_desc = "Rất sáng / Ngoài trời (> 1000 lx)"
            self.lbl_lux_level.config(text=f"Mức ánh sáng: {lux_desc}")
            self._draw_lux_bar(lux)
        else:
            lux = None
            self.lbl_bh_status.config(text="BH1750: Chưa thấy", fg="#ff7b72")
            self.env_val_labels["lux"].config(text="-- lx")
            self.lbl_lux_level.config(text="Mức ánh sáng: Chưa kết nối BH1750")
            self._draw_lux_bar(0.0)

        if self.active_view == "ENV" and not self.device_sleeping:
            online_cnt = int(aht_ok) + int(bmp_ok) + int(bh_ok)
            if online_cnt > 0:
                self._set_badge(f"● MÔI TRƯỜNG & ÁNH SÁNG ({online_cnt}/3 CẢM BIẾN OK)", "#238636", "#ffffff")
            else:
                self._set_badge("● CHƯA TÌM THẤY AHT20 / BMP280 / BH1750", "#da3633", "#ffffff")

        if not self.pause_env_graph.get():
            if aht_t is not None:
                self.hist_aht_t.append(aht_t)
            if bmp_t is not None:
                self.hist_bmp_t.append(bmp_t)
            if aht_h is not None:
                self.hist_hum.append(aht_h)
            if bmp_p is not None:
                self.hist_press.append(bmp_p)
            if lux is not None:
                self.hist_lux.append(lux)
            if self.active_view == "ENV":
                self._redraw_env_graph()

    def _draw_lux_bar(self, lux: float) -> None:
        c = self.lux_bar_canvas
        c.delete("all")
        w = max(c.winfo_width(), 200)
        h = max(c.winfo_height(), 18)
        # Log scale up to 10,000 lx
        ratio = min(1.0, math.log10(max(lux, 1.0)) / 4.0) if lux > 0 else 0.0
        fill_w = int(ratio * w)
        if fill_w > 0:
            c.create_rectangle(0, 0, fill_w, h, fill="#f2cc60", outline="")

    @staticmethod
    def _rotate_point(pt: tuple[float, float, float], roll_deg: float, pitch_deg: float, yaw_deg: float) -> tuple[float, float, float]:
        r = math.radians(roll_deg)
        p = math.radians(pitch_deg)
        y = math.radians(yaw_deg)

        x, y0, z = pt
        y1 = y0 * math.cos(r) - z * math.sin(r)
        z1 = y0 * math.sin(r) + z * math.cos(r)
        x1 = x

        x2 = x1 * math.cos(p) + z1 * math.sin(p)
        z2 = -x1 * math.sin(p) + z1 * math.cos(p)
        y2 = y1

        x3 = x2 * math.cos(y) - y2 * math.sin(y)
        y3 = x2 * math.sin(y) + y2 * math.cos(y)
        z3 = z2
        return (x3, y3, z3)

    def _draw_cube(self, roll: float, pitch: float, yaw: float) -> None:
        c = self.cube_canvas
        c.delete("all")
        w = int(c["width"])
        h = int(c["height"])
        cx, cy = w / 2, h / 2 + 6

        def project(pt3d: tuple[float, float, float]) -> tuple[float, float, float]:
            rx, ry, rz = self._rotate_point(pt3d, 22.0, 0.0, 0.0)
            scale = 64.0 / (4.2 - ry * 0.45)
            return (cx + rx * scale, cy - rz * scale, ry)

        rot_verts = [self._rotate_point(v, roll, pitch, yaw) for v in CUBE_VERTICES]
        proj_verts = [project(v) for v in rot_verts]

        face_list = []
        for i0, i1, i2, i3, color in CUBE_FACES:
            avg_depth = (proj_verts[i0][2] + proj_verts[i1][2] + proj_verts[i2][2] + proj_verts[i3][2]) / 4.0
            face_list.append((avg_depth, [proj_verts[i0], proj_verts[i1], proj_verts[i2], proj_verts[i3]], color))
        face_list.sort(key=lambda item: item[0])

        for _, pts, color in face_list:
            coords = [coord for p in pts for coord in (p[0], p[1])]
            c.create_polygon(coords, fill=color, outline="")

        for a, b in CUBE_EDGES:
            c.create_line(
                proj_verts[a][0], proj_verts[a][1],
                proj_verts[b][0], proj_verts[b][1],
                fill="#e6edf3", width=2
            )

        origin = project((0.0, 0.0, 0.0))
        for vec, color, label in [
            ((1.65, 0.0, 0.0), "#ff7b72", "X"),
            ((0.0, 1.3, 0.0), "#7ee787", "Y"),
            ((0.0, 0.0, 0.9), "#79c0ff", "Z"),
        ]:
            tip = project(self._rotate_point(vec, roll, pitch, yaw))
            c.create_line(origin[0], origin[1], tip[0], tip[1], fill=color, width=2, arrow=tk.LAST)
            c.create_text(tip[0], tip[1] - 8, text=label, fill=color, font=("Consolas", 9, "bold"))

        c.create_text(
            10, 12, anchor="w",
            text=f"Roll: {roll:+.1f}°   Pitch: {pitch:+.1f}°   Yaw: {yaw:+.1f}°",
            fill="#c9d1d9", font=("Consolas", 9)
        )

    def _draw_series_canvas(
        self,
        canvas: tk.Canvas,
        series: list[tuple[str, deque, str]],
        y_min: float,
        y_max: float,
        max_len: int,
    ) -> None:
        canvas.delete("all")
        w = max(canvas.winfo_width(), 200)
        h = max(canvas.winfo_height(), 150)

        pad_l, pad_r, pad_t, pad_b = 58, 16, 26, 20
        pw = max(w - pad_l - pad_r, 40)
        ph = max(h - pad_t - pad_b, 40)

        span = max(y_max - y_min, 1e-3)
        for step in range(5):
            frac = step / 4.0
            y = pad_t + frac * ph
            val = y_max - frac * span
            canvas.create_line(pad_l, y, pad_l + pw, y, fill="#21262d", dash=(3, 3))
            txt = f"{val:.1f}" if span <= 20 else f"{val:.0f}"
            canvas.create_text(pad_l - 6, y, text=txt, anchor="e", fill="#8b949e", font=("Consolas", 8))

        canvas.create_rectangle(pad_l, pad_t, pad_l + pw, pad_t + ph, outline="#30363d")

        lx = pad_l + 6
        for name, buf, color in series:
            canvas.create_line(lx, 12, lx + 16, 12, fill=color, width=2)
            canvas.create_text(lx + 22, 12, text=name, anchor="w", fill="#e6edf3", font=("Segoe UI", 8, "bold"))
            lx += 115

            if len(buf) < 2:
                continue
            pts = []
            for idx, v in enumerate(buf):
                x = pad_l + (idx / max(max_len - 1, 1)) * pw
                clamped = max(y_min, min(y_max, v))
                y = pad_t + (1.0 - (clamped - y_min) / span) * ph
                pts.extend((x, y))
            canvas.create_line(pts, fill=color, width=2)

    def _redraw_graph(self) -> None:
        mode = self.graph_mode.get()
        if mode == "accel":
            series = [
                ("AX (g)", self.hist_ax, "#ff7b72"),
                ("AY (g)", self.hist_ay, "#7ee787"),
                ("AZ (g)", self.hist_az, "#79c0ff"),
            ]
            y_min, y_max = -2.2, 2.2
        elif mode == "gyro":
            series = [
                ("GX (°/s)", self.hist_gx, "#ff7b72"),
                ("GY (°/s)", self.hist_gy, "#7ee787"),
                ("GZ (°/s)", self.hist_gz, "#79c0ff"),
            ]
            y_min, y_max = -260.0, 260.0
        else:
            series = [
                ("Roll (°)", self.hist_roll, "#ffa657"),
                ("Pitch (°)", self.hist_pitch, "#79c0ff"),
                ("Yaw (°)", self.hist_yaw, "#7ee787"),
            ]
            y_min, y_max = -180.0, 180.0

        self._draw_series_canvas(self.graph_canvas, series, y_min, y_max, MAX_HISTORY)

    def _redraw_env_graph(self) -> None:
        mode = self.env_graph_mode.get()
        if mode == "temp":
            series = [
                ("AHT20 (°C)", self.hist_aht_t, "#ff7b72"),
                ("BMP280 (°C)", self.hist_bmp_t, "#ffa657"),
            ]
            vals = list(self.hist_aht_t) + list(self.hist_bmp_t)
            y_min = (min(vals) - 2.0) if vals else 15.0
            y_max = (max(vals) + 2.0) if vals else 45.0
        elif mode == "hum":
            series = [("Độ ẩm (%)", self.hist_hum, "#79c0ff")]
            y_min, y_max = 0.0, 100.0
        elif mode == "press":
            series = [("Áp suất (hPa)", self.hist_press, "#7ee787")]
            vals = list(self.hist_press)
            y_min = (min(vals) - 2.0) if vals else 990.0
            y_max = (max(vals) + 2.0) if vals else 1030.0
        else:
            series = [("Ánh sáng (lx)", self.hist_lux, "#f2cc60")]
            vals = list(self.hist_lux)
            y_min = 0.0
            y_max = max(200.0, (max(vals) * 1.15) if vals else 1000.0)

        self._draw_series_canvas(self.env_graph_canvas, series, y_min, y_max, MAX_ENV_HISTORY)

    def on_close(self) -> None:
        self.disconnect()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    Mpu6050MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
