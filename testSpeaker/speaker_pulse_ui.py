r"""
ESP32-S3 Speaker Pulse Generator & Multimeter Probe UI
======================================================
Giao dien chuyen dung cho firmware phat xung test loa I2S MAX98357A
va do chan GPIO bang dong ho van nang (khop so do D:\Plant\main.fzz).

Chuc nang chinh:
  1. Che do Phat Xung I2S (I2S Pulse & Waveform Generator):
     - 8 dang song: PULSE (chinh Duty 1-99%), SQUARE, SINE, TRIANGLE,
       SAWTOOTH, CLICK (xung kim), SWEEP (quet tan so), NOISE.
     - 3 che do nhip: CW (Lien tuc), BEEP (ON/OFF ms), ONCE (1 lan).
     - Chinh nong: Tan so (1-10000 Hz), Duty, Bien do, Sample Rate,
       16/32-bit, I2S/MSB, I2S_0/I2S_1, Slot BOTH/LEFT/RIGHT,
       SD_MODE 3 trang thai (HIGH / FLOAT / LOW), va doi chan GPIO nong.
  2. Che do Do Chan GPIO (Multimeter Probe Mode):
     - Ep tung chan LRC/BCLK/DIN/SD len 3.3V, 0V, Tha noi, Nhay 1Hz,
       hoac Quet tuan tu (Walking 3.3V) de kiem tra day noi bang dong ho.
"""

from __future__ import annotations

import json
import math
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
ACCENT_GREEN = "#22c55e"
ACCENT_AMBER = "#f59e0b"
ACCENT_RED = "#ef4444"
ACCENT_PURPLE = "#a855f7"

WAVE_LIST = [
    ("PULSE", "Xung Duty"),
    ("SQUARE", "Xung Vuong"),
    ("SINE", "Song Sin"),
    ("TRIANGLE", "Tam Giac"),
    ("SAWTOOTH", "Rang Cua"),
    ("CLICK", "Xung Kim"),
    ("SWEEP", "Quet Tan"),
    ("NOISE", "Nhieu Trang"),
]

PIN_NAMES = ["LRC (WS)", "BCLK", "DIN", "SD_MODE"]


class SerialBridge:
    def __init__(self, out_q: queue.Queue):
        self.out_q = out_q
        self.ser: serial.Serial | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._rx_thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self, port: str, baud: int) -> None:
        self.disconnect()
        s = serial.Serial()
        s.port = port
        s.baudrate = baud
        s.timeout = 0.05
        s.write_timeout = 0.25
        s.dtr = False
        s.rts = False
        s.open()
        self.ser = s
        self._stop.clear()
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=0.4)
        self._rx_thread = None
        with self._lock:
            if self.ser is not None:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None

    def send(self, cmd: str) -> bool:
        if not self.connected:
            return False
        data = (cmd.strip() + "\n").encode("utf-8", errors="ignore")
        with self._lock:
            if not self.ser or not self.ser.is_open:
                return False
            try:
                self.ser.write(data)
                return True
            except Exception as exc:
                self.out_q.put(("err", str(exc)))
                return False

    def _rx_loop(self) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            s = self.ser
            if s is None or not s.is_open:
                break
            try:
                n = s.in_waiting
                chunk = s.read(n if n > 0 else 1)
                if not chunk:
                    continue
                buf.extend(chunk)
                while b"\n" in buf:
                    raw, _, rest = buf.partition(b"\n")
                    buf = bytearray(rest)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line:
                        self.out_q.put(("line", line))
                if len(buf) > 4096:
                    buf.clear()
            except Exception as exc:
                if not self._stop.is_set():
                    self.out_q.put(("err", str(exc)))
                break


class SpeakerPulseApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ESP32-S3 Pulse Generator & MAX98357A Multimeter Probe")
        self.root.geometry("1260x840")
        self.root.minsize(1100, 720)
        self.root.configure(bg=BG_ROOT)

        self.ev_q: queue.Queue = queue.Queue()
        self.link = SerialBridge(self.ev_q)

        # Trang thai dong bo tu ESP32
        self.state: dict = {
            "mode": "I2S",
            "running": False,
            "i2s_ok": False,
            "gate_on": False,
            "lrclk": 15,
            "bclk": 16,
            "din": 17,
            "sd_pin": 18,
            "i2s_port": 0,
            "rate": 24000,
            "bits": 16,
            "fmt": "I2S",
            "slot": "BOTH",
            "sd": "HIGH",
            "wave": "PULSE",
            "freq": 1000.0,
            "sweep_end": 4000.0,
            "sweep_ms": 2500,
            "duty": 50.0,
            "amp": 60,
            "gate": "CW",
            "on_ms": 400,
            "off_ms": 400,
            "live_freq": 1000.0,
            "peak": 0.0,
            "probe_walk": False,
            "probe_ms": 1000,
            "walk_idx": -1,
            "pm": ["LOW", "LOW", "LOW", "LOW"],
            "pl": [0, 0, 0, 0],
        }
        self._anim_phase = 0.0
        self._debounce_ids: dict[str, str] = {}

        # Bien Tkinter
        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value="115200")
        self.freq_var = tk.DoubleVar(value=1000.0)
        self.sweep_end_var = tk.DoubleVar(value=4000.0)
        self.sweep_ms_var = tk.IntVar(value=2500)
        self.duty_var = tk.DoubleVar(value=50.0)
        self.amp_var = tk.IntVar(value=60)
        self.gate_var = tk.StringVar(value="CW")
        self.on_ms_var = tk.IntVar(value=400)
        self.off_ms_var = tk.IntVar(value=400)

        self.pin_lrc_var = tk.IntVar(value=15)
        self.pin_bclk_var = tk.IntVar(value=16)
        self.pin_din_var = tk.IntVar(value=17)
        self.pin_sd_var = tk.IntVar(value=18)

        self.rate_var = tk.StringVar(value="24000")
        self.bits_var = tk.StringVar(value="16")
        self.fmt_var = tk.StringVar(value="I2S")
        self.i2s_port_var = tk.StringVar(value="0")
        self.slot_var = tk.StringVar(value="BOTH")
        self.probe_ms_var = tk.IntVar(value=1000)
        self.manual_cmd_var = tk.StringVar()

        self.wave_buttons: dict[str, tk.Button] = {}
        self.sd_buttons: dict[str, tk.Button] = {}
        self.probe_row_btns: list[dict[str, tk.Button]] = []
        self.probe_row_lbls: list[tk.Label] = []
        self.meter_val_lbls: dict[str, tk.Label] = {}

        self._setup_style()
        self._build_ui()
        self._refresh_ports()
        self._update_mode_views()
        self._update_multimeter_table()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(40, self._tick)

    def _setup_style(self) -> None:
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TCombobox", fieldbackground=BG_INP, background=BG_INP, foreground=FG_MAIN)

    def _card(self, parent: tk.Widget, title: str, accent: str = ACCENT_CYAN) -> tk.Frame:
        box = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        hdr = tk.Label(
            box,
            text=title,
            bg=BG_CARD,
            fg=accent,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
            padx=12,
            pady=6,
        )
        hdr.pack(fill="x")
        tk.Frame(box, bg=BORDER, height=1).pack(fill="x")
        body = tk.Frame(box, bg=BG_CARD, padx=12, pady=10)
        body.pack(fill="both", expand=True)
        box._body = body  # type: ignore[attr-defined]
        return box

    def _btn(
        self,
        parent: tk.Widget,
        text: str,
        cmd,
        bg: str = BG_INP,
        fg: str = FG_MAIN,
        active: str = "#334155",
        font=("Segoe UI", 9, "bold"),
        padx: int = 10,
        pady: int = 5,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            bg=bg,
            fg=fg,
            activebackground=active,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=font,
            padx=padx,
            pady=pady,
        )

    def _build_ui(self) -> None:
        # ---- TOP HEADER ----
        top = tk.Frame(self.root, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1, padx=12, pady=8)
        top.pack(fill="x", padx=10, pady=(10, 6))

        tk.Label(
            top,
            text="ESP32-S3 PULSE GENERATOR & SPEAKER PROBE",
            bg=BG_CARD,
            fg=ACCENT_CYAN,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left", padx=(0, 16))

        tk.Label(top, text="COM:", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9)).pack(side="left")
        self.port_cb = ttk.Combobox(top, textvariable=self.port_var, width=28, state="readonly")
        self.port_cb.pack(side="left", padx=(4, 6))
        self._btn(top, "Quet", self._refresh_ports).pack(side="left", padx=(0, 8))

        tk.Label(top, text="Baud:", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9)).pack(side="left")
        ttk.Combobox(
            top, textvariable=self.baud_var, width=8, state="readonly", values=["115200", "921600"]
        ).pack(side="left", padx=(4, 8))

        self.conn_btn = self._btn(
            top, "Ket noi ESP32", self._toggle_connect, bg="#15803d", active="#16a34a", padx=14
        )
        self.conn_btn.pack(side="left", padx=(2, 10))

        self.status_pill = tk.Label(
            top, text="OFFLINE", bg="#334155", fg=FG_MAIN, font=("Consolas", 9, "bold"), padx=10, pady=4
        )
        self.status_pill.pack(side="right")

        # ---- MODE SWITCHER BAR ----
        mbar = tk.Frame(self.root, bg=BG_ROOT)
        mbar.pack(fill="x", padx=10, pady=(0, 6))

        self.btn_mode_i2s = self._btn(
            mbar,
            "1. CHE DO PHAT XUNG I2S (TEST LOA MAX98357A)",
            lambda: self._switch_mode("I2S"),
            bg="#0284c7",
            active="#0369a1",
            font=("Segoe UI", 10, "bold"),
            pady=8,
        )
        self.btn_mode_i2s.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_mode_probe = self._btn(
            mbar,
            "2. CHE DO DO CHAN GPIO (DO BANG DONG HO VAN NANG)",
            lambda: self._switch_mode("PROBE"),
            bg=BG_INP,
            active="#334155",
            font=("Segoe UI", 10, "bold"),
            pady=8,
        )
        self.btn_mode_probe.pack(side="left", fill="x", expand=True)

        # ---- 2-COLUMN WORKSPACE ----
        body = tk.Frame(self.root, bg=BG_ROOT)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        body.columnconfigure(0, weight=13)
        body.columnconfigure(1, weight=9)
        body.rowconfigure(0, weight=1)

        self.left_col = tk.Frame(body, bg=BG_ROOT)
        self.left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.right_col = tk.Frame(body, bg=BG_ROOT)
        self.right_col.grid(row=0, column=1, sticky="nsew")

        # ---- LEFT TOP: LIVE OSCILLOSCOPE / PROBE VISUALIZER ----
        c_scope = self._card(self.left_col, "MO PHONG DANG XUNG / TRANG THAI CHAN TRUC QUAN", ACCENT_CYAN)
        c_scope.pack(fill="x", pady=(0, 8))
        b_sc = c_scope._body  # type: ignore[attr-defined]

        self.scope_canvas = tk.Canvas(
            b_sc, bg=BG_ROOT, height=175, highlightbackground=BORDER, highlightthickness=1
        )
        self.scope_canvas.pack(fill="x")

        # ---- LEFT BOTTOM: DYNAMIC CONTROL PANEL (I2S vs PROBE) ----
        self.panel_container = tk.Frame(self.left_col, bg=BG_ROOT)
        self.panel_container.pack(fill="both", expand=True)

        self._build_i2s_panel()
        self._build_probe_panel()

        # ---- RIGHT 1: LIVE MULTIMETER EXPECTED VOLTAGES ----
        c_meter = self._card(self.right_col, "BANG DIEN AP KY VONG KHI DO DONG HO (DC V)", ACCENT_AMBER)
        c_meter.pack(fill="x", pady=(0, 8))
        b_m = c_meter._body  # type: ignore[attr-defined]

        tk.Label(
            b_m,
            text="Kep que DEN vao GND cua MAX98357A, cham que DO vao cac chan sau:",
            bg=BG_CARD,
            fg=FG_DIM,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(0, 6))

        mgrid = tk.Frame(b_m, bg=BG_ROOT, padx=8, pady=6, highlightbackground=BORDER, highlightthickness=1)
        mgrid.pack(fill="x")
        mgrid.columnconfigure(1, weight=1)

        meter_rows = [
            ("vin", "VIN (Nguon 5V)"),
            ("lrc", "LRC / LRCLK"),
            ("bclk", "BCLK"),
            ("din", "DIN"),
            ("sd", "SD_MODE"),
            ("vo", "VO+ / VO- (DC)"),
        ]
        for r, (key, label) in enumerate(meter_rows):
            tk.Label(
                mgrid, text=label, bg=BG_ROOT, fg=FG_DIM, font=("Consolas", 9, "bold"), anchor="w", width=18
            ).grid(row=r, column=0, sticky="w", pady=2)
            vl = tk.Label(mgrid, text="--", bg=BG_ROOT, fg=ACCENT_GREEN, font=("Consolas", 9, "bold"), anchor="w")
            vl.grid(row=r, column=1, sticky="w", padx=(6, 0), pady=2)
            self.meter_val_lbls[key] = vl

        # ---- RIGHT 2: HARDWARE I2S & PIN REMAPPER ----
        c_hw = self._card(self.right_col, "CAU HINH PHAN CUNG I2S & DOI CHAN GPIO NONG", ACCENT_PURPLE)
        c_hw.pack(fill="x", pady=(0, 8))
        b_hw = c_hw._body  # type: ignore[attr-defined]

        # 4 chan GPIO
        prow = tk.Frame(b_hw, bg=BG_CARD)
        prow.pack(fill="x", pady=(0, 6))
        for txt, var in (
            ("LRC:", self.pin_lrc_var),
            ("BCLK:", self.pin_bclk_var),
            ("DIN:", self.pin_din_var),
            ("SD:", self.pin_sd_var),
        ):
            tk.Label(prow, text=txt, bg=BG_CARD, fg=FG_DIM, font=("Consolas", 9, "bold")).pack(side="left")
            tk.Spinbox(
                prow, from_=1, to=48, textvariable=var, width=4,
                bg=BG_INP, fg=FG_MAIN, insertbackground=FG_MAIN, relief="flat", font=("Consolas", 9, "bold")
            ).pack(side="left", padx=(2, 8))

        pbtn_row = tk.Frame(b_hw, bg=BG_CARD)
        pbtn_row.pack(fill="x", pady=(0, 8))
        self._btn(pbtn_row, "Ap dung chan", self._apply_pins, bg="#7e22ce", active="#9333ea").pack(
            side="left", padx=(0, 4)
        )
        self._btn(pbtn_row, "Chuan main.fzz (15/16/17/18)", self._preset_pins_main_fzz).pack(
            side="left", padx=(0, 4)
        )
        self._btn(
            pbtn_row, "Dao LRC<->BCLK", lambda: self._send("SWAP_CLK"), bg="#b45309", active="#d97706"
        ).pack(side="left", fill="x", expand=True)

        # SD_MODE 3 trang thai
        sd_row = tk.Frame(b_hw, bg=BG_CARD)
        sd_row.pack(fill="x", pady=(0, 8))
        tk.Label(sd_row, text="Chan SD_MODE:", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9, "bold")).pack(
            side="left", padx=(0, 6)
        )
        for st_key, st_lbl in (("HIGH", "HIGH (3.3V)"), ("FLOAT", "THA NOI (FLOAT)"), ("LOW", "LOW (TAT)")):
            btn = self._btn(sd_row, st_lbl, lambda k=st_key: self._send(f"SD:{k}"), padx=8, pady=4)
            btn.pack(side="left", fill="x", expand=True, padx=2)
            self.sd_buttons[st_key] = btn

        # Tham so I2S nang cao (Rate, Bits, Format, Port, Slot)
        i2s_r1 = tk.Frame(b_hw, bg=BG_CARD)
        i2s_r1.pack(fill="x", pady=(0, 4))
        self._labeled_combo(
            i2s_r1, "Rate:", self.rate_var, ["8000", "16000", "22050", "24000", "32000", "44100", "48000"],
            lambda: self._send(f"RATE:{self.rate_var.get()}"), width=7
        )
        self._labeled_combo(
            i2s_r1, "Bits:", self.bits_var, ["16", "32"],
            lambda: self._send(f"BITS:{self.bits_var.get()}"), width=4
        )
        self._labeled_combo(
            i2s_r1, "Chuan:", self.fmt_var, ["I2S", "MSB"],
            lambda: self._send(f"FMT:{self.fmt_var.get()}"), width=5
        )

        i2s_r2 = tk.Frame(b_hw, bg=BG_CARD)
        i2s_r2.pack(fill="x")
        self._labeled_combo(
            i2s_r2, "I2S Port:", self.i2s_port_var, ["0", "1"],
            lambda: self._send(f"PORT:{self.i2s_port_var.get()}"), width=4
        )
        self._labeled_combo(
            i2s_r2, "Khe kenh:", self.slot_var, ["BOTH", "LEFT", "RIGHT"],
            lambda: self._send(f"SLOT:{self.slot_var.get()}"), width=7
        )

        # ---- RIGHT 3: LOG & COMMAND ----
        c_log = self._card(self.right_col, "NHAT KY PHAN HOI ESP32 & LENH TRUC TIEP", FG_DIM)
        c_log.pack(fill="both", expand=True)
        b_log = c_log._body  # type: ignore[attr-defined]

        self.log_box = tk.Text(
            b_log, bg=BG_ROOT, fg="#cbd5e1", font=("Consolas", 9), height=8, relief="flat", state="disabled"
        )
        self.log_box.pack(fill="both", expand=True, pady=(0, 6))

        crow = tk.Frame(b_log, bg=BG_CARD)
        crow.pack(fill="x")
        ent = tk.Entry(
            crow, textvariable=self.manual_cmd_var, bg=BG_ROOT, fg=FG_MAIN,
            insertbackground=FG_MAIN, relief="flat", font=("Consolas", 9)
        )
        ent.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
        ent.bind("<Return>", lambda _e: self._send_manual())
        self._btn(crow, "Gui", self._send_manual).pack(side="left", padx=(0, 4))
        self._btn(crow, "INFO", lambda: self._send("INFO")).pack(side="left")

    def _labeled_combo(self, parent, text, var, values, on_change, width=7):
        tk.Label(parent, text=text, bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9)).pack(side="left")
        cb = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width)
        cb.pack(side="left", padx=(3, 10))
        cb.bind("<<ComboboxSelected>>", lambda _e: on_change())

    # ========================================================================
    # PANEL 1: BO PHAT XUNG I2S
    # ========================================================================
    def _build_i2s_panel(self) -> None:
        self.i2s_card = self._card(self.panel_container, "DIEU KHIEN BO PHAT XUNG I2S TEST LOA", ACCENT_GREEN)
        b = self.i2s_card._body  # type: ignore[attr-defined]

        # Nut chinh Phat / Dung + Preset nhanh
        top_r = tk.Frame(b, bg=BG_CARD)
        top_r.pack(fill="x", pady=(0, 8))
        self.run_btn = self._btn(
            top_r,
            "BAT DAU PHAT XUNG RA LOA",
            self._toggle_run,
            bg="#15803d",
            active="#16a34a",
            font=("Segoe UI", 11, "bold"),
            pady=10,
        )
        self.run_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._btn(
            top_r,
            "DUNG",
            lambda: self._send("STOP"),
            bg="#b91c1c",
            active="#dc2626",
            font=("Segoe UI", 10, "bold"),
            padx=16,
            pady=10,
        ).pack(side="left")

        # Hang preset 1 bam
        pre_r = tk.Frame(b, bg=BG_CARD)
        pre_r.pack(fill="x", pady=(0, 10))
        tk.Label(pre_r, text="Test nhanh:", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        presets = [
            ("Xung Vuong 1kHz", self._preset_sq_1k),
            ("Tach-Tach 4Hz", self._preset_click_4hz),
            ("Bip 300ms/300ms", self._preset_beep),
            ("Sin 1kHz (Do AC)", self._preset_sine_1k),
            ("Quet 200-4kHz", self._preset_sweep),
        ]
        for label, fn in presets:
            self._btn(pre_r, label, fn, padx=8, pady=4, font=("Segoe UI", 8, "bold")).pack(
                side="left", fill="x", expand=True, padx=2
            )

        # Luoi 8 dang song
        wgrid = tk.Frame(b, bg=BG_CARD)
        wgrid.pack(fill="x", pady=(0, 10))
        for idx, (wkey, wlabel) in enumerate(WAVE_LIST):
            r, c = divmod(idx, 4)
            wgrid.columnconfigure(c, weight=1)
            btn = self._btn(wgrid, f"{wkey}\n{wlabel}", lambda k=wkey: self._select_wave(k), pady=5, font=("Segoe UI", 8, "bold"))
            btn.grid(row=r, column=c, sticky="ew", padx=2, pady=2)
            self.wave_buttons[wkey] = btn

        # Thanh truot Tan so
        f_row = tk.Frame(b, bg=BG_CARD)
        f_row.pack(fill="x", pady=(0, 4))
        tk.Label(f_row, text="Tan so (Hz):", bg=BG_CARD, fg=FG_MAIN, font=("Segoe UI", 9, "bold"), width=14, anchor="w").pack(side="left")
        tk.Scale(
            f_row, from_=1, to=6000, resolution=1, orient="horizontal", variable=self.freq_var,
            bg=BG_CARD, fg=FG_MAIN, troughcolor=BG_ROOT, highlightthickness=0, showvalue=False,
            command=self._on_freq_drag
        ).pack(side="left", fill="x", expand=True, padx=6)
        self.freq_lbl = tk.Label(f_row, text="1000 Hz", bg=BG_CARD, fg=ACCENT_CYAN, width=9, font=("Consolas", 10, "bold"))
        self.freq_lbl.pack(side="left")

        fq_row = tk.Frame(b, bg=BG_CARD)
        fq_row.pack(fill="x", pady=(0, 8))
        tk.Label(fq_row, text="Moc tan so:", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 8), width=14, anchor="w").pack(side="left")
        for hz in (2, 4, 100, 440, 1000, 2000, 4000):
            self._btn(fq_row, f"{hz}Hz", lambda h=hz: self._set_freq(h), padx=6, pady=2, font=("Consolas", 8, "bold")).pack(
                side="left", padx=2
            )

        # Thanh truot Duty Cycle (cho PULSE)
        d_row = tk.Frame(b, bg=BG_CARD)
        d_row.pack(fill="x", pady=(0, 6))
        tk.Label(d_row, text="Do rong xung:", bg=BG_CARD, fg=FG_MAIN, font=("Segoe UI", 9, "bold"), width=14, anchor="w").pack(side="left")
        tk.Scale(
            d_row, from_=5, to=95, resolution=1, orient="horizontal", variable=self.duty_var,
            bg=BG_CARD, fg=FG_MAIN, troughcolor=BG_ROOT, highlightthickness=0, showvalue=False,
            command=self._on_duty_drag
        ).pack(side="left", fill="x", expand=True, padx=6)
        self.duty_lbl = tk.Label(d_row, text="50%", bg=BG_CARD, fg=ACCENT_AMBER, width=9, font=("Consolas", 10, "bold"))
        self.duty_lbl.pack(side="left")

        # Thanh truot Bien do / Am luong
        a_row = tk.Frame(b, bg=BG_CARD)
        a_row.pack(fill="x", pady=(0, 8))
        tk.Label(a_row, text="Bien do / Vol:", bg=BG_CARD, fg=FG_MAIN, font=("Segoe UI", 9, "bold"), width=14, anchor="w").pack(side="left")
        tk.Scale(
            a_row, from_=0, to=100, resolution=1, orient="horizontal", variable=self.amp_var,
            bg=BG_CARD, fg=FG_MAIN, troughcolor=BG_ROOT, highlightthickness=0, showvalue=False,
            command=self._on_amp_drag
        ).pack(side="left", fill="x", expand=True, padx=6)
        self.amp_lbl = tk.Label(a_row, text="60%", bg=BG_CARD, fg=ACCENT_GREEN, width=9, font=("Consolas", 10, "bold"))
        self.amp_lbl.pack(side="left")

        # Che do Nhip (Gate CW / BEEP / ONCE) + Sweep End
        g_row = tk.Frame(b, bg=BG_CARD)
        g_row.pack(fill="x", pady=(2, 0))
        tk.Label(g_row, text="Che do nhip:", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9, "bold")).pack(side="left")
        gcb = ttk.Combobox(g_row, textvariable=self.gate_var, values=["CW", "BEEP", "ONCE"], state="readonly", width=7)
        gcb.pack(side="left", padx=(4, 10))
        gcb.bind("<<ComboboxSelected>>", lambda _e: self._send(f"GATE:{self.gate_var.get()}"))

        tk.Label(g_row, text="ON(ms):", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9)).pack(side="left")
        tk.Spinbox(
            g_row, from_=50, to=10000, increment=50, textvariable=self.on_ms_var, width=6,
            bg=BG_INP, fg=FG_MAIN, relief="flat", font=("Consolas", 9)
        ).pack(side="left", padx=(2, 8))

        tk.Label(g_row, text="OFF(ms):", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9)).pack(side="left")
        tk.Spinbox(
            g_row, from_=50, to=10000, increment=50, textvariable=self.off_ms_var, width=6,
            bg=BG_INP, fg=FG_MAIN, relief="flat", font=("Consolas", 9)
        ).pack(side="left", padx=(2, 8))

        self._btn(g_row, "Ap dung ON/OFF", self._apply_gate_ms, padx=8, pady=3).pack(side="left")

    # ========================================================================
    # PANEL 2: BO DO CHAN GPIO BANG DONG HO VAN NANG
    # ========================================================================
    def _build_probe_panel(self) -> None:
        self.probe_card = self._card(self.panel_container, "DO CHAN GPIO TRUC TIEP (TAM TAT I2S DE DO DONG HO)", ACCENT_AMBER)
        b = self.probe_card._body  # type: ignore[attr-defined]

        tk.Label(
            b,
            text=(
                "Che do nay tat tam I2S va dieu khien truc tiep muc dien ap 0V / 3.3V tren tung chan.\n"
                "Bam 'Quet tung chan (WALK)' hoac chon '3.3V' cho tung chan roi cam que DO dong ho kiem tra ngay tren board MAX98357A!"
            ),
            bg=BG_ROOT,
            fg="#fde68a",
            font=("Segoe UI", 9),
            justify="left",
            anchor="w",
            padx=10,
            pady=8,
            highlightbackground=BORDER,
            highlightthickness=1,
        ).pack(fill="x", pady=(0, 10))

        # Nut macro
        mrow = tk.Frame(b, bg=BG_CARD)
        mrow.pack(fill="x", pady=(0, 10))
        self.walk_btn = self._btn(
            mrow, "QUET TUNG CHAN (WALK 3.3V)", self._toggle_walk, bg="#b45309", active="#d97706", pady=8
        )
        self.walk_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._btn(mrow, "Tat ca 3.3V", lambda: self._send("PROBE_ALL:HIGH"), bg="#15803d", pady=8).pack(
            side="left", padx=2
        )
        self._btn(mrow, "Tat ca 0V", lambda: self._send("PROBE_ALL:LOW"), pady=8).pack(side="left", padx=2)
        self._btn(mrow, "Nhay 1Hz", lambda: self._send("PROBE_ALL:BLINK"), bg="#0284c7", pady=8).pack(
            side="left", padx=(2, 0)
        )

        # Chu ky nhay/quet
        per_row = tk.Frame(b, bg=BG_CARD)
        per_row.pack(fill="x", pady=(0, 10))
        tk.Label(per_row, text="Chu ky nhay / quet (ms):", bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 9)).pack(side="left")
        tk.Spinbox(
            per_row, from_=200, to=5000, increment=200, textvariable=self.probe_ms_var, width=6,
            bg=BG_INP, fg=FG_MAIN, relief="flat", font=("Consolas", 9)
        ).pack(side="left", padx=6)
        self._btn(
            per_row, "Dat chu ky", lambda: self._send(f"PROBE_MS:{self.probe_ms_var.get()}"), padx=8, pady=3
        ).pack(side="left")

        # 4 hang dieu khien rieng tung chan LRC, BCLK, DIN, SD
        self.probe_row_btns.clear()
        self.probe_row_lbls.clear()
        for idx, pname in enumerate(PIN_NAMES):
            row = tk.Frame(b, bg=BG_ROOT, padx=10, pady=7, highlightbackground=BORDER, highlightthickness=1)
            row.pack(fill="x", pady=3)
            lbl = tk.Label(row, text=f"{pname} (GPIO ?)", bg=BG_ROOT, fg=FG_MAIN, font=("Consolas", 10, "bold"), width=20, anchor="w")
            lbl.pack(side="left")
            self.probe_row_lbls.append(lbl)

            rbtns: dict[str, tk.Button] = {}
            for mkey, mtxt in (("LOW", "0V (LOW)"), ("HIGH", "3.3V (HIGH)"), ("FLOAT", "THA NOI"), ("BLINK", "NHAY")):
                btn = self._btn(
                    row, mtxt, lambda i=idx, m=mkey: self._send(f"PROBE:{i}:{m}"), padx=10, pady=4, font=("Segoe UI", 8, "bold")
                )
                btn.pack(side="left", fill="x", expand=True, padx=2)
                rbtns[mkey] = btn
            self.probe_row_btns.append(rbtns)

    # ========================================================================
    # ACTIONS & COMMAND HELPERS
    # ========================================================================
    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{ts}] {msg}\n")
        if int(self.log_box.index("end-1c").split(".")[0]) > 200:
            self.log_box.delete("1.0", "40.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _refresh_ports(self) -> None:
        ports = list(serial.tools.list_ports.comports())
        items = []
        best = ""
        for p in ports:
            desc = p.description or ""
            label = f"{p.device} - {desc}"
            items.append(label)
            if not best and (p.vid == 0x303A or any(k in desc.lower() for k in ("esp32", "usb serial", "ch340", "cp210"))):
                best = label
        self.port_cb["values"] = items
        self.port_var.set(best or (items[0] if items else ""))

    def _toggle_connect(self) -> None:
        if self.link.connected:
            self.link.disconnect()
            self.conn_btn.config(text="Ket noi ESP32", bg="#15803d", activebackground="#16a34a")
            self.status_pill.config(text="OFFLINE", bg="#334155")
            self._log("Da ngat ket noi.")
            return
        raw = self.port_var.get().strip()
        if not raw:
            messagebox.showwarning("Chua co cong COM", "Hay cam ESP32-S3 va bam 'Quet'.")
            return
        port = raw.split(" - ", 1)[0].strip()
        baud = int(self.baud_var.get())
        try:
            self.link.connect(port, baud)
        except Exception as exc:
            messagebox.showerror("Loi ket noi", f"Khong mo duoc {port}:\n{exc}")
            return
        self.conn_btn.config(text="Ngat ket noi", bg="#b91c1c", activebackground="#dc2626")
        self.status_pill.config(text=f"ONLINE ({port})", bg="#15803d")
        self._log(f"Da ket noi {port} @ {baud} baud.")
        self.root.after(150, lambda: self._send("INFO"))

    def _send(self, cmd: str, quiet: bool = False) -> None:
        if not self.link.connected:
            if not quiet:
                self._log(f"[Chua ket noi] {cmd}")
            return
        if self.link.send(cmd) and not quiet:
            self._log(f">>> {cmd}")

    def _send_debounced(self, key: str, cmd: str, delay_ms: int = 70) -> None:
        if key in self._debounce_ids:
            self.root.after_cancel(self._debounce_ids[key])
        self._debounce_ids[key] = self.root.after(delay_ms, lambda: self._send(cmd, quiet=True))

    def _send_manual(self) -> None:
        c = self.manual_cmd_var.get().strip()
        if c:
            self._send(c)
            self.manual_cmd_var.set("")

    def _switch_mode(self, mode: str) -> None:
        self.state["mode"] = mode
        self._update_mode_views()
        self._send(f"MODE:{mode}")

    def _toggle_run(self) -> None:
        if self.state.get("running"):
            self._send("STOP")
        else:
            self._send("START")

    def _select_wave(self, wkey: str) -> None:
        self.state["wave"] = wkey
        self._update_wave_highlights()
        self._send(f"WAVE:{wkey}")

    def _set_freq(self, hz: float) -> None:
        self.freq_var.set(hz)
        self.freq_lbl.config(text=f"{int(hz)} Hz")
        self.state["freq"] = float(hz)
        self._send(f"FREQ:{hz:.1f}")

    def _on_freq_drag(self, val: str) -> None:
        hz = float(val)
        self.freq_lbl.config(text=f"{int(hz)} Hz")
        self.state["freq"] = hz
        self._send_debounced("freq", f"FREQ:{hz:.1f}")

    def _on_duty_drag(self, val: str) -> None:
        d = float(val)
        self.duty_lbl.config(text=f"{int(d)}%")
        self.state["duty"] = d
        self._send_debounced("duty", f"DUTY:{d:.1f}")

    def _on_amp_drag(self, val: str) -> None:
        a = int(float(val))
        self.amp_lbl.config(text=f"{a}%")
        self.state["amp"] = a
        self._send_debounced("amp", f"AMP:{a}")

    def _apply_gate_ms(self) -> None:
        self._send(f"ON_MS:{self.on_ms_var.get()}")
        self._send(f"OFF_MS:{self.off_ms_var.get()}")

    def _apply_pins(self) -> None:
        lrc = self.pin_lrc_var.get()
        bclk = self.pin_bclk_var.get()
        din = self.pin_din_var.get()
        sd = self.pin_sd_var.get()
        self._send(f"PINS:{lrc}:{bclk}:{din}:{sd}")

    def _preset_pins_main_fzz(self) -> None:
        self.pin_lrc_var.set(15)
        self.pin_bclk_var.set(16)
        self.pin_din_var.set(17)
        self.pin_sd_var.set(18)
        self._apply_pins()

    def _toggle_walk(self) -> None:
        new_w = not bool(self.state.get("probe_walk", False))
        self._send(f"PROBE_WALK:{1 if new_w else 0}")

    # Presets phat xung 1 bam
    def _preset_sq_1k(self) -> None:
        self._send("WAVE:SQUARE")
        self._set_freq(1000)
        self._send("GATE:CW")
        self._send("START")

    def _preset_click_4hz(self) -> None:
        self._send("WAVE:CLICK")
        self._set_freq(4)
        self._send("GATE:CW")
        self._send("START")

    def _preset_beep(self) -> None:
        self._send("WAVE:PULSE")
        self._set_freq(1000)
        self._send("DUTY:50")
        self._send("ON_MS:300")
        self._send("OFF_MS:300")
        self._send("GATE:BEEP")
        self._send("START")

    def _preset_sine_1k(self) -> None:
        self._send("WAVE:SINE")
        self._set_freq(1000)
        self._send("GATE:CW")
        self._send("START")

    def _preset_sweep(self) -> None:
        self._send("WAVE:SWEEP")
        self._set_freq(200)
        self._send("SWEEP_END:4000")
        self._send("GATE:CW")
        self._send("START")

    # ========================================================================
    # UI SYNC & RENDERING
    # ========================================================================
    def _update_mode_views(self) -> None:
        is_i2s = self.state.get("mode", "I2S") == "I2S"
        self.i2s_card.pack_forget()
        self.probe_card.pack_forget()
        if is_i2s:
            self.i2s_card.pack(fill="both", expand=True)
            self.btn_mode_i2s.config(bg="#0284c7", fg="#ffffff")
            self.btn_mode_probe.config(bg=BG_INP, fg=FG_DIM)
        else:
            self.probe_card.pack(fill="both", expand=True)
            self.btn_mode_probe.config(bg="#b45309", fg="#ffffff")
            self.btn_mode_i2s.config(bg=BG_INP, fg=FG_DIM)
        self._update_wave_highlights()
        self._update_sd_highlights()
        self._update_probe_highlights()

    def _update_wave_highlights(self) -> None:
        cur = self.state.get("wave", "PULSE")
        for k, btn in self.wave_buttons.items():
            if k == cur:
                btn.config(bg="#0284c7", fg="#ffffff")
            else:
                btn.config(bg=BG_INP, fg=FG_DIM)

    def _update_sd_highlights(self) -> None:
        cur = self.state.get("sd", "HIGH")
        colors = {"HIGH": "#15803d", "FLOAT": "#b45309", "LOW": "#b91c1c"}
        for k, btn in self.sd_buttons.items():
            btn.config(bg=colors[k] if k == cur else BG_INP, fg="#ffffff" if k == cur else FG_DIM)

    def _update_probe_highlights(self) -> None:
        pins = [
            self.state.get("lrclk", 15),
            self.state.get("bclk", 16),
            self.state.get("din", 17),
            self.state.get("sd_pin", 18),
        ]
        pms = self.state.get("pm", ["LOW", "LOW", "LOW", "LOW"])
        pls = self.state.get("pl", [0, 0, 0, 0])
        walk = bool(self.state.get("probe_walk", False))
        self.walk_btn.config(
            text="DUNG QUET (WALKING ON)" if walk else "QUET TUNG CHAN (WALK 3.3V)",
            bg="#dc2626" if walk else "#b45309",
        )
        for i in range(4):
            lvl_txt = "3.3V" if pls[i] == 1 else ("FLOAT" if pls[i] == -1 else "0.0V")
            col = ACCENT_GREEN if pls[i] == 1 else (ACCENT_AMBER if pls[i] == -1 else FG_DIM)
            self.probe_row_lbls[i].config(text=f"{PIN_NAMES[i]} (GPIO {pins[i]}): {lvl_txt}", fg=col)
            for mk, btn in self.probe_row_btns[i].items():
                active = (not walk) and (pms[i] == mk)
                btn.config(bg="#0284c7" if active else BG_INP, fg="#ffffff" if active else FG_DIM)

    def _update_multimeter_table(self) -> None:
        s = self.state
        mode = s.get("mode", "I2S")
        lrc, bclk, din, sd_p = s["lrclk"], s["bclk"], s["din"], s["sd_pin"]
        self.meter_val_lbls["vin"].config(text="4.5V - 5.2V (hoac 3.3V)", fg=ACCENT_CYAN)

        if mode == "PROBE":
            pls = s.get("pl", [0, 0, 0, 0])
            def fmt_p(gpio, lv):
                if lv == 1:
                    return f"GPIO {gpio} -> ~3.30V (HIGH)", ACCENT_GREEN
                if lv == -1:
                    return f"GPIO {gpio} -> THA NOI (High-Z)", ACCENT_AMBER
                return f"GPIO {gpio} -> ~0.00V (LOW)", FG_DIM

            for key, gpio, lv in (("lrc", lrc, pls[0]), ("bclk", bclk, pls[1]), ("din", din, pls[2]), ("sd", sd_p, pls[3])):
                txt, col = fmt_p(gpio, lv)
                self.meter_val_lbls[key].config(text=txt, fg=col)
            self.meter_val_lbls["vo"].config(text="Tam tat I2S (che do Do chan)", fg=FG_DIM)
            return

        # Che do I2S
        i2s_ok = bool(s.get("i2s_ok", False))
        gate_on = bool(s.get("gate_on", False))
        sd_st = s.get("sd", "HIGH")

        if i2s_ok:
            self.meter_val_lbls["lrc"].config(text=f"GPIO {lrc} -> ~1.65V DC ({s['rate']} Hz)", fg=ACCENT_GREEN)
            self.meter_val_lbls["bclk"].config(
                text=f"GPIO {bclk} -> ~1.65V DC ({s['rate']*s['bits']*2//1000} kHz)", fg=ACCENT_GREEN
            )
            if gate_on and s.get("amp", 0) > 0:
                self.meter_val_lbls["din"].config(text=f"GPIO {din} -> ~0.8 - 1.65V DC (dang phat)", fg=ACCENT_GREEN)
            else:
                self.meter_val_lbls["din"].config(text=f"GPIO {din} -> ~0.00V (dang dung/im lang)", fg=FG_DIM)
        else:
            for k, g in (("lrc", lrc), ("bclk", bclk), ("din", din)):
                self.meter_val_lbls[k].config(text=f"GPIO {g} -> Chua khoi tao I2S", fg=ACCENT_RED)

        if sd_st == "HIGH":
            self.meter_val_lbls["sd"].config(text=f"GPIO {sd_p} -> ~3.30V (Amp BAT - Kenh L)", fg=ACCENT_GREEN)
            self.meter_val_lbls["vo"].config(text="~VIN/2 (~2.5V DC) | AC 0.5-2.5V khi phat", fg=ACCENT_CYAN)
        elif sd_st == "FLOAT":
            self.meter_val_lbls["sd"].config(text=f"GPIO {sd_p} -> ~0.4-1.5V (Tha noi - L+R/2)", fg=ACCENT_AMBER)
            self.meter_val_lbls["vo"].config(text="~VIN/2 (~2.5V DC) | AC 0.5-2.5V khi phat", fg=ACCENT_CYAN)
        else:
            self.meter_val_lbls["sd"].config(text=f"GPIO {sd_p} -> ~0.00V (Amp TAT Shutdown)", fg=ACCENT_RED)
            self.meter_val_lbls["vo"].config(text="~0.00V (Dang tat Shutdown)", fg=ACCENT_RED)

    def _draw_scope(self) -> None:
        c = self.scope_canvas
        w = max(200, c.winfo_width())
        h = max(120, c.winfo_height())
        c.delete("all")

        # Luoi oscilloscope
        for gx in range(0, w, 60):
            c.create_line(gx, 0, gx, h, fill="#0f172a")
        for gy in range(0, h, 35):
            c.create_line(0, gy, w, gy, fill="#0f172a")
        mid = h / 2.0
        c.create_line(0, mid, w, mid, fill="#1e293b", dash=(4, 4))

        if self.state.get("mode") == "PROBE":
            # Ve 4 o den LED dien ap cho 4 chan GPIO
            pins = [self.state["lrclk"], self.state["bclk"], self.state["din"], self.state["sd_pin"]]
            pls = self.state.get("pl", [0, 0, 0, 0])
            box_w = (w - 50) / 4.0
            for i in range(4):
                x0 = 10 + i * (box_w + 10)
                x1 = x0 + box_w
                lv = pls[i]
                fill = "#14532d" if lv == 1 else ("#78350f" if lv == -1 else "#0f172a")
                outl = ACCENT_GREEN if lv == 1 else (ACCENT_AMBER if lv == -1 else BORDER)
                c.create_rectangle(x0, 26, x1, h - 18, fill=fill, outline=outl, width=2)
                c.create_text((x0 + x1) / 2, 54, text=PIN_NAMES[i], fill=FG_MAIN, font=("Segoe UI", 10, "bold"))
                c.create_text((x0 + x1) / 2, 82, text=f"GPIO {pins[i]}", fill=ACCENT_CYAN, font=("Consolas", 11, "bold"))
                vtxt = "3.30 V (HIGH)" if lv == 1 else ("FLOAT (High-Z)" if lv == -1 else "0.00 V (LOW)")
                c.create_text((x0 + x1) / 2, 118, text=vtxt, fill=outl if lv != 0 else FG_DIM, font=("Consolas", 11, "bold"))
            return

        # Che do I2S: ve dang song thuc te
        running = bool(self.state.get("running", False))
        gate_on = bool(self.state.get("gate_on", False))
        wave = self.state.get("wave", "PULSE")
        duty = max(0.05, min(0.95, float(self.state.get("duty", 50.0)) / 100.0))
        amp = (float(self.state.get("amp", 60)) / 100.0) if (running and gate_on) else (float(self.state.get("amp", 60)) / 100.0 * 0.35)

        if running and gate_on:
            self._anim_phase = (self._anim_phase + 0.08) % 1.0

        cycles = 4.0 if wave != "CLICK" else 3.0
        pts = []
        steps = 220
        scale_y = (h * 0.38) * amp
        for i in range(steps):
            x = (i / float(steps - 1)) * w
            ph = ((i / float(steps - 1)) * cycles + self._anim_phase) % 1.0
            if wave in ("SINE", "SWEEP"):
                y_val = math.sin(ph * 2.0 * math.pi)
            elif wave == "SQUARE":
                y_val = 0.9 if ph < 0.5 else -0.9
            elif wave == "PULSE":
                y_val = 0.9 if ph < duty else -0.9
            elif wave == "TRIANGLE":
                y_val = (4.0 * ph - 1.0) if ph < 0.5 else (3.0 - 4.0 * ph)
            elif wave == "SAWTOOTH":
                y_val = 2.0 * ph - 1.0
            elif wave == "CLICK":
                y_val = 0.95 if ph < 0.05 else (-0.95 if ph < 0.10 else 0.0)
            else:  # NOISE
                y_val = math.sin(i * 12.9898 + self._anim_phase * 97.0) * 0.85
            pts.extend((x, mid - y_val * scale_y))

        trace_col = ACCENT_GREEN if (running and gate_on) else ("#f59e0b" if running else "#475569")
        c.create_line(*pts, fill=trace_col, width=2)

        # Nhan thong so tren goc
        badge = "DANG PHAT XUNG" if (running and gate_on) else ("DANG NGHI (GATE OFF)" if running else "DANG DUNG (XEM TRUOC)")
        c.create_text(12, 14, text=badge, fill=trace_col, anchor="w", font=("Consolas", 10, "bold"))
        info = f"{wave} | {self.state.get('live_freq', 1000):.0f} Hz | Duty {self.state.get('duty', 50):.0f}% | Amp {self.state.get('amp', 60)}% | Gate {self.state.get('gate', 'CW')}"
        c.create_text(w - 12, 14, text=info, fill=ACCENT_CYAN, anchor="e", font=("Consolas", 9, "bold"))

    def _handle_esp_json(self, line: str) -> None:
        if not line.startswith("{"):
            self._log(f"ESP: {line}")
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            self._log(f"ESP: {line}")
            return

        ev = obj.get("event", "")
        if ev != "LIVE":
            self._log(f"ESP [{ev}]: {line}")

        prev_mode = self.state.get("mode")
        for k in self.state:
            if k in obj:
                self.state[k] = obj[k]

        if ev in ("STATE", "BOOT_READY", "HW_READY", "ONCE_DONE"):
            self.pin_lrc_var.set(int(self.state["lrclk"]))
            self.pin_bclk_var.set(int(self.state["bclk"]))
            self.pin_din_var.set(int(self.state["din"]))
            self.pin_sd_var.set(int(self.state["sd_pin"]))
            self.rate_var.set(str(self.state["rate"]))
            self.bits_var.set(str(self.state["bits"]))
            self.fmt_var.set(str(self.state["fmt"]))
            self.i2s_port_var.set(str(self.state["i2s_port"]))
            self.slot_var.set(str(self.state["slot"]))
            self.gate_var.set(str(self.state["gate"]))
            self.amp_var.set(int(self.state["amp"]))
            self.amp_lbl.config(text=f"{int(self.state['amp'])}%")
            self.duty_var.set(float(self.state["duty"]))
            self.duty_lbl.config(text=f"{int(float(self.state['duty']))}%")
            self.freq_var.set(float(self.state["freq"]))
            self.freq_lbl.config(text=f"{int(float(self.state['freq']))} Hz")

        if self.state.get("mode") != prev_mode:
            self._update_mode_views()
        else:
            self._update_wave_highlights()
            self._update_sd_highlights()
            self._update_probe_highlights()

        running = bool(self.state.get("running", False))
        self.run_btn.config(
            text="DANG PHAT XUNG (BAM DE DUNG)" if running else "BAT DAU PHAT XUNG RA LOA",
            bg="#dc2626" if running else "#15803d",
            activebackground="#ef4444" if running else "#16a34a",
        )
        self._update_multimeter_table()

    def _tick(self) -> None:
        for _ in range(30):
            try:
                kind, msg = self.ev_q.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                self._handle_esp_json(msg)
            elif kind == "err":
                self._log(f"[LOI SERIAL] {msg}")
                if self.link.connected:
                    self._toggle_connect()

        self._draw_scope()
        self.root.after(45, self._tick)

    def _on_close(self) -> None:
        if self.link.connected:
            self.link.send("STOP")
        self.link.disconnect()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    SpeakerPulseApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
