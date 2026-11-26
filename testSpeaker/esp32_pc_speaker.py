r"""
ESP32-S3 PC Virtual Speaker & MAX98357A Hardware Tester
=======================================================
Gia lap loa ESP32-S3 (MAX98357A + Loa 4 Ohm 3W) thanh loa may tinh Windows:
  - Thu truc tiep am thanh he thong Windows (YouTube, Spotify, Chrome, Game...)
    bang Windows WASAPI Loopback thuan (ctypes - KHONG can cai them thu vien am thanh).
  - Ho tro chuyen nhanh dau ra mac dinh Windows sang Virtual Audio Cable (VB-Cable)
    de tat hoan toan loa laptop va chi phat ra loa ESP32-S3 (tu dong khoi phuc loa goc khi tat).
  - Phat file .WAV truc tiep ra loa ESP32-S3 va tich hop bo test phan cung MAX98357A
    (Sine, Sweep, Scale, Noise, SD_MODE, Channel L/R, Swap BCLK/LRCLK).

So do chan khop 100% D:\Plant\main.fzz:
  - MAX98357A LRCLK (LRC)  -> ESP32-S3 GPIO 15 (Pin 8)
  - MAX98357A BCLK         -> ESP32-S3 GPIO 16 (Pin 9)
  - MAX98357A DIN          -> ESP32-S3 GPIO 17 (Pin 10)
  - MAX98357A SD_MODE (SD) -> ESP32-S3 GPIO 18 (Pin 11)
  - MAX98357A GAIN         -> GND (12 dB)
  - MAX98357A VIN / GND    -> 5V / GND
"""

from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
import json
import math
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import uuid
import wave

import numpy as np

try:
    from scipy import signal as sp_signal
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    import serial
    import serial.tools.list_ports
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Thieu thu vien 'pyserial'. Hay chay: pip install pyserial numpy scipy") from exc


# ============================================================================
# 1. HANG SO GIAO THUC KHUNG NHI PHAN VOI ESP32-S3
# ============================================================================
PKT_SYNC_0 = 0xAA
PKT_SYNC_1 = 0x55
PKT_TYPE_PCM_MONO = 0x01
PKT_TYPE_CMD = 0x02

SAMPLE_RATES = [16000, 24000, 32000, 44100, 48000]
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_BAUD = 921600

# ============================================================================
# 2. WINDOWS CORE AUDIO (WASAPI) NATIVE CTYPES WRAPPER (ZERO PIP DEPS)
# ============================================================================
IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.HRESULT
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    ole32.PropVariantClear.argtypes = [ctypes.c_void_p]
    ole32.PropVariantClear.restype = ctypes.HRESULT
    ole32.CoCreateInstance.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    ole32.CoCreateInstance.restype = ctypes.HRESULT

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

        def __init__(self, guid_str: str = "{00000000-0000-0000-0000-000000000000}"):
            u = uuid.UUID(guid_str)
            super().__init__(
                u.time_low,
                u.time_mid,
                u.time_hi_version,
                (ctypes.c_ubyte * 8)(*u.bytes[8:]),
            )

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", wintypes.WORD),
            ("r1", wintypes.WORD),
            ("r2", wintypes.WORD),
            ("r3", wintypes.WORD),
            ("val_ptr", ctypes.c_void_p),
            ("pad", ctypes.c_void_p),
        ]

    class WAVEFORMATEX(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("wFormatTag", wintypes.WORD),
            ("nChannels", wintypes.WORD),
            ("nSamplesPerSec", wintypes.DWORD),
            ("nAvgBytesPerSec", wintypes.DWORD),
            ("nBlockAlign", wintypes.WORD),
            ("wBitsPerSample", wintypes.WORD),
            ("cbSize", wintypes.WORD),
        ]

    class WAVEFORMATEXTENSIBLE(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("Format", WAVEFORMATEX),
            ("wValidBitsPerSample", wintypes.WORD),
            ("dwChannelMask", wintypes.DWORD),
            ("SubFormat", GUID),
        ]

    CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    IID_IAudioClient = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
    IID_IAudioCaptureClient = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    CLSID_PolicyConfigClient = GUID("{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}")
    IID_IPolicyConfig = GUID("{F8679F50-850A-41CF-9C72-430F290290C8}")
    PKEY_Device_FriendlyName = PROPERTYKEY(GUID("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14)

    CLSCTX_ALL = 1 | 2 | 4 | 16
    COINIT_MULTITHREADED = 0x0
    AUDCLNT_SHAREMODE_SHARED = 0
    AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
    AUDCLNT_BUFFERFLAGS_SILENT = 0x00000002
    DEVICE_STATE_ACTIVE = 0x00000001
    E_RENDER = 0
    E_CAPTURE = 1

    def _vcall(ptr: ctypes.c_void_p, idx: int, restype, *argtypes):
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[idx])

    def _com_release(ptr: ctypes.c_void_p | None) -> None:
        if ptr and ptr.value:
            try:
                _vcall(ptr, 2, wintypes.ULONG)(ptr)
            except Exception:
                pass
            ptr.value = None

    def _com_init() -> bool:
        try:
            hr = ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
            return hr in (0, 1)
        except OSError:
            # RPC_E_CHANGED_MODE (already initialized in STA) is fine
            return False

    def _com_uninit(did_init: bool) -> None:
        if did_init:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass

    def _get_device_id_and_name(dev_ptr: ctypes.c_void_p) -> tuple[str, str]:
        did_p = ctypes.c_void_p()
        _vcall(dev_ptr, 5, ctypes.HRESULT, ctypes.POINTER(ctypes.c_void_p))(
            dev_ptr, ctypes.byref(did_p)
        )
        dev_id = ctypes.wstring_at(did_p.value) if did_p.value else ""
        if did_p.value:
            ole32.CoTaskMemFree(did_p)

        name = dev_id
        ps = ctypes.c_void_p()
        try:
            _vcall(dev_ptr, 4, ctypes.HRESULT, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(
                dev_ptr, 0, ctypes.byref(ps)
            )
            pv = PROPVARIANT()
            _vcall(ps, 5, ctypes.HRESULT, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(
                ps, ctypes.byref(PKEY_Device_FriendlyName), ctypes.byref(pv)
            )
            try:
                if pv.vt == 31 and pv.val_ptr:
                    name = ctypes.wstring_at(pv.val_ptr)
            finally:
                ole32.PropVariantClear(ctypes.byref(pv))
        except Exception:
            pass
        finally:
            _com_release(ps)
        return dev_id, name


class AudioEndpointInfo:
    def __init__(self, dev_id: str, name: str, flow: str, is_default: bool, is_virtual: bool):
        self.dev_id = dev_id
        self.name = name
        self.flow = flow  # "loopback" (render) or "capture" (mic/input)
        self.is_default = is_default
        self.is_virtual = is_virtual

    def display_label(self) -> str:
        tag = "[Loa PC / Loopback]" if self.flow == "loopback" else "[Mic / Input]"
        badges = []
        if self.is_virtual:
            badges.append("VIRTUAL")
        if self.is_default:
            badges.append("MAC DINH")
        suffix = f" ({', '.join(badges)})" if badges else ""
        return f"{tag} {self.name}{suffix}"


def _is_virtual_audio_name(name: str) -> bool:
    low = name.lower()
    keywords = ("vb-audio", "cable input", "cable output", "voicemeeter", "virtual", "esp32")
    return any(k in low for k in keywords)


def list_windows_audio_endpoints() -> list[AudioEndpointInfo]:
    if not IS_WINDOWS:
        return []
    did_init = _com_init()
    enum_ptr = ctypes.c_void_p()
    endpoints: list[AudioEndpointInfo] = []
    try:
        ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDeviceEnumerator),
            None,
            CLSCTX_ALL,
            ctypes.byref(IID_IMMDeviceEnumerator),
            ctypes.byref(enum_ptr),
        )
        enum_fn = _vcall(
            enum_ptr, 3, ctypes.HRESULT, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)
        )
        def_fn = _vcall(
            enum_ptr, 4, ctypes.HRESULT, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
        )

        for flow_int, flow_str in ((E_RENDER, "loopback"), (E_CAPTURE, "capture")):
            default_id = ""
            def_dev = ctypes.c_void_p()
            try:
                def_fn(enum_ptr, flow_int, 0, ctypes.byref(def_dev))
                default_id, _ = _get_device_id_and_name(def_dev)
            except Exception:
                pass
            finally:
                _com_release(def_dev)

            coll = ctypes.c_void_p()
            try:
                enum_fn(enum_ptr, flow_int, DEVICE_STATE_ACTIVE, ctypes.byref(coll))
                cnt = wintypes.UINT(0)
                _vcall(coll, 3, ctypes.HRESULT, ctypes.POINTER(wintypes.UINT))(coll, ctypes.byref(cnt))
                item_fn = _vcall(coll, 4, ctypes.HRESULT, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))
                for i in range(cnt.value):
                    dev = ctypes.c_void_p()
                    try:
                        item_fn(coll, i, ctypes.byref(dev))
                        did, name = _get_device_id_and_name(dev)
                        endpoints.append(
                            AudioEndpointInfo(
                                dev_id=did,
                                name=name,
                                flow=flow_str,
                                is_default=(did == default_id and bool(default_id)),
                                is_virtual=_is_virtual_audio_name(name),
                            )
                        )
                    finally:
                        _com_release(dev)
            finally:
                _com_release(coll)
    except Exception:
        pass
    finally:
        _com_release(enum_ptr)
        _com_uninit(did_init)
    return endpoints


def get_default_playback_id() -> str:
    for ep in list_windows_audio_endpoints():
        if ep.flow == "loopback" and ep.is_default:
            return ep.dev_id
    return ""


def set_windows_default_playback(dev_id: str) -> tuple[bool, str]:
    """Chuyen thiet bi phat mac dinh cua Windows (eConsole/eMultimedia/eCommunications)."""
    if not IS_WINDOWS or not dev_id:
        return False, "Chi ho tro tren Windows."
    did_init = _com_init()
    pc_ptr = ctypes.c_void_p()
    try:
        ole32.CoCreateInstance(
            ctypes.byref(CLSID_PolicyConfigClient),
            None,
            CLSCTX_ALL,
            ctypes.byref(IID_IPolicyConfig),
            ctypes.byref(pc_ptr),
        )
        # IPolicyConfig::SetDefaultEndpoint = vtable index 13
        set_def = _vcall(pc_ptr, 13, ctypes.HRESULT, ctypes.c_wchar_p, ctypes.c_int)
        for role in (0, 1, 2):
            set_def(pc_ptr, dev_id, role)
        return True, "Da chuyen loa mac dinh Windows thanh cong."
    except Exception as exc:
        return False, f"Khong chuyen duoc loa mac dinh Windows: {exc}"
    finally:
        _com_release(pc_ptr)
        _com_uninit(did_init)


# ============================================================================
# 3. BO CHUYEN DOI TAN SO LAY MAU LIEN TUC (CLICK-FREE STATEFUL DSP)
# ============================================================================
class StatefulAudioProcessor:
    """Chuyen doi tan so lay mau lien tuc giua cac chunk khong gay tieng click."""

    def __init__(self, src_rate: int, dst_rate: int):
        self.src_rate = max(1, int(src_rate))
        self.dst_rate = max(1, int(dst_rate))
        self._sos = None
        self._zi = None
        self._phase = 0.0
        self._prev_sample = 0.0

        if HAS_SCIPY and self.dst_rate < self.src_rate:
            nyq = self.src_rate * 0.5
            cutoff = min(0.95, (self.dst_rate * 0.44) / nyq)
            if 0.01 < cutoff < 0.99:
                self._sos = sp_signal.butter(4, cutoff, btype="low", output="sos")
                self._zi = np.zeros((self._sos.shape[0], 2), dtype=np.float64)

    def reset(self, src_rate: int, dst_rate: int) -> None:
        self.__init__(src_rate, dst_rate)

    def process(
        self,
        mono_f32: np.ndarray,
        gain: float = 1.0,
        soft_limit: bool = True,
    ) -> np.ndarray:
        if mono_f32.size == 0:
            return np.empty(0, dtype=np.int16)

        x = mono_f32.astype(np.float64, copy=False)

        # 1. Loc thong thap chong chong pho (giu trang thai _zi lien tuc giua cac goi)
        if self._sos is not None:
            x, self._zi = sp_signal.sosfilt(self._sos, x, zi=self._zi)

        # 2. Noi suy tuyen tinh lien tuc voi pha _phase giu qua bien chunk
        if self.src_rate == self.dst_rate:
            y = x
            self._prev_sample = float(x[-1])
        else:
            step = self.src_rate / float(self.dst_rate)
            ext = np.empty(x.size + 1, dtype=np.float64)
            ext[0] = self._prev_sample
            ext[1:] = x

            max_t = float(x.size)
            if self._phase < max_t:
                n_out = int(math.floor((max_t - self._phase - 1e-12) / step)) + 1
                if n_out > 0:
                    positions = self._phase + np.arange(n_out, dtype=np.float64) * step
                    # ext[0] ung voi vi tri -1, ext[1] ung voi 0 -> dich +1
                    shifted = positions + 1.0
                    idx0 = np.floor(shifted).astype(np.int64)
                    idx0 = np.clip(idx0, 0, x.size - 1)
                    frac = shifted - idx0
                    y = ext[idx0] * (1.0 - frac) + ext[idx0 + 1] * frac
                    self._phase = float(positions[-1] + step - max_t)
                else:
                    y = np.empty(0, dtype=np.float64)
                    self._phase -= max_t
            else:
                y = np.empty(0, dtype=np.float64)
                self._phase -= max_t
            self._prev_sample = float(x[-1])

        if y.size == 0:
            return np.empty(0, dtype=np.int16)

        # 3. Tang ich + Soft-clipper chong re loa 3W
        y = y * float(gain)
        if soft_limit:
            # Giu tuyen tinh den 0.80, nen mem voi tanh tu 0.80 -> 0.99
            abs_y = np.abs(y)
            mask = abs_y > 0.80
            if np.any(mask):
                sign = np.sign(y[mask])
                excess = (abs_y[mask] - 0.80) / 0.20
                y[mask] = sign * (0.80 + 0.19 * np.tanh(excess))
        y = np.clip(y, -1.0, 1.0)
        return np.round(y * 32767.0).astype(np.int16)


# ============================================================================
# 4. KET NOI SERIAL KHUNG NHI PHAN VOI ESP32-S3
# ============================================================================
def build_binary_packet(pkt_type: int, seq: int, payload: bytes) -> bytes:
    header = struct.pack("<BBH", pkt_type & 0xFF, seq & 0xFF, len(payload))
    chk = (sum(header) + sum(payload)) & 0xFFFF
    return bytes((PKT_SYNC_0, PKT_SYNC_1)) + header + payload + struct.pack("<H", chk)


class Esp32SpeakerLink:
    """Quan ly cong Serial hai chieu (ghi goi PCM/lenh + doc trang thai JSON)."""

    def __init__(self, event_queue: queue.Queue):
        self.event_queue = event_queue
        self.ser: serial.Serial | None = None
        self._tx_lock = threading.Lock()
        self._rx_thread: threading.Thread | None = None
        self._stop_rx = threading.Event()
        self._seq = 0
        self.tx_bytes_window = 0
        self.tx_packets_total = 0

    @property
    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self, port: str, baud: int) -> None:
        self.disconnect()
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = baud
        ser.timeout = 0.05
        ser.write_timeout = 0.25
        ser.dtr = False
        ser.rts = False
        ser.open()
        self.ser = ser
        self._seq = 0
        self.tx_bytes_window = 0
        self.tx_packets_total = 0
        self._stop_rx.clear()
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

    def disconnect(self) -> None:
        self._stop_rx.set()
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=0.4)
        self._rx_thread = None
        with self._tx_lock:
            if self.ser is not None:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None

    def send_command(self, cmd: str) -> bool:
        if not self.is_connected:
            return False
        payload = cmd.strip().encode("utf-8", errors="ignore")
        if not payload:
            return False
        with self._tx_lock:
            if not self.ser or not self.ser.is_open:
                return False
            self._seq = (self._seq + 1) & 0xFF
            pkt = build_binary_packet(PKT_TYPE_CMD, self._seq, payload)
            try:
                self.ser.write(pkt)
                return True
            except Exception as exc:
                self.event_queue.put(("serial_err", str(exc)))
                return False

    def send_pcm_samples(self, samples_i16: np.ndarray) -> bool:
        if not self.is_connected or samples_i16.size == 0:
            return False
        raw = samples_i16.astype("<i2", copy=False).tobytes()
        # Chia toi da 960 bytes (480 mau) moi khung de khong vuot PKT_MAX_PAYLOAD
        max_bytes = 960
        with self._tx_lock:
            if not self.ser or not self.ser.is_open:
                return False
            try:
                for off in range(0, len(raw), max_bytes):
                    chunk = raw[off : off + max_bytes]
                    self._seq = (self._seq + 1) & 0xFF
                    pkt = build_binary_packet(PKT_TYPE_PCM_MONO, self._seq, chunk)
                    self.ser.write(pkt)
                    self.tx_bytes_window += len(pkt)
                    self.tx_packets_total += 1
                return True
            except Exception as exc:
                self.event_queue.put(("serial_err", str(exc)))
                return False

    def _rx_loop(self) -> None:
        buf = bytearray()
        while not self._stop_rx.is_set():
            ser = self.ser
            if ser is None or not ser.is_open:
                break
            try:
                n = ser.in_waiting
                data = ser.read(n if n > 0 else 1)
                if not data:
                    continue
                buf.extend(data)
                while b"\n" in buf:
                    raw_line, _, rest = buf.partition(b"\n")
                    buf = bytearray(rest)
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if line:
                        self.event_queue.put(("esp_line", line))
                if len(buf) > 4096:
                    buf.clear()
            except Exception as exc:
                if not self._stop_rx.is_set():
                    self.event_queue.put(("serial_err", str(exc)))
                break


# ============================================================================
# 5. LUONG THU AM THANH WINDOWS WASAPI -> STREAM ESP32-S3
# ============================================================================
class WasapiStreamerThread(threading.Thread):
    """Thu am thanh truc tiep tu thiet bi Windows (Loopback hoac Input) va day sang ESP32."""

    def __init__(
        self,
        endpoint: AudioEndpointInfo,
        link: Esp32SpeakerLink,
        event_queue: queue.Queue,
        get_settings_cb,
    ):
        super().__init__(daemon=True)
        self.endpoint = endpoint
        self.link = link
        self.event_queue = event_queue
        self.get_settings = get_settings_cb
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def _decode_buffer_to_mono(
        self,
        raw_bytes: bytes,
        channels: int,
        bits: int,
        is_float: bool,
        mix_mode: str,
    ) -> np.ndarray:
        if not raw_bytes:
            return np.empty(0, dtype=np.float32)

        if is_float and bits == 32:
            arr = np.frombuffer(raw_bytes, dtype="<f4")
        elif bits == 16:
            arr = np.frombuffer(raw_bytes, dtype="<i2").astype(np.float32) / 32768.0
        elif bits == 32:
            arr = np.frombuffer(raw_bytes, dtype="<i4").astype(np.float32) / 2147483648.0
        elif bits == 24:
            u8 = np.frombuffer(raw_bytes, dtype=np.uint8).reshape(-1, 3)
            i32 = (
                u8[:, 0].astype(np.int32)
                | (u8[:, 1].astype(np.int32) << 8)
                | (u8[:, 2].astype(np.int32) << 16)
            )
            i32 = np.where(i32 & 0x800000, i32 - 0x1000000, i32)
            arr = i32.astype(np.float32) / 8388608.0
        else:
            return np.empty(0, dtype=np.float32)

        if channels <= 1:
            return arr
        frames = arr.size // channels
        if frames == 0:
            return np.empty(0, dtype=np.float32)
        mat = arr[: frames * channels].reshape(frames, channels)
        if mix_mode == "LEFT":
            return mat[:, 0]
        if mix_mode == "RIGHT" and channels >= 2:
            return mat[:, 1]
        if channels >= 2:
            return 0.5 * (mat[:, 0] + mat[:, 1])
        return mat[:, 0]

    def run(self) -> None:
        if not IS_WINDOWS:
            self.event_queue.put(("stream_err", "WASAPI chi ho tro tren Windows."))
            return

        did_init = _com_init()
        enum_ptr = ctypes.c_void_p()
        dev_ptr = ctypes.c_void_p()
        ac_ptr = ctypes.c_void_p()
        cap_ptr = ctypes.c_void_p()
        fmt_ptr = ctypes.c_void_p()
        started = False

        try:
            ole32.CoCreateInstance(
                ctypes.byref(CLSID_MMDeviceEnumerator),
                None,
                CLSCTX_ALL,
                ctypes.byref(IID_IMMDeviceEnumerator),
                ctypes.byref(enum_ptr),
            )
            # IMMDeviceEnumerator::GetDevice = index 5
            _vcall(enum_ptr, 5, ctypes.HRESULT, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p))(
                enum_ptr, self.endpoint.dev_id, ctypes.byref(dev_ptr)
            )
            _vcall(
                dev_ptr, 3, ctypes.HRESULT,
                ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
            )(dev_ptr, ctypes.byref(IID_IAudioClient), CLSCTX_ALL, None, ctypes.byref(ac_ptr))

            _vcall(ac_ptr, 8, ctypes.HRESULT, ctypes.POINTER(ctypes.c_void_p))(
                ac_ptr, ctypes.byref(fmt_ptr)
            )
            wf = ctypes.cast(fmt_ptr, ctypes.POINTER(WAVEFORMATEX)).contents
            src_rate = int(wf.nSamplesPerSec)
            channels = int(wf.nChannels)
            bits = int(wf.wBitsPerSample)
            block_align = int(wf.nBlockAlign)

            is_float = wf.wFormatTag == 3
            if wf.wFormatTag == 0xFFFE:
                wfe = ctypes.cast(fmt_ptr, ctypes.POINTER(WAVEFORMATEXTENSIBLE)).contents
                is_float = (wfe.SubFormat.Data1 == 3)

            stream_flags = (
                AUDCLNT_STREAMFLAGS_LOOPBACK if self.endpoint.flow == "loopback" else 0
            )
            # Buffer 100 ms (1_000_000 * 100ns)
            _vcall(
                ac_ptr, 3, ctypes.HRESULT,
                ctypes.c_int, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong,
                ctypes.c_void_p, ctypes.c_void_p,
            )(ac_ptr, AUDCLNT_SHAREMODE_SHARED, stream_flags, 1000000, 0, fmt_ptr, None)

            _vcall(ac_ptr, 14, ctypes.HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                ac_ptr, ctypes.byref(IID_IAudioCaptureClient), ctypes.byref(cap_ptr)
            )

            _vcall(ac_ptr, 10, ctypes.HRESULT)(ac_ptr)  # Start
            started = True

            self.event_queue.put(
                (
                    "stream_info",
                    f"Dang thu WASAPI ({'Loopback' if stream_flags else 'Input'}): "
                    f"{self.endpoint.name} | {src_rate} Hz, {channels}ch, "
                    f"{bits}-bit {'Float' if is_float else 'PCM'}",
                )
            )

            get_next_pkt = _vcall(cap_ptr, 5, ctypes.HRESULT, ctypes.POINTER(wintypes.UINT))
            get_buf = _vcall(
                cap_ptr, 3, ctypes.HRESULT,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(wintypes.UINT),
                ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(ctypes.c_uint64),
                ctypes.POINTER(ctypes.c_uint64),
            )
            rel_buf = _vcall(cap_ptr, 4, ctypes.HRESULT, wintypes.UINT)

            cfg = self.get_settings()
            dst_rate = int(cfg["sample_rate"])
            dsp = StatefulAudioProcessor(src_rate, dst_rate)

            out_accum = np.empty(0, dtype=np.int16)
            last_audio_time = time.monotonic()
            last_ui_vis_time = 0.0
            last_keepalive_time = time.monotonic()

            while not self._stop_event.is_set():
                cfg = self.get_settings()
                if int(cfg["sample_rate"]) != dst_rate:
                    dst_rate = int(cfg["sample_rate"])
                    dsp.reset(src_rate, dst_rate)
                    out_accum = np.empty(0, dtype=np.int16)

                packet_samples = max(120, int(dst_rate * 0.010))  # 10 ms / goi
                pkt_len = wintypes.UINT(0)
                get_next_pkt(cap_ptr, ctypes.byref(pkt_len))

                if pkt_len.value == 0:
                    now = time.monotonic()
                    if cfg["keep_alive"] and (now - last_keepalive_time >= 0.010):
                        last_keepalive_time = now
                        zeros = np.zeros(packet_samples, dtype=np.int16)
                        if self.link.is_connected:
                            self.link.send_pcm_samples(zeros)
                    elif now - last_ui_vis_time >= 0.08:
                        last_ui_vis_time = now
                        self.event_queue.put(("vis", np.zeros(64, dtype=np.float32), 0.0, False))
                    time.sleep(0.003)
                    continue

                while pkt_len.value > 0 and not self._stop_event.is_set():
                    data_ptr = ctypes.c_void_p()
                    num_frames = wintypes.UINT(0)
                    flags = wintypes.DWORD(0)
                    dev_pos = ctypes.c_uint64(0)
                    qpc_pos = ctypes.c_uint64(0)

                    get_buf(
                        cap_ptr,
                        ctypes.byref(data_ptr),
                        ctypes.byref(num_frames),
                        ctypes.byref(flags),
                        ctypes.byref(dev_pos),
                        ctypes.byref(qpc_pos),
                    )
                    n_fr = int(num_frames.value)
                    try:
                        if n_fr > 0:
                            if (flags.value & AUDCLNT_BUFFERFLAGS_SILENT) or not data_ptr.value:
                                mono = np.zeros(n_fr, dtype=np.float32)
                            else:
                                raw = ctypes.string_at(data_ptr.value, n_fr * block_align)
                                mono = self._decode_buffer_to_mono(
                                    raw, channels, bits, is_float, cfg["mix_mode"]
                                )
                        else:
                            mono = np.empty(0, dtype=np.float32)
                    finally:
                        rel_buf(cap_ptr, num_frames)

                    if mono.size > 0:
                        pcm_i16 = dsp.process(
                            mono,
                            gain=float(cfg["pc_gain"]),
                            soft_limit=bool(cfg["soft_limit"]),
                        )
                        if pcm_i16.size > 0:
                            peak = float(np.max(np.abs(pcm_i16))) / 32767.0
                            now = time.monotonic()
                            if peak > 0.0015:
                                last_audio_time = now

                            should_send = bool(cfg["keep_alive"]) or (now - last_audio_time < 0.75)
                            if should_send:
                                if out_accum.size == 0:
                                    out_accum = pcm_i16
                                else:
                                    out_accum = np.concatenate((out_accum, pcm_i16))

                                while out_accum.size >= packet_samples and not self._stop_event.is_set():
                                    chunk = out_accum[:packet_samples]
                                    out_accum = out_accum[packet_samples:]
                                    last_keepalive_time = now
                                    if self.link.is_connected:
                                        self.link.send_pcm_samples(chunk)
                            else:
                                out_accum = np.empty(0, dtype=np.int16)

                            if now - last_ui_vis_time >= 0.04:
                                last_ui_vis_time = now
                                vis_len = 96
                                if pcm_i16.size >= vis_len:
                                    idx = np.linspace(0, pcm_i16.size - 1, vis_len, dtype=np.int32)
                                    wave_pts = pcm_i16[idx].astype(np.float32) / 32767.0
                                else:
                                    wave_pts = pcm_i16.astype(np.float32) / 32767.0
                                self.event_queue.put(("vis", wave_pts, peak, peak >= 0.98))

                    get_next_pkt(cap_ptr, ctypes.byref(pkt_len))

        except Exception as exc:
            self.event_queue.put(("stream_err", f"Loi WASAPI: {exc}"))
        finally:
            if started and ac_ptr.value:
                try:
                    _vcall(ac_ptr, 11, ctypes.HRESULT)(ac_ptr)  # Stop
                except Exception:
                    pass
            if fmt_ptr.value:
                try:
                    ole32.CoTaskMemFree(fmt_ptr)
                except Exception:
                    pass
            _com_release(cap_ptr)
            _com_release(ac_ptr)
            _com_release(dev_ptr)
            _com_release(enum_ptr)
            _com_uninit(did_init)
            self.event_queue.put(("stream_stopped", ""))


# ============================================================================
# 6. LUONG PHAT FILE .WAV TRUC TIEP RA LOA ESP32-S3
# ============================================================================
class WavStreamerThread(threading.Thread):
    def __init__(self, wav_path: str, link: Esp32SpeakerLink, event_queue: queue.Queue, get_settings_cb):
        super().__init__(daemon=True)
        self.wav_path = wav_path
        self.link = link
        self.event_queue = event_queue
        self.get_settings = get_settings_cb
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            with wave.open(self.wav_path, "rb") as wf:
                src_rate = wf.getframerate()
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                total_frames = wf.getnframes()

                cfg = self.get_settings()
                dst_rate = int(cfg["sample_rate"])
                dsp = StatefulAudioProcessor(src_rate, dst_rate)

                self.event_queue.put(
                    (
                        "wav_info",
                        f"Dang phat WAV: {os.path.basename(self.wav_path)} "
                        f"({src_rate} Hz, {channels}ch, {sampwidth*8}-bit, {total_frames/max(1,src_rate):.1f}s)",
                    )
                )

                read_frames = max(256, int(src_rate * 0.020))  # doc 20ms/lan
                next_send_time = time.perf_counter()

                while not self._stop_event.is_set() and self.link.is_connected:
                    raw = wf.readframes(read_frames)
                    if not raw:
                        break
                    cfg = self.get_settings()
                    if int(cfg["sample_rate"]) != dst_rate:
                        dst_rate = int(cfg["sample_rate"])
                        dsp.reset(src_rate, dst_rate)

                    if sampwidth == 1:
                        arr = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
                    elif sampwidth == 2:
                        arr = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                    elif sampwidth == 3:
                        u8 = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
                        i32 = (
                            u8[:, 0].astype(np.int32)
                            | (u8[:, 1].astype(np.int32) << 8)
                            | (u8[:, 2].astype(np.int32) << 16)
                        )
                        i32 = np.where(i32 & 0x800000, i32 - 0x1000000, i32)
                        arr = i32.astype(np.float32) / 8388608.0
                    elif sampwidth == 4:
                        arr = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
                    else:
                        raise ValueError(f"Khong ho tro WAV {sampwidth*8}-bit")

                    if channels >= 2:
                        n_fr = arr.size // channels
                        mat = arr[: n_fr * channels].reshape(n_fr, channels)
                        mono = 0.5 * (mat[:, 0] + mat[:, 1])
                    else:
                        mono = arr

                    pcm_i16 = dsp.process(
                        mono, gain=float(cfg["pc_gain"]), soft_limit=bool(cfg["soft_limit"])
                    )
                    if pcm_i16.size > 0:
                        self.link.send_pcm_samples(pcm_i16)
                        peak = float(np.max(np.abs(pcm_i16))) / 32767.0
                        idx = np.linspace(0, pcm_i16.size - 1, min(96, pcm_i16.size), dtype=np.int32)
                        self.event_queue.put(("vis", pcm_i16[idx].astype(np.float32) / 32767.0, peak, peak >= 0.98))

                        chunk_dur = pcm_i16.size / float(dst_rate)
                        next_send_time += chunk_dur
                        sleep_s = next_send_time - time.perf_counter()
                        if sleep_s > 0:
                            time.sleep(min(sleep_s, 0.05))
                        elif sleep_s < -0.15:
                            next_send_time = time.perf_counter()
        except Exception as exc:
            self.event_queue.put(("wav_err", f"Loi phat WAV: {exc}"))
        finally:
            self.event_queue.put(("wav_stopped", ""))


# ============================================================================
# 7. GIAO DIEN TKINTER DARK-MODE: GIA LAP LOA PC & TEST PHAN CUNG MAX98357A
# ============================================================================
class Esp32PcSpeakerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ESP32-S3 PC Speaker Bridge & MAX98357A Tester (main.fzz)")
        self.root.geometry("1220x800")
        self.root.minsize(1060, 700)
        self.root.configure(bg="#0d1117")

        self.event_queue: queue.Queue = queue.Queue()
        self.link = Esp32SpeakerLink(self.event_queue)
        self.streamer: WasapiStreamerThread | None = None
        self.wav_thread: WavStreamerThread | None = None

        self.endpoints: list[AudioEndpointInfo] = []
        self.original_default_playback_id: str = ""
        self.switched_default_playback: bool = False

        self._last_kb_time = time.monotonic()
        self._current_kbps = 0.0
        self._peak_hold = 0.0
        self._peak_hold_ts = 0.0
        self._last_wave = np.zeros(96, dtype=np.float32)

        # Bien Tkinter
        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value=str(DEFAULT_BAUD))
        self.rate_var = tk.StringVar(value=str(DEFAULT_SAMPLE_RATE))
        self.endpoint_var = tk.StringVar()
        self.mix_mode_var = tk.StringVar(value="L+R")
        self.pc_gain_var = tk.DoubleVar(value=100.0)
        self.fw_vol_var = tk.IntVar(value=60)
        self.soft_limit_var = tk.BooleanVar(value=True)
        self.keep_alive_var = tk.BooleanVar(value=False)
        self.auto_switch_vbcable_var = tk.BooleanVar(value=True)
        self.mute_var = tk.BooleanVar(value=False)
        self.sd_var = tk.BooleanVar(value=True)
        self.auto_sd_var = tk.BooleanVar(value=False)
        self.i2s_ch_var = tk.StringVar(value="BOTH")
        self.tone_freq_var = tk.IntVar(value=1000)
        self.tone_dur_var = tk.IntVar(value=700)
        self.manual_cmd_var = tk.StringVar()

        self._build_styles()
        self._build_ui()
        self._refresh_ports()
        self._refresh_audio_endpoints()

        atexit.register(self._restore_windows_audio_if_needed)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(40, self._poll_events)

    def _build_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TCombobox", fieldbackground="#161b22", background="#21262d", foreground="#e6edf3")
        style.configure("TScale", background="#161b22")

    def _card(self, parent: tk.Widget, title: str) -> tk.Frame:
        outer = tk.Frame(parent, bg="#161b22", highlightbackground="#30363d", highlightthickness=1)
        hdr = tk.Label(
            outer,
            text=title,
            bg="#161b22",
            fg="#58a6ff",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
            padx=12,
            pady=7,
        )
        hdr.pack(fill="x")
        tk.Frame(outer, bg="#30363d", height=1).pack(fill="x")
        body = tk.Frame(outer, bg="#161b22", padx=12, pady=10)
        body.pack(fill="both", expand=True)
        outer._body = body  # type: ignore[attr-defined]
        return outer

    def _btn(
        self,
        parent: tk.Widget,
        text: str,
        cmd,
        bg: str = "#21262d",
        fg: str = "#e6edf3",
        active_bg: str = "#30363d",
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
            activebackground=active_bg,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=font,
            padx=padx,
            pady=pady,
        )

    def _build_ui(self) -> None:
        # ---- TOP CONNECTION BAR ----
        top = tk.Frame(self.root, bg="#161b22", highlightbackground="#30363d", highlightthickness=1, padx=12, pady=8)
        top.pack(fill="x", padx=10, pady=(10, 6))

        tk.Label(top, text="ESP32-S3 PC SPEAKER", bg="#161b22", fg="#3fb950", font=("Segoe UI", 12, "bold")).pack(
            side="left", padx=(0, 14)
        )

        tk.Label(top, text="Cong COM:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9)).pack(side="left")
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=26, state="readonly")
        self.port_combo.pack(side="left", padx=(4, 6))
        self._btn(top, "Quet", self._refresh_ports).pack(side="left", padx=(0, 10))

        tk.Label(top, text="Baud:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9)).pack(side="left")
        ttk.Combobox(
            top, textvariable=self.baud_var, width=8, state="readonly", values=["115200", "460800", "921600"]
        ).pack(side="left", padx=(4, 10))

        tk.Label(top, text="Sample Rate:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9)).pack(side="left")
        rate_cb = ttk.Combobox(
            top, textvariable=self.rate_var, width=7, state="readonly", values=[str(r) for r in SAMPLE_RATES]
        )
        rate_cb.pack(side="left", padx=(4, 10))
        rate_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_rate_changed())

        self.conn_btn = self._btn(
            top, "Ket noi ESP32", self._toggle_connect, bg="#238636", active_bg="#2ea043", padx=14, pady=5
        )
        self.conn_btn.pack(side="left", padx=(4, 10))

        self.conn_badge = tk.Label(
            top, text="CHUA KET NOI", bg="#30363d", fg="#f0f6fc", font=("Segoe UI", 9, "bold"), padx=10, pady=4
        )
        self.conn_badge.pack(side="right")

        # ---- MAIN 2-COLUMN BODY ----
        main = tk.Frame(self.root, bg="#0d1117")
        main.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        main.columnconfigure(0, weight=13)
        main.columnconfigure(1, weight=9)
        main.rowconfigure(0, weight=1)

        left_col = tk.Frame(main, bg="#0d1117")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right_col = tk.Frame(main, bg="#0d1117")
        right_col.grid(row=0, column=1, sticky="nsew")

        # ================= LEFT 1: PC SPEAKER BRIDGE =================
        c_pc = self._card(left_col, "1. GIA LAP LOA MAY TINH -> HUONG AM THANH PC RA LOA ESP32-S3")
        c_pc.pack(fill="x", pady=(0, 8))
        b_pc = c_pc._body  # type: ignore[attr-defined]

        row_dev = tk.Frame(b_pc, bg="#161b22")
        row_dev.pack(fill="x", pady=(0, 8))
        tk.Label(row_dev, text="Nguon am thanh Windows:", bg="#161b22", fg="#c9d1d9", font=("Segoe UI", 9)).pack(
            side="left"
        )
        self.ep_combo = ttk.Combobox(row_dev, textvariable=self.endpoint_var, state="readonly", width=48)
        self.ep_combo.pack(side="left", fill="x", expand=True, padx=6)
        self._btn(row_dev, "Lam moi", self._refresh_audio_endpoints).pack(side="left")

        self.vbcable_status_lbl = tk.Label(
            b_pc,
            text="",
            bg="#161b22",
            fg="#d29922",
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=640,
        )
        self.vbcable_status_lbl.pack(fill="x", pady=(0, 8))

        # Nut chinh bat/tat PC Speaker streaming
        row_act = tk.Frame(b_pc, bg="#161b22")
        row_act.pack(fill="x", pady=(0, 8))
        self.stream_btn = self._btn(
            row_act,
            "BAT DAU PHAT AM THANH MAY TINH RA LOA ESP32",
            self._toggle_pc_stream,
            bg="#1f6feb",
            active_bg="#388bfd",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=9,
        )
        self.stream_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.route_btn = self._btn(
            row_act,
            "Dat lam loa mac dinh PC",
            self._toggle_windows_default_output,
            bg="#6e40c9",
            active_bg="#8957e5",
            padx=10,
            pady=9,
        )
        self.route_btn.pack(side="left")

        # Tuy chon xu ly am thanh PC
        row_opt = tk.Frame(b_pc, bg="#161b22")
        row_opt.pack(fill="x", pady=(0, 6))

        tk.Label(row_opt, text="PC Boost:", bg="#161b22", fg="#c9d1d9", font=("Segoe UI", 9)).pack(side="left")
        tk.Scale(
            row_opt,
            from_=10,
            to=200,
            orient="horizontal",
            variable=self.pc_gain_var,
            bg="#161b22",
            fg="#e6edf3",
            troughcolor="#0d1117",
            highlightthickness=0,
            length=150,
            showvalue=False,
            command=lambda v: self.pc_gain_lbl.config(text=f"{int(float(v))}%"),
        ).pack(side="left", padx=(4, 4))
        self.pc_gain_lbl = tk.Label(row_opt, text="100%", bg="#161b22", fg="#58a6ff", width=5, font=("Consolas", 9, "bold"))
        self.pc_gain_lbl.pack(side="left", padx=(0, 10))

        tk.Label(row_opt, text="Tron kenh:", bg="#161b22", fg="#c9d1d9", font=("Segoe UI", 9)).pack(side="left")
        ttk.Combobox(
            row_opt, textvariable=self.mix_mode_var, values=["L+R", "LEFT", "RIGHT"], state="readonly", width=6
        ).pack(side="left", padx=(4, 10))

        tk.Checkbutton(
            row_opt,
            text="Chong re (Limiter)",
            variable=self.soft_limit_var,
            bg="#161b22",
            fg="#c9d1d9",
            selectcolor="#0d1117",
            activebackground="#161b22",
            activeforeground="#ffffff",
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(0, 8))

        tk.Checkbutton(
            row_opt,
            text="Keep-alive",
            variable=self.keep_alive_var,
            bg="#161b22",
            fg="#c9d1d9",
            selectcolor="#0d1117",
            activebackground="#161b22",
            activeforeground="#ffffff",
            font=("Segoe UI", 9),
        ).pack(side="left")

        # Hang tien ich: Phat file WAV + Sound Settings + Huong dan VB-Cable
        row_util = tk.Frame(b_pc, bg="#161b22")
        row_util.pack(fill="x", pady=(4, 0))

        self.wav_btn = self._btn(row_util, "Phat file .WAV ra loa ESP32", self._toggle_wav_play, bg="#21262d")
        self.wav_btn.pack(side="left", padx=(0, 6))
        self._btn(row_util, "Mo Windows Sound (mmsys.cpl)", self._open_sound_settings).pack(side="left", padx=(0, 6))
        self._btn(row_util, "Huong dan tat tieng loa Laptop (VB-Cable)", self._show_vbcable_guide).pack(side="left")

        # ================= LEFT 2: LIVE WAVEFORM & STREAM TELEMETRY =================
        c_vis = self._card(left_col, "2. SONG AM THANH THOI GIAN THUC & TRANG THAI BO DEM ESP32")
        c_vis.pack(fill="both", expand=True)
        b_vis = c_vis._body  # type: ignore[attr-defined]

        self.wave_canvas = tk.Canvas(
            b_vis, bg="#0d1117", height=165, highlightbackground="#30363d", highlightthickness=1
        )
        self.wave_canvas.pack(fill="both", expand=True, pady=(0, 8))

        # VU Meter
        vu_row = tk.Frame(b_vis, bg="#161b22")
        vu_row.pack(fill="x", pady=(0, 8))
        tk.Label(vu_row, text="Muc am:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9)).pack(side="left")
        self.vu_canvas = tk.Canvas(vu_row, bg="#0d1117", height=18, highlightbackground="#30363d", highlightthickness=1)
        self.vu_canvas.pack(side="left", fill="x", expand=True, padx=8)
        self.vu_db_lbl = tk.Label(vu_row, text="-inf dBFS", bg="#161b22", fg="#3fb950", width=11, font=("Consolas", 9, "bold"))
        self.vu_db_lbl.pack(side="left")

        # Grid thong so stream
        stats = tk.Frame(b_vis, bg="#0d1117", padx=10, pady=8, highlightbackground="#30363d", highlightthickness=1)
        stats.pack(fill="x")
        self.stat_labels: dict[str, tk.Label] = {}
        stat_defs = [
            ("buf", "Bo dem ESP32", "0 ms"),
            ("kbps", "Toc do TX", "0.0 KB/s"),
            ("rx_ok", "Goi OK / Loi", "0 / 0"),
            ("underrun", "Underrun / Drift", "0 / 0"),
            ("amp", "Mach MAX98357A", "--"),
            ("rate_live", "Tan so I2S", "--"),
        ]
        for idx, (key, title, init_val) in enumerate(stat_defs):
            r, c = divmod(idx, 3)
            cell = tk.Frame(stats, bg="#0d1117")
            cell.grid(row=r, column=c, sticky="ew", padx=6, pady=3)
            stats.columnconfigure(c, weight=1)
            tk.Label(cell, text=title, bg="#0d1117", fg="#8b949e", font=("Segoe UI", 8)).pack(anchor="w")
            lbl = tk.Label(cell, text=init_val, bg="#0d1117", fg="#e6edf3", font=("Consolas", 10, "bold"))
            lbl.pack(anchor="w")
            self.stat_labels[key] = lbl

        # ================= RIGHT 1: PINOUT & HARDWARE CONTROL =================
        c_hw = self._card(right_col, "3. SO DO CHAN (main.fzz) & DIEU KHIEN PHAN CUNG")
        c_hw.pack(fill="x", pady=(0, 8))
        b_hw = c_hw._body  # type: ignore[attr-defined]

        self.pinout_lbl = tk.Label(
            b_hw,
            text=(
                "MAX98357A -> ESP32-S3 (khop D:\\Plant\\main.fzz):\n"
                "  LRC (LRCLK) = GPIO 15   |   BCLK = GPIO 16\n"
                "  DIN         = GPIO 17   |   SD_MODE = GPIO 18\n"
                "  GAIN = GND (12 dB)      |   VIN = 5V, GND = GND"
            ),
            bg="#0d1117",
            fg="#79c0ff",
            font=("Consolas", 9),
            justify="left",
            anchor="w",
            padx=10,
            pady=7,
            highlightbackground="#30363d",
            highlightthickness=1,
        )
        self.pinout_lbl.pack(fill="x", pady=(0, 8))

        # Volume firmware
        vol_row = tk.Frame(b_hw, bg="#161b22")
        vol_row.pack(fill="x", pady=(0, 6))
        tk.Label(vol_row, text="Am luong ESP32:", bg="#161b22", fg="#c9d1d9", font=("Segoe UI", 9)).pack(side="left")
        tk.Scale(
            vol_row,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.fw_vol_var,
            bg="#161b22",
            fg="#e6edf3",
            troughcolor="#0d1117",
            highlightthickness=0,
            showvalue=False,
            command=self._on_fw_vol_drag,
        ).pack(side="left", fill="x", expand=True, padx=6)
        self.fw_vol_lbl = tk.Label(vol_row, text="60%", bg="#161b22", fg="#3fb950", width=5, font=("Consolas", 10, "bold"))
        self.fw_vol_lbl.pack(side="left")

        # Nut phan cung
        hw_r1 = tk.Frame(b_hw, bg="#161b22")
        hw_r1.pack(fill="x", pady=(2, 4))
        self.mute_btn = self._btn(hw_r1, "Mute: OFF", self._toggle_mute)
        self.mute_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.sd_btn = self._btn(hw_r1, "SD_MODE: ON", self._toggle_sd, bg="#238636")
        self.sd_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.auto_sd_btn = self._btn(hw_r1, "Auto-Sleep SD: OFF", self._toggle_auto_sd)
        self.auto_sd_btn.pack(side="left", fill="x", expand=True)

        hw_r2 = tk.Frame(b_hw, bg="#161b22")
        hw_r2.pack(fill="x", pady=(2, 0))
        tk.Label(hw_r2, text="Kenh I2S:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9)).pack(side="left")
        ch_cb = ttk.Combobox(
            hw_r2, textvariable=self.i2s_ch_var, values=["BOTH", "LEFT", "RIGHT"], state="readonly", width=7
        )
        ch_cb.pack(side="left", padx=(4, 8))
        ch_cb.bind("<<ComboboxSelected>>", lambda _e: self._send_cmd(f"CH:{self.i2s_ch_var.get()}"))
        self._btn(
            hw_r2, "Dao BCLK <-> LRCLK (Chan doan)", lambda: self._send_cmd("SWAP_CLK"), bg="#9e6a03", active_bg="#bb8009"
        ).pack(side="left", fill="x", expand=True)

        # ================= RIGHT 2: SPEAKER TEST SIGNALS =================
        c_test = self._card(right_col, "4. BO TEST LOA NOI BO TREN ESP32-S3")
        c_test.pack(fill="x", pady=(0, 8))
        b_test = c_test._body  # type: ignore[attr-defined]

        g_btns = tk.Frame(b_test, bg="#161b22")
        g_btns.pack(fill="x", pady=(0, 6))
        quick_tests = [
            ("Boot Chime", "BOOT", "#21262d"),
            ("440 Hz (La4)", "TONE:440:700", "#21262d"),
            ("1 kHz Chuan", "TONE:1000:700", "#21262d"),
            ("Thang am C4-C5", "SCALE", "#1f6feb"),
            ("Quet 200-4kHz", "SWEEP", "#1f6feb"),
            ("Nhieu trang", "NOISE:900", "#21262d"),
        ]
        for idx, (label, cmd, color) in enumerate(quick_tests):
            r, c = divmod(idx, 3)
            g_btns.columnconfigure(c, weight=1)
            self._btn(g_btns, label, lambda k=cmd: self._send_cmd(k), bg=color).grid(
                row=r, column=c, sticky="ew", padx=2, pady=2
            )

        custom_row = tk.Frame(b_test, bg="#161b22")
        custom_row.pack(fill="x", pady=(4, 0))
        tk.Label(custom_row, text="Hz:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9)).pack(side="left")
        tk.Spinbox(
            custom_row, from_=60, to=10000, increment=50, textvariable=self.tone_freq_var,
            width=6, bg="#0d1117", fg="#e6edf3", insertbackground="#e6edf3", relief="flat"
        ).pack(side="left", padx=(3, 8))
        tk.Label(custom_row, text="ms:", bg="#161b22", fg="#8b949e", font=("Segoe UI", 9)).pack(side="left")
        tk.Spinbox(
            custom_row, from_=100, to=5000, increment=100, textvariable=self.tone_dur_var,
            width=6, bg="#0d1117", fg="#e6edf3", insertbackground="#e6edf3", relief="flat"
        ).pack(side="left", padx=(3, 8))
        self._btn(
            custom_row,
            "Phat Sin",
            lambda: self._send_cmd(f"TONE:{self.tone_freq_var.get()}:{self.tone_dur_var.get()}"),
            bg="#238636",
        ).pack(side="left", padx=(0, 4))
        self._btn(custom_row, "Dung", lambda: self._send_cmd("STOP"), bg="#da3633", active_bg="#f85149").pack(
            side="left"
        )

        # ================= RIGHT 3: SERIAL LOG & CONSOLE =================
        c_log = self._card(right_col, "5. NHAT KY & LENH TRUC TIEP")
        c_log.pack(fill="both", expand=True)
        b_log = c_log._body  # type: ignore[attr-defined]

        self.log_text = tk.Text(
            b_log,
            bg="#0d1117",
            fg="#c9d1d9",
            font=("Consolas", 9),
            height=9,
            relief="flat",
            wrap="word",
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True, pady=(0, 6))

        cmd_row = tk.Frame(b_log, bg="#161b22")
        cmd_row.pack(fill="x")
        ent = tk.Entry(
            cmd_row,
            textvariable=self.manual_cmd_var,
            bg="#0d1117",
            fg="#e6edf3",
            insertbackground="#e6edf3",
            relief="flat",
            font=("Consolas", 9),
        )
        ent.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
        ent.bind("<Return>", lambda _e: self._send_manual_cmd())
        self._btn(cmd_row, "Gui", self._send_manual_cmd).pack(side="left", padx=(0, 4))
        self._btn(cmd_row, "INFO", lambda: self._send_cmd("INFO")).pack(side="left")

    # ========================================================================
    # HELPER LOG & SETTINGS
    # ========================================================================
    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 250:
            self.log_text.delete("1.0", "50.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _get_stream_settings(self) -> dict:
        return {
            "sample_rate": int(self.rate_var.get()),
            "pc_gain": float(self.pc_gain_var.get()) / 100.0,
            "soft_limit": bool(self.soft_limit_var.get()),
            "keep_alive": bool(self.keep_alive_var.get()),
            "mix_mode": self.mix_mode_var.get(),
        }

    # ========================================================================
    # COM PORTS & SERIAL CONNECT
    # ========================================================================
    def _refresh_ports(self) -> None:
        ports = list(serial.tools.list_ports.comports())
        items = []
        best = ""
        for p in ports:
            desc = p.description or ""
            label = f"{p.device} - {desc}"
            items.append(label)
            if not best and (
                (p.vid == 0x303A)
                or any(k in desc.lower() for k in ("esp32", "usb serial", "ch340", "cp210", "jtag"))
            ):
                best = label
        self.port_combo["values"] = items
        if best:
            self.port_var.set(best)
        elif items:
            self.port_var.set(items[0])
        else:
            self.port_var.set("")

    def _selected_port_device(self) -> str:
        val = self.port_var.get().strip()
        return val.split(" - ", 1)[0].strip() if val else ""

    def _toggle_connect(self) -> None:
        if self.link.is_connected:
            self._stop_pc_stream()
            self._stop_wav_play()
            self.link.disconnect()
            self.conn_btn.config(text="Ket noi ESP32", bg="#238636", activebackground="#2ea043")
            self.conn_badge.config(text="CHUA KET NOI", bg="#30363d", fg="#f0f6fc")
            self._log("Da ngat ket noi ESP32-S3.")
            return

        port = self._selected_port_device()
        if not port:
            messagebox.showwarning("Chua co cong COM", "Hay cam ESP32-S3 vao may tinh va bam 'Quet'.")
            return
        baud = int(self.baud_var.get())
        try:
            self.link.connect(port, baud)
        except Exception as exc:
            messagebox.showerror("Loi ket noi", f"Khong mo duoc {port}:\n{exc}")
            return

        self.conn_btn.config(text="Ngat ket noi", bg="#da3633", activebackground="#f85149")
        self.conn_badge.config(text=f"DA KET NOI ({port})", bg="#238636", fg="#ffffff")
        self._log(f"Da ket noi {port} @ {baud} baud.")
        # Dong bo tan so lay mau, am luong & lay thong tin pinout
        self.root.after(150, lambda: self._send_cmd(f"RATE:{self.rate_var.get()}"))
        self.root.after(260, lambda: self._send_cmd(f"VOL:{self.fw_vol_var.get()}"))
        self.root.after(360, lambda: self._send_cmd("INFO"))

    def _send_cmd(self, cmd: str) -> None:
        if not self.link.is_connected:
            self._log(f"[Chua ket noi] Bo qua lenh: {cmd}")
            return
        if self.link.send_command(cmd):
            self._log(f">>> {cmd}")

    def _send_manual_cmd(self) -> None:
        cmd = self.manual_cmd_var.get().strip()
        if cmd:
            self._send_cmd(cmd)
            self.manual_cmd_var.set("")

    # ========================================================================
    # WINDOWS AUDIO ENDPOINTS & VIRTUAL SPEAKER ROUTING
    # ========================================================================
    def _refresh_audio_endpoints(self) -> None:
        self.endpoints = list_windows_audio_endpoints()
        labels = [ep.display_label() for ep in self.endpoints]
        self.ep_combo["values"] = labels

        # Uu tien chon Virtual Cable Loopback neu co, khong thi chon Default Speaker Loopback
        preferred_idx = 0
        virtual_render = [i for i, ep in enumerate(self.endpoints) if ep.flow == "loopback" and ep.is_virtual]
        default_render = [i for i, ep in enumerate(self.endpoints) if ep.flow == "loopback" and ep.is_default]

        if virtual_render:
            preferred_idx = virtual_render[0]
            self.vbcable_status_lbl.config(
                text=(
                    f"[SAN SANG LOA AO] Da phat hien '{self.endpoints[preferred_idx].name}'. "
                    "Bam 'Dat lam loa mac dinh PC' hoac 'Bat dau' de chuyen 100% am thanh PC sang loa ESP32 (loa laptop im lang)."
                ),
                fg="#3fb950",
            )
        elif default_render:
            preferred_idx = default_render[0]
            self.vbcable_status_lbl.config(
                text=(
                    f"[WASAPI LOOPBACK] Dang dung truc tiep '{self.endpoints[preferred_idx].name}' "
                    "(khong can cai them driver). Moi am thanh PC se phat ra loa ESP32. "
                    "Neu muon tat hoan toan loa laptop, cai them VB-Cable mien phi (xem nut Huong dan)."
                ),
                fg="#d29922",
            )
        else:
            self.vbcable_status_lbl.config(text="Khong tim thay thiet bi am thanh Windows.", fg="#f85149")

        if labels:
            self.endpoint_var.set(labels[preferred_idx])

    def _selected_endpoint(self) -> AudioEndpointInfo | None:
        idx = self.ep_combo.current()
        if 0 <= idx < len(self.endpoints):
            return self.endpoints[idx]
        return None

    def _toggle_windows_default_output(self) -> None:
        if self.switched_default_playback and self.original_default_playback_id:
            self._restore_windows_audio_if_needed()
            self._refresh_audio_endpoints()
            return

        ep = self._selected_endpoint()
        if ep is None or ep.flow != "loopback":
            messagebox.showinfo(
                "Chon thiet bi phat",
                "Hay chon mot thiet bi loai [Loa PC / Loopback] (vi du: CABLE Input) de dat lam loa mac dinh Windows.",
            )
            return

        cur_def = get_default_playback_id()
        if cur_def == ep.dev_id:
            messagebox.showinfo("Da la mac dinh", f"'{ep.name}' hien da la loa mac dinh cua Windows.")
            return

        if not self.original_default_playback_id:
            self.original_default_playback_id = cur_def

        ok, msg = set_windows_default_playback(ep.dev_id)
        self._log(msg)
        if ok:
            self.switched_default_playback = True
            self.route_btn.config(text="Khoi phuc loa goc PC", bg="#9e6a03", activebackground="#bb8009")
            self._refresh_audio_endpoints()
            # Giu lua chon dung thiet bi vua chuyen
            for i, item in enumerate(self.endpoints):
                if item.dev_id == ep.dev_id:
                    self.ep_combo.current(i)
                    break
        else:
            messagebox.showerror("Khong chuyen duoc loa", msg)

    def _restore_windows_audio_if_needed(self) -> None:
        if self.switched_default_playback and self.original_default_playback_id:
            ok, msg = set_windows_default_playback(self.original_default_playback_id)
            self.switched_default_playback = False
            self.original_default_playback_id = ""
            try:
                self.route_btn.config(text="Dat lam loa mac dinh PC", bg="#6e40c9", activebackground="#8957e5")
                self._log("Da khoi phuc loa mac dinh goc cua Windows." if ok else msg)
            except Exception:
                pass

    # ========================================================================
    # PC AUDIO STREAMING & WAV PLAYER
    # ========================================================================
    def _toggle_pc_stream(self) -> None:
        if self.streamer is not None and self.streamer.is_alive():
            self._stop_pc_stream()
            return

        if not self.link.is_connected:
            messagebox.showwarning("Chua ket noi ESP32", "Hay ket noi cong COM cua ESP32-S3 truoc khi phat.")
            return

        ep = self._selected_endpoint()
        if ep is None:
            messagebox.showwarning("Chua co nguon am thanh", "Khong tim thay thiet bi am thanh Windows.")
            return

        self._stop_wav_play()

        # Neu nguon duoc chon la Virtual Cable Output/Input dang Playback va chua la mac dinh -> tu chuyen Windows sang no!
        if ep.flow == "loopback" and ep.is_virtual and not ep.is_default:
            cur_def = get_default_playback_id()
            if cur_def and cur_def != ep.dev_id:
                self.original_default_playback_id = cur_def
                ok, msg = set_windows_default_playback(ep.dev_id)
                if ok:
                    self.switched_default_playback = True
                    self.route_btn.config(text="Khoi phuc loa goc PC", bg="#9e6a03", activebackground="#bb8009")
                    self._log(f"Tu dong chuyen dau ra mac dinh Windows -> {ep.name}")

        self._send_cmd(f"RATE:{self.rate_var.get()}")
        self.streamer = WasapiStreamerThread(ep, self.link, self.event_queue, self._get_stream_settings)
        self.streamer.start()
        self.stream_btn.config(
            text="DUNG PHAT AM THANH MAY TINH", bg="#da3633", activebackground="#f85149"
        )

    def _stop_pc_stream(self) -> None:
        if self.streamer is not None:
            self.streamer.stop()
            self.streamer.join(timeout=0.8)
            self.streamer = None
        if self.link.is_connected:
            self._send_cmd("STOP")
        self.stream_btn.config(
            text="BAT DAU PHAT AM THANH MAY TINH RA LOA ESP32", bg="#1f6feb", activebackground="#388bfd"
        )

    def _toggle_wav_play(self) -> None:
        if self.wav_thread is not None and self.wav_thread.is_alive():
            self._stop_wav_play()
            return
        if not self.link.is_connected:
            messagebox.showwarning("Chua ket noi ESP32", "Hay ket noi ESP32-S3 truoc khi phat file WAV.")
            return
        path = filedialog.askopenfilename(
            title="Chon file am thanh .WAV de test loa ESP32-S3",
            filetypes=[("WAV Audio", "*.wav"), ("Tat ca file", "*.*")],
        )
        if not path:
            return
        self._stop_pc_stream()
        self._send_cmd(f"RATE:{self.rate_var.get()}")
        self.wav_thread = WavStreamerThread(path, self.link, self.event_queue, self._get_stream_settings)
        self.wav_thread.start()
        self.wav_btn.config(text="Dung phat .WAV", bg="#da3633")

    def _stop_wav_play(self) -> None:
        if self.wav_thread is not None:
            self.wav_thread.stop()
            self.wav_thread.join(timeout=0.8)
            self.wav_thread = None
        self.wav_btn.config(text="Phat file .WAV ra loa ESP32", bg="#21262d")

    # ========================================================================
    # HARDWARE CONTROL CALLBACKS
    # ========================================================================
    def _on_rate_changed(self) -> None:
        sr = self.rate_var.get()
        if self.link.is_connected:
            self._send_cmd(f"RATE:{sr}")

    def _on_fw_vol_drag(self, val: str) -> None:
        v = int(float(val))
        self.fw_vol_lbl.config(text=f"{v}%")
        if hasattr(self, "_vol_after_id") and self._vol_after_id:
            self.root.after_cancel(self._vol_after_id)
        self._vol_after_id = self.root.after(90, lambda: self._send_cmd(f"VOL:{v}"))

    def _toggle_mute(self) -> None:
        new_val = not self.mute_var.get()
        self.mute_var.set(new_val)
        self.mute_btn.config(
            text="Mute: ON" if new_val else "Mute: OFF",
            bg="#da3633" if new_val else "#21262d",
        )
        self._send_cmd(f"MUTE:{1 if new_val else 0}")

    def _toggle_sd(self) -> None:
        new_val = not self.sd_var.get()
        self.sd_var.set(new_val)
        self.sd_btn.config(
            text="SD_MODE: ON" if new_val else "SD_MODE: OFF",
            bg="#238636" if new_val else "#da3633",
        )
        self._send_cmd(f"SD:{1 if new_val else 0}")

    def _toggle_auto_sd(self) -> None:
        new_val = not self.auto_sd_var.get()
        self.auto_sd_var.set(new_val)
        self.auto_sd_btn.config(
            text="Auto-Sleep SD: ON" if new_val else "Auto-Sleep SD: OFF",
            bg="#1f6feb" if new_val else "#21262d",
        )
        self._send_cmd(f"AUTO_SD:{1 if new_val else 0}")

    def _open_sound_settings(self) -> None:
        if IS_WINDOWS:
            try:
                subprocess.Popen(["control", "mmsys.cpl", "sounds"])
            except Exception as exc:
                self._log(f"Khong mo duoc mmsys.cpl: {exc}")

    def _show_vbcable_guide(self) -> None:
        messagebox.showinfo(
            "Huong dan bien ESP32 thanh loa PC doc lap (tat hoan toan loa Laptop)",
            "Cach 1 (Dung ngay - khong can cai them gi):\n"
            "  - Chon '[Loa PC / Loopback] Speaker (Realtek(R) Audio)' roi bam BAT DAU.\n"
            "  - Moi am thanh YouTube / nhac / Windows se phat dong thoi sang loa ESP32-S3.\n\n"
            "Cach 2 (Chuyen 100% am thanh PC sang ESP32, loa laptop im lang hoan toan):\n"
            "  1. Tai va cai driver mien phi VB-Cable tai: https://vb-audio.com/Cable/\n"
            "  2. (Tuy chon) Mo 'Windows Sound (mmsys.cpl)' -> Playback -> doi ten\n"
            "     'CABLE Input' thanh 'ESP32-S3 Speaker'.\n"
            "  3. Bam 'Lam moi' trong app nay -> app se tu phat hien 'CABLE Input (VIRTUAL)'\n"
            "     va tu chuyen dau ra mac dinh Windows sang no khi bam BAT DAU!\n"
            "  4. Khi dong app, loa goc cua Windows se tu dong duoc khoi phuc.",
        )

    # ========================================================================
    # EVENT LOOP & VISUALIZATION
    # ========================================================================
    def _poll_events(self) -> None:
        # Cap nhat toc do truyen KB/s moi 0.5s
        now = time.monotonic()
        dt = now - self._last_kb_time
        if dt >= 0.5:
            self._current_kbps = (self.link.tx_bytes_window / 1024.0) / dt
            self.link.tx_bytes_window = 0
            self._last_kb_time = now
            self.stat_labels["kbps"].config(text=f"{self._current_kbps:.1f} KB/s")

        processed = 0
        while processed < 40:
            try:
                kind, *rest = self.event_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1

            if kind == "esp_line":
                self._handle_esp_line(rest[0])
            elif kind == "serial_err":
                self._log(f"[LOI SERIAL] {rest[0]}")
                self._toggle_connect()
            elif kind == "stream_info":
                self._log(rest[0])
            elif kind == "stream_err":
                self._log(rest[0])
                self._stop_pc_stream()
                messagebox.showerror("Loi thu am thanh Windows", rest[0])
            elif kind == "stream_stopped":
                self.stream_btn.config(
                    text="BAT DAU PHAT AM THANH MAY TINH RA LOA ESP32", bg="#1f6feb", activebackground="#388bfd"
                )
                self._draw_visuals(np.zeros(96, dtype=np.float32), 0.0, False)
            elif kind == "wav_info":
                self._log(rest[0])
            elif kind == "wav_err":
                self._log(rest[0])
                self._stop_wav_play()
                messagebox.showerror("Loi phat WAV", rest[0])
            elif kind == "wav_stopped":
                self.wav_btn.config(text="Phat file .WAV ra loa ESP32", bg="#21262d")
                self._draw_visuals(np.zeros(96, dtype=np.float32), 0.0, False)
            elif kind == "vis":
                wave_pts, peak, clipped = rest
                self._draw_visuals(wave_pts, peak, clipped)

        self.root.after(30, self._poll_events)

    def _handle_esp_line(self, line: str) -> None:
        if not line.startswith("{"):
            self._log(f"ESP: {line}")
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            self._log(f"ESP: {line}")
            return

        ev = obj.get("event", "")
        if ev != "HB":
            self._log(f"ESP [{ev}]: {line}")

        if "buf_ms" in obj:
            self.stat_labels["buf"].config(text=f"{obj['buf_ms']} ms ({obj.get('buf_samples', 0)})")
        if "rx_ok" in obj:
            bad = obj.get("rx_bad", 0)
            self.stat_labels["rx_ok"].config(
                text=f"{obj['rx_ok']} / {bad}",
                fg="#3fb950" if bad == 0 else "#f85149",
            )
        if "underrun" in obj:
            self.stat_labels["underrun"].config(
                text=f"{obj['underrun']} / {obj.get('drift_drop', 0)}"
            )
        if "amp_on" in obj:
            amp_txt = "BAT (SD=HIGH)" if obj["amp_on"] else "TAT (SD=LOW)"
            self.stat_labels["amp"].config(
                text=amp_txt,
                fg="#3fb950" if obj["amp_on"] else "#d29922",
            )
        if "rate" in obj:
            self.stat_labels["rate_live"].config(text=f"{obj['rate']} Hz")
        if "lrclk" in obj and "bclk" in obj:
            sw = " [DA DAO CLK]" if obj.get("clk_swapped") else " [khop main.fzz]"
            self.pinout_lbl.config(
                text=(
                    f"MAX98357A -> ESP32-S3{sw}:\n"
                    f"  LRC (LRCLK) = GPIO {obj['lrclk']}   |   BCLK = GPIO {obj['bclk']}\n"
                    f"  DIN         = GPIO {obj.get('din', 17)}   |   SD_MODE = GPIO {obj.get('sd_pin', 18)}\n"
                    f"  GAIN = GND (12 dB)      |   VIN = 5V, GND = GND"
                )
            )
        if "vol" in obj and ev in ("INFO", "BOOT_READY"):
            self.fw_vol_var.set(int(obj["vol"]))
            self.fw_vol_lbl.config(text=f"{int(obj['vol'])}%")

    def _draw_visuals(self, wave_pts: np.ndarray, peak: float, clipped: bool) -> None:
        # 1. Waveform canvas
        c = self.wave_canvas
        w = max(100, c.winfo_width())
        h = max(60, c.winfo_height())
        mid = h / 2.0
        c.delete("all")
        c.create_line(0, mid, w, mid, fill="#21262d", dash=(3, 3))

        if wave_pts.size >= 2:
            coords = []
            step_x = w / float(wave_pts.size - 1)
            scale_y = (h * 0.44)
            for i, v in enumerate(wave_pts):
                coords.append(i * step_x)
                coords.append(mid - float(v) * scale_y)
            color = "#f85149" if clipped else "#3fb950"
            c.create_line(*coords, fill=color, width=2, smooth=False)

        # 2. VU Meter
        now = time.monotonic()
        if peak >= self._peak_hold or (now - self._peak_hold_ts) > 0.9:
            self._peak_hold = peak
            self._peak_hold_ts = now

        vc = self.vu_canvas
        vw = max(60, vc.winfo_width())
        vh = max(10, vc.winfo_height())
        vc.delete("all")
        fill_w = int(min(1.0, peak) * vw)
        bar_col = "#f85149" if clipped else ("#d29922" if peak > 0.78 else "#3fb950")
        if fill_w > 0:
            vc.create_rectangle(0, 0, fill_w, vh, fill=bar_col, width=0)
        hold_x = int(min(1.0, self._peak_hold) * vw)
        if hold_x > 2:
            vc.create_line(hold_x, 0, hold_x, vh, fill="#f0f6fc", width=2)

        if peak > 1e-5:
            db = 20.0 * math.log10(peak)
            self.vu_db_lbl.config(
                text=f"{db:5.1f} dBFS" + (" CLIP" if clipped else ""),
                fg="#f85149" if clipped else "#3fb950",
            )
        else:
            self.vu_db_lbl.config(text="-inf dBFS", fg="#8b949e")

    def _on_close(self) -> None:
        self._stop_pc_stream()
        self._stop_wav_play()
        self.link.disconnect()
        self._restore_windows_audio_if_needed()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    Esp32PcSpeakerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
