r"""
ESP32-S3 Pump & XKC-Y25-V Liquid Level Sensor Test UI
=====================================================
Giao dien test truc quan cho:
  - Cam bien muc nuoc khong tiep xuc XKC-Y25-V (M1 -> PC817 U5 -> GPIO 7)
  - Bom mini 5V (GPIO 14 -> PC817 U4 -> MOSFET Q1 -> WATER PUMP1)
Khop 100% so do chan trong D:\Plant\main.fzz.
"""

from __future__ import annotations

import collections
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

try:
    import serial
    import serial.tools.list_ports
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Thieu thu vien 'pyserial'. Hay chay: pip install pyserial") from exc


BG_ROOT = "#020617"
BG_CARD = "#0f172a"
BG_INP = "#1e293b"
BORDER = "#334155"
FG_MAIN = "#f8fafc"
FG_DIM = "#94a3b8"
ACCENT_CYAN = "#38bdf8"
ACCENT_BLUE = "#0ea5e9"
ACCENT_GREEN = "#22c55e"
ACCENT_AMBER = "#f59e0b"
ACCENT_RED = "#ef4444"
ACCENT_PURPLE = "#a855f7"

MODE_OPTIONS = [
    ("MANUAL", "Thủ công (UI bật/tắt tự do)"),
    ("PROTECT", "Thủ công + Bảo vệ chống cạn (XKC cạn → tự ngắt)"),
    ("AUTO_WATER", "Tự động theo XKC: CÓ nước → BẬT bơm, CẠN → TẮT"),
    ("AUTO_DRY", "Tự động bơm bù: CẠN nước → BẬT bơm, ĐẦY → TẮT"),
]


class SerialWorker:
    """Thread doc/ghi Serial khong chan giao dien Tkinter."""

    def __init__(self, rx_queue: queue.Queue) -> None:
        self.rx_queue = rx_queue
        self._ser: serial.Serial | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def connect(self, port: str, baud: int = 115200) -> None:
        self.disconnect()
        ser = serial.Serial(port=port, baudrate=baud, timeout=0.1, write_timeout=0.5)
        # Tranh reset khong can thiet tren mot so mach USB-CDC
        try:
            ser.dtr = False
            ser.rts = False
        except Exception:
            pass
        self._ser = ser
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None

    def send(self, line: str) -> bool:
        with self._lock:
            if not self.connected or self._ser is None:
                return False
            try:
                data = (line.strip() + "\n").encode("utf-8")
                self._ser.write(data)
                self._ser.flush()
                return True
            except Exception as exc:
                self.rx_queue.put({"type": "_error", "msg": f"Lỗi gửi Serial: {exc}"})
                return False

    def _reader_loop(self) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            ser = self._ser
            if ser is None or not ser.is_open:
                break
            try:
                chunk = ser.read(256)
                if not chunk:
                    continue
                buf.extend(chunk)
                while b"\n" in buf:
                    raw_line, _, rest = buf.partition(b"\n")
                    buf = bytearray(rest)
                    text = raw_line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    try:
                        obj = json.loads(text)
                        if isinstance(obj, dict):
                            self.rx_queue.put(obj)
                            continue
                    except json.JSONDecodeError:
                        pass
                    self.rx_queue.put({"type": "raw", "msg": text})
            except Exception as exc:
                if not self._stop.is_set():
                    self.rx_queue.put({"type": "_disconnect", "msg": f"Mất kết nối Serial: {exc}"})
                break


class PumpXkcApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Test Bơm 5V & Cảm Biến XKC-Y25-V — ESP32 30 chân (D27 / D26)")
        self.geometry("1180x780")
        self.minsize(1020, 680)
        self.configure(bg=BG_ROOT)

        self.rx_queue: queue.Queue = queue.Queue()
        self.worker = SerialWorker(self.rx_queue)

        # State cache
        self.state: dict = {
            "xkc_pin": 27,
            "pump_pin": 26,
            "xkc_raw": 1,
            "xkc_out_est": 0,
            "water": False,
            "xkc_active_low": True,
            "xkc_pullup": False,
            "debounce_ms": 150,
            "xkc_changes": 0,
            "xkc_hold_ms": 0,
            "pump_on": False,
            "pump_raw": 0,
            "pump_active_high": True,
            "pump_pwm": 100,
            "mode": "MANUAL",
            "pulse_rem_ms": 0,
            "pump_run_ms": 0,
            "pump_total_ms": 0,
            "max_run_sec": 30,
            "last_reason": "DISCONNECTED",
        }
        self._last_rx_time = 0.0
        self._syncing_controls = False

        # History for live waveform (max 240 samples ~ 24s at 10Hz)
        self.history: collections.deque[tuple[float, int, int]] = collections.deque(maxlen=240)

        # Tkinter variables
        self.var_port = tk.StringVar()
        self.var_mode = tk.StringVar(value="MANUAL")
        self.var_xkc_active_low = tk.BooleanVar(value=True)
        self.var_xkc_pullup = tk.BooleanVar(value=False)
        self.var_pump_active_high = tk.BooleanVar(value=True)
        self.var_debounce = tk.StringVar(value="150")
        self.var_max_run = tk.StringVar(value="30")
        self.var_pwm = tk.IntVar(value=100)
        self.var_pulse_sec = tk.StringVar(value="3")
        self.var_xkc_pin = tk.StringVar(value="27")
        self.var_pump_pin = tk.StringVar(value="26")

        self._configure_styles()
        self._build_ui()
        self._refresh_ports()
        self._draw_tank_visual()
        self._draw_timeline()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._poll_queue)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "TCombobox",
            fieldbackground=BG_INP,
            background=BG_INP,
            foreground=FG_MAIN,
            bordercolor=BORDER,
            arrowcolor=FG_MAIN,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", BG_INP)],
            foreground=[("readonly", FG_MAIN)],
        )

    def _make_card(self, parent: tk.Widget, title: str, accent: str = ACCENT_CYAN) -> tk.Frame:
        outer = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        header = tk.Frame(outer, bg=BG_CARD)
        header.pack(fill="x", padx=12, pady=(10, 6))
        tk.Frame(header, bg=accent, width=4, height=16).pack(side="left", padx=(0, 8))
        tk.Label(
            header,
            text=title,
            bg=BG_CARD,
            fg=FG_MAIN,
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")
        body = tk.Frame(outer, bg=BG_CARD)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        outer.body = body  # type: ignore[attr-defined]
        return outer

    def _btn(
        self,
        parent: tk.Widget,
        text: str,
        cmd,
        bg: str = BG_INP,
        fg: str = FG_MAIN,
        font=("Segoe UI", 9, "bold"),
        padx: int = 10,
        pady: int = 6,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=font,
            padx=padx,
            pady=pady,
        )

    def _build_ui(self) -> None:
        # ── TOP CONNECTION BAR ──────────────────────────────────────────────
        top = tk.Frame(self, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        top.pack(fill="x", padx=12, pady=(10, 8))

        left_top = tk.Frame(top, bg=BG_CARD)
        left_top.pack(side="left", padx=12, pady=8)
        tk.Label(
            left_top,
            text="ESP32 30 CHÂN • TEST BƠM 5V & CẢM BIẾN XKC-Y25-V",
            bg=BG_CARD,
            fg=ACCENT_CYAN,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            left_top,
            text="Đang cấu hình tạm cho ESP32 30 chân: XKC-Y25-V → PC817 (U5) → GPIO 27 (D27)  |  GPIO 26 (D26) → PC817 (U4) → MOSFET Q1 → Bơm 5V",
            bg=BG_CARD,
            fg=FG_DIM,
            font=("Segoe UI", 8),
        ).pack(anchor="w")

        right_top = tk.Frame(top, bg=BG_CARD)
        right_top.pack(side="right", padx=12, pady=8)

        tk.Label(right_top, text="Cổng COM:", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        self.cmb_port = ttk.Combobox(right_top, textvariable=self.var_port, width=22, state="readonly")
        self.cmb_port.pack(side="left", padx=(0, 6))

        self._btn(right_top, "Làm mới", self._refresh_ports, bg=BG_INP, padx=8, pady=4).pack(side="left", padx=(0, 6))
        self.btn_connect = self._btn(right_top, "Kết nối", self._toggle_connect, bg=ACCENT_BLUE, padx=12, pady=4)
        self.btn_connect.pack(side="left", padx=(0, 8))

        self.lbl_conn_badge = tk.Label(
            right_top,
            text="CHƯA KẾT NỐI",
            bg="#334155",
            fg=FG_MAIN,
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=4,
        )
        self.lbl_conn_badge.pack(side="left", padx=(0, 8))

        self._btn(
            right_top,
            "DỪNG KHẨN CẤP",
            lambda: self._send_cmd("STOP"),
            bg=ACCENT_RED,
            fg="#ffffff",
            padx=10,
            pady=4,
        ).pack(side="left")

        # ── MAIN 2-COLUMN AREA ──────────────────────────────────────────────
        main = tk.Frame(self, bg=BG_ROOT)
        main.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        main.columnconfigure(0, weight=1, uniform="col")
        main.columnconfigure(1, weight=1, uniform="col")
        main.rowconfigure(0, weight=3)
        main.rowconfigure(1, weight=2)

        # ── LEFT CARD: XKC-Y25-V SENSOR ─────────────────────────────────────
        card_xkc = self._make_card(main, "1. CẢM BIẾN MỰC NƯỚC XKC-Y25-V (GPIO 27 / D27)", ACCENT_CYAN)
        card_xkc.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        xb = card_xkc.body  # type: ignore[attr-defined]

        x_top = tk.Frame(xb, bg=BG_CARD)
        x_top.pack(fill="x", pady=(0, 8))

        self.canvas_tank = tk.Canvas(
            x_top, width=150, height=145, bg=BG_ROOT, highlightthickness=1, highlightbackground=BORDER
        )
        self.canvas_tank.pack(side="left", padx=(0, 12))

        x_info = tk.Frame(x_top, bg=BG_CARD)
        x_info.pack(side="left", fill="both", expand=True)

        self.lbl_water_big = tk.Label(
            x_info,
            text="CHƯA CÓ DỮ LIỆU",
            bg="#1e293b",
            fg=FG_DIM,
            font=("Segoe UI", 16, "bold"),
            pady=10,
        )
        self.lbl_water_big.pack(fill="x", pady=(0, 8))

        metrics = tk.Frame(x_info, bg=BG_CARD)
        metrics.pack(fill="x")
        metrics.columnconfigure(0, weight=1)
        metrics.columnconfigure(1, weight=1)

        self.lbl_gpio_raw = self._metric_box(metrics, "Mức GPIO ESP32", "—", 0, 0)
        self.lbl_xkc_out = self._metric_box(metrics, "XKC OUT (qua PC817)", "—", 0, 1)
        self.lbl_xkc_changes = self._metric_box(metrics, "Số lần chuyển trạng thái", "0", 1, 0)
        self.lbl_xkc_hold = self._metric_box(metrics, "Giữ trạng thái", "0.0 s", 1, 1)

        # XKC config panel
        x_cfg = tk.Frame(xb, bg=BG_INP, highlightbackground=BORDER, highlightthickness=1)
        x_cfg.pack(fill="x", pady=(4, 0))

        row1 = tk.Frame(x_cfg, bg=BG_INP)
        row1.pack(fill="x", padx=10, pady=(8, 4))
        tk.Checkbutton(
            row1,
            text="Đảo logic PC817 (GPIO LOW = CÓ NƯỚC)",
            variable=self.var_xkc_active_low,
            command=self._on_xkc_active_low_changed,
            bg=BG_INP,
            fg=FG_MAIN,
            selectcolor=BG_ROOT,
            activebackground=BG_INP,
            activeforeground=FG_MAIN,
            font=("Segoe UI", 9),
        ).pack(side="left")

        row2 = tk.Frame(x_cfg, bg=BG_INP)
        row2.pack(fill="x", padx=10, pady=2)
        tk.Checkbutton(
            row2,
            text="Bật INPUT_PULLUP nội (chỉ bật nếu không có R4 10k ngoài)",
            variable=self.var_xkc_pullup,
            command=self._on_xkc_pullup_changed,
            bg=BG_INP,
            fg=FG_DIM,
            selectcolor=BG_ROOT,
            activebackground=BG_INP,
            activeforeground=FG_MAIN,
            font=("Segoe UI", 8),
        ).pack(side="left")

        row3 = tk.Frame(x_cfg, bg=BG_INP)
        row3.pack(fill="x", padx=10, pady=(4, 8))
        tk.Label(row3, text="Lọc nhiễu Debounce (ms):", bg=BG_INP, fg=FG_DIM, font=("Segoe UI", 9)).pack(side="left")
        tk.Entry(
            row3, textvariable=self.var_debounce, width=7, bg=BG_ROOT, fg=FG_MAIN, insertbackground=FG_MAIN, relief="flat"
        ).pack(side="left", padx=6)
        self._btn(row3, "Áp dụng", self._apply_debounce, bg=ACCENT_BLUE, padx=8, pady=2).pack(side="left")
        self._btn(row3, "Reset đếm", lambda: self._send_cmd("RESET_STATS"), bg=BORDER, padx=8, pady=2).pack(side="right")

        # ── RIGHT CARD: PUMP CONTROL ────────────────────────────────────────
        card_pump = self._make_card(main, "2. ĐIỀU KHIỂN BƠM MINI 5V (GPIO 26 / D26)", ACCENT_GREEN)
        card_pump.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))
        pb = card_pump.body  # type: ignore[attr-defined]

        # Status banner
        p_head = tk.Frame(pb, bg=BG_CARD)
        p_head.pack(fill="x", pady=(0, 8))

        self.lbl_pump_big = tk.Label(
            p_head,
            text="BƠM ĐANG TẮT",
            bg="#1e293b",
            fg=FG_DIM,
            font=("Segoe UI", 16, "bold"),
            pady=10,
        )
        self.lbl_pump_big.pack(fill="x")

        self.lbl_pump_sub = tk.Label(
            p_head,
            text="GPIO 26: LOW | Đang chạy: 0.0s | Tổng: 0.0s | Lý do: —",
            bg=BG_CARD,
            fg=FG_DIM,
            font=("Segoe UI", 8),
        )
        self.lbl_pump_sub.pack(anchor="w", pady=(4, 0))

        # Big ON / OFF buttons
        btn_row = tk.Frame(pb, bg=BG_CARD)
        btn_row.pack(fill="x", pady=(2, 8))
        btn_row.columnconfigure(0, weight=1)
        btn_row.columnconfigure(1, weight=1)

        self.btn_pump_on = self._btn(
            btn_row,
            "▶  BẬT BƠM (ON)",
            lambda: self._send_cmd("PUMP:ON"),
            bg=ACCENT_GREEN,
            fg="#052e16",
            font=("Segoe UI", 11, "bold"),
            pady=10,
        )
        self.btn_pump_on.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_pump_off = self._btn(
            btn_row,
            "■  TẮT BƠM (OFF)",
            lambda: self._send_cmd("PUMP:OFF"),
            bg="#475569",
            fg=FG_MAIN,
            font=("Segoe UI", 11, "bold"),
            pady=10,
        )
        self.btn_pump_off.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        # Quick timed pulse
        pulse_box = tk.Frame(pb, bg=BG_INP, highlightbackground=BORDER, highlightthickness=1)
        pulse_box.pack(fill="x", pady=(0, 8))
        pr = tk.Frame(pulse_box, bg=BG_INP)
        pr.pack(fill="x", padx=10, pady=6)
        tk.Label(pr, text="Bơm hẹn giờ:", bg=BG_INP, fg=FG_DIM, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 6))
        for sec in (1, 3, 5, 10):
            self._btn(
                pr, f"{sec}s", lambda s=sec: self._send_cmd(f"PUMP:PULSE:{s * 1000}"), bg=BORDER, padx=8, pady=3
            ).pack(side="left", padx=2)
        tk.Entry(
            pr, textvariable=self.var_pulse_sec, width=5, bg=BG_ROOT, fg=FG_MAIN, insertbackground=FG_MAIN, relief="flat"
        ).pack(side="left", padx=(8, 4))
        tk.Label(pr, text="s", bg=BG_INP, fg=FG_DIM, font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        self._btn(pr, "Chạy", self._run_custom_pulse, bg=ACCENT_BLUE, padx=8, pady=3).pack(side="left")

        # Interlock mode selector
        mode_box = tk.Frame(pb, bg=BG_INP, highlightbackground=BORDER, highlightthickness=1)
        mode_box.pack(fill="x", pady=(0, 8))
        tk.Label(
            mode_box,
            text="Chế độ liên động với XKC-Y25-V:",
            bg=BG_INP,
            fg=ACCENT_AMBER,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=10, pady=(6, 2))
        for code, desc in MODE_OPTIONS:
            tk.Radiobutton(
                mode_box,
                text=desc,
                variable=self.var_mode,
                value=code,
                command=lambda c=code: self._send_cmd(f"MODE:{c}"),
                bg=BG_INP,
                fg=FG_MAIN,
                selectcolor=BG_ROOT,
                activebackground=BG_INP,
                activeforeground=FG_MAIN,
                font=("Segoe UI", 8),
            ).pack(anchor="w", padx=12, pady=1)

        # PWM & Safety row
        pwm_box = tk.Frame(pb, bg=BG_INP, highlightbackground=BORDER, highlightthickness=1)
        pwm_box.pack(fill="x")
        pw1 = tk.Frame(pwm_box, bg=BG_INP)
        pw1.pack(fill="x", padx=10, pady=(6, 2))
        tk.Label(pw1, text="Công suất PWM:", bg=BG_INP, fg=FG_DIM, font=("Segoe UI", 8)).pack(side="left")
        self.lbl_pwm_val = tk.Label(pw1, text="100%", bg=BG_INP, fg=ACCENT_CYAN, font=("Segoe UI", 8, "bold"), width=5)
        self.lbl_pwm_val.pack(side="left", padx=4)
        self.scale_pwm = tk.Scale(
            pw1,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.var_pwm,
            command=self._on_pwm_slide,
            showvalue=False,
            bg=BG_INP,
            troughcolor=BG_ROOT,
            highlightthickness=0,
            activebackground=ACCENT_CYAN,
        )
        self.scale_pwm.pack(side="left", fill="x", expand=True, padx=6)
        self.scale_pwm.bind("<ButtonRelease-1>", lambda _e: self._send_cmd(f"PUMP:PWM:{self.var_pwm.get()}"))
        self._btn(pw1, "100%", lambda: self._set_pwm_preset(100), bg=BORDER, padx=6, pady=1).pack(side="left", padx=2)

        pw2 = tk.Frame(pwm_box, bg=BG_INP)
        pw2.pack(fill="x", padx=10, pady=(2, 6))
        tk.Label(pw2, text="Tự ngắt an toàn (s, 0=tắt):", bg=BG_INP, fg=FG_DIM, font=("Segoe UI", 8)).pack(side="left")
        tk.Entry(
            pw2, textvariable=self.var_max_run, width=5, bg=BG_ROOT, fg=FG_MAIN, insertbackground=FG_MAIN, relief="flat"
        ).pack(side="left", padx=6)
        self._btn(pw2, "Lưu", self._apply_max_run, bg=BORDER, padx=6, pady=1).pack(side="left")

        tk.Checkbutton(
            pw2,
            text="Bơm Active-HIGH",
            variable=self.var_pump_active_high,
            command=lambda: self._send_cmd(f"PUMP:ACTIVE_HIGH:{1 if self.var_pump_active_high.get() else 0}"),
            bg=BG_INP,
            fg=FG_DIM,
            selectcolor=BG_ROOT,
            activebackground=BG_INP,
            activeforeground=FG_MAIN,
            font=("Segoe UI", 8),
        ).pack(side="right")

        # ── BOTTOM LEFT: LIVE TIMELINE CHART ────────────────────────────────
        card_chart = self._make_card(main, "3. BIỂU ĐỒ TÍN HIỆU THỜI GIAN THỰC (XKC & BƠM)", ACCENT_PURPLE)
        card_chart.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        cb = card_chart.body  # type: ignore[attr-defined]

        self.canvas_wave = tk.Canvas(cb, bg=BG_ROOT, height=150, highlightthickness=1, highlightbackground=BORDER)
        self.canvas_wave.pack(fill="both", expand=True)
        self.canvas_wave.bind("<Configure>", lambda _e: self._draw_timeline())

        pin_bar = tk.Frame(cb, bg=BG_CARD)
        pin_bar.pack(fill="x", pady=(6, 0))
        tk.Label(pin_bar, text="Đổi chân nóng — XKC GPIO:", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 8)).pack(side="left")
        tk.Entry(
            pin_bar, textvariable=self.var_xkc_pin, width=4, bg=BG_INP, fg=FG_MAIN, insertbackground=FG_MAIN, relief="flat"
        ).pack(side="left", padx=4)
        tk.Label(pin_bar, text="Bơm GPIO:", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))
        tk.Entry(
            pin_bar, textvariable=self.var_pump_pin, width=4, bg=BG_INP, fg=FG_MAIN, insertbackground=FG_MAIN, relief="flat"
        ).pack(side="left", padx=4)
        self._btn(pin_bar, "Đổi chân", self._apply_pins, bg=BORDER, padx=8, pady=2).pack(side="left", padx=6)
        self._btn(pin_bar, "Mặc định (27, 26)", self._reset_default_pins, bg=BG_INP, padx=8, pady=2).pack(side="left")

        # ── BOTTOM RIGHT: EVENT LOG ─────────────────────────────────────────
        card_log = self._make_card(main, "4. NHẬT KÝ SỰ KIỆN & CHẨN ĐOÁN", ACCENT_AMBER)
        card_log.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        lb = card_log.body  # type: ignore[attr-defined]

        log_frame = tk.Frame(lb, bg=BG_ROOT)
        log_frame.pack(fill="both", expand=True)

        self.txt_log = tk.Text(
            log_frame,
            bg=BG_ROOT,
            fg=FG_MAIN,
            font=("Consolas", 9),
            relief="flat",
            state="disabled",
            wrap="word",
            height=8,
        )
        sb = tk.Scrollbar(log_frame, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.txt_log.tag_configure("INFO", foreground=ACCENT_CYAN)
        self.txt_log.tag_configure("WARN", foreground=ACCENT_AMBER)
        self.txt_log.tag_configure("ERROR", foreground=ACCENT_RED)
        self.txt_log.tag_configure("DIM", foreground=FG_DIM)

        log_btns = tk.Frame(lb, bg=BG_CARD)
        log_btns.pack(fill="x", pady=(6, 0))
        self._btn(log_btns, "Xóa nhật ký", self._clear_log, bg=BG_INP, padx=8, pady=2).pack(side="right")
        self._btn(log_btns, "Đọc STATUS", lambda: self._send_cmd("STATUS"), bg=BG_INP, padx=8, pady=2).pack(
            side="right", padx=6
        )

        self._log("INFO", "Sẵn sàng. Chọn cổng COM của ESP32 30 chân rồi bấm 'Kết nối'.")
        self._log("DIM", "Cấu hình tạm ESP32 30 chân: XKC-Y25-V = GPIO 27 (D27), Bơm 5V = GPIO 26 (D26).")

    def _metric_box(self, parent: tk.Widget, label: str, val: str, row: int, col: int) -> tk.Label:
        box = tk.Frame(parent, bg=BG_INP, highlightbackground=BORDER, highlightthickness=1)
        box.grid(row=row, column=col, sticky="ew", padx=3, pady=3)
        tk.Label(box, text=label, bg=BG_INP, fg=FG_DIM, font=("Segoe UI", 8)).pack(anchor="w", padx=8, pady=(4, 0))
        lbl = tk.Label(box, text=val, bg=BG_INP, fg=FG_MAIN, font=("Consolas", 10, "bold"))
        lbl.pack(anchor="w", padx=8, pady=(0, 4))
        return lbl

    # ── SERIAL & COMMAND ACTIONS ────────────────────────────────────────────
    def _refresh_ports(self) -> None:
        ports = list(serial.tools.list_ports.comports())
        items = [f"{p.device} — {p.description}" for p in ports]
        self.cmb_port["values"] = items
        if not items:
            self.var_port.set("")
            return
        # Uu tien chon cong ESP32 / USB Serial / CH340 / CP210x
        chosen = items[0]
        for item in items:
            low = item.lower()
            if any(k in low for k in ("esp32", "usb serial", "jtag", "cp210", "ch340", "ch910")):
                chosen = item
                break
        self.var_port.set(chosen)

    def _selected_port_device(self) -> str:
        val = self.var_port.get().strip()
        if " — " in val:
            return val.split(" — ", 1)[0].strip()
        return val

    def _toggle_connect(self) -> None:
        if self.worker.connected:
            self.worker.disconnect()
            self._set_connected_ui(False)
            self._log("INFO", "Đã ngắt kết nối Serial.")
            return

        port = self._selected_port_device()
        if not port:
            messagebox.showwarning("Chưa chọn cổng COM", "Không tìm thấy cổng COM. Hãy cắm cáp ESP32-S3 rồi bấm Làm mới.")
            return
        try:
            self.worker.connect(port, 115200)
            self._set_connected_ui(True, port)
            self._log("INFO", f"Đã kết nối {port} (115200 baud).")
            self.after(200, lambda: self._send_cmd("STATUS"))
        except Exception as exc:
            messagebox.showerror("Lỗi kết nối", f"Không mở được cổng {port}:\n{exc}")

    def _set_connected_ui(self, is_conn: bool, port: str = "") -> None:
        if is_conn:
            self.btn_connect.configure(text="Ngắt kết nối", bg="#475569")
            self.lbl_conn_badge.configure(text=f"ĐÃ KẾT NỐI ({port})", bg=ACCENT_GREEN, fg="#052e16")
        else:
            self.btn_connect.configure(text="Kết nối", bg=ACCENT_BLUE)
            self.lbl_conn_badge.configure(text="CHƯA KẾT NỐI", bg="#334155", fg=FG_MAIN)

    def _send_cmd(self, cmd: str) -> None:
        if not self.worker.connected:
            self._log("WARN", f"Chưa kết nối ESP32-S3 — không thể gửi lệnh: {cmd}")
            return
        self.worker.send(cmd)

    def _on_xkc_active_low_changed(self) -> None:
        if self._syncing_controls:
            return
        self._send_cmd(f"XKC:ACTIVE_LOW:{1 if self.var_xkc_active_low.get() else 0}")

    def _on_xkc_pullup_changed(self) -> None:
        if self._syncing_controls:
            return
        self._send_cmd(f"XKC:PULLUP:{1 if self.var_xkc_pullup.get() else 0}")

    def _apply_debounce(self) -> None:
        try:
            ms = int(self.var_debounce.get().strip())
        except ValueError:
            messagebox.showerror("Giá trị không hợp lệ", "Debounce phải là số nguyên (ms).")
            return
        self._send_cmd(f"XKC:DEBOUNCE:{ms}")

    def _apply_max_run(self) -> None:
        try:
            sec = int(self.var_max_run.get().strip())
        except ValueError:
            messagebox.showerror("Giá trị không hợp lệ", "Thời gian tự ngắt phải là số nguyên (giây).")
            return
        self._send_cmd(f"SAFETY:MAX_RUN:{sec}")

    def _run_custom_pulse(self) -> None:
        try:
            sec = float(self.var_pulse_sec.get().strip())
            if sec <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Giá trị không hợp lệ", "Nhập số giây > 0 (ví dụ: 2.5 hoặc 5).")
            return
        self._send_cmd(f"PUMP:PULSE:{int(sec * 1000)}")

    def _on_pwm_slide(self, val: str) -> None:
        self.lbl_pwm_val.configure(text=f"{int(float(val))}%")

    def _set_pwm_preset(self, pct: int) -> None:
        self.var_pwm.set(pct)
        self.lbl_pwm_val.configure(text=f"{pct}%")
        self._send_cmd(f"PUMP:PWM:{pct}")

    def _apply_pins(self) -> None:
        try:
            x_pin = int(self.var_xkc_pin.get().strip())
            p_pin = int(self.var_pump_pin.get().strip())
        except ValueError:
            messagebox.showerror("Chân GPIO không hợp lệ", "Vui lòng nhập số GPIO hợp lệ.")
            return
        self._send_cmd(f"PINS:{x_pin},{p_pin}")

    def _reset_default_pins(self) -> None:
        self.var_xkc_pin.set("27")
        self.var_pump_pin.set("26")
        self._send_cmd("PINS:27,26")

    # ── TELEMETRY PROCESSING ────────────────────────────────────────────────
    def _poll_queue(self) -> None:
        got_state = False
        while True:
            try:
                msg = self.rx_queue.get_nowait()
            except queue.Empty:
                break

            mtype = msg.get("type")
            if mtype == "state":
                self.state.update(msg)
                self._last_rx_time = time.time()
                self.history.append(
                    (
                        self._last_rx_time,
                        1 if self.state.get("water") else 0,
                        1 if self.state.get("pump_on") else 0,
                    )
                )
                got_state = True
            elif mtype == "event":
                self._log(msg.get("level", "INFO"), msg.get("msg", ""))
            elif mtype == "_disconnect":
                self.worker.disconnect()
                self._set_connected_ui(False)
                self._log("ERROR", msg.get("msg", "Mất kết nối Serial."))
            elif mtype == "_error":
                self._log("ERROR", msg.get("msg", "Lỗi Serial."))
            elif mtype == "raw":
                self._log("DIM", f"[RAW] {msg.get('msg', '')}")

        if got_state:
            self._update_views()

        self.after(60, self._poll_queue)

    def _update_views(self) -> None:
        st = self.state
        water = bool(st.get("water", False))
        pump_on = bool(st.get("pump_on", False))
        xkc_raw = int(st.get("xkc_raw", 1))
        xkc_out = int(st.get("xkc_out_est", 0))
        xkc_pin = int(st.get("xkc_pin", 7))
        pump_pin = int(st.get("pump_pin", 14))
        pump_raw = int(st.get("pump_raw", 0))

        # XKC status
        if water:
            self.lbl_water_big.configure(text="💧  CÓ NƯỚC (WET)", bg="#0c4a6e", fg="#38bdf8")
        else:
            self.lbl_water_big.configure(text="⚠  CẠN NƯỚC (DRY)", bg="#451a03", fg="#fbbf24")

        self.lbl_gpio_raw.configure(
            text=f"GPIO {xkc_pin}: {'HIGH (3.3V)' if xkc_raw else 'LOW (0V)'}",
            fg=ACCENT_CYAN if xkc_raw == 0 else FG_MAIN,
        )
        self.lbl_xkc_out.configure(
            text=f"{'HIGH (5V)' if xkc_out else 'LOW (0V)'}",
            fg=ACCENT_GREEN if xkc_out else FG_DIM,
        )
        self.lbl_xkc_changes.configure(text=str(st.get("xkc_changes", 0)))
        self.lbl_xkc_hold.configure(text=f"{st.get('xkc_hold_ms', 0) / 1000.0:.1f} s")

        # Pump status
        pwm_pct = int(st.get("pump_pwm", 100))
        pulse_rem = int(st.get("pulse_rem_ms", 0))
        if pump_on:
            pulse_txt = f"  ({pulse_rem / 1000.0:.1f}s)" if pulse_rem > 0 else ""
            self.lbl_pump_big.configure(
                text=f"⚡ BƠM ĐANG CHẠY ({pwm_pct}%){pulse_txt}",
                bg="#14532d",
                fg="#4ade80",
            )
        else:
            self.lbl_pump_big.configure(text="BƠM ĐANG TẮT", bg="#1e293b", fg=FG_DIM)

        run_s = st.get("pump_run_ms", 0) / 1000.0
        tot_s = st.get("pump_total_ms", 0) / 1000.0
        reason = st.get("last_reason", "—")
        self.lbl_pump_sub.configure(
            text=f"GPIO {pump_pin}: {'HIGH' if pump_raw else 'LOW'} | Chạy hiện tại: {run_s:.1f}s | Tổng: {tot_s:.1f}s | Lý do: {reason}"
        )

        # Sync controls with firmware state (without re-firing commands)
        self._syncing_controls = True
        try:
            self.var_mode.set(st.get("mode", "MANUAL"))
            self.var_xkc_active_low.set(bool(st.get("xkc_active_low", True)))
            self.var_xkc_pullup.set(bool(st.get("xkc_pullup", False)))
            self.var_pump_active_high.set(bool(st.get("pump_active_high", True)))
            self.lbl_pwm_val.configure(text=f"{pwm_pct}%")
        finally:
            self._syncing_controls = False

        self._draw_tank_visual()
        self._draw_timeline()

    def _draw_tank_visual(self) -> None:
        c = self.canvas_tank
        c.delete("all")
        water = bool(self.state.get("water", False))
        pump_on = bool(self.state.get("pump_on", False))

        # Tank walls
        c.create_rectangle(24, 18, 108, 128, outline="#64748b", width=2)
        if water:
            c.create_rectangle(26, 48, 106, 126, fill="#0284c7", outline="")
            c.create_line(26, 48, 106, 48, fill="#7dd3fc", width=2)
            c.create_text(66, 88, text="CÓ NƯỚC", fill="#e0f2fe", font=("Segoe UI", 9, "bold"))
        else:
            c.create_rectangle(26, 114, 106, 126, fill="#1e293b", outline="")
            c.create_text(66, 78, text="CẠN", fill="#fbbf24", font=("Segoe UI", 10, "bold"))

        # XKC-Y25-V sensor mounted outside tank wall
        c.create_rectangle(110, 56, 138, 92, fill="#1e293b", outline=ACCENT_CYAN if water else "#64748b", width=2)
        c.create_oval(119, 68, 129, 78, fill=ACCENT_GREEN if water else "#475569", outline="")
        c.create_text(124, 102, text="XKC", fill=FG_DIM, font=("Segoe UI", 7, "bold"))

        # Pump indicator dot at bottom
        c.create_text(
            75,
            137,
            text="BƠM: ON" if pump_on else "BƠM: OFF",
            fill=ACCENT_GREEN if pump_on else FG_DIM,
            font=("Segoe UI", 8, "bold"),
        )

    def _draw_timeline(self) -> None:
        c = self.canvas_wave
        c.delete("all")
        w = max(c.winfo_width(), 200)
        h = max(c.winfo_height(), 120)

        # Grid lines
        for frac in (0.25, 0.5, 0.75):
            y = int(h * frac)
            c.create_line(0, y, w, y, fill="#1e293b", dash=(3, 3))

        # Labels
        c.create_text(8, 12, text="XKC (Nước)", anchor="w", fill=ACCENT_CYAN, font=("Segoe UI", 8, "bold"))
        c.create_text(
            8,
            h // 2 + 12,
            text=f"BƠM (GPIO {self.state.get('pump_pin', 26)})",
            anchor="w",
            fill=ACCENT_GREEN,
            font=("Segoe UI", 8, "bold"),
        )

        if len(self.history) < 2:
            c.create_text(w // 2, h // 2, text="Chờ dữ liệu từ ESP32-S3...", fill=FG_DIM, font=("Segoe UI", 9))
            return

        samples = list(self.history)
        n = len(samples)
        left_pad = 92
        plot_w = max(w - left_pad - 10, 50)
        dx = plot_w / max(self.history.maxlen - 1, 1)

        y_xkc_hi = 20
        y_xkc_lo = h // 2 - 12
        y_pmp_hi = h // 2 + 20
        y_pmp_lo = h - 14

        pts_xkc: list[float] = []
        pts_pmp: list[float] = []
        for idx, (_t, w_val, p_val) in enumerate(samples):
            x = left_pad + idx * dx
            yw = y_xkc_hi if w_val else y_xkc_lo
            yp = y_pmp_hi if p_val else y_pmp_lo
            if pts_xkc:
                pts_xkc.extend([x, pts_xkc[-1]])
                pts_pmp.extend([x, pts_pmp[-1]])
            pts_xkc.extend([x, yw])
            pts_pmp.extend([x, yp])

        if len(pts_xkc) >= 4:
            c.create_line(*pts_xkc, fill=ACCENT_CYAN, width=2)
            c.create_line(*pts_pmp, fill=ACCENT_GREEN, width=2)

    def _log(self, level: str, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        tag = level if level in ("INFO", "WARN", "ERROR", "DIM") else "INFO"
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", f"[{ts}] ", "DIM")
        self.txt_log.insert("end", f"[{level}] {msg}\n", tag)
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    def _on_close(self) -> None:
        # Gui lenh tat bom an toan truoc khi dong
        if self.worker.connected:
            self.worker.send("PUMP:OFF")
            time.sleep(0.05)
        self.worker.disconnect()
        self.destroy()


if __name__ == "__main__":
    app = PumpXkcApp()
    app.mainloop()
