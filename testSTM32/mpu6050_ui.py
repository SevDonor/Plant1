r"""
ESP32-S3 Multi-Sensor & Peripheral Status Monitor UI (Theo so do D:\Plant\main.fzz)
Ho tro 4 che do chuyen doi ngay trong cung giao dien:
  1. MPU6050 (Gia toc, Gyro, Goc nghieng 3D, Tu ngu/Thuc - SDA=11, SCL=12, INT=10)
  2. Moi truong & Anh sang (AHT20 + BMP280 + BH1750 - SDA=1, SCL=2)
  3. Micro so I2S (INMP441 - WS=GPIO4, SCK=GPIO5, SD=GPIO6, L/R=GND)
  4. Man hinh OLED 0.96" 128x64 (OLED1: SDA=11/SCL=12, OLED2: SDA=1/SCL=2)
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
MAX_MIC_HISTORY = 150

VIEW_ORDER = ["MPU", "ENV", "MIC", "OLED"]

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
    "0x3C": "OLED 0.96\"",
    "0x3D": "OLED 0.96\"",
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
        buf = bytearray()
        while not self._stop_event.is_set():
            try:
                waiting = self.ser.in_waiting
                chunk = self.ser.read(waiting if waiting > 0 else 1)
                if not chunk:
                    continue
                buf.extend(chunk)
                while b"\n" in buf:
                    raw_line, _, rest = buf.partition(b"\n")
                    buf = bytearray(rest)
                    text = raw_line.decode("utf-8", errors="replace").strip()
                    if text:
                        self.out_queue.put(("line", text))
                if len(buf) > 4096:
                    buf.clear()
            except serial.SerialException as err:
                self.out_queue.put(("serial_err", str(err)))
                break
            except Exception as err:  # pragma: no cover
                self.out_queue.put(("serial_err", str(err)))
                break


class Mpu6050MonitorApp:
    """Ung dung Tkinter giam sat MPU6050, AHT20+BMP280+BH1750, Micro INMP441 va OLED 0.96\"."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ESP32-S3 Smart Plant Hardware Tester (MPU6050 + AHT20/BMP280/BH1750 + INMP441 + OLED 0.96\")")
        self.root.geometry("1260x830")
        self.root.minsize(1060, 700)
        self.root.configure(bg="#0d1117")

        self.ser: serial.Serial | None = None
        self.reader: SerialReaderThread | None = None
        self.rx_queue: queue.Queue = queue.Queue()
        self.port_map: dict[str, str] = {}

        self.packet_count = 0
        self.env_packet_count = 0
        self.mic_packet_count = 0
        self.oled_packet_count = 0
        self.last_rx_time = 0.0
        self.last_error_log_key = ""
        self.last_error_log_ts = 0.0
        self.device_sleeping = False
        self.waiting_wake_reconnect = False
        self.connected_device = ""

        # Active view: "MPU", "ENV", "MIC", "OLED"
        self.active_view = "MPU"

        # Latest sensor snapshot for OLED preview
        self.last_roll = 0.0
        self.last_pitch = 0.0
        self.last_yaw = 0.0
        self.last_aht_t = 0.0
        self.last_aht_h = 0.0
        self.last_bmp_p = 0.0
        self.last_lux = 0.0
        self.last_mic_rms = 0.0
        self.last_mic_db = 30.0
        self.last_mic_vad = False

        # OLED state snapshot
        self.oled_state = {
            "oled1_ok": False,
            "oled1_addr": "0x3C",
            "oled2_ok": False,
            "oled2_addr": "0x3C",
            "oled2_sda": 1,
            "oled2_scl": 2,
            "mode": "EYES",
            "auto_emo": True,
            "emo": "NORMAL",
            "contrast": 220,
            "invert": False,
            "power": True,
            "swap": False,
            "rot1": 90,
            "rot2": 270,
            "eye_x": 0,
            "eye_y": 0,
            "blink": False,
            "text1": "SMART PLANT",
            "text2": "OLED 0.96 OK",
        }

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

        # Microphone history
        self.hist_mic_rms = deque([0.0] * MAX_MIC_HISTORY, maxlen=MAX_MIC_HISTORY)
        self.hist_mic_peak = deque([0.0] * MAX_MIC_HISTORY, maxlen=MAX_MIC_HISTORY)
        self.hist_mic_db = deque([30.0] * MAX_MIC_HISTORY, maxlen=MAX_MIC_HISTORY)
        self.hist_mic_wave = deque([0.0] * MAX_MIC_HISTORY, maxlen=MAX_MIC_HISTORY)

        self.graph_mode = tk.StringVar(value="accel")
        self.env_graph_mode = tk.StringVar(value="temp")
        self.mic_graph_mode = tk.StringVar(value="rms")
        self.pause_graph = tk.BooleanVar(value=False)
        self.pause_env_graph = tk.BooleanVar(value=False)
        self.pause_mic_graph = tk.BooleanVar(value=False)

        self.rate_var = tk.StringVar(value="25")
        self.auto_sleep_var = tk.BooleanVar(value=True)
        self.sleep_delay_var = tk.StringVar(value="5")
        self.sleep_mode_var = tk.StringVar(value="LIGHT")
        self.baud_var = tk.StringVar(value="115200")
        self.port_var = tk.StringVar()

        # Mic & OLED UI variables
        self.mic_thr_var = tk.StringVar(value="8")
        self.mic_gain_var = tk.StringVar(value="1.0")
        self.oled_text1_var = tk.StringVar(value="SMART PLANT")
        self.oled_text2_var = tk.StringVar(value="OLED 0.96 OK")
        self.oled_contrast_var = tk.IntVar(value=220)
        self.oled_invert_var = tk.BooleanVar(value=False)
        self.oled_power_var = tk.BooleanVar(value=True)
        self.oled_swap_var = tk.BooleanVar(value=False)
        self.oled_rot1_var = tk.StringVar(value="90°")
        self.oled_rot2_var = tk.StringVar(value="270°")

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

        # 2. Mode switcher bar (4 views)
        mode_bar = tk.Frame(self.root, bg="#161b22", padx=12, pady=7, highlightthickness=1, highlightbackground="#30363d")
        mode_bar.pack(fill="x", padx=10, pady=(0, 6))

        tk.Label(mode_bar, text="CHỨC NĂNG:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 8))

        self.btn_mode_mpu = tk.Button(
            mode_bar, text="1. MPU6050 (3D)",
            command=lambda: self.switch_view("MPU"),
            font=("Segoe UI", 9, "bold"), relief="flat", padx=11, pady=4, cursor="hand2"
        )
        self.btn_mode_mpu.pack(side="left", padx=(0, 5))

        self.btn_mode_env = tk.Button(
            mode_bar, text="2. Môi trường & Ánh sáng",
            command=lambda: self.switch_view("ENV"),
            font=("Segoe UI", 9, "bold"), relief="flat", padx=11, pady=4, cursor="hand2"
        )
        self.btn_mode_env.pack(side="left", padx=(0, 5))

        self.btn_mode_mic = tk.Button(
            mode_bar, text="3. Micro I2S (INMP441)",
            command=lambda: self.switch_view("MIC"),
            font=("Segoe UI", 9, "bold"), relief="flat", padx=11, pady=4, cursor="hand2"
        )
        self.btn_mode_mic.pack(side="left", padx=(0, 5))

        self.btn_mode_oled = tk.Button(
            mode_bar, text="4. Màn hình OLED 0.96\"",
            command=lambda: self.switch_view("OLED"),
            font=("Segoe UI", 9, "bold"), relief="flat", padx=11, pady=4, cursor="hand2"
        )
        self.btn_mode_oled.pack(side="left", padx=(0, 8))

        self.btn_toggle_mode = tk.Button(
            mode_bar, text="⇄ Chuyển tab",
            command=self.toggle_view_mode,
            bg="#8957e5", fg="#ffffff", font=("Segoe UI", 9, "bold"),
            relief="flat", padx=11, pady=4, cursor="hand2"
        )
        self.btn_toggle_mode.pack(side="left")

        tk.Button(
            mode_bar, text="Quét I2C (SCAN)", command=lambda: self.send_command("SCAN"),
            bg="#1f6feb", fg="#ffffff", font=("Segoe UI", 9, "bold"),
            relief="flat", padx=11, pady=4, cursor="hand2"
        ).pack(side="right")

        # 3. View container
        self.view_container = tk.Frame(self.root, bg="#0d1117")
        self.view_container.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.mpu_view = tk.Frame(self.view_container, bg="#0d1117")
        self.env_view = tk.Frame(self.view_container, bg="#0d1117")
        self.mic_view = tk.Frame(self.view_container, bg="#0d1117")
        self.oled_view = tk.Frame(self.view_container, bg="#0d1117")

        self._build_mpu_view(self.mpu_view)
        self._build_env_view(self.env_view)
        self._build_mic_view(self.mic_view)
        self._build_oled_view(self.oled_view)

        # 4. Shared Log console
        log_card = tk.Frame(self.root, bg="#161b22", height=130, highlightthickness=1, highlightbackground="#30363d")
        log_card.pack(fill="x", padx=10, pady=(0, 10))
        log_card.pack_propagate(False)

        l_hdr = tk.Frame(log_card, bg="#161b22", padx=10, pady=4)
        l_hdr.pack(fill="x")
        tk.Label(l_hdr, text="NHẬT KÝ & CHẨN ĐOÁN (SƠ ĐỒ CHÂN: D:\\Plant\\main.fzz)", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(side="left")
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
        self._redraw_mic_graph()
        self._draw_oled_previews()
        self.append_log("Sẵn sàng. Sơ đồ chân: I2C0(SDA=11,SCL=12: MPU6050+OLED1), I2C1(SDA=1,SCL=2: ENV+OLED2), MPU_INT=10, INMP441(WS=4,SCK=5,SD=6).")

    def _build_mpu_view(self, parent: tk.Frame) -> None:
        info_bar = tk.Frame(parent, bg="#161b22", padx=12, pady=6, highlightthickness=1, highlightbackground="#30363d")
        info_bar.pack(fill="x", pady=(0, 6))

        self.lbl_addr = self._make_chip(info_bar, "I2C Addr: --")
        self.lbl_whoami = self._make_chip(info_bar, "WHO_AM_I: --")
        self.lbl_temp = self._make_chip(info_bar, "Nhiệt độ: -- °C")
        self.lbl_hz = self._make_chip(info_bar, "Tốc độ: -- Hz")
        self.lbl_cal = self._make_chip(info_bar, "Calib: --")
        self.lbl_motion = self._make_chip(info_bar, "Trạng thái: --")
        self.lbl_pkt = self._make_chip(info_bar, "Gói tin: 0")

        tk.Label(
            info_bar, text="Bus I2C0: SDA=GPIO11, SCL=GPIO12, INT=GPIO10",
            bg="#161b22", fg="#8b949e", font=("Segoe UI", 8)
        ).pack(side="right")

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
        env_bar = tk.Frame(parent, bg="#161b22", padx=12, pady=7, highlightthickness=1, highlightbackground="#30363d")
        env_bar.pack(fill="x", pady=(0, 6))

        self.lbl_aht_status = self._make_chip(env_bar, "AHT20 (0x38): --")
        self.lbl_bmp_status = self._make_chip(env_bar, "BMP280 (0x76/0x77): --")
        self.lbl_bh_status = self._make_chip(env_bar, "BH1750 (0x23/0x5C): --")
        self.lbl_env_pkt = self._make_chip(env_bar, "Gói Env: 0")

        tk.Label(
            env_bar,
            text="Bus I2C1: SDA=GPIO1, SCL=GPIO2, VCC=3V3, GND",
            bg="#161b22", fg="#8b949e", font=("Segoe UI", 8)
        ).pack(side="right")

        main = tk.Frame(parent, bg="#0d1117")
        main.pack(fill="both", expand=True)

        left = tk.Frame(main, bg="#0d1117", width=410)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)

        self.env_val_labels: dict[str, tk.Label] = {}

        aht_card = tk.Frame(left, bg="#161b22", padx=12, pady=10, highlightthickness=1, highlightbackground="#30363d")
        aht_card.pack(fill="x", pady=(0, 8))
        tk.Label(aht_card, text="CẢM BIẾN NHIỆT ĐỘ & ĐỘ ẨM (AHT20 - 0x38)", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
        aht_row = tk.Frame(aht_card, bg="#161b22")
        aht_row.pack(fill="x")
        self._add_env_cell(aht_row, "aht_t", "Nhiệt độ", "-- °C", "#ff7b72")
        self._add_env_cell(aht_row, "aht_h", "Độ ẩm", "-- %", "#79c0ff")
        self.lbl_hum_level = tk.Label(aht_card, text="Mức độ ẩm: --", bg="#161b22", fg="#c9d1d9", font=("Segoe UI", 9), pady=4)
        self.lbl_hum_level.pack(anchor="w")

        bmp_card = tk.Frame(left, bg="#161b22", padx=12, pady=10, highlightthickness=1, highlightbackground="#30363d")
        bmp_card.pack(fill="x", pady=(0, 8))
        tk.Label(bmp_card, text="CẢM BIẾN ÁP SUẤT & ĐỘ CAO (BMP280 - 0x76/0x77)", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
        bmp_row = tk.Frame(bmp_card, bg="#161b22")
        bmp_row.pack(fill="x")
        self._add_env_cell(bmp_row, "bmp_p", "Áp suất", "-- hPa", "#7ee787")
        self._add_env_cell(bmp_row, "bmp_t", "Nhiệt độ BMP", "-- °C", "#ffa657")
        self._add_env_cell(bmp_row, "bmp_alt", "Độ cao (ước tính)", "-- m", "#d2a8ff")

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

    def _build_mic_view(self, parent: tk.Frame) -> None:
        mic_bar = tk.Frame(parent, bg="#161b22", padx=12, pady=7, highlightthickness=1, highlightbackground="#30363d")
        mic_bar.pack(fill="x", pady=(0, 6))

        self.lbl_mic_status = self._make_chip(mic_bar, "INMP441 (I2S): --")
        self.lbl_mic_vad = self._make_chip(mic_bar, "VAD: --")
        self.lbl_mic_pins = self._make_chip(mic_bar, "WS=GPIO4  SCK=GPIO5  SD=GPIO6  L/R=GND")
        self.lbl_mic_pkt = self._make_chip(mic_bar, "Gói Mic: 0")

        tk.Label(
            mic_bar,
            text="Chuẩn I2S 16kHz / 24-bit (Theo sơ đồ D:\\Plant\\main.fzz)",
            bg="#161b22", fg="#8b949e", font=("Segoe UI", 8)
        ).pack(side="right")

        main = tk.Frame(parent, bg="#0d1117")
        main.pack(fill="both", expand=True)

        left = tk.Frame(main, bg="#0d1117", width=410)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)

        self.mic_val_labels: dict[str, tk.Label] = {}

        # Metrics Card
        m_card = tk.Frame(left, bg="#161b22", padx=12, pady=10, highlightthickness=1, highlightbackground="#30363d")
        m_card.pack(fill="x", pady=(0, 8))
        tk.Label(m_card, text="THÔNG SỐ MICRO SỐ I2S (INMP441)", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))

        r1 = tk.Frame(m_card, bg="#161b22")
        r1.pack(fill="x", pady=(0, 6))
        self._add_mic_cell(r1, "db", "Cường độ ước tính", "-- dB", "#7ee787")
        self._add_mic_cell(r1, "dbfs", "Mức tín hiệu số", "-- dBFS", "#79c0ff")

        r2 = tk.Frame(m_card, bg="#161b22")
        r2.pack(fill="x")
        self._add_mic_cell(r2, "rms", "Biên độ RMS", "-- %", "#ffa657")
        self._add_mic_cell(r2, "peak", "Đỉnh Peak", "-- %", "#ff7b72")

        # VU Meter & VAD Card
        vu_card = tk.Frame(left, bg="#161b22", padx=12, pady=10, highlightthickness=1, highlightbackground="#30363d")
        vu_card.pack(fill="x", pady=(0, 8))
        tk.Label(vu_card, text="THANH ĐO MỨC ÂM (VU METER) & NHẬN DIỆN GIỌNG NÓI", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))

        self.lbl_mic_state_banner = tk.Label(
            vu_card, text="● ĐANG CHỜ TÍN HIỆU MICRO...",
            bg="#21262d", fg="#c9d1d9", font=("Segoe UI", 9, "bold"), pady=6, padx=6, wraplength=370
        )
        self.lbl_mic_state_banner.pack(fill="x", pady=(0, 8))

        self.vu_canvas = tk.Canvas(vu_card, height=26, bg="#0d1117", highlightthickness=1, highlightbackground="#30363d")
        self.vu_canvas.pack(fill="x", pady=(0, 6))

        tk.Label(
            vu_card, text="Vạch trắng: Ngưỡng kích hoạt giọng nói (VAD) | Vạch đỏ: Peak",
            bg="#161b22", fg="#8b949e", font=("Segoe UI", 8)
        ).pack(anchor="w")

        # Mic Sensitivity & Gain Controls Card
        cfg_card = tk.Frame(left, bg="#161b22", padx=12, pady=10, highlightthickness=1, highlightbackground="#30363d")
        cfg_card.pack(fill="both", expand=True)
        tk.Label(cfg_card, text="ĐIỀU CHỈNH ĐỘ NHẠY & KHUẾCH ĐẠI MICRO", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 8))

        row_thr = tk.Frame(cfg_card, bg="#161b22")
        row_thr.pack(fill="x", pady=(0, 8))
        tk.Label(row_thr, text="Ngưỡng phát hiện âm (VAD %):", bg="#161b22", fg="#e6edf3", font=("Segoe UI", 9)).pack(side="left")
        thr_cb = ttk.Combobox(row_thr, textvariable=self.mic_thr_var, values=["3", "5", "8", "12", "20", "35"], width=6, state="readonly")
        thr_cb.pack(side="right")
        thr_cb.bind("<<ComboboxSelected>>", lambda _e: self.send_command(f"MIC_THR={self.mic_thr_var.get()}"))

        row_gain = tk.Frame(cfg_card, bg="#161b22")
        row_gain.pack(fill="x", pady=(0, 8))
        tk.Label(row_gain, text="Hệ số khuếch đại (Software Gain):", bg="#161b22", fg="#e6edf3", font=("Segoe UI", 9)).pack(side="left")
        gain_cb = ttk.Combobox(row_gain, textvariable=self.mic_gain_var, values=["0.5", "1.0", "1.5", "2.0", "3.0", "5.0"], width=6, state="readonly")
        gain_cb.pack(side="right")
        gain_cb.bind("<<ComboboxSelected>>", lambda _e: self.send_command(f"MIC_GAIN={self.mic_gain_var.get()}"))

        tk.Label(
            cfg_card,
            text="Gợi ý kiểm tra nhanh:\n• Vỗ tay hoặc nói gần micro INMP441 để xem thanh VU và đồ thị nhảy.\n• Khi VAD bật, mắt OLED 0.96\" (ở chế độ AUTO) sẽ chuyển sang trạng thái Đang lắng nghe (o_o).",
            bg="#161b22", fg="#8b949e", font=("Segoe UI", 8), justify="left", wraplength=380
        ).pack(anchor="w", pady=(6, 0))

        # Right: Microphone real-time graph
        right = tk.Frame(main, bg="#0d1117")
        right.pack(side="left", fill="both", expand=True)

        mic_graph_card = tk.Frame(right, bg="#161b22", highlightthickness=1, highlightbackground="#30363d")
        mic_graph_card.pack(fill="both", expand=True)

        mhdr = tk.Frame(mic_graph_card, bg="#161b22", padx=10, pady=6)
        mhdr.pack(fill="x")
        tk.Label(mhdr, text="ĐỒ THỊ ÂM THANH:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(side="left")

        for text, mode in [
            ("RMS & Peak (%)", "rms"),
            ("Cường độ (dB SPL)", "db"),
            ("Xung sóng âm (Waveform)", "wave"),
        ]:
            tk.Radiobutton(
                mhdr, text=text, variable=self.mic_graph_mode, value=mode,
                command=self._redraw_mic_graph,
                bg="#161b22", fg="#e6edf3", selectcolor="#0d1117",
                activebackground="#161b22", activeforeground="#ffffff",
                font=("Segoe UI", 9)
            ).pack(side="left", padx=(6, 0))

        tk.Button(
            mhdr, text="Xóa đồ thị", command=self.clear_mic_history,
            bg="#21262d", fg="#e6edf3", relief="flat", padx=8, pady=2, font=("Segoe UI", 9)
        ).pack(side="right", padx=(6, 0))

        tk.Checkbutton(
            mhdr, text="Tạm dừng", variable=self.pause_mic_graph,
            bg="#161b22", fg="#e6edf3", selectcolor="#0d1117",
            activebackground="#161b22", activeforeground="#ffffff",
            font=("Segoe UI", 9)
        ).pack(side="right")

        self.mic_graph_canvas = tk.Canvas(mic_graph_card, bg="#0d1117", highlightthickness=0)
        self.mic_graph_canvas.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.mic_graph_canvas.bind("<Configure>", lambda _e: self._redraw_mic_graph())

    def _build_oled_view(self, parent: tk.Frame) -> None:
        oled_bar = tk.Frame(parent, bg="#161b22", padx=12, pady=7, highlightthickness=1, highlightbackground="#30363d")
        oled_bar.pack(fill="x", pady=(0, 6))

        self.lbl_oled1_status = self._make_chip(oled_bar, "OLED 1 (SDA=11, SCL=12): --")
        self.lbl_oled2_status = self._make_chip(oled_bar, "OLED 2 (SDA=1, SCL=2): --")
        self.lbl_oled_mode = self._make_chip(oled_bar, "Chế độ: EYES")
        self.lbl_oled_emo = self._make_chip(oled_bar, "Biểu cảm: NORMAL")
        self.lbl_oled_pkt = self._make_chip(oled_bar, "Gói OLED: 0")

        tk.Button(
            oled_bar, text="Quét & Khởi tạo lại OLED", command=lambda: self.send_command("OLED_PROBE"),
            bg="#1f6feb", fg="#ffffff", font=("Segoe UI", 8, "bold"),
            relief="flat", padx=10, pady=2, cursor="hand2"
        ).pack(side="right")

        main = tk.Frame(parent, bg="#0d1117")
        main.pack(fill="both", expand=True)

        left = tk.Frame(main, bg="#0d1117", width=430)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)

        # 1. Display Mode Card
        mode_card = tk.Frame(left, bg="#161b22", padx=12, pady=9, highlightthickness=1, highlightbackground="#30363d")
        mode_card.pack(fill="x", pady=(0, 7))
        tk.Label(mode_card, text="1. CHẾ ĐỘ HIỂN THỊ MÀN HÌNH OLED 0.96\"", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))

        m_row = tk.Frame(mode_card, bg="#161b22")
        m_row.pack(fill="x")
        for label, cmd_val, col in [
            ("Đôi mắt Pet", "EYES", "#238636"),
            ("Dashboard Số", "DASHBOARD", "#1f6feb"),
            ("Văn bản", "TEXT", "#8957e5"),
            ("Test Điểm ảnh", "TEST", "#21262d"),
        ]:
            tk.Button(
                m_row, text=label, command=lambda v=cmd_val: self.send_command(f"OLED_MODE={v}"),
                bg=col, fg="#ffffff", font=("Segoe UI", 8, "bold"),
                relief="flat", padx=8, pady=4, cursor="hand2"
            ).pack(side="left", fill="x", expand=True, padx=2)

        # 2. Emotion Picker Card
        emo_card = tk.Frame(left, bg="#161b22", padx=12, pady=9, highlightthickness=1, highlightbackground="#30363d")
        emo_card.pack(fill="x", pady=(0, 7))
        tk.Label(emo_card, text="2. ĐIỀU KHIỂN BIỂU CẢM 2 MẮT OLED (TRÁI & PHẢI)", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))

        emotions = [
            ("Tự động (AUTO)", "AUTO", "#238636"),
            ("Bình thường (● ●)", "NORMAL", "#21262d"),
            ("Vui vẻ (^ ‿ ^)", "HAPPY", "#21262d"),
            ("Ngạc nhiên (O_O)", "SURPRISED", "#21262d"),
            ("Chóng mặt (@_@)", "DIZZY", "#21262d"),
            ("Nghe Mic (o_o)", "LISTENING", "#21262d"),
            ("AI Nghĩ (-_-)", "THINKING", "#21262d"),
            ("Đang nói (^o^)", "SPEAKING", "#21262d"),
            ("Buồn (T_T)", "SAD", "#21262d"),
            ("Khát / Cạn (><)", "THIRSTY", "#21262d"),
            ("Đang tưới (♥_♥)", "LOVE", "#21262d"),
            ("Ngái ngủ (-.- zZ)", "SLEEPY", "#21262d"),
        ]
        grid_f = tk.Frame(emo_card, bg="#161b22")
        grid_f.pack(fill="x")
        for idx, (label, emo_code, bg_col) in enumerate(emotions):
            r, c = divmod(idx, 3)
            tk.Button(
                grid_f, text=label,
                command=lambda e=emo_code: self.send_command(f"OLED_EMO={e}"),
                bg=bg_col, fg="#e6edf3", font=("Segoe UI", 8, "bold"),
                relief="flat", padx=6, pady=4, cursor="hand2"
            ).grid(row=r, column=c, sticky="ew", padx=2, pady=2)
        for c in range(3):
            grid_f.columnconfigure(c, weight=1)

        tk.Button(
            emo_card, text="✨ Chớp mắt ngay (Trigger Blink Animation)",
            command=lambda: self.send_command("OLED_BLINK"),
            bg="#1f6feb", fg="#ffffff", font=("Segoe UI", 8, "bold"),
            relief="flat", pady=4, cursor="hand2"
        ).pack(fill="x", pady=(5, 0))

        # 3. Custom Text Card
        txt_card = tk.Frame(left, bg="#161b22", padx=12, pady=9, highlightthickness=1, highlightbackground="#30363d")
        txt_card.pack(fill="x", pady=(0, 7))
        tk.Label(txt_card, text="3. GỬI VĂN BẢN LÊN MÀN HÌNH OLED 0.96\"", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))

        r_t1 = tk.Frame(txt_card, bg="#161b22")
        r_t1.pack(fill="x", pady=(0, 4))
        tk.Label(r_t1, text="Dòng 1 (Chữ to):", bg="#161b22", fg="#c9d1d9", font=("Segoe UI", 8), width=15, anchor="w").pack(side="left")
        tk.Entry(r_t1, textvariable=self.oled_text1_var, bg="#0d1117", fg="#f0f6fc", insertbackground="#f0f6fc", relief="flat", font=("Consolas", 10)).pack(side="left", fill="x", expand=True, padx=(4, 0))

        r_t2 = tk.Frame(txt_card, bg="#161b22")
        r_t2.pack(fill="x", pady=(0, 6))
        tk.Label(r_t2, text="Dòng 2 (Chữ nhỏ):", bg="#161b22", fg="#c9d1d9", font=("Segoe UI", 8), width=15, anchor="w").pack(side="left")
        tk.Entry(r_t2, textvariable=self.oled_text2_var, bg="#0d1117", fg="#f0f6fc", insertbackground="#f0f6fc", relief="flat", font=("Consolas", 10)).pack(side="left", fill="x", expand=True, padx=(4, 0))

        tk.Button(
            txt_card, text="Hiển thị văn bản lên OLED",
            command=self._send_oled_text,
            bg="#8957e5", fg="#ffffff", font=("Segoe UI", 8, "bold"),
            relief="flat", pady=4, cursor="hand2"
        ).pack(fill="x")

        # 4. Hardware Controls Card
        hw_card = tk.Frame(left, bg="#161b22", padx=12, pady=9, highlightthickness=1, highlightbackground="#30363d")
        hw_card.pack(fill="both", expand=True)
        tk.Label(hw_card, text="4. CÀI ĐẶT PHẦN CỨNG OLED (SSD1306 / SSD1309)", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))

        chk_row = tk.Frame(hw_card, bg="#161b22")
        chk_row.pack(fill="x", pady=(0, 4))
        tk.Checkbutton(
            chk_row, text="Bật màn hình", variable=self.oled_power_var,
            command=lambda: self.send_command(f"OLED_POWER={1 if self.oled_power_var.get() else 0}"),
            bg="#161b22", fg="#e6edf3", selectcolor="#0d1117",
            activebackground="#161b22", activeforeground="#ffffff", font=("Segoe UI", 9)
        ).pack(side="left", padx=(0, 14))

        tk.Checkbutton(
            chk_row, text="Đảo màu (Invert)", variable=self.oled_invert_var,
            command=lambda: self.send_command(f"OLED_INVERT={1 if self.oled_invert_var.get() else 0}"),
            bg="#161b22", fg="#e6edf3", selectcolor="#0d1117",
            activebackground="#161b22", activeforeground="#ffffff", font=("Segoe UI", 9)
        ).pack(side="left", padx=(0, 12))

        tk.Checkbutton(
            chk_row, text="Đổi Trái/Phải", variable=self.oled_swap_var,
            command=lambda: self.send_command(f"OLED_SWAP={1 if self.oled_swap_var.get() else 0}"),
            bg="#161b22", fg="#e6edf3", selectcolor="#0d1117",
            activebackground="#161b22", activeforeground="#ffffff", font=("Segoe UI", 9)
        ).pack(side="left")

        c_row = tk.Frame(hw_card, bg="#161b22")
        c_row.pack(fill="x", pady=(0, 6))
        tk.Label(c_row, text="Độ sáng:", bg="#161b22", fg="#c9d1d9", font=("Segoe UI", 8)).pack(side="left")
        contrast_scale = tk.Scale(
            c_row, from_=10, to=255, orient="horizontal", variable=self.oled_contrast_var,
            command=self._on_contrast_release,
            bg="#161b22", fg="#e6edf3", highlightthickness=0, troughcolor="#0d1117", length=180
        )
        contrast_scale.pack(side="right", fill="x", expand=True, padx=(8, 0))

        # Screen rotation controls (OLED 1 & OLED 2 individual + quick presets)
        rot_row = tk.Frame(hw_card, bg="#161b22")
        rot_row.pack(fill="x", pady=(0, 5))
        tk.Label(rot_row, text="Xoay OLED 1:", bg="#161b22", fg="#c9d1d9", font=("Segoe UI", 8, "bold")).pack(side="left")
        rot1_cb = ttk.Combobox(rot_row, textvariable=self.oled_rot1_var, values=["0°", "90°", "180°", "270°"], width=5, state="readonly")
        rot1_cb.pack(side="left", padx=(4, 2))
        rot1_cb.bind("<<ComboboxSelected>>", lambda _e: self._set_oled_rotation(1, int(self.oled_rot1_var.get().replace("°", ""))))
        tk.Button(
            rot_row, text="↻", command=lambda: self._step_oled_rotation(1),
            bg="#21262d", fg="#e6edf3", font=("Segoe UI", 8, "bold"), relief="flat", padx=6, pady=1, cursor="hand2"
        ).pack(side="left", padx=(0, 10))

        tk.Label(rot_row, text="OLED 2:", bg="#161b22", fg="#c9d1d9", font=("Segoe UI", 8, "bold")).pack(side="left")
        rot2_cb = ttk.Combobox(rot_row, textvariable=self.oled_rot2_var, values=["0°", "90°", "180°", "270°"], width=5, state="readonly")
        rot2_cb.pack(side="left", padx=(4, 2))
        rot2_cb.bind("<<ComboboxSelected>>", lambda _e: self._set_oled_rotation(2, int(self.oled_rot2_var.get().replace("°", ""))))
        tk.Button(
            rot_row, text="↻", command=lambda: self._step_oled_rotation(2),
            bg="#21262d", fg="#e6edf3", font=("Segoe UI", 8, "bold"), relief="flat", padx=6, pady=1, cursor="hand2"
        ).pack(side="left")

        preset_row = tk.Frame(hw_card, bg="#161b22")
        preset_row.pack(fill="x")
        for label, fn, bg_col in [
            ("↻ Xoay cả 2 (+90°)", self._step_both_oled_rotation, "#1f6feb"),
            ("Chụm chân (90°/270°)", lambda: self._apply_oled_rot_preset(90, 270), "#238636"),
            ("Ngang (0°/0°)", lambda: self._apply_oled_rot_preset(0, 0), "#21262d"),
        ]:
            tk.Button(
                preset_row, text=label, command=fn,
                bg=bg_col, fg="#ffffff", font=("Segoe UI", 8, "bold"),
                relief="flat", padx=6, pady=3, cursor="hand2"
            ).pack(side="left", fill="x", expand=True, padx=2)

        # Right: Dual 0.96" OLED Live Simulator Canvas
        right = tk.Frame(main, bg="#0d1117")
        right.pack(side="left", fill="both", expand=True)

        sim_card = tk.Frame(right, bg="#161b22", highlightthickness=1, highlightbackground="#30363d")
        sim_card.pack(fill="both", expand=True)

        shdr = tk.Frame(sim_card, bg="#161b22", padx=12, pady=8)
        shdr.pack(fill="x")
        tk.Label(
            shdr,
            text="MÔ PHỎNG TRỰC QUAN 2 MÀN HÌNH OLED 0.96\" (128×64) — ĐỒNG BỘ 1:1 VỚI MẠCH THẬT",
            bg="#161b22", fg="#8b949e", font=("Segoe UI", 9, "bold")
        ).pack(side="left")

        self.oled_sim_canvas = tk.Canvas(sim_card, bg="#0d1117", highlightthickness=0)
        self.oled_sim_canvas.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.oled_sim_canvas.bind("<Configure>", lambda _e: self._draw_oled_previews())

    def _add_env_cell(self, parent: tk.Frame, key: str, caption: str, default_txt: str, color: str) -> None:
        cell = tk.Frame(parent, bg="#0d1117", padx=8, pady=8, highlightthickness=1, highlightbackground="#30363d")
        cell.pack(side="left", fill="x", expand=True, padx=2)
        tk.Label(cell, text=caption, bg="#0d1117", fg=color, font=("Segoe UI", 8, "bold")).pack()
        lbl = tk.Label(cell, text=default_txt, bg="#0d1117", fg="#f0f6fc", font=("Consolas", 13, "bold"))
        lbl.pack(pady=(2, 0))
        self.env_val_labels[key] = lbl

    def _add_mic_cell(self, parent: tk.Frame, key: str, caption: str, default_txt: str, color: str) -> None:
        cell = tk.Frame(parent, bg="#0d1117", padx=8, pady=8, highlightthickness=1, highlightbackground="#30363d")
        cell.pack(side="left", fill="x", expand=True, padx=2)
        tk.Label(cell, text=caption, bg="#0d1117", fg=color, font=("Segoe UI", 8, "bold")).pack()
        lbl = tk.Label(cell, text=default_txt, bg="#0d1117", fg="#f0f6fc", font=("Consolas", 13, "bold"))
        lbl.pack(pady=(2, 0))
        self.mic_val_labels[key] = lbl

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
        for frame in (self.mpu_view, self.env_view, self.mic_view, self.oled_view):
            frame.pack_forget()

        btn_map = {
            "MPU": (self.btn_mode_mpu, "#1f6feb"),
            "ENV": (self.btn_mode_env, "#238636"),
            "MIC": (self.btn_mode_mic, "#d29922"),
            "OLED": (self.btn_mode_oled, "#8957e5"),
        }
        for key, (btn, active_col) in btn_map.items():
            if key == mode:
                btn.config(bg=active_col, fg="#ffffff")
            else:
                btn.config(bg="#21262d", fg="#c9d1d9")

        if mode == "MPU":
            self.mpu_view.pack(fill="both", expand=True)
            self._redraw_graph()
        elif mode == "ENV":
            self.env_view.pack(fill="both", expand=True)
            self._redraw_env_graph()
        elif mode == "MIC":
            self.mic_view.pack(fill="both", expand=True)
            self._redraw_mic_graph()
        elif mode == "OLED":
            self.oled_view.pack(fill="both", expand=True)
            self._draw_oled_previews()

        if send_cmd and self.ser and self.ser.is_open:
            self.send_command(f"MODE={mode}")

    def toggle_view_mode(self) -> None:
        idx = VIEW_ORDER.index(self.active_view) if self.active_view in VIEW_ORDER else 0
        next_mode = VIEW_ORDER[(idx + 1) % len(VIEW_ORDER)]
        self.switch_view(next_mode, send_cmd=True)

    def _send_oled_text(self) -> None:
        t1 = self.oled_text1_var.get().strip() or "SMART PLANT"
        t2 = self.oled_text2_var.get().strip()
        self.send_command(f"OLED_TEXT={t1}|{t2}")

    def _on_contrast_release(self, val: str) -> None:
        if self.ser and self.ser.is_open:
            self.send_command(f"OLED_CONTRAST={int(float(val))}")

    def _set_oled_rotation(self, screen_num: int, deg: int) -> None:
        deg = (deg % 360)
        if screen_num == 1:
            self.oled_state["rot1"] = deg
            self.oled_rot1_var.set(f"{deg}°")
            if self.ser and self.ser.is_open:
                self.send_command(f"OLED_ROT1={deg}")
        else:
            self.oled_state["rot2"] = deg
            self.oled_rot2_var.set(f"{deg}°")
            if self.ser and self.ser.is_open:
                self.send_command(f"OLED_ROT2={deg}")
        self._draw_oled_previews()

    def _step_oled_rotation(self, screen_num: int) -> None:
        cur = int(self.oled_state.get("rot1" if screen_num == 1 else "rot2", 90 if screen_num == 1 else 270))
        self._set_oled_rotation(screen_num, (cur + 90) % 360)

    def _step_both_oled_rotation(self) -> None:
        r1 = (int(self.oled_state.get("rot1", 90)) + 90) % 360
        r2 = (int(self.oled_state.get("rot2", 270)) + 90) % 360
        self._apply_oled_rot_preset(r1, r2)

    def _apply_oled_rot_preset(self, r1: int, r2: int) -> None:
        self.oled_state["rot1"] = r1 % 360
        self.oled_state["rot2"] = r2 % 360
        self.oled_rot1_var.set(f"{r1 % 360}°")
        self.oled_rot2_var.set(f"{r2 % 360}°")
        if self.ser and self.ser.is_open:
            if (r1 % 360) == (r2 % 360):
                self.send_command(f"OLED_ROT_ALL={r1 % 360}")
            else:
                self.send_command(f"OLED_ROT1={r1 % 360}")
                self.send_command(f"OLED_ROT2={r2 % 360}")
        self._draw_oled_previews()

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
        self.mic_packet_count = 0
        self.oled_packet_count = 0
        self.device_sleeping = False
        self.waiting_wake_reconnect = False
        self.connected_device = device
        self.connect_btn.config(text="Ngắt kết nối", bg="#da3633")
        self._set_badge("● ĐANG CHỜ DỮ LIỆU...", "#9e6a03", "#ffffff")
        self.append_log(f"Đã kết nối {device} ({baud} baud). Đang gửi PING...")
        self.root.after(200, lambda: self.send_command("PING"))
        self.root.after(320, lambda: self.send_command(f"MODE={self.active_view}"))
        if self.active_view != "MIC":
            self.root.after(450, lambda: self.send_command("SCAN"))

    def _open_serial(self, device: str, baud: int, show_error: bool = False) -> bool:
        try:
            # Keep DTR=True & RTS=True (same as VS Code Serial Monitor):
            # - Required by ESP32-S3 Native USB CDC (HWCDC) to stream TX packets reliably
            # - Safe on CH340/CP2102 auto-reset circuit (DTR=1, RTS=1 => EN=1, IO0=1)
            self.ser = serial.Serial(port=device, baudrate=baud, timeout=0.1)
            self.ser.dtr = True
            self.ser.rts = True
            self.ser.reset_input_buffer()
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

    def clear_mic_history(self) -> None:
        for buf in (
            self.hist_mic_rms, self.hist_mic_peak,
            self.hist_mic_db, self.hist_mic_wave,
        ):
            buf.clear()
        self._redraw_mic_graph()

    def _poll_queue(self) -> None:
        processed = 0
        while processed < 60:
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
                    self.append_log(f"ESP32-S3 đã ngắt USB để ngủ ({self.connected_device}). Tự kết nối lại khi có chuyển động (GPIO10)...")
                else:
                    self.append_log(f"Mất kết nối Serial: {payload}")
                    self.disconnect()
                break
            if kind == "line":
                try:
                    self._handle_line(payload)
                except Exception as exc:
                    self.append_log(f"[UI ERR] {exc}")

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
                addrs0 = data.get("addrs", [])
                addrs1 = data.get("bus1_addrs", [])
                fmt0 = [f"{a} ({KNOWN_I2C_NAMES[a]})" if a in KNOWN_I2C_NAMES else a for a in addrs0]
                fmt1 = [f"{a} ({KNOWN_I2C_NAMES[a]})" if a in KNOWN_I2C_NAMES else a for a in addrs1]
                s0 = ", ".join(fmt0) if fmt0 else "Trống"
                s1 = ", ".join(fmt1) if fmt1 else "Trống"
                self.append_log(
                    f"[I2C SCAN] Bus0(SDA={data.get('sda')},SCL={data.get('scl')}): [{s0}] | "
                    f"Bus1(SDA={data.get('bus1_sda', 1)},SCL={data.get('bus1_scl', 2)}): [{s1}]"
                )
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

        pkt_type = data.get("type", "")
        if pkt_type == "env":
            self._handle_env_packet(data)
            return
        if pkt_type == "mic":
            self._handle_mic_packet(data)
            return
        if pkt_type == "oled":
            self._handle_oled_packet(data)
            return

        status = data.get("status", "")
        if status == "SLEEPING":
            self.device_sleeping = True
            mode = data.get("mode", "LIGHT")
            self._set_badge(f"● ĐANG NGỦ ({mode}) - CHỜ CHUYỂN ĐỘNG (GPIO10)", "#8957e5", "#ffffff")
            self.lbl_motion.config(text=f"Trạng thái: ĐANG NGỦ ({mode})", fg="#d2a8ff")
            self.append_log(f"[SLEEP] {data.get('msg', 'ESP32-S3 đã vào chế độ ngủ')}")
            if self.active_view == "OLED":
                self._draw_oled_previews()
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
                self._set_badge(f"● MPU6050 HOẠT ĐỘNG TỐT ({addr})", "#238636", "#ffffff")
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

        self.last_roll = roll
        self.last_pitch = pitch
        self.last_yaw = yaw

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
        elif self.active_view == "OLED":
            self._draw_oled_previews()

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
            self.last_aht_t = aht_t
            self.last_aht_h = aht_h
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
            self.last_bmp_p = bmp_p
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
            self.last_lux = lux
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

    def _handle_mic_packet(self, data: dict) -> None:
        self.mic_packet_count += 1
        self.lbl_mic_pkt.config(text=f"Gói Mic: {self.mic_packet_count}")

        mic_ok = bool(data.get("ok", False))
        rms = float(data.get("rms", 0.0))
        peak = float(data.get("peak", 0.0))
        dbfs = float(data.get("dbfs", -90.0))
        db_spl = float(data.get("db", 30.0))
        vad = bool(data.get("vad", False))
        thr = float(data.get("thr", 8.0))
        wave = data.get("wave", [0] * 8)

        self.last_mic_rms = rms
        self.last_mic_db = db_spl
        self.last_mic_vad = vad

        ws_pin = data.get("ws", 4)
        sck_pin = data.get("sck", 5)
        sd_pin = data.get("sd", 6)
        self.lbl_mic_pins.config(text=f"WS=GPIO{ws_pin}  SCK=GPIO{sck_pin}  SD=GPIO{sd_pin}  L/R=GND")

        if mic_ok:
            self.lbl_mic_status.config(text="INMP441 (I2S): OK", fg="#7ee787")
            self.mic_val_labels["db"].config(text=f"{db_spl:.1f} dB")
            self.mic_val_labels["dbfs"].config(text=f"{dbfs:.1f} dBFS")
            self.mic_val_labels["rms"].config(text=f"{rms:.1f} %")
            self.mic_val_labels["peak"].config(text=f"{peak:.1f} %")

            if vad:
                self.lbl_mic_vad.config(text="VAD: PHÁT HIỆN ÂM THANH", fg="#ffa657")
                self.lbl_mic_state_banner.config(
                    text=f"🎤 PHÁT HIỆN GIỌNG NÓI / ÂM THANH ({db_spl:.0f} dB)",
                    bg="#238636", fg="#ffffff"
                )
            else:
                self.lbl_mic_vad.config(text="VAD: Yên tĩnh", fg="#8b949e")
                self.lbl_mic_state_banner.config(
                    text=f"● MÔI TRƯỜNG YÊN TĨNH ({db_spl:.0f} dB)",
                    bg="#21262d", fg="#c9d1d9"
                )
        else:
            self.lbl_mic_status.config(text="INMP441: Chưa có tín hiệu SD", fg="#ff7b72")
            self.lbl_mic_vad.config(text="VAD: --", fg="#8b949e")
            self.mic_val_labels["db"].config(text="-- dB")
            self.mic_val_labels["dbfs"].config(text="-- dBFS")
            self.mic_val_labels["rms"].config(text="-- %")
            self.mic_val_labels["peak"].config(text="-- %")
            self.lbl_mic_state_banner.config(
                text="● CHƯA NHẬN ĐƯỢC DỮ LIỆU TỪ INMP441 (Kiểm tra WS=4, SCK=5, SD=6, VDD=3V3, GND)",
                bg="#da3633", fg="#ffffff"
            )

        self._draw_vu_meter(rms, peak, thr)

        if self.active_view == "MIC" and not self.device_sleeping:
            if mic_ok:
                self._set_badge(f"● MICRO INMP441 HOẠT ĐỘNG TỐT ({db_spl:.0f} dB)", "#238636", "#ffffff")
            else:
                self._set_badge("● CHƯA TÌM THẤY TÍN HIỆU INMP441 (GPIO 4/5/6)", "#da3633", "#ffffff")

        if not self.pause_mic_graph.get():
            self.hist_mic_rms.append(rms)
            self.hist_mic_peak.append(peak)
            self.hist_mic_db.append(db_spl)
            for wv in wave[:4]:
                self.hist_mic_wave.append(float(wv))
            if self.active_view == "MIC":
                self._redraw_mic_graph()

    def _handle_oled_packet(self, data: dict) -> None:
        self.oled_packet_count += 1
        self.lbl_oled_pkt.config(text=f"Gói OLED: {self.oled_packet_count}")

        self.oled_state.update(data)
        o1_ok = bool(data.get("oled1_ok", False))
        o1_addr = data.get("oled1_addr", "0x3C")
        o2_ok = bool(data.get("oled2_ok", False))
        o2_addr = data.get("oled2_addr", "0x3C")
        o2_sda = data.get("oled2_sda", 1)
        o2_scl = data.get("oled2_scl", 2)
        mode = str(data.get("mode", "EYES"))
        emo = str(data.get("emo", "NORMAL"))
        auto_emo = bool(data.get("auto_emo", True))

        o1_sda = data.get("oled1_sda", 11)
        o1_scl = data.get("oled1_scl", 12)
        if o1_ok:
            self.lbl_oled1_status.config(text=f"OLED 1 (SDA={o1_sda},SCL={o1_scl}): OK ({o1_addr})", fg="#7ee787")
        else:
            self.lbl_oled1_status.config(text=f"OLED 1 (SDA={o1_sda},SCL={o1_scl}): Chưa thấy", fg="#ff7b72")

        if o2_ok:
            self.lbl_oled2_status.config(text=f"OLED 2 (SDA={o2_sda},SCL={o2_scl}): OK ({o2_addr})", fg="#7ee787")
        else:
            self.lbl_oled2_status.config(text=f"OLED 2 (SDA={o2_sda},SCL={o2_scl}): Chưa thấy", fg="#ff7b72")

        if "rot1" in data:
            self.oled_rot1_var.set(f"{int(data['rot1']) % 360}°")
        if "rot2" in data:
            self.oled_rot2_var.set(f"{int(data['rot2']) % 360}°")
        if "swap" in data:
            self.oled_swap_var.set(bool(data["swap"]))

        self.lbl_oled_mode.config(text=f"Chế độ: {mode}")
        self.lbl_oled_emo.config(text=f"Biểu cảm: {emo} ({'AUTO' if auto_emo else 'MANUAL'})")

        if self.active_view == "OLED" and not self.device_sleeping:
            cnt = int(o1_ok) + int(o2_ok)
            if cnt > 0:
                self._set_badge(f"● MÀN HÌNH OLED 0.96\" ({cnt}/2 MÀN HÌNH OK)", "#238636", "#ffffff")
            else:
                self._set_badge("● CHƯA TÌM THẤY OLED 0.96\" (0x3C/0x3D)", "#da3633", "#ffffff")
            self._draw_oled_previews()

    def _draw_lux_bar(self, lux: float) -> None:
        c = self.lux_bar_canvas
        c.delete("all")
        w = max(c.winfo_width(), 200)
        h = max(c.winfo_height(), 18)
        ratio = min(1.0, math.log10(max(lux, 1.0)) / 4.0) if lux > 0 else 0.0
        fill_w = int(ratio * w)
        if fill_w > 0:
            c.create_rectangle(0, 0, fill_w, h, fill="#f2cc60", outline="")

    def _draw_vu_meter(self, rms: float, peak: float, thr: float) -> None:
        c = self.vu_canvas
        c.delete("all")
        w = max(c.winfo_width(), 300)
        h = max(c.winfo_height(), 26)

        rms_w = int(min(100.0, max(0.0, rms)) / 100.0 * w)
        peak_x = int(min(100.0, max(0.0, peak)) / 100.0 * w)
        thr_x = int(min(100.0, max(0.0, thr)) / 100.0 * w)

        # Draw segmented color bar
        g_end = int(0.55 * w)
        y_end = int(0.80 * w)
        if rms_w > 0:
            c.create_rectangle(0, 0, min(rms_w, g_end), h, fill="#238636", outline="")
        if rms_w > g_end:
            c.create_rectangle(g_end, 0, min(rms_w, y_end), h, fill="#d29922", outline="")
        if rms_w > y_end:
            c.create_rectangle(y_end, 0, rms_w, h, fill="#da3633", outline="")

        # Threshold marker
        c.create_line(thr_x, 0, thr_x, h, fill="#ffffff", width=2, dash=(3, 2))
        # Peak hold marker
        if peak_x > 2:
            c.create_line(peak_x, 0, peak_x, h, fill="#ff7b72", width=3)

    def _draw_oled_previews(self) -> None:
        c = self.oled_sim_canvas
        c.delete("all")
        w = max(c.winfo_width(), 520)
        h = max(c.winfo_height(), 360)

        swapped = bool(self.oled_state.get("swap", self.oled_swap_var.get()))
        rot1 = int(self.oled_state.get("rot1", 90)) % 360
        rot2 = int(self.oled_state.get("rot2", 270)) % 360
        o1_ok = bool(self.oled_state.get("oled1_ok", False))
        o1_addr = str(self.oled_state.get("oled1_addr", "0x3C"))
        o2_ok = bool(self.oled_state.get("oled2_ok", False))
        o2_addr = str(self.oled_state.get("oled2_addr", "0x3C"))
        o2_sda = self.oled_state.get("oled2_sda", 1)
        o2_scl = self.oled_state.get("oled2_scl", 2)

        def dims_for(rot_deg: int) -> tuple[int, int]:
            return (64, 128) if rot_deg in (90, 270) else (128, 64)

        left_rot = rot2 if swapped else rot1
        right_rot = rot1 if swapped else rot2
        lw_u, lh_u = dims_for(left_rot)
        rw_u, rh_u = dims_for(right_rot)

        # Compute scale so both panels fit comfortably side by side
        scale = min((w - 170) / float(lw_u + rw_u + 16), (h - 130) / float(max(lh_u, rh_u) + 8))
        scale = max(1.4, min(scale, 2.7))

        l_sw, l_sh = int(lw_u * scale), int(lh_u * scale)
        r_sw, r_sh = int(rw_u * scale), int(rh_u * scale)

        def pcb_pads(rot_deg: int) -> tuple[int, int, int, int]:
            # (left_pad, top_pad, right_pad, bottom_pad)
            if rot_deg == 0:
                return (14, 30, 14, 16)   # Pins on TOP
            if rot_deg == 90:
                return (16, 12, 34, 12)   # Pins on RIGHT
            if rot_deg == 180:
                return (14, 16, 14, 30)   # Pins on BOTTOM
            return (34, 12, 16, 12)       # 270: Pins on LEFT

        lp = pcb_pads(left_rot)
        rp = pcb_pads(right_rot)
        l_box_w = l_sw + lp[0] + lp[2]
        r_box_w = r_sw + rp[0] + rp[2]
        gap = 18
        total_w = l_box_w + r_box_w + gap
        start_x = max(20, (w - total_w) // 2)
        mid_y = h // 2 + 6

        left_x0 = start_x + lp[0]
        left_y0 = mid_y - l_sh // 2
        right_x0 = start_x + l_box_w + gap + rp[0]
        right_y0 = mid_y - r_sh // 2

        left_info = (
            f"OLED 2 — MẮT TRÁI ({left_rot}°)" if swapped else f"OLED 1 — MẮT TRÁI ({left_rot}°)",
            o2_ok if swapped else o1_ok,
            o2_addr if swapped else o1_addr,
            2 if swapped else 1,
            left_rot,
            l_sw,
            l_sh,
            lp,
        )
        right_info = (
            f"OLED 1 — MẮT PHẢI ({right_rot}°)" if swapped else f"OLED 2 — MẮT PHẢI ({right_rot}°)",
            o1_ok if swapped else o2_ok,
            o1_addr if swapped else o2_addr,
            1 if swapped else 2,
            right_rot,
            r_sw,
            r_sh,
            rp,
        )

        screens = [
            (left_x0, left_y0, *left_info, True),
            (right_x0, right_y0, *right_info, False),
        ]

        mode = str(self.oled_state.get("mode", "EYES"))
        emo = str(self.oled_state.get("emo", "NORMAL"))
        power = bool(self.oled_state.get("power", True)) and not self.device_sleeping
        invert = bool(self.oled_state.get("invert", False))
        blink = bool(self.oled_state.get("blink", False))
        px = int(self.oled_state.get("eye_x", int(self.last_roll * 0.25)))
        py = int(self.oled_state.get("eye_y", int(self.last_pitch * 0.45)))
        px = max(-12, min(12, px))
        py = max(-18, min(18, py))

        fg_col = "#061018" if invert else "#00e5ff"
        bg_col = "#00e5ff" if invert else "#05080d"

        c.create_text(
            w // 2, 16,
            text=f"Góc xoay hiện tại: OLED 1 = {rot1}° | OLED 2 = {rot2}°  (Bấm nút xoay ở khung trái để đổi góc trực tiếp)",
            fill="#8b949e", font=("Segoe UI", 8, "italic")
        )

        for x0, y0, title, is_ok, addr, screen_num, rot_deg, sw, sh, pads, is_left in screens:
            pcb_l = x0 - pads[0]
            pcb_t = y0 - pads[1]
            pcb_r = x0 + sw + pads[2]
            pcb_b = y0 + sh + pads[3]

            # Ribbon cable on opposite side of header pins
            if rot_deg == 0:
                c.create_rectangle(x0 + sw // 2 - 22, pcb_b - 5, x0 + sw // 2 + 22, pcb_b + 5, fill="#b45309", outline="#f59e0b")
            elif rot_deg == 90:
                c.create_rectangle(pcb_l - 5, y0 + sh // 2 - 22, pcb_l + 5, y0 + sh // 2 + 22, fill="#b45309", outline="#f59e0b")
            elif rot_deg == 180:
                c.create_rectangle(x0 + sw // 2 - 22, pcb_t - 5, x0 + sw // 2 + 22, pcb_t + 5, fill="#b45309", outline="#f59e0b")
            else:
                c.create_rectangle(pcb_r - 5, y0 + sh // 2 - 22, pcb_r + 5, y0 + sh // 2 + 22, fill="#b45309", outline="#f59e0b")

            # PCB frame + 4 corner holes
            c.create_rectangle(pcb_l, pcb_t, pcb_r, pcb_b, fill="#0f172a", outline="#334155", width=2)
            for hx in (pcb_l + 6, pcb_r - 6):
                for hy in (pcb_t + 6, pcb_b - 6):
                    c.create_oval(hx - 3, hy - 3, hx + 3, hy + 3, fill="#0d1117", outline="#94a3b8")

            # 4 header pins on the physical side matching rot_deg
            if rot_deg == 0:
                for i, lbl in enumerate(["GND", "VCC", "SCL", "SDA"]):
                    px_p = x0 + sw // 2 - 36 + i * 24
                    c.create_oval(px_p - 4, pcb_t + 6, px_p + 4, pcb_t + 14, fill="#eab308", outline="#ca8a04")
                    c.create_text(px_p, pcb_t + 22, text=lbl, fill="#94a3b8", font=("Consolas", 6))
            elif rot_deg == 90:
                for i, lbl in enumerate(["GND", "VCC", "SCL", "SDA"]):
                    py_p = y0 + sh // 2 - 36 + i * 24
                    c.create_oval(x0 + sw + 18, py_p - 4, x0 + sw + 26, py_p + 4, fill="#eab308", outline="#ca8a04")
                    c.create_text(x0 + sw + 10, py_p, text=lbl, fill="#94a3b8", font=("Consolas", 6))
            elif rot_deg == 180:
                for i, lbl in enumerate(["SDA", "SCL", "VCC", "GND"]):
                    px_p = x0 + sw // 2 - 36 + i * 24
                    c.create_oval(px_p - 4, pcb_b - 14, px_p + 4, pcb_b - 6, fill="#eab308", outline="#ca8a04")
                    c.create_text(px_p, pcb_b - 22, text=lbl, fill="#94a3b8", font=("Consolas", 6))
            else:
                for i, lbl in enumerate(["SDA", "SCL", "VCC", "GND"]):
                    py_p = y0 + sh // 2 - 36 + i * 24
                    c.create_oval(x0 - 26, py_p - 4, x0 - 18, py_p + 4, fill="#eab308", outline="#ca8a04")
                    c.create_text(x0 - 10, py_p, text=lbl, fill="#94a3b8", font=("Consolas", 6))

            title_cx = (pcb_l + pcb_r) // 2
            c.create_text(title_cx, pcb_t - 12, text=title, fill="#e6edf3", font=("Segoe UI", 8, "bold"))

            status_txt = f"● ĐÃ KẾT NỐI ({addr})" if is_ok else "○ CHƯA KẾT NỐI (Mô phỏng)"
            status_col = "#7ee787" if is_ok else "#ffa657"
            c.create_text(title_cx, pcb_b + 13, text=status_txt, fill=status_col, font=("Segoe UI", 8, "bold"))

            # Active glass area
            c.create_rectangle(x0, y0, x0 + sw, y0 + sh, fill=bg_col if power else "#030508", outline="#1e293b", width=2)
            if not power:
                c.create_text(x0 + sw // 2, y0 + sh // 2, text="SLEEP\nOFF", fill="#334155", font=("Consolas", 10, "bold"), justify="center")
                continue

            portrait = rot_deg in (90, 270)
            if mode == "EYES":
                self._draw_sim_eye(c, x0, y0, sw, sh, scale, fg_col, bg_col, emo, is_left, blink, px, py, portrait)
            elif mode == "DASHBOARD":
                self._draw_sim_dashboard(c, x0, y0, sw, sh, fg_col, is_left, portrait)
            elif mode == "TEXT":
                t1 = str(self.oled_state.get("text1", self.oled_text1_var.get()))
                t2 = str(self.oled_state.get("text2", self.oled_text2_var.get()))
                c.create_rectangle(x0 + 3, y0 + 3, x0 + sw - 3, y0 + sh - 3, outline=fg_col)
                c.create_text(x0 + sw // 2, y0 + 16, text=f"OLED {screen_num} ({addr})", fill="#facc15" if not invert else fg_col, font=("Consolas", 8, "bold"))
                c.create_line(x0 + 4, y0 + 28, x0 + sw - 4, y0 + 28, fill=fg_col)
                c.create_text(x0 + sw // 2, y0 + sh // 2, text=f"{t1}\n{t2}", fill=fg_col, font=("Consolas", 10, "bold"), width=sw - 10, justify="center")
            else:
                c.create_rectangle(x0 + 3, y0 + 3, x0 + sw - 3, y0 + sh - 3, outline=fg_col, width=2)
                c.create_text(x0 + sw // 2, y0 + 16, text="^ TOP ^", fill="#facc15" if not invert else fg_col, font=("Consolas", 8, "bold"))
                c.create_text(x0 + sw // 2, y0 + sh // 2 + 4, text=f"OLED{screen_num} {rot_deg}°\n{addr}", fill=fg_col, font=("Consolas", 9, "bold"), justify="center")

    def _draw_sim_eye(
        self, c: tk.Canvas, x0: int, y0: int, sw: int, sh: int, scale: float,
        fg: str, bg: str, emo: str, is_left: bool, blink: bool, px: int, py: int, portrait: bool = True
    ) -> None:
        cx = x0 + sw // 2 + int((px // 2 if portrait else px) * scale)
        cy = y0 + sh // 2 + int((py if portrait else py // 2) * scale)
        s = lambda v: int(v * scale)

        if blink:
            bw = s(23 if portrait else 30)
            c.create_rectangle(cx - bw, cy - s(3), cx + bw, cy + s(3), fill=fg, outline="")
            return

        if emo == "HAPPY":
            r = s(23 if portrait else 26)
            c.create_arc(cx - r, cy - s(14), cx + r, cy + s(28), start=0, extent=180, style="arc", outline=fg, width=max(4, s(5)))
        elif emo == "SURPRISED":
            r = s(23 if portrait else 25)
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=fg, width=max(3, s(5)))
            c.create_oval(cx - s(6), cy - s(6), cx + s(6), cy + s(6), fill=fg, outline="")
        elif emo == "DIZZY":
            for r in (23, 15, 7):
                c.create_oval(cx - s(r), cy - s(r), cx + s(r), cy + s(r), outline=fg, width=max(2, s(3)))
        elif emo == "LISTENING":
            rx, ry = (s(21), s(26)) if portrait else (s(22), s(22))
            c.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, fill=fg, outline="")
            c.create_oval(cx - s(11), cy - s(12), cx - s(3), cy - s(4), fill=bg, outline="")
            if portrait:
                bw = s(min(54, int(self.last_mic_rms * 0.50 + 8)))
                c.create_rectangle(x0 + sw // 2 - bw // 2, y0 + s(8), x0 + sw // 2 + bw // 2, y0 + s(14), fill=fg, outline="")
                c.create_rectangle(x0 + sw // 2 - bw // 2, y0 + sh - s(14), x0 + sw // 2 + bw // 2, y0 + sh - s(8), fill=fg, outline="")
            else:
                bh = s(min(54, int(self.last_mic_rms * 0.48 + 8)))
                c.create_rectangle(x0 + s(4), y0 + sh // 2 - bh // 2, x0 + s(10), y0 + sh // 2 + bh // 2, fill=fg, outline="")
                c.create_rectangle(x0 + sw - s(10), y0 + sh // 2 - bh // 2, x0 + sw - s(4), y0 + sh // 2 + bh // 2, fill=fg, outline="")
        elif emo == "THINKING":
            bw = s(22 if portrait else 27)
            c.create_rectangle(cx - bw, cy - s(4), cx + bw, cy + s(4), fill=fg, outline="")
            for i in range(3):
                dx = (i - 1) * s(12)
                c.create_oval(cx + dx - s(3), cy + s(16), cx + dx + s(3), cy + s(22), fill=fg, outline="")
        elif emo == "SPEAKING":
            rx, ry = (s(22), s(27)) if portrait else (s(24), s(21))
            c.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, fill=fg, outline="")
            c.create_oval(cx - s(11), cy - s(13), cx - s(3), cy - s(5), fill=bg, outline="")
            c.create_oval(cx + s(6), cy + s(6), cx + s(11), cy + s(11), fill=bg, outline="")
        elif emo == "SAD":
            c.create_oval(cx - s(21), cy - s(20), cx + s(21), cy + s(22), fill=fg, outline="")
            if is_left:
                c.create_line(cx - s(24), cy - s(9), cx + s(24), cy - s(24), fill=bg, width=max(4, s(9)))
            else:
                c.create_line(cx - s(24), cy - s(24), cx + s(24), cy - s(9), fill=bg, width=max(4, s(9)))
            tear_x = cx - s(12) if is_left else cx + s(12)
            c.create_oval(tear_x - s(4), cy + s(24), tear_x + s(4), cy + s(32), fill=fg, outline="")
        elif emo == "THIRSTY":
            sp = s(20 if portrait else 24)
            if is_left:
                c.create_line(cx - sp, cy - s(15), cx + sp, cy, fill=fg, width=max(3, s(5)))
                c.create_line(cx + sp, cy, cx - sp, cy + s(15), fill=fg, width=max(3, s(5)))
            else:
                c.create_line(cx + sp, cy - s(15), cx - sp, cy, fill=fg, width=max(3, s(5)))
                c.create_line(cx - sp, cy, cx + sp, cy + s(15), fill=fg, width=max(3, s(5)))
        elif emo == "LOVE":
            c.create_oval(cx - s(22), cy - s(19), cx + s(2), cy + s(5), fill=fg, outline="")
            c.create_oval(cx - s(2), cy - s(19), cx + s(22), cy + s(5), fill=fg, outline="")
            c.create_polygon(cx - s(22), cy - s(3), cx + s(22), cy - s(3), cx, cy + s(19), fill=fg, outline="")
        elif emo == "SLEEPY":
            c.create_arc(cx - s(22), cy - s(18), cx + s(22), cy + s(18), start=180, extent=180, fill=fg, outline=fg)
            c.create_text(x0 + sw - s(16), y0 + s(16), text="Zzz", fill=fg, font=("Consolas", max(9, s(6)), "bold"))
        else:
            rx, ry = (s(22), s(30)) if portrait else (s(24), s(23))
            c.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, fill=fg, outline="")
            c.create_oval(cx - s(12), cy - s(15 if portrait else 12), cx - s(3), cy - s(6 if portrait else 3), fill=bg, outline="")
            c.create_oval(cx + s(7), cy + s(9), cx + s(11), cy + s(13), fill=bg, outline="")

    def _draw_sim_dashboard(self, c: tk.Canvas, x0: int, y0: int, sw: int, sh: int, fg: str, is_first: bool, portrait: bool = True) -> None:
        c.create_rectangle(x0 + 3, y0 + 3, x0 + sw - 3, y0 + sh - 3, outline=fg)
        c.create_line(x0 + 3, y0 + 22, x0 + sw - 3, y0 + 22, fill=fg)
        if portrait:
            if is_first:
                c.create_text(x0 + sw // 2, y0 + 12, text="PLANT S3", fill="#facc15", font=("Consolas", 9, "bold"))
                lines = [
                    f"R:{self.last_roll:+5.1f}",
                    f"P:{self.last_pitch:+5.1f}",
                    f"T:{self.last_aht_t:4.1f}C",
                    f"H:{self.last_aht_h:4.0f}%",
                    f"L:{self.last_lux:4.0f}lx",
                    f"P:{self.last_bmp_p:4.0f}",
                    f"MIC:{self.last_mic_db:2.0f}dB",
                    "VAD:TALK" if self.last_mic_vad else "VAD:IDLE",
                ]
                step_y = max(15, (sh - 32) // len(lines))
                for idx, ln in enumerate(lines):
                    c.create_text(x0 + 8, y0 + 32 + idx * step_y, anchor="w", text=ln, fill=fg, font=("Consolas", 8, "bold"))
            else:
                c.create_text(x0 + sw // 2, y0 + 12, text="OLED 2", fill="#facc15", font=("Consolas", 9, "bold"))
                c.create_text(x0 + 8, y0 + 34, anchor="w", text=f"Y:{self.last_yaw:+5.1f}", fill=fg, font=("Consolas", 9, "bold"))
                c.create_text(x0 + 8, y0 + 52, anchor="w", text=f"RMS:{self.last_mic_rms:2.0f}%", fill=fg, font=("Consolas", 9, "bold"))
                bx1, by1, bx2, by2 = x0 + sw // 3, y0 + 70, x0 + (2 * sw) // 3, y0 + sh - 10
                c.create_rectangle(bx1, by1, bx2, by2, outline=fg)
                bh = int((min(100.0, self.last_mic_rms) / 100.0) * max(1, by2 - by1 - 4))
                if bh > 0:
                    c.create_rectangle(bx1 + 2, by2 - 2 - bh, bx2 - 2, by2 - 2, fill=fg, outline="")
        else:
            if is_first:
                c.create_text(x0 + 8, y0 + 12, anchor="w", text="SMART PLANT S3", fill="#facc15", font=("Consolas", 9, "bold"))
                lines = [
                    f"R:{self.last_roll:+5.1f} P:{self.last_pitch:+5.1f}",
                    f"T:{self.last_aht_t:.1f}C H:{self.last_aht_h:.0f}%",
                    f"L:{self.last_lux:.0f}lx P:{self.last_bmp_p:.0f}",
                    f"MIC:{self.last_mic_db:.0f}dB {'TALK' if self.last_mic_vad else 'IDLE'}",
                ]
                step_y = max(16, (sh - 30) // len(lines))
                for idx, ln in enumerate(lines):
                    c.create_text(x0 + 8, y0 + 32 + idx * step_y, anchor="w", text=ln, fill=fg, font=("Consolas", 9, "bold"))
            else:
                c.create_text(x0 + 8, y0 + 12, anchor="w", text="OLED 2", fill="#facc15", font=("Consolas", 9, "bold"))
                c.create_text(x0 + 8, y0 + 36, anchor="w", text=f"YAW: {self.last_yaw:+5.1f} DEG", fill=fg, font=("Consolas", 9, "bold"))
                c.create_text(x0 + 8, y0 + 58, anchor="w", text=f"RMS: {self.last_mic_rms:4.1f}%", fill=fg, font=("Consolas", 9, "bold"))
                c.create_rectangle(x0 + 8, y0 + sh - 24, x0 + sw - 8, y0 + sh - 10, outline=fg)
                bw = int((min(100.0, self.last_mic_rms) / 100.0) * max(1, sw - 20))
                if bw > 0:
                    c.create_rectangle(x0 + 10, y0 + sh - 22, x0 + 10 + bw, y0 + sh - 12, fill=fg, outline="")

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
            lx += 125

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

    def _redraw_mic_graph(self) -> None:
        mode = self.mic_graph_mode.get()
        if mode == "rms":
            series = [
                ("Biên độ RMS (%)", self.hist_mic_rms, "#ffa657"),
                ("Đỉnh Peak (%)", self.hist_mic_peak, "#ff7b72"),
            ]
            y_min, y_max = 0.0, 100.0
        elif mode == "db":
            series = [("Cường độ âm (dB SPL)", self.hist_mic_db, "#7ee787")]
            y_min, y_max = 25.0, 105.0
        else:
            series = [("Xung âm thanh (%)", self.hist_mic_wave, "#79c0ff")]
            y_min, y_max = -100.0, 100.0

        self._draw_series_canvas(self.mic_graph_canvas, series, y_min, y_max, MAX_MIC_HISTORY)

    def on_close(self) -> None:
        self.disconnect()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    Mpu6050MonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
