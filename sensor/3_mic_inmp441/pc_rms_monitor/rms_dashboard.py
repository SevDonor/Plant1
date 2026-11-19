#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INMP441 RMS Monitor & Live Table (External Desktop App)
Đọc luồng Serial từ firmware 3_mic_inmp441 (ESP32), vẽ đồ thị RMS theo thời gian thực,
hiển thị bảng dữ liệu RMS/Peak/dBFS, thống kê rớt tín hiệu (RMS=0) và xuất CSV.
"""

import argparse
import csv
import datetime
import queue
import re
import threading
import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox, ttk

import serial
import serial.tools.list_ports

LINE_RE = re.compile(
    r"\[INMP441\s+Gain=(\d+)x\]\s+RMS:\s*(\d+)\s*\|\s*Peak:\s*(\d+)\s*\|\s*([+-]?\d+(?:\.\d+)?)\s*dBFS\s*\|\s*\[[^\]]*\]\s*(.+)"
)

MAX_CHART_POINTS = 120
MAX_TABLE_ROWS = 500


def detect_ports():
    ports = list(serial.tools.list_ports.comports())
    devices = [p.device for p in ports]
    preferred = None
    keywords = ("ch340", "cp210", "ch910", "ftdi", "uart", "usb serial", "esp32")
    for p in ports:
        text = f"{p.description or ''} {p.hwid or ''}".lower()
        if any(k in text for k in keywords):
            preferred = p.device
            break
    if not preferred and devices:
        preferred = devices[0]
    return devices, preferred


class RmsDashboardApp:
    def __init__(self, root: tk.Tk, initial_port: str | None = None, auto_connect: bool = False):
        self.root = root
        self.root.title("INMP441 RMS Monitor & Bảng Dữ Liệu")
        self.root.geometry("1040x680")
        self.root.minsize(880, 560)
        self.root.configure(bg="#0f172a")

        self.ser = None
        self.reader_thread = None
        self.stop_event = threading.Event()
        self.data_queue: queue.Queue = queue.Queue()

        self.paused = False
        self.sample_index = 0
        self.zero_count = 0
        self.rms_sum = 0.0
        self.rms_min = None
        self.rms_max = 0
        self.history_all: list[tuple] = []
        self.chart_rms = deque(maxlen=MAX_CHART_POINTS)
        self.chart_peak = deque(maxlen=MAX_CHART_POINTS)

        self._setup_styles()
        self._build_ui()
        self._refresh_ports(preferred_override=initial_port)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(50, self._poll_queue)

        if auto_connect and self.port_var.get():
            self.root.after(150, self.toggle_connect)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background="#1e293b",
            foreground="#e2e8f0",
            fieldbackground="#1e293b",
            rowheight=24,
            font=("Consolas", 10),
        )
        style.configure(
            "Treeview.Heading",
            background="#334155",
            foreground="#f8fafc",
            font=("Segoe UI", 10, "bold"),
        )
        style.map("Treeview", background=[("selected", "#2563eb")])

    def _build_ui(self):
        # Top control bar
        top = tk.Frame(self.root, bg="#1e293b", padx=12, pady=10)
        top.pack(fill=tk.X, padx=10, pady=(10, 6))

        tk.Label(top, text="Cổng COM:", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=10, state="readonly")
        self.port_combo.pack(side=tk.LEFT, padx=(6, 4))

        tk.Button(
            top, text="Làm mới", command=self._refresh_ports, bg="#334155", fg="#f8fafc", relief=tk.FLAT, padx=8
        ).pack(side=tk.LEFT, padx=4)

        tk.Label(top, text="Baud:", bg="#1e293b", fg="#cbd5e1", font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(10, 4))
        self.baud_var = tk.StringVar(value="921600")
        ttk.Combobox(
            top, textvariable=self.baud_var, values=["921600", "115200"], width=9, state="readonly"
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.connect_btn = tk.Button(
            top, text="Kết nối", command=self.toggle_connect, bg="#16a34a", fg="white",
            font=("Segoe UI", 10, "bold"), relief=tk.FLAT, padx=12
        )
        self.connect_btn.pack(side=tk.LEFT, padx=4)

        # Gain & utility controls
        tk.Button(
            top, text="Gain -", command=lambda: self._send_cmd("-"), bg="#475569", fg="white", relief=tk.FLAT, padx=8
        ).pack(side=tk.LEFT, padx=(14, 3))
        tk.Button(
            top, text="Gain +", command=lambda: self._send_cmd("+"), bg="#475569", fg="white", relief=tk.FLAT, padx=8
        ).pack(side=tk.LEFT, padx=3)

        self.pause_btn = tk.Button(
            top, text="Tạm dừng bảng", command=self._toggle_pause, bg="#d97706", fg="white", relief=tk.FLAT, padx=10
        )
        self.pause_btn.pack(side=tk.LEFT, padx=(14, 4))

        tk.Button(
            top, text="Xóa bảng", command=self._clear_data, bg="#64748b", fg="white", relief=tk.FLAT, padx=10
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            top, text="Xuất CSV", command=self._export_csv, bg="#2563eb", fg="white",
            font=("Segoe UI", 9, "bold"), relief=tk.FLAT, padx=10
        ).pack(side=tk.RIGHT, padx=4)

        # KPI Cards
        kpi_frame = tk.Frame(self.root, bg="#0f172a")
        kpi_frame.pack(fill=tk.X, padx=10, pady=4)

        self.kpi_vars = {}
        cards = [
            ("rms", "RMS hiện tại", "#38bdf8"),
            ("peak", "Peak hiện tại", "#a78bfa"),
            ("dbfs", "Mức dBFS", "#34d399"),
            ("gain", "Digital Gain", "#fbbf24"),
            ("avg", "RMS TB / Max", "#f472b6"),
            ("zero", "Tỉ lệ RMS=0", "#f87171"),
        ]
        for idx, (key, title, color) in enumerate(cards):
            card = tk.Frame(kpi_frame, bg="#1e293b", padx=12, pady=8, highlightbackground="#334155", highlightthickness=1)
            card.grid(row=0, column=idx, sticky="nsew", padx=4)
            kpi_frame.columnconfigure(idx, weight=1)
            tk.Label(card, text=title, bg="#1e293b", fg="#94a3b8", font=("Segoe UI", 9)).pack(anchor="w")
            var = tk.StringVar(value="--")
            self.kpi_vars[key] = var
            tk.Label(card, textvariable=var, bg="#1e293b", fg=color, font=("Consolas", 14, "bold")).pack(anchor="w")

        # Live Chart
        chart_wrap = tk.Frame(self.root, bg="#1e293b", padx=10, pady=8)
        chart_wrap.pack(fill=tk.X, padx=10, pady=6)

        header_row = tk.Frame(chart_wrap, bg="#1e293b")
        header_row.pack(fill=tk.X)
        tk.Label(
            header_row, text="Đồ thị RMS theo thời gian thực (120 mẫu gần nhất)",
            bg="#1e293b", fg="#e2e8f0", font=("Segoe UI", 10, "bold")
        ).pack(side=tk.LEFT)
        self.state_label = tk.Label(
            header_row, text="Trạng thái: Chưa kết nối", bg="#1e293b", fg="#94a3b8", font=("Segoe UI", 10, "bold")
        )
        self.state_label.pack(side=tk.RIGHT)

        self.canvas = tk.Canvas(chart_wrap, height=175, bg="#0b1220", highlightthickness=0)
        self.canvas.pack(fill=tk.X, pady=(6, 0))
        self.canvas.bind("<Configure>", lambda _e: self._draw_chart())

        # Live RMS Table
        table_wrap = tk.Frame(self.root, bg="#1e293b", padx=10, pady=8)
        table_wrap.pack(fill=tk.BOTH, expand=True, padx=10, pady=(2, 10))

        tk.Label(
            table_wrap, text="Bảng giá trị RMS chi tiết", bg="#1e293b", fg="#e2e8f0", font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(0, 6))

        cols = ("stt", "time", "gain", "rms", "peak", "dbfs", "state")
        self.tree = ttk.Treeview(table_wrap, columns=cols, show="headings", selectmode="browse")
        headers = [
            ("stt", "STT", 60, "center"),
            ("time", "Thời gian", 110, "center"),
            ("gain", "Gain", 70, "center"),
            ("rms", "RMS", 90, "e"),
            ("peak", "Peak", 90, "e"),
            ("dbfs", "dBFS", 95, "e"),
            ("state", "Trạng thái", 360, "w"),
        ]
        for cid, text, width, anchor in headers:
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, anchor=anchor)

        vsb = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.tag_configure("zero", foreground="#f87171")
        self.tree.tag_configure("quiet", foreground="#94a3b8")
        self.tree.tag_configure("light", foreground="#38bdf8")
        self.tree.tag_configure("speech", foreground="#4ade80")
        self.tree.tag_configure("loud", foreground="#fbbf24")

    def _refresh_ports(self, preferred_override: str | None = None):
        devices, preferred = detect_ports()
        self.port_combo["values"] = devices
        target = preferred_override if preferred_override in devices else preferred
        if target:
            self.port_var.set(target)
        elif devices:
            self.port_var.set(devices[0])
        else:
            self.port_var.set("")

    def toggle_connect(self):
        if self.ser and self.ser.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("Thiếu cổng COM", "Không tìm thấy cổng COM nào. Hãy cắm ESP32 rồi bấm Làm mới.")
            return
        baud = int(self.baud_var.get())
        try:
            self.ser = serial.Serial()
            self.ser.port = port
            self.ser.baudrate = baud
            self.ser.timeout = 0.2
            self.ser.dtr = False
            self.ser.rts = False
            self.ser.open()
        except Exception as exc:
            messagebox.showerror("Lỗi mở cổng", f"Không thể mở {port}: {exc}")
            self.ser = None
            return

        self.stop_event.clear()
        self.reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.reader_thread.start()
        self.connect_btn.configure(text="Ngắt kết nối", bg="#dc2626")
        self.state_label.configure(text=f"Đang nghe {port} @ {baud} baud", fg="#38bdf8")

    def _disconnect(self):
        self.stop_event.set()
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=0.6)
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self.connect_btn.configure(text="Kết nối", bg="#16a34a")
        self.state_label.configure(text="Trạng thái: Đã ngắt kết nối", fg="#94a3b8")

    def _read_loop(self):
        while not self.stop_event.is_set() and self.ser and self.ser.is_open:
            try:
                raw = self.ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()
                m = LINE_RE.search(line)
                if m:
                    gain = int(m.group(1))
                    rms = int(m.group(2))
                    peak = int(m.group(3))
                    dbfs = float(m.group(4))
                    state = m.group(5).strip()
                    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    self.data_queue.put((ts, gain, rms, peak, dbfs, state))
            except Exception as exc:
                self.data_queue.put(("ERR", str(exc)))
                break

    def _poll_queue(self):
        updated = False
        while True:
            try:
                item = self.data_queue.get_nowait()
            except queue.Empty:
                break

            if item[0] == "ERR":
                self._disconnect()
                self.state_label.configure(text=f"Mất kết nối: {item[1]}", fg="#f87171")
                break

            ts, gain, rms, peak, dbfs, state = item
            self.sample_index += 1
            if rms == 0:
                self.zero_count += 1
            self.rms_sum += rms
            self.rms_min = rms if self.rms_min is None else min(self.rms_min, rms)
            self.rms_max = max(self.rms_max, rms)

            row = (self.sample_index, ts, f"{gain}x", rms, peak, f"{dbfs:+.1f}", state)
            self.history_all.append(row)

            if not self.paused:
                self.chart_rms.append(rms)
                self.chart_peak.append(peak)
                self._insert_row(row, rms, state)
                self._update_kpis(gain, rms, peak, dbfs, state)
                updated = True

        if updated:
            self._draw_chart()

        self.root.after(50, self._poll_queue)

    def _insert_row(self, row, rms: int, state: str):
        if rms == 0:
            tag = "zero"
        elif "LỚN" in state or "VỖ TAY" in state:
            tag = "loud"
        elif "tiếng nói" in state.lower():
            tag = "speech"
        elif "nhẹ" in state.lower():
            tag = "light"
        else:
            tag = "quiet"

        self.tree.insert("", tk.END, values=row, tags=(tag,))
        children = self.tree.get_children()
        if len(children) > MAX_TABLE_ROWS:
            self.tree.delete(children[0])
        self.tree.yview_moveto(1.0)

    def _update_kpis(self, gain: int, rms: int, peak: int, dbfs: float, state: str):
        avg = self.rms_sum / max(1, self.sample_index)
        zero_pct = (self.zero_count * 100.0) / max(1, self.sample_index)
        self.kpi_vars["rms"].set(f"{rms}")
        self.kpi_vars["peak"].set(f"{peak}")
        self.kpi_vars["dbfs"].set(f"{dbfs:+.1f} dB")
        self.kpi_vars["gain"].set(f"{gain}x")
        self.kpi_vars["avg"].set(f"{avg:.0f} / {self.rms_max}")
        self.kpi_vars["zero"].set(f"{zero_pct:.1f}% ({self.zero_count})")
        color = "#f87171" if rms == 0 else ("#4ade80" if rms > 400 else "#38bdf8")
        self.state_label.configure(text=f"Trạng thái: {state}", fg=color)

    def _draw_chart(self):
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 200)
        h = max(c.winfo_height(), 120)
        pad_l, pad_r, pad_t, pad_b = 46, 12, 12, 20
        pw = w - pad_l - pad_r
        ph = h - pad_t - pad_b

        max_val = max([1000] + list(self.chart_rms))
        # Round up nicely
        step = 500 if max_val <= 3000 else 2000
        y_max = ((max_val + step - 1) // step) * step

        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = pad_t + ph * (1.0 - frac)
            val = int(y_max * frac)
            c.create_line(pad_l, y, w - pad_r, y, fill="#1e293b", dash=(2, 4))
            c.create_text(pad_l - 6, y, text=str(val), anchor="e", fill="#64748b", font=("Consolas", 8))

        pts = list(self.chart_rms)
        if len(pts) < 2:
            c.create_text(w // 2, h // 2, text="Đang chờ dữ liệu RMS từ ESP32...", fill="#64748b", font=("Segoe UI", 10))
            return

        n = len(pts)
        coords = []
        for idx, val in enumerate(pts):
            x = pad_l + (idx / max(1, MAX_CHART_POINTS - 1)) * pw
            y = pad_t + ph * (1.0 - min(1.0, val / y_max))
            coords.extend((x, y))

        # Bars + line for clarity
        bar_w = max(2, pw / MAX_CHART_POINTS - 1)
        for idx, val in enumerate(pts):
            x = pad_l + (idx / max(1, MAX_CHART_POINTS - 1)) * pw
            y = pad_t + ph * (1.0 - min(1.0, val / y_max))
            fill = "#ef4444" if val == 0 else ("#22c55e" if val > 400 else "#0ea5e9")
            c.create_rectangle(x - bar_w / 2, y, x + bar_w / 2, pad_t + ph, fill=fill, outline="")

        c.create_line(*coords, fill="#bae6fd", width=1.5)

    def _send_cmd(self, cmd: str):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write((cmd + "\n").encode("ascii"))
            except Exception as exc:
                messagebox.showerror("Lỗi gửi lệnh", str(exc))

    def _toggle_pause(self):
        self.paused = not self.paused
        self.pause_btn.configure(
            text="Tiếp tục bảng" if self.paused else "Tạm dừng bảng",
            bg="#0284c7" if self.paused else "#d97706",
        )

    def _clear_data(self):
        self.sample_index = 0
        self.zero_count = 0
        self.rms_sum = 0.0
        self.rms_min = None
        self.rms_max = 0
        self.history_all.clear()
        self.chart_rms.clear()
        self.chart_peak.clear()
        self.tree.delete(*self.tree.get_children())
        for var in self.kpi_vars.values():
            var.set("--")
        self._draw_chart()

    def _export_csv(self):
        if not self.history_all:
            messagebox.showinfo("Chưa có dữ liệu", "Bảng RMS hiện đang trống.")
            return
        default_name = f"inmp441_rms_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fp:
            writer = csv.writer(fp)
            writer.writerow(["STT", "Thoi_gian", "Gain", "RMS", "Peak", "dBFS", "Trang_thai"])
            writer.writerows(self.history_all)
        messagebox.showinfo("Đã xuất CSV", f"Đã lưu {len(self.history_all)} dòng vào:\n{path}")

    def _on_close(self):
        self._disconnect()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="App ngoài hiển thị bảng & đồ thị RMS cho INMP441")
    parser.add_argument("-p", "--port", help="Cổng COM mặc định (ví dụ COM4)")
    parser.add_argument("--auto", action="store_true", help="Tự động kết nối ngay khi mở app")
    args = parser.parse_args()

    root = tk.Tk()
    RmsDashboardApp(root, initial_port=args.port, auto_connect=args.auto)
    root.mainloop()


if __name__ == "__main__":
    main()
