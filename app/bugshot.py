"""Bug capture: press Ctrl+Alt+PgDn anywhere (even inside the game) and it saves a screenshot plus a system
report into the `screenshots` folder next to this file.

    screenshots\\bug_2026-10-22_08-04-11.png     the whole screen
    screenshots\\bug_2026-10-22_08-04-11.txt     CPU / memory / GPU load, the game copies running, recent launcher log

The launcher starts the listener for you. To use it on its own:  python bugshot.py   (Ctrl+C to stop).
Windows only, no extra packages. A game in true exclusive fullscreen can capture as black: use windowed or
borderless for the screenshot to show the game.
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import os
import subprocess
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

HERE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
FOLDER = HERE / "screenshots"
HOTKEY = "Ctrl+Alt+PgDn"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
VK_NEXT = 0x22                      # Page Down
WM_HOTKEY, WM_QUIT = 0x312, 0x12

user32 = ctypes.windll.user32 if os.name == "nt" else None
gdi32 = ctypes.windll.gdi32 if os.name == "nt" else None
kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None


def _png(width, height, rgb):
    """Encode raw RGB rows as a PNG (no Pillow needed)."""
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 3)) + chunk(b"IEND", b""))


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def screenshot_png():
    """The whole virtual desktop (every monitor) as PNG bytes."""
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass
    x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    w, h = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    screen = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, 0x00CC0020 | 0x40000000)     # SRCCOPY | CAPTUREBLT
    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, screen)
    bgra = buf.raw
    rgb = bytearray(w * h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    return _png(w, h, bytes(rgb))


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def cpu_percent(seconds=1.0):
    """Whole-machine CPU use over a short sample, from GetSystemTimes."""
    class FT(ctypes.Structure):
        _fields_ = [("lo", wt.DWORD), ("hi", wt.DWORD)]

    def sample():
        idle, kern, usr = FT(), FT(), FT()
        kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(usr))
        v = lambda f: (f.hi << 32) | f.lo
        return v(idle), v(kern) + v(usr)

    i0, t0 = sample()
    time.sleep(seconds)
    i1, t1 = sample()
    total = t1 - t0
    return 100.0 * (1 - (i1 - i0) / total) if total else 0.0


def memory_line():
    class MS(ctypes.Structure):
        _fields_ = [("len", wt.DWORD), ("load", wt.DWORD), ("totPhys", ctypes.c_ulonglong),
                    ("availPhys", ctypes.c_ulonglong), ("totPage", ctypes.c_ulonglong),
                    ("availPage", ctypes.c_ulonglong), ("totVirt", ctypes.c_ulonglong),
                    ("availVirt", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]
    m = MS()
    m.len = ctypes.sizeof(MS)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    gb = 1024 ** 3
    return f"{m.load}% used ({(m.totPhys - m.availPhys) / gb:.1f} of {m.totPhys / gb:.1f} GB)"


def gpu_lines():
    out = _run(["nvidia-smi", "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,"
                "temperature.gpu,clocks.gr", "--format=csv,noheader"])
    if out:
        return ["NVIDIA (name, GPU %, memory-controller %, VRAM used, VRAM total, temp C, core clock):"] + \
               ["  " + ln for ln in out.splitlines()]
    # AMD / Intel / anything without nvidia-smi: the Windows GPU counters work for every vendor.
    ps = ("$n=(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) -join '; '; "
          "$u=(Get-Counter '\\GPU Engine(*engtype_3D)\\Utilization Percentage' -ErrorAction SilentlyContinue)."
          "CounterSamples | Measure-Object CookedValue -Sum; "
          "$d=(Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue)."
          "CounterSamples | Measure-Object CookedValue -Maximum; "
          "\"$n | 3D engine use: $([math]::Round($u.Sum,1))% | VRAM in use: $([math]::Round($d.Maximum/1MB)) MB\"")
    out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=15)
    return [out or "GPU: no reading available (no nvidia-smi, and the Windows GPU counters failed)"]


def process_lines():
    ps = ("Get-Process OutlawsAHOM,python,pythonw -ErrorAction SilentlyContinue | "
          "Select-Object Name,Id,@{n='CPUs';e={[math]::Round($_.CPU,1)}},@{n='MB';e={[math]::Round($_.WorkingSet64/1MB)}} | "
          "Format-Table -AutoSize | Out-String -Width 200")
    return _run(["powershell", "-NoProfile", "-Command", ps], timeout=15).splitlines() or ["(none running)"]


def bot_log_lines(keep=150, max_age=900):
    """The tail of each bot's console log (botctl writes them to botlogs\\); only logs touched recently."""
    out = []
    folder = HERE / "botlogs"
    if not folder.is_dir():
        return ["(no botlogs folder yet: start the bots from the launcher first)"]
    now = time.time()
    for f in sorted(folder.glob("*.log")):
        try:
            if now - f.stat().st_mtime > max_age:
                continue
            tail = [ln for ln in f.read_text(encoding="utf-8", errors="replace").splitlines()
                    if not ln.startswith("persona:")][-keep:]
        except OSError:
            continue
        out += ["", f"--- {f.stem} ---"] + tail
    return out or ["(no bot logs written in the last 15 minutes)"]


def build_report(stamp, extra_log=None):
    lines = [f"Bug capture {stamp}", "=" * 40, ""]
    try:
        lines.append(f"CPU: {cpu_percent():.0f}% busy (1 s sample)")
    except Exception as e:
        lines.append(f"CPU: unavailable ({e})")
    try:
        lines.append(f"Memory: {memory_line()}")
    except Exception as e:
        lines.append(f"Memory: unavailable ({e})")
    lines += ["", "GPU:"] + gpu_lines() + ["", "Game and bot processes:"] + process_lines()
    lines += ["", "Bot console logs (last 150 lines of each, from botlogs\\):"] + bot_log_lines()
    if extra_log:
        lines += ["", "Recent launcher log:", "-" * 40] + list(extra_log)
    return "\n".join(lines) + "\n"


def capture(extra_log=None):
    """Save a screenshot and a report; returns (png_path, txt_path)."""
    FOLDER.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    png = FOLDER / f"bug_{stamp}.png"
    txt = FOLDER / f"bug_{stamp}.txt"
    png.write_bytes(screenshot_png())                 # first, so it shows the bug and not the report running
    txt.write_text(build_report(stamp, extra_log() if callable(extra_log) else extra_log), encoding="utf-8")
    return png, txt


class Listener:
    """Global Ctrl+Alt+PgDn hotkey on a background thread. on_done(png, txt) or on_error(text) are called from it."""

    def __init__(self, extra_log=None, on_done=None, on_error=None):
        self.extra_log, self.on_done, self.on_error = extra_log, on_done, on_error
        self.thread_id = None
        self.ok = threading.Event()
        self.error = None
        self.busy = False

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        self.ok.wait(3)
        return self.error is None

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)

    def _loop(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_NEXT):
            self.error = f"{HOTKEY} is already taken by another program"
            self.ok.set()
            return
        self.ok.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and not self.busy:
                self.busy = True
                threading.Thread(target=self._grab, daemon=True).start()
        user32.UnregisterHotKey(None, 1)

    def _grab(self):
        try:
            try:
                import winsound
                winsound.MessageBeep()                # a click so you know it registered
            except Exception:
                pass
            png, txt = capture(self.extra_log)
            if self.on_done:
                self.on_done(png, txt)
        except Exception as e:                        # never let a capture problem touch the game or launcher
            if self.on_error:
                self.on_error(f"bug capture failed: {e}")
        finally:
            self.busy = False


if __name__ == "__main__":
    if os.name != "nt":
        sys.exit("bugshot only works on Windows")
    lst = Listener(on_done=lambda p, t: print("saved", p.name, "and", t.name),
                   on_error=print)
    if not lst.start():
        sys.exit(lst.error)
    print(f"Bug capture ready: press {HOTKEY} to save a screenshot and system report to {FOLDER}. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        lst.stop()
