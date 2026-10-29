"""Outlaws Companion: join (or host) Outlaws Remastered LAN games over ZeroTier.
JOIN: press Join. The app finds the host on the games' ZeroTier network by itself, asks the host which mods the game needs (zombie skins, custom maps ...),
downloads and installs only those, starts your game and puts everything back as soon as the game closes.
HOST: sets up the bot toolkit once, then hosts a bot game with a few simple choices.
Your own files are never overwritten: whatever is replaced is backed up first and restored afterwards (also after a crash:
the next start puts it right). Standard library only; build with build_companion.cmd."""
import os, re, sys, json, time, shutil, hashlib, threading, subprocess, urllib.request, zipfile, tempfile, webbrowser, random
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP = "Outlaws Companion"
DATA = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "OutlawsCompanion")
CONFIG, JOURNAL, BACKUP = (os.path.join(DATA, n) for n in ("config.json", "journal.json", "backup"))
VERSION = 28                              # GitHub raises this by itself on every push that changes companion.py
REPO = "AteEm85/Companion-Launcher"     # GitHub builds OutlawsCompanion.exe here on every push (.github/workflows/build.yml)
BRANCH = "main"
RAW = "https://raw.githubusercontent.com/%s/%s/companion/" % (REPO, BRANCH)
PORT = 8765
DISCOVER_PORT = 8766                     # UDP: hosts answer a broadcast with their name, mode and map
PY_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"   # installed for the user when hosting needs it
ZT_NET = "b6079f73c6535907"            # the games' ZeroTier network
ZT_SERVICE = "ZeroTierOneService"
ZT_MSI = "https://download.zerotier.com/dist/ZeroTier%20One.msi"
ZT_CLI = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "ZeroTier", "One", "zerotier-cli.bat")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ALLOWED_EXT = (".spx", ".wav", ".lab", ".rcm", ".obt", ".obb")      # .wav: the zombie death sounds
CHARS = ["Sanchez", "James Anderson", "Two Feathers", "Dr Death", "Bob", "Mary"]
SCREENS = {"Window": (["-window", "-width", "1280", "-height", "720"], "0"),        # (flags, v_windowmode)
           "Borderless window": ([], "1"),
           "Full screen": (["-fullscreen"], "2")}

def game_cmd(game, screen, char=None, join=None, horde=False):
    """The command line for your own game. The game reads ONE -setvar block (name value name value ...): a second
    -setvar is ignored, which is how the character got lost before. So every setting goes in a single block, last.
    horde=True: the Survival look only exists in the Vulkan shaders (the game rejects edited Direct3D ones), so the game
    runs on Vulkan; and "Full screen" becomes borderless full screen, because nothing (the banner, the tier label, the
    lightning) can be drawn over a game in exclusive full screen."""
    if horde and screen == "Full screen": screen = "Borderless window"
    flags, winmode = SCREENS.get(screen, ([], None))
    size = (1280, 720) if screen == "Window" else desktop_size() if screen in SCREENS else None
    cmd = [os.path.join(game, "OutlawsAHOM.exe"), "-netdriver", "lan", "-skipintromovies"] + flags
    if join: cmd += ["-join", join]
    setvars = []
    if winmode is not None: setvars += ["v_windowmode", winmode]
    if size: setvars += ["v_width", str(size[0]), "v_height", str(size[1])]    # the game keeps the last size it ran at
    # (a "Window" game saves 1280 x 720), and a borderless window that size is just a window: so always say the size
    if char in CHARS: setvars += ["mp_defaultChar", str(CHARS.index(char) + 1)]
    if horde: setvars += ["r_rhirenderfamily", "vulkan"]
    return cmd + (["-setvar"] + setvars if setvars else [])
def desktop_size():
    """The main screen's real resolution in pixels (not scaled by Windows' display scaling), or None."""
    try:
        import ctypes
        class DEVMODE(ctypes.Structure):
            _fields_ = [("dmDeviceName", ctypes.c_wchar * 32), ("dmSpecVersion", ctypes.c_ushort), ("dmDriverVersion", ctypes.c_ushort),
                        ("dmSize", ctypes.c_ushort), ("dmDriverExtra", ctypes.c_ushort), ("dmFields", ctypes.c_ulong),
                        ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long), ("dmDisplayOrientation", ctypes.c_ulong),
                        ("dmDisplayFixedOutput", ctypes.c_ulong), ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                        ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
                        ("dmFormName", ctypes.c_wchar * 32), ("dmLogPixels", ctypes.c_ushort), ("dmBitsPerPel", ctypes.c_ulong),
                        ("dmPelsWidth", ctypes.c_ulong), ("dmPelsHeight", ctypes.c_ulong), ("dmDisplayFlags", ctypes.c_ulong),
                        ("dmDisplayFrequency", ctypes.c_ulong)]
        dm = DEVMODE(); dm.dmSize = ctypes.sizeof(DEVMODE)
        if ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)) and dm.dmPelsWidth >= 640:   # -1: current mode
            return int(dm.dmPelsWidth), int(dm.dmPelsHeight)
    except Exception:
        pass
    return None

MAPS = [("Buckshotville", "civpow2"), ("Dry Gulch", "drygulch"), ("The Fort", "fort"), ("The Granary", "granary"),
        ("Hargrove's Pattern Repair", "hargrove"), ("Bunkers", "lilbatl"), ("Simms At Night", "mltsimms"),
        ("Orleans", "orleans"), ("Thud Tower", "thudtowr"), ("Sanctuary", "town_2p"), ("Wilderness", "wilderns")]
OTHER_TEAM = {"red": "blue", "blue": "red"}
RANDOM_MAP = "__random__"        # "Random" at the top of the standard maps: one is picked when hosting starts
GAMES = [("Survival Horde", "horde"), ("Normal bot game", "dm")]
LEVELS = ["Easiest", "Easy", "Normal", "Hard", "Nightmare", "Hell"]   # the toolkit's lv_easiest .. lv_hell
PARCH, INK, RUST = "#f1e4c3", "#3b2a1a", "#b5530f"
WIN_W, WIN_H = 580, 824
PANEL_END = 636
CLOSE_W = 160
DARK, TXT, DIM, GOLD, WARN, EDGE = "#120b07", "#f6ead0", "#cdb994", "#f2b24a", "#ff9e6e", "#7a5a32"   # on the dark picture
BTN, BTN_HOT, BTN_EDGE = "#8a3b12", "#a84a18", "#4a1e08"


# ---- settings -------------------------------------------------------------------------------------------------------
def load():
    try:
        with open(CONFIG) as f: return json.load(f)
    except Exception: return {}

def save(cfg):
    os.makedirs(DATA, exist_ok=True)
    with open(CONFIG, "w") as f: json.dump(cfg, f, indent=1)

def game_running():
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True, creationflags=NO_WINDOW).stdout.lower()
        return "outlawsahom.exe" in out
    except Exception:
        return False

def on_network_drive(path):
    """True for a program on a network share (\\\\server\\share\\... or a mapped network drive)."""
    p = os.path.abspath(path)
    if p.startswith("\\\\"): return True
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(os.path.splitdrive(p)[0] + "\\") == 4      # DRIVE_REMOTE
    except Exception:
        return False

def clean_env():
    """This app's environment without PyInstaller's own variables. A new OutlawsCompanion.exe started with them looks
    for Python in THIS copy's temporary folder (deleted when it quits): "Failed to load Python DLL ... python312.dll"."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("_MEIPASS", "_PYI_", "PYINSTALLER_"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for k in ("TCL_LIBRARY", "TK_LIBRARY"):
        if getattr(sys, "frozen", False) and k in env and getattr(sys, "_MEIPASS", "\0") in env[k]: env.pop(k)
    return env

def run_elevated(exe, args):
    ps = ("Start-Process -FilePath '%s' -ArgumentList '%s' -Verb RunAs -Wait -WindowStyle Hidden" % (exe, args.replace("'", "''")))
    return subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, creationflags=NO_WINDOW).returncode == 0

def zt_state():
    """'missing', 'off' or 'on' (the ZeroTier service)."""
    try:
        r = subprocess.run(["sc", "query", ZT_SERVICE], capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError): return "missing"
    if r.returncode != 0: return "missing"
    return "on" if "RUNNING" in (r.stdout or "") else "off"

def zt_joined():
    try:
        out = subprocess.run([ZT_CLI, "listnetworks"], capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW).stdout
        return any(ZT_NET in l and "OK" in l for l in out.splitlines())
    except Exception: return False

def zt_ip():
    ps = ("Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -like '*ZeroTier*' } | "
          "Select-Object -First 1 -ExpandProperty IPAddress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout.strip()
        return out.splitlines()[0] if out else None
    except Exception: return None

def vtext(n):
    """How a version number is shown: 13 -> v0.13, 120 -> v1.20."""
    n = int(n); return "v%d.%02d" % divmod(n, 100)

def zt_broadcast():
    """The ZeroTier subnet's own broadcast address (e.g. 10.147.17.255), or None. Windows does not always send the
    all-ones broadcast (255.255.255.255) out of the ZeroTier adapter, so discovery sends to both."""
    import ipaddress
    ps = ("Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -like '*ZeroTier*' } | "
          "Select-Object -First 1 | ForEach-Object { '{0}/{1}' -f $_.IPAddress, $_.PrefixLength }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout.strip()
        return str(ipaddress.ip_interface(out.splitlines()[0]).network.broadcast_address) if out else None
    except Exception: return None

def lan_address(a):
    """Only private (LAN / ZeroTier) IPv4 addresses are accepted: this app never joins internet multiplayer."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(a)
        return ip.version == 4 and (ip.is_private or ip.is_loopback)
    except ValueError:
        return False

def hardware():
    """(RAM in GB, logical CPUs) of this PC."""
    import ctypes
    class MS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in ("tp", "ap", "tpf", "apf", "tv", "av", "ae")]
    try:
        m = MS(); m.l = ctypes.sizeof(MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return round(m.tp / 2**30), os.cpu_count() or 0
    except Exception:
        return 0, os.cpu_count() or 0

STANDARD_KEYS = {k.lower() for _, k in MAPS}
# Each standard map's modes, from its .rcm (MODES: D deathmatch, M team deathmatch, C capture the flag, K chicken, T tag)
STD_MODES = {"civpow2": "DCKM", "drygulch": "CKMD", "fort": "CKMD", "granary": "CKMD", "hargrove": "DCKM", "lilbatl": "DKM",
             "mltsimms": "CKMD", "orleans": "CKMD", "thudtowr": "DM", "town_2p": "DKCTM", "wilderns": "CKMD"}   # Sanctuary: Team Play is added for the game (companionhost.py)
GAME_MODES = [("Deathmatch", "D", "dm"), ("Team deathmatch", "M", "team"), ("Capture the flag", "C", "ctf"),
              ("Chicken (KFC)", "K", "chicken"),
              ("TNT Only Deathmatch", "D", "dm"),      # (shown, .rcm letter, botctl --mode); TNT only: every bot Sanchez, TNT then knife or fists
              ("Melee Only Deathmatch", "D", "dm")]    # Melee only: the bots are Mary (mostly fists) and the chief (the knife), no guns or TNT
TNT_ONLY = "TNT Only Deathmatch"
MELEE_ONLY = "Melee Only Deathmatch"

def custom_maps(game, toolkit=None):
    """[(label, key, modes)] for the custom maps in the game folder: every <key>.rcm with its <key>.lab beside it.
    Name and game modes come from the .rcm (NAME: / MODES:, letters D deathmatch, M team deathmatch ...). Maps the bot
    toolkit's map scan marked as broken for bots (maps_data.json "skip") are left out."""
    import re
    skip = set()
    try:
        for m in json.load(open(os.path.join(toolkit or "", "maps_data.json"))):
            if m.get("skip"): skip.add(m["key"].lower())
    except Exception:
        pass
    out, seen = [], set()
    try: names = os.listdir(game)
    except OSError: return []
    labs = {os.path.splitext(n)[0].lower() for n in names if n.lower().endswith(".lab")}
    for n in names:
        key, ext = os.path.splitext(n)
        if ext.lower() != ".rcm" or key.lower() in STANDARD_KEYS or key.lower() in skip or key.lower() not in labs: continue
        try: text = open(os.path.join(game, n), "rb").read(65536).decode("latin-1")
        except OSError: continue
        nm = re.search(r"NAME:\s*(.+)", text); md = re.search(r"MODES:\s*(\w+)", text)
        label = (nm.group(1).strip() if nm else "") or key
        if label.lower() in seen: label = "%s (%s)" % (label, key)
        seen.add(label.lower())
        out.append((label, key, md.group(1).upper() if md else ""))
    return sorted(out, key=lambda t: t[0].lower())

GOG_ID = "1536913668"                    # Outlaws + Handful of Missions: Remaster on GOG

def find_games():
    """Folders on this PC that hold OutlawsAHOM.exe: the GOG install (registry, Galaxy's usual folders) and every
    Steam library. Steam and GOG ship the same game files, so the companion treats both the same."""
    found, cand = [], []
    try:
        import winreg
        for root, key in ((winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\GOG.com\\Games\\" + GOG_ID),
                          (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\GOG.com\\Games\\" + GOG_ID)):
            try:
                with winreg.OpenKey(root, key) as k: cand.append(winreg.QueryValueEx(k, "path")[0])
            except OSError: pass
        steam = None
        for root, key, val in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(root, key) as k: steam = winreg.QueryValueEx(k, val)[0]; break
            except OSError: pass
        if steam:
            libs = [steam]
            try:
                import re
                vdf = open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace").read()
                libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
            except OSError: pass
            cand += [os.path.join(l, "steamapps", "common", "Outlaws Remastered") for l in libs]
    except ImportError:
        pass
    for pf in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if pf: cand.append(os.path.join(pf, "GOG Galaxy", "Games", "Outlaws Plus Handful of Missions Remaster"))
    for d in "CDEFGH":
        cand.append(d + r":\GOG Games\Outlaws Plus Handful of Missions Remaster")
    for c in cand:
        c = os.path.normpath(c)
        if os.path.exists(os.path.join(c, "OutlawsAHOM.exe")) and c.lower() not in (f.lower() for f in found): found.append(c)
    return found

def writable(folder):
    try:
        t = os.path.join(folder, ".companion_write_test"); open(t, "w").close(); os.remove(t); return True
    except OSError:
        return False

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()

def safe_dest(name):
    """Mod files go straight into the game folder: a plain file name with an expected extension, nothing else."""
    return (name and name == os.path.basename(name) and "/" not in name and "\\" not in name and ".." not in name
            and name.lower().endswith(ALLOWED_EXT))


# ---- installing and removing mod files ------------------------------------------------------------------------------
def journal_read():
    try:
        with open(JOURNAL) as f: return json.load(f)
    except Exception: return None

def journal_write(j):
    os.makedirs(DATA, exist_ok=True)
    with open(JOURNAL, "w") as f: json.dump(j, f)

def install_archive(game, items):
    """The zombie skins and death sounds as ONE archive the game loads itself (olremaster<N>.lab): loose files don't
    win on every PC. Written to the journal first, so it always comes out again."""
    import modlab
    j = journal_read() or {"game": game, "files": []}
    j["archive"] = True; journal_write(j)
    return modlab.build(game, items)

def install_files(game, items):
    """items: [(dest name, downloaded temp path)]. Existing files are moved to the backup folder first; the journal is
    written before every change so a crash can always be undone."""
    j = journal_read() or {"game": game, "files": []}
    os.makedirs(BACKUP, exist_ok=True)
    for dest, src in items:
        target = os.path.join(game, dest)
        if any(e["dest"].lower() == dest.lower() for e in j["files"]):     # already ours from an unfinished session:
            shutil.copyfile(src, target); continue                         # its original is in the backup already
        had = os.path.exists(target)
        j["files"].append({"dest": dest, "had": had}); journal_write(j)
        if had: shutil.move(target, os.path.join(BACKUP, dest))
        shutil.copyfile(src, target)

def restore_files():
    """Put the game folder back as it was. True when done (or nothing to do)."""
    j = journal_read()
    if not j: return True
    game, ok = j["game"], True
    if j.get("archive"):                                  # the zombie skins / death sounds archive (modlab.py)
        try:
            import modlab
            if not modlab.remove(game): ok = False
        except Exception:
            ok = False
    for e in reversed(j["files"]):
        target = os.path.join(game, e["dest"]); b = os.path.join(BACKUP, e["dest"])
        try:
            if os.path.exists(target): os.remove(target)
            if e["had"] and os.path.exists(b): shutil.move(b, target)
        except OSError:
            ok = False
    if ok:
        try: os.remove(JOURNAL)
        except OSError: pass
    return ok


# ---- the window -----------------------------------------------------------------------------------------------------
class Txt:
    """Text drawn straight on the background picture, with a dark shadow so it reads on any part of it."""
    def __init__(self, cv, x, y, text="", tags=(), fill=None, font=None, width=0, anchor="w"):
        self.cv = cv; font = font or ("Segoe UI", 10); fill = fill or TXT
        self.sh = cv.create_text(x + 1, y + 1, text=text, fill="#000000", font=font, width=width, anchor=anchor, tags=tags)
        self.id = cv.create_text(x, y, text=text, fill=fill, font=font, width=width, anchor=anchor, tags=tags)
    def configure(self, text=None, fill=None):
        if text is not None: self.cv.itemconfigure(self.sh, text=text); self.cv.itemconfigure(self.id, text=text)
        if fill is not None: self.cv.itemconfigure(self.id, fill=fill)
    def bind(self, fn):
        for i in (self.sh, self.id): self.cv.tag_bind(i, "<Button-1>", lambda e: fn()); self.cv.tag_bind(i, "<Enter>", lambda e: self.cv.configure(cursor="hand2")); self.cv.tag_bind(i, "<Leave>", lambda e: self.cv.configure(cursor=""))


class App(tk.Tk):
    def __init__(self):
        try:                                     # its own taskbar button and icon, not Python's
            import ctypes; ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OutlawsCompanion")
        except Exception:
            pass
        super().__init__()
        self.title(APP); self.configure(bg=DARK)
        self.cfg = load(); self.busy = False; self.host_proc = None; self.session = False
        # The window can be resized and maximized: everything is drawn for WIN_W x WIN_H and zoomed to fit (rezoom).
        self.resizable(True, True); self.minsize(WIN_W, WIN_H)
        g = self.cfg.get("win_geom") or ""
        self.geometry(g if re.match(r"^\d+x\d+(\+-?\d+\+-?\d+)?$", g) else "%dx%d" % (WIN_W, WIN_H))
        if self.cfg.get("win_zoomed"):
            try: self.state("zoomed")
            except tk.TclError: pass
        st = ttk.Style(self)
        try: st.theme_use("clam")
        except tk.TclError: pass
        st.configure(".", background=DARK, foreground=TXT, font=("Segoe UI", 10))
        st.configure("TButton", background=BTN, foreground=TXT, bordercolor=BTN_EDGE, lightcolor=BTN, darkcolor=BTN_EDGE, padding=6)
        st.map("TButton", background=[("disabled", "#4a3b2c"), ("active", BTN_HOT)], foreground=[("disabled", DIM)])
        st.configure("Big.TButton", font=("Segoe UI", 13, "bold"), padding=10)
        st.configure("Close.TButton", font=("Segoe UI", 10, "bold"), background="#4a2a18", bordercolor="#2a1408",
                     lightcolor="#4a2a18", darkcolor="#2a1408")
        st.map("Close.TButton", background=[("disabled", "#3a2c20"), ("active", "#6a3a20")])
        # Boxes you can change are white; boxes the chosen game ignores are grey, arrow included.
        GREY, GREY_TXT = "#d6cfbd", "#8f8571"
        for w in ("TCombobox", "TSpinbox"):
            st.configure(w, fieldbackground="white", foreground=INK, background="#efe6d2", arrowcolor=INK)
            st.map(w, fieldbackground=[("disabled", GREY), ("readonly", "white"), ("!disabled", "white")],
                   background=[("disabled", GREY)], foreground=[("disabled", GREY_TXT), ("!disabled", INK)],
                   arrowcolor=[("disabled", GREY_TXT)], selectbackground=[("disabled", GREY), ("readonly", "white")],
                   selectforeground=[("disabled", GREY_TXT), ("readonly", INK)])
        self.option_add("*TCombobox*Listbox.background", "white"); self.option_add("*TCombobox*Listbox.foreground", INK)
        cv = self.cv = tk.Canvas(self, width=WIN_W, height=WIN_H, highlightthickness=0, bd=0, bg=DARK)
        cv.pack(fill="both", expand=True)
        self._zf, self._zoom_base, self._zoom_job = (1.0, 0.0, 0.0), None, None   # (rezoom)
        try:
            import companion_bg
            self.bg_img = tk.PhotoImage(data=companion_bg.PNG); self.bg_item = cv.create_image(0, 0, image=self.bg_img, anchor="nw")
        except Exception:
            self.bg_img = None
        try:                                     # the window's and the taskbar's icon (the exe's own icon is set at build time)
            import companion_bg
            self.icon_img = tk.PhotoImage(data=companion_bg.ICON); self.iconphoto(True, self.icon_img)
        except Exception:
            pass
        cv.create_rectangle(12, 104, WIN_W - 12, PANEL_END, outline=EDGE)                      # the panel the tabs share
        Txt(cv, WIN_W // 2, 30, "OUTLAWS COMPANION", fill=GOLD, font=("Georgia", 20, "bold"), anchor="center")
        self.folder_lbl = Txt(cv, WIN_W // 2, 58, "", fill=DIM, font=("Segoe UI", 9, "underline"), anchor="center")
        self.folder_lbl.bind(lambda: self.pick_folder())          # click the game folder to pick another one
        self.tabs = {}
        for i, (key, label) in enumerate((("join", "Join a game"), ("host", "Host a game"))):
            t = Txt(cv, 28 + i * 150, 86, label, font=("Segoe UI", 12, "bold"))
            t.bind(lambda k=key: self.show_tab(k)); self.tabs[key] = t
        self.tab_line = cv.create_line(28, 99, 128, 99, fill=GOLD, width=3)
        self.log_box = tk.Text(self, height=7, bg="#140d08", fg=TXT, insertbackground=TXT, relief="flat", bd=0, state="disabled",
                               font=("Consolas", 9), highlightthickness=1, highlightbackground=EDGE, highlightcolor=EDGE)
        cv.create_window(12, PANEL_END + 10, window=self.log_box, anchor="nw", width=WIN_W - 24, height=118)
        Txt(cv, 16, WIN_H - 28, vtext(VERSION), fill=DIM)
        self.upd_btn = ttk.Button(self, text="Update Launcher", command=self.update_launcher)
        cv.create_window(WIN_W - 12, WIN_H - 28, window=self.upd_btn, anchor="e")
        self.tab = "join"; self.have_toolkit = False
        self.build_join(); self.build_host(); self.show_tab("join")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        cv.bind("<Configure>", lambda e: self.rezoom_soon())
        self.after(200, self.startup)

    # -- a window that can be resized: everything is laid out for WIN_W x WIN_H and zoomed to the window's size
    def rezoom_soon(self):
        if self._zoom_job: self.after_cancel(self._zoom_job)
        self._zoom_job = self.after(120, self.rezoom)

    def at(self, x, y):
        """A point of the WIN_W x WIN_H layout, where it is on the zoomed canvas."""
        s, ox, oy = self._zf
        return x * s + ox, y * s + oy

    def _font_spec(self, spec):
        """A font as it was asked for (family, size, weight, slant, underline), read from its description itself, not
        from what this PC resolved it to."""
        from tkinter import font as tkfont
        out = {"family": "Segoe UI", "size": 10, "weight": "normal", "slant": "roman", "underline": 0}
        try:
            if isinstance(spec, str) and spec in tkfont.names(self):
                f = tkfont.nametofont(spec)
                return {k: f.cget(k) for k in out}
            parts = list(spec) if isinstance(spec, (tuple, list)) else list(self.tk.splitlist(spec))
            if parts: out["family"] = parts[0]
            if len(parts) > 1: out["size"] = int(parts[1])
            for w in parts[2:]:
                if w in ("bold", "normal"): out["weight"] = w
                elif w in ("italic", "roman"): out["slant"] = w
                elif w == "underline": out["underline"] = 1
        except (tk.TclError, ValueError):
            pass
        return out

    def rezoom(self):
        """Zoom the whole layout to fit the window (aspect kept, centred): positions, text sizes, widget fonts and sizes,
        the background picture. Everything is worked out again from the layout's own sizes, so nothing drifts."""
        self._zoom_job = None
        from tkinter import font as tkfont
        cv = self.cv
        W, H = max(1, cv.winfo_width()), max(1, cv.winfo_height())
        s = max(1.0, min(W / WIN_W, H / WIN_H))                     # (never smaller than it was made)
        ox, oy = (W - WIN_W * s) / 2, (H - WIN_H * s) / 2
        s0, ox0, oy0 = self._zf
        if abs(s - s0) < 0.01 and abs(ox - ox0) < 1 and abs(oy - oy0) < 1: return
        parked = self.__dict__.setdefault("_parked", {})
        for i, c in list(parked.items()): cv.coords(i, *c)          # hidden widgets back in place for the move
        parked.clear()
        if self._zoom_base is None:                                  # the layout's own sizes, read once at 1:1
            base = {}
            for i in cv.find_all():
                t = cv.type(i)
                if t == "text":
                    f = self._font_spec(cv.itemcget(i, "font"))
                    base[i] = ("text", f["family"], f["size"], f["weight"], f["slant"], f["underline"], float(cv.itemcget(i, "width") or 0))
                elif t == "window":
                    base[i] = ("window", float(cv.itemcget(i, "width") or 0), float(cv.itemcget(i, "height") or 0))
                elif t == "line":
                    base[i] = ("line", float(cv.itemcget(i, "width") or 1))
            st = ttk.Style(self)
            styles = {n: self._font_spec(st.lookup(n, "font") or "TkDefaultFont") for n in (".", "Big.TButton", "Close.TButton")}
            named = {n: self._font_spec(n) for n in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkFixedFont")}
            logf = self._font_spec(self.log_box.cget("font"))
            self._zoom_base = (base, styles, named, logf)
        base, styles, named, logf = self._zoom_base
        cv.move("all", -ox0, -oy0); cv.scale("all", 0, 0, s / s0, s / s0); cv.move("all", ox, oy)
        self._zf = (s, ox, oy)
        size = lambda n: max(6, int(round(abs(n) * s))) * (-1 if n < 0 else 1)
        for i, b in base.items():
            try:
                if b[0] == "text":
                    font = (b[1], size(b[2]), b[3]) + (("italic",) if b[4] == "italic" else ()) + (("underline",) if b[5] else ())
                    cv.itemconfigure(i, font=font, **({"width": b[6] * s} if b[6] else {}))
                elif b[0] == "window":
                    kw = {k: v * s for k, v in (("width", b[1]), ("height", b[2])) if v}
                    if kw: cv.itemconfigure(i, **kw)
                elif b[0] == "line":
                    cv.itemconfigure(i, width=max(1, b[1] * s))
            except tk.TclError:
                pass
        st = ttk.Style(self)
        for n, f in styles.items():
            st.configure(n, font=(f["family"], size(f["size"]), f["weight"]))
        for n, f in named.items():
            tkfont.nametofont(n).configure(size=size(f["size"]))
        self.log_box.configure(font=(logf["family"], size(logf["size"]), logf["weight"]))
        if getattr(self, "bg_img", None) is not None and getattr(self, "bg_item", None):
            if abs(s - 1) < 0.02:
                img = self.bg_img
            else:                                                    # PhotoImage zooms by whole numbers: s ~ a / b
                a, b = min(((-(-int(s * b * 1000) // 1000), b) for b in range(1, 5)), key=lambda ab: ab[0] / ab[1] - s)   # (covers the layout)
                img = self.bg_img.zoom(max(1, a)).subsample(b)
            self._bg_zoomed = img                                    # (kept, or Tk drops the picture)
            cv.itemconfigure(self.bg_item, image=img)
        self.visibility()

    def show_tab(self, key):
        self.tab = key
        for k, t in self.tabs.items(): t.configure(fill=GOLD if k == key else DIM)
        x = 28 + (0 if key == "join" else 150)
        self.cv.coords(self.tab_line, *self.at(x, 99), *self.at(x + (100 if key == "join" else 104), 99))
        self.visibility()

    def visibility(self):
        """Show the items of the open tab only (and on the Host tab, the setup or the form; Horde mode only for Horde)."""
        cv = self.cv
        parked = self.__dict__.setdefault("_parked", {})
        def show(tag, on):
            cv.itemconfigure(tag, state="normal" if on else "hidden")
            for i in cv.find_withtag(tag):                     # embedded widgets: also moved off the window when hidden
                if cv.type(i) != "window": continue
                if not on and i not in parked: parked[i] = cv.coords(i); cv.coords(i, -2000, -2000)
                elif on and i in parked: cv.coords(i, *parked.pop(i))
        horde = dict(GAMES).get(self.h_game.get()) == "horde"
        show("join", self.tab == "join")
        show("host_setup", self.tab == "host" and not self.have_toolkit)
        show("host_form", self.tab == "host" and self.have_toolkit)
        show("hmode", self.tab == "host" and self.have_toolkit and horde)
        show("normal", self.tab == "host" and self.have_toolkit and not horde)
        show("teamsplit", self.tab == "host" and self.have_toolkit and not horde and self.team_game())   # Red/Blue bots: team games only

    def put(self, widget, x, y, tags, **kw):
        """A widget on the background, at x, y (its left middle)."""
        return self.cv.create_window(x, y, window=widget, anchor=kw.pop("anchor", "w"), tags=tags, **kw)

    # -- helpers
    def note(self, msg):
        """A line in companion_log.txt only (not in the window)."""
        try:
            os.makedirs(DATA, exist_ok=True)
            with open(os.path.join(DATA, "companion_log.txt"), "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + vtext(VERSION) + " " + msg.rstrip() + "\n")
        except OSError:
            pass

    def say(self, msg):
        try:
            os.makedirs(DATA, exist_ok=True)
            lf = os.path.join(DATA, "companion_log.txt")
            if os.path.exists(lf) and os.path.getsize(lf) > 512 * 1024: os.replace(lf, lf + ".old")
            with open(lf, "a", encoding="utf-8") as f: f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + vtext(VERSION) + " " + msg.rstrip() + "\n")
        except OSError:
            pass
        def w():
            self.log_box.configure(state="normal"); self.log_box.insert("end", msg.rstrip() + "\n")
            self.log_box.see("end"); self.log_box.configure(state="disabled")
        self.after(0, w)

    def game_dir(self): return self.cfg.get("game", "")
    def toolkit(self): return os.path.join(self.game_dir(), "botctl")

    def startup(self):
        if not os.path.exists(os.path.join(self.game_dir(), "OutlawsAHOM.exe")):
            games = find_games()
            if len(games) == 1 or (games and messagebox.askyesno(APP, "Found Outlaws in more than one place:\n\n%s\n\nUse the first one?\n"
                                                                  "(No: pick the folder yourself.)" % "\n".join(games))):
                self.cfg["game"] = games[0]; save(self.cfg); self.say("Found your game: %s" % games[0])
            else:
                self.pick_folder(first=True)
        self.folder_lbl.configure(text=self.game_dir())
        self.map_choices()
        if not game_running():          # the Survival look left over from a session that was cut short (or stuck by an older version)
            threading.Thread(target=self.look_check, daemon=True).start()
        if journal_read():
            if game_running():
                self.say("The game is running with mods from an earlier session; they come out when it closes.")
                self.session = True
                threading.Thread(target=self.wait_and_restore, args=(False,), daemon=True).start()
            elif restore_files():
                self.say("Put your game folder back as it was after the last session.")
            else:
                self.say("Could not fully restore some files; close the game and restart this app.")
        self.refresh_host()
        self.note("started %s from %s" % (vtext(VERSION), sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)))
        threading.Thread(target=self.desktop_shortcut, daemon=True).start()
        threading.Thread(target=self.check_update_quiet, daemon=True).start()
        if zt_state() != "missing":
            threading.Thread(target=self.ensure_zerotier, daemon=True).start()

    def desktop_shortcut(self):
        """'Outlaws Companion' on the desktop, with this app's icon, always starting THIS copy (an old shortcut to an
        older download is pointed here instead, so an update is never 'lost' by opening another copy)."""
        if not getattr(sys, "frozen", False): return
        exe = sys.executable.replace("'", "''")
        ps = ("$d=[Environment]::GetFolderPath('Desktop'); $l=Join-Path $d 'Outlaws Companion.lnk'; $s=(New-Object -ComObject WScript.Shell).CreateShortcut($l); "
              "if ($s.TargetPath -ne '%s' -or $s.IconLocation -ne '%s,0') { $s.TargetPath='%s'; $s.WorkingDirectory=(Split-Path '%s'); "
              "$s.IconLocation='%s,0'; $s.Description='Outlaws Companion'; $s.Save(); 'updated' }" % (exe, exe, exe, exe, exe))
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                               timeout=30, creationflags=NO_WINDOW)
            if "updated" in (r.stdout or ""):
                self.note("desktop shortcut set to %s" % sys.executable)
                subprocess.run(["ie4uinit.exe", "-show"], capture_output=True, timeout=30, creationflags=NO_WINDOW)   # refresh icons
        except Exception:
            pass

    def pick_folder(self, first=False):
        if first:
            messagebox.showinfo(APP, "Welcome! First, show me where Outlaws Remastered is installed:\n"
                                "the folder that contains OutlawsAHOM.exe.\n(Steam: right-click the game > Manage > Browse local files.\n"
                                "GOG Galaxy: the game's settings button > Manage installation > Show folder.)")
        while True:
            d = filedialog.askdirectory(title="Pick your Outlaws Remastered folder (the one with OutlawsAHOM.exe)")
            if not d:
                if first and not os.path.exists(os.path.join(self.game_dir(), "OutlawsAHOM.exe")): self.destroy(); sys.exit(0)
                return
            d = os.path.normpath(d)
            if os.path.exists(os.path.join(d, "OutlawsAHOM.exe")):
                self.cfg["game"] = d; save(self.cfg); self.folder_lbl.configure(text=d); self.refresh_host(); self.map_choices(); return
            messagebox.showerror(APP, "OutlawsAHOM.exe is not in that folder. Try the folder Steam's 'Browse local files' "
                                 "or GOG Galaxy's 'Show folder' opens.")

    def on_close(self):
        if self.session:
            if game_running():
                messagebox.showinfo(APP, "Your game is still running. Close it first; this app then puts your game folder back.")
                return
            restore_files()
        if self.host_proc and self.host_proc.poll() is None:
            if not messagebox.askyesno(APP, "You are hosting a game. Stop it and quit?"): return
            self.stop_host()
        try:                                                         # the window's size and place, for next time
            zoomed = self.state() == "zoomed"
            self.cfg["win_zoomed"] = zoomed
            if not zoomed: self.cfg["win_geom"] = self.geometry()
            save(self.cfg)
        except Exception: pass
        self.destroy()

    def ensure_writable(self, folder):
        """Mods (and the hosting toolkit) go into the game folder. GOG often installs under Program Files, which Windows
        protects: offer to let this Windows user change that one folder (one Windows prompt). Worker thread."""
        if writable(folder): return True
        if not self.ask("askyesno", APP, "Windows protects your game folder:\n%s\n\nThe game's mods have to be copied into it "
                        "(and taken out again afterwards). Allow your Windows user to change this folder? Windows will ask you once." % folder):
            return False
        run_elevated("icacls", '"%s" /grant *S-1-5-32-545:(OI)(CI)M /T /Q' % folder)
        if writable(folder): self.say("Your game folder can now take mods."); return True
        self.say("Could not unlock the game folder. Run the companion as administrator, or move the game out of Program Files.")
        return False

    def discover(self, wait=6.0):
        """Hosts on the ZeroTier network: [(ip, info)]. A UDP broadcast, sent out of the ZeroTier adapter only."""
        import socket
        found = {}
        ip = zt_ip()
        if not ip: return []
        targets = ["255.255.255.255"] + [b for b in [zt_broadcast()] if b]
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind((ip, 0)); s.settimeout(0.5)
            end = time.time() + wait; last = 0
            while time.time() < end:
                if time.time() - last > 1:
                    last = time.time()
                    for t in targets:
                        try: s.sendto(b"OUTLAWS?", (t, DISCOVER_PORT))
                        except OSError: pass
                try: data, a = s.recvfrom(4096)
                except socket.timeout: continue
                try: info = json.loads(data.decode())
                except ValueError: continue
                if lan_address(a[0]) and a[0] != ip: found[a[0]] = info
        finally:
            s.close()
        return sorted(found.items())

    def choose_host(self, hosts):
        """One host: it. Several: ask which (called from a worker thread)."""
        if len(hosts) == 1: return hosts[0][0]
        ev, ans = threading.Event(), []
        def ask():
            win = tk.Toplevel(self); win.title("Which game?"); win.configure(bg=DARK); win.grab_set()
            ttk.Label(win, text="Several games found. Pick one:").pack(padx=14, pady=8)
            for ip, info in hosts:
                ttk.Button(win, text="%s: %s on %s" % (info.get("name", ip), info.get("mode") or "game", info.get("map") or "?"),
                           command=lambda ip=ip: (ans.append(ip), win.destroy())).pack(fill="x", padx=14, pady=3)
            win.protocol("WM_DELETE_WINDOW", win.destroy)
            win.wait_window(); ev.set()
        self.after(0, ask); ev.wait()
        return ans[0] if ans else None

    def ensure_zerotier(self):
        """Installed, running, joined to the games' network. Runs in a worker thread. True when the network is usable."""
        try:
            st = zt_state()
            if st == "missing":
                ev, ans = threading.Event(), []
                def ask():
                    ans.append(messagebox.askyesno(APP, "Must install ZeroTier to join LAN games. Do you want to continue?\n\n(ZeroTier is downloaded and installed, then this PC joins the games' network. Windows will ask you to allow it.)"))
                    ev.set()
                self.after(0, ask); ev.wait()
                if not ans[0]: self.say("ZeroTier is needed to reach the game."); return False
                self.say("Downloading ZeroTier...")
                msi = os.path.join(tempfile.gettempdir(), "ZeroTierOne.msi")
                req = urllib.request.Request(ZT_MSI, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=120) as r, open(msi, "wb") as f: shutil.copyfileobj(r, f)
                self.say("Installing ZeroTier (allow the Windows prompt)...")
                run_elevated("msiexec", '/i "%s" /qn /norestart' % msi)
                for _ in range(40):
                    if zt_state() != "missing": break
                    time.sleep(1)
                if zt_state() == "missing":
                    webbrowser.open("https://www.zerotier.com/download")
                    self.say("Could not install it automatically; I opened zerotier.com/download. Install it, then try again."); return False
            if zt_state() != "on":
                self.say("Turning ZeroTier on (allow the Windows prompt)...")
                run_elevated("cmd", "/c sc start " + ZT_SERVICE)
                for _ in range(20):
                    if zt_state() == "on": break
                    time.sleep(1)
                if zt_state() != "on": self.say("Could not turn ZeroTier on."); return False
            if not zt_joined():
                self.say("Joining the games network...")
                run_elevated("cmd", '/c ""%s" join %s"' % (ZT_CLI, ZT_NET))
            for i in range(30):
                if zt_ip(): break
                if i == 3: self.say("Waiting for the network to give this PC an address. If it stays stuck, ask the network owner to authorise you.")
                time.sleep(2)
            ip = zt_ip()
            self.say("ZeroTier is on; your address is %s" % ip if ip else "ZeroTier is on, but this PC isn't authorised on the network yet.")
            return bool(ip)
        except Exception as e:
            self.say("ZeroTier: %s" % e); return False

    # -- updates: the newest OutlawsCompanion.exe is kept in the repo's companion/ folder next to version.txt ("<number> <sha256>")
    def gh_commit(self):
        """The commit main is at right now (one small API call), or None. Files are then read from raw links pinned to
        that commit: those never serve an old copy, unlike the branch's raw links (cached about five minutes)."""
        req = urllib.request.Request("https://api.github.com/repos/%s/commits/%s" % (REPO, BRANCH),
                                     headers={"User-Agent": "OutlawsCompanion", "Accept": "application/vnd.github.sha"})
        tok = self.cfg.get("github_token")
        if tok: req.add_header("Authorization", "token " + tok)
        try:
            sha = urllib.request.urlopen(req, timeout=15).read().decode().strip()
            return sha if len(sha) == 40 else None
        except Exception:
            return None

    def gh(self, name, timeout=30, root=False, commit=None):
        """A file from the GitHub repo, from the given commit (or the current one). Without the API (its limit is 60
        calls an hour per internet connection) it falls back to the branch's raw link, which can be a few minutes old."""
        path = name if root else "companion/" + name
        tok = self.cfg.get("github_token")
        ref = commit or self.gh_commit()
        url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, ref or BRANCH, path)
        req = urllib.request.Request(url if ref else url + "?t=%d" % time.time(),
                                     headers={"User-Agent": "OutlawsCompanion", "Cache-Control": "no-cache"})
        if tok: req.add_header("Authorization", "token " + tok)
        return urllib.request.urlopen(req, timeout=timeout)

    def check_update_quiet(self):
        """At start: light up the button when GitHub has a newer build (nothing is downloaded)."""
        try:                                                # the updater's last word since this copy last started
            with open(os.path.join(DATA, "update_log.txt"), encoding="utf-8", errors="replace") as f: last = f.read().strip().splitlines()[-1:]
            if last and ("could not replace" in last[0] or "is gone" in last[0]) and not self.cfg.get("update_fail_seen") == last[0]:
                self.cfg["update_fail_seen"] = last[0]; save(self.cfg)
                self.after(0, self.update_failed_prompt)
        except (OSError, IndexError):
            pass
        if getattr(sys, "frozen", False) and os.path.exists(sys.executable + ".new"):
            try: os.remove(sys.executable + ".new")
            except OSError: pass
            self.say("The last update couldn't replace this app's file (antivirus often holds a new download while it "
                     "scans it). Press Update Launcher again; if it keeps failing, download the app from GitHub instead.")
        try:
            have = self.cfg.get("toolkit_stamp")
            if have and os.path.exists(os.path.join(self.toolkit(), "botctl.py")):
                new = self.gh("toolkit/version.txt", 15, root=True).read().decode().split()[0]
                if new > have:                              # fetched by itself (only when this PC has hosted before)
                    self.say("A newer hosting toolkit is on GitHub: getting it now...")
                    self.after(0, self.auto_refresh_toolkit)
        except Exception:
            pass
        try:
            num = int(self.gh("version.txt", 15).read().decode().split()[0])
            self.seen_version = max(getattr(self, "seen_version", 0), num)
            if num > VERSION:
                self.after(0, lambda: self.upd_btn.configure(text="Update available (%s)" % vtext(num)))
                self.say("A new launcher version (%s) is on GitHub: press Update available to install it." % vtext(num))
        except Exception:
            pass

    def update_failed_prompt(self):
        if messagebox.askyesno(APP, "The last update couldn't be installed: Windows wouldn't let the companion replace "
                               "its own file. That's usually antivirus (check Windows Security > Protection history) or the "
                               "app running from a network drive.\n\nOpen the download page to get the newest version?"):
            webbrowser.open("https://github.com/%s/raw/%s/companion/OutlawsCompanion.exe" % (REPO, BRANCH))

    def update_launcher(self):
        if self.busy: return
        self.upd_btn.configure(state="disabled", text="Checking...")
        threading.Thread(target=self.update_work, daemon=True).start()

    def update_work(self):
        try:
            commit = self.gh_commit()                  # version.txt and the exe from the same snapshot of the repo
            try:
                num, sha = self.gh("version.txt", commit=commit).read().decode().split()[:2]
            except Exception as e:
                self.say("Could not reach the update source (%s). If the repository is still private, updates start working once it is public." % e); return
            seen = getattr(self, "seen_version", 0)
            if int(num) < seen:
                self.say("GitHub is still handing out %s although %s is out (its copies are a few minutes old). "
                         "Try Update Launcher again in about 5 minutes." % (vtext(num), vtext(seen))); return
            if int(num) <= VERSION:
                self.say("Your launcher is up to date (%s)." % vtext(VERSION)); return
            if game_running() or self.session:
                self.say("%s is available. Close the game first, then press Update Launcher." % vtext(num)); return
            if not getattr(sys, "frozen", False):
                self.update_source(num); return
            self.remove_old_copies()
            self.say("Downloading %s..." % vtext(num))
            exe = sys.executable
            if on_network_drive(exe):
                # Run from a network share (a NAS): Windows and the share keep the exe locked long after it closes, so
                # the file can't be swapped. Install the new version on this PC instead and run it from there.
                local = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Programs", "OutlawsCompanion")
                os.makedirs(local, exist_ok=True)
                exe = os.path.join(local, "OutlawsCompanion.exe")
                self.cfg["replace_old"] = sys.executable; self.cfg.pop("replace_old_tries", None); save(self.cfg)   # updated later
                self.say("This copy runs from a network drive; the new version is installed on this PC (%s)." % local)
            new = exe + ".new"
            with self.gh("OutlawsCompanion.exe", 120, commit=commit) as r, open(new, "wb") as f: shutil.copyfileobj(r, f)
            if sha256(new) != sha.lower():
                os.remove(new); self.say("The download was damaged; nothing changed. Try again."); return
            bat = os.path.join(tempfile.gettempdir(), "outlaws_companion_update.cmd")
            log = os.path.join(DATA, "update_log.txt")
            # 1. wait until this copy (and the exe's own unpacker process) has really exited, 2. swap the files, retrying
            # for up to 3 minutes (antivirus often holds a new exe while it scans it), 3. start the app through Explorer
            # (nothing of this copy is inherited). Every step goes to update_log.txt so a failed update can be looked at.
            lines = ['@echo off', 'set PYINSTALLER_RESET_ENVIRONMENT=1', 'set n=0',
                     'echo %%date%% %%time%% update to %s: waiting for the old copy to close>> "%s"' % (vtext(num), log),
                     ':wait']
            for pid in {os.getpid(), os.getppid()}:
                lines.append('tasklist /FI "PID eq %d" /NH 2>nul | find " %d " >nul && (ping -n 2 127.0.0.1 >nul & goto wait)' % (pid, pid))
            lines += [':again',
                      'if not exist "%s" (echo %%date%% %%time%% the downloaded file is gone - antivirus may have removed it>> "%s" & goto failed)' % (new, log),
                      'move /y "%s" "%s" >nul 2>&1 && goto done' % (new, exe),
                      'set /a n+=1', 'if %n% geq 90 goto failed', 'ping -n 3 127.0.0.1 >nul', 'goto again',
                      ':done', 'echo %%date%% %%time%% updated>> "%s"' % log, 'explorer.exe "%s"' % exe, 'goto end',
                      ':failed', 'echo %%date%% %%time%% could not replace the app file>> "%s"' % log,
                      'explorer.exe "%s"' % exe, ':end', 'del "%~f0"']
            with open(bat, "w") as f: f.write("\r\n".join(lines) + "\r\n")
            self.say("Updating; the app restarts by itself.")
            subprocess.Popen(["cmd", "/c", bat], creationflags=NO_WINDOW, env=clean_env())
            self.after(500, self.destroy)
        except Exception as e:
            self.say("Update failed: %s" % e)
        finally:
            self.after(0, lambda: self.upd_btn.configure(state="normal", text="Update Launcher"))

    def remove_old_copies(self):
        """Before an update: delete older copies of this app (OutlawsCompanion*.exe in its own folder, Downloads,
        Desktop and Documents) and leftovers of earlier updates, so only one copy is left to open afterwards. Only
        files that really are this app are removed, never the copy that is running, and never one that is in use."""
        if not getattr(sys, "frozen", False): return
        me = os.path.normcase(os.path.abspath(sys.executable))
        home = os.path.expanduser("~")
        dirs = {os.path.dirname(sys.executable)} | {os.path.join(home, d) for d in ("Downloads", "Desktop", "Documents")}
        removed = []
        for d in dirs:
            try: names = os.listdir(d)
            except OSError: continue
            for n in names:
                low = n.lower(); p = os.path.join(d, n)
                if os.path.normcase(os.path.abspath(p)) == me: continue
                leftover = low.startswith("outlawscompanion") and low.endswith((".exe.new", ".exe.bak", ".exe.old"))
                copy = low.startswith("outlawscompanion") and low.endswith(".exe")
                if not (leftover or copy) or not os.path.isfile(p): continue
                try:
                    if copy:                                          # only this app (its built-in background picture)
                        with open(p, "rb") as f: data = f.read()
                        if b"companion_bg" not in data: continue
                    os.remove(p); removed.append(p)
                except OSError:
                    pass                                              # in use (open) or not ours to delete: left alone
        for p in removed: self.note("removed an old copy: %s" % p)
        if removed: self.say("Removed %d old copy/copies of the companion." % len(removed))

    def update_source(self, num):
        """Running companion.py itself: fetch the new companion.py from GitHub, rebuild the exe (build_companion.cmd)
        when this folder has it, and restart."""
        me = os.path.abspath(__file__); new = me + ".new"
        self.say("Downloading %s (source)..." % vtext(num))
        with self.gh("companion.py", 60, root=True) as r, open(new, "wb") as f: shutil.copyfileobj(r, f)
        try:
            compile(open(new, encoding="utf-8").read(), new, "exec")
        except SyntaxError as e:
            os.remove(new); self.say("The downloaded companion.py is broken (%s); nothing changed." % e); return
        shutil.copyfile(me, me + ".bak"); os.replace(new, me)
        try:                                                   # the background picture lives beside it
            for extra in ("companion_bg.py", "input_agent.js", "teamcolors.py", "darkmode.py", "spvpatch.py", "dxpatch.py", "modlab.py", "teamcolors_game.zip"):
                with self.gh(extra, 60, root=True) as r: data = r.read()
                with open(os.path.join(os.path.dirname(me), extra), "wb") as f: f.write(data)
        except Exception: pass
        build = os.path.join(os.path.dirname(me), "build_companion.cmd")
        if os.path.exists(build):
            self.say("Updated companion.py; rebuilding OutlawsCompanion.exe in a new window...")
            subprocess.Popen(["cmd", "/c", "start", "Rebuild Outlaws Companion", "cmd", "/c", build], cwd=os.path.dirname(me))
        else:
            self.say("Updated companion.py; restarting.")
        subprocess.Popen([sys.executable, me], cwd=os.path.dirname(me))
        self.after(500, self.destroy)

    # -- JOIN
    def build_join(self):
        cv, T = self.cv, ("join",)
        Txt(cv, 36, 140, "Character", T)
        self.char = ttk.Combobox(self, values=["(pick in game)"] + CHARS, state="readonly", width=22)
        self.char.set(self.cfg.get("char", "(pick in game)")); self.put(self.char, 210, 140, T)
        Txt(cv, 36, 178, "Screen", T)
        self.screen = ttk.Combobox(self, values=list(SCREENS), state="readonly", width=22)
        self.screen.set(self.cfg.get("screen") if self.cfg.get("screen") in SCREENS else "Full screen"); self.put(self.screen, 210, 178, T)
        self.join_btn = ttk.Button(self, text="JOIN GAME", style="Big.TButton", command=self.join)
        self.put(self.join_btn, 36, 238, T, width=WIN_W - 72 - CLOSE_W - 10)
        self.close_btns = [ttk.Button(self, text="Close All Windows", style="Close.TButton", command=self.close_all)]
        self.put(self.close_btns[0], WIN_W - 36, 238, T, anchor="e", width=CLOSE_W, height=48)
        Txt(cv, 36, 280, (
            "Any mods the game needs are downloaded and installed for you, and removed again when you close the game. "
            "Your game joins the host's game by itself, as the character picked above. "
            "In a Survival Horde game you are put on the Survivors team for you."), T, fill=DIM, width=WIN_W - 72, anchor="nw")

    def join(self):
        if getattr(self, "joining", False): self.stop_join(); return
        if self.busy: return
        if game_running():
            messagebox.showinfo(APP, "Outlaws is already running. Close it first."); return
        self.cfg.update(char=self.char.get(), screen=self.screen.get()); save(self.cfg)
        self.busy = True; self.joining = True; self.join_stop = False; self.join_game = None
        self.join_btn.configure(text="STOP", state="normal")
        threading.Thread(target=self.join_work, daemon=True).start()

    def stop_join(self):
        """The Join button while joining (STOP): stop where it is. A game already started is closed (your game folder is
        then put back as it was); a join still finding the host or getting mods stops at the next step."""
        self.join_stop = True
        self.join_btn.configure(text="Stopping...", state="disabled")
        self.say("Stopping...")
        g = getattr(self, "join_game", None)
        try:
            if g and g.poll() is None:
                subprocess.run(["taskkill", "/PID", str(g.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        except OSError: pass

    def stopped(self):
        """True once STOP was pressed on a join."""
        return bool(getattr(self, "join_stop", False))

    def ask(self, kind, title, msg):
        """A message box from a worker thread."""
        ev, ans = threading.Event(), []
        self.after(0, lambda: (ans.append(getattr(messagebox, kind)(title, msg)), ev.set())); ev.wait()
        return ans[0]

    def find_host(self):
        """The host's address, None to play without mods, False to cancel. Broadcasts a few times, then tries the host
        this PC joined last time directly (works even where the network drops broadcasts)."""
        while True:
            for attempt in range(2):
                self.say("Looking for games on the network..." if attempt == 0 else "Still looking...")
                hosts = self.discover()
                if hosts: return self.choose_host(hosts)
            last = self.cfg.get("last_host")
            if last and lan_address(last):
                try:
                    json.load(self.fetch(last, "/manifest.json", 60))
                    self.say("Found the game at %s (the host you joined last time)." % last); return last
                except Exception: pass
            r = self.ask("askyesnocancel", APP, "No hosted game was found on the network, so the game's mods (zombie skins, "
                         "maps...) can't be installed.\n\nYes: look again (make sure your friend's game is up)\n"
                         "No: open the game anyway, without mods\nCancel: don't open the game")
            if r is None: return False
            if r is False: return None

    def fetch(self, addr, path, timeout=8):
        return urllib.request.urlopen("http://%s:%d%s" % (addr, PORT, path), timeout=timeout)

    def join_work(self):
        try:
            if not self.ensure_zerotier():
                self.say("Joining cancelled: this PC isn't on the games' ZeroTier network yet."); return
            addr = self.find_host()
            if addr is False or self.stopped(): self.say("Joining cancelled."); return
            if not addr:
                self.say("No hosted game found, so no mods were installed. Opening the game anyway: use LAN > Join Game.")
            game = self.game_dir(); items = []; archived = []; tmp = tempfile.mkdtemp(prefix="outlaws_comp_"); mode = ""; self.music_path = None; man = None
            try:
                if not addr: raise OSError("no host")
                for i in range(3):                       # the host hashes its mod files when a game starts: give it time
                    try: man = json.load(self.fetch(addr, "/manifest.json", 60)); break
                    except Exception:
                        if i == 2: raise
                        self.say("The host is still getting its mods ready; asking again..."); time.sleep(5)
                self.cfg["last_host"] = addr; save(self.cfg)
                self.say("Connected to %s: %s on %s" % (man.get("name", "host"), man.get("mode") or "game", man.get("map") or "?"))
                mode = man.get("mode") or ""
                self.say("Host's mods: %s%s" % (", ".join(m.get("title", "?") for m in man.get("mods", [])) or "none",
                                                 "; music" if man.get("music") else ""))
                if mode in ("horde", "dark") and not any(m.get("id") == "zombie_skins" for m in man.get("mods", [])):
                    self.say("This Horde host has no zombie skins, death sounds or music to share: their hosting toolkit is out "
                             "of date. They should press 'Refresh hosting toolkit' on their Host tab, then host again.")
                music = man.get("music") if mode in ("horde", "dark") else None
                if music:
                    try: self.music_path = self.get_music(addr, music)
                    except Exception as e: self.say("Couldn't get the Horde music (%s)." % e)
                archived = []                             # zombie skins / death sounds: one archive, not loose files
                for mod in man.get("mods", []):
                    todo = []
                    into_archive = mod.get("id") == "zombie_skins"
                    for fl in mod.get("files", []):
                        dest = fl.get("dest", "")
                        if not safe_dest(dest): self.say("Skipped an unexpected file name: %r" % dest); continue
                        cur = os.path.join(game, dest)
                        if not into_archive and os.path.exists(cur) and sha256(cur) == fl["sha256"]: continue      # already there
                        todo.append(fl)
                    if not todo: self.say("%s: already in place" % mod["title"]); continue
                    new = sum(1 for fl in todo if not self.cached_mod(fl["sha256"]))
                    if new: self.say("Downloading %s (%d file(s))..." % (mod["title"], new))
                    cached = 0
                    for fl in todo:
                        if self.stopped(): raise RuntimeError("you pressed Stop")
                        p = self.cached_mod(fl["sha256"])
                        if p: cached += 1
                        else:
                            p = os.path.join(tmp, fl["dest"])
                            with self.fetch(addr, fl["url"], 60) as r, open(p, "wb") as out: shutil.copyfileobj(r, out)
                            if sha256(p) != fl["sha256"]: raise RuntimeError("download of %s was damaged" % fl["dest"])
                            self.keep_mod(p, fl["sha256"])
                        (archived if into_archive else items).append((fl["dest"], p))
                    if cached: self.say("%s: %d of %d file(s) were already on this PC." % (mod["title"], cached, len(todo)))
                if items or archived:
                    if not self.ensure_writable(game): raise RuntimeError("the game folder is protected, so the mods can't be installed")
                if archived:
                    path = install_archive(game, archived); self.session = True
                    self.say("Packed %d zombie file(s) into %s; it is removed when the game closes." % (len(archived), os.path.basename(path)))
                if items:
                    install_files(game, items); self.session = True
                    self.say("Installed %d mod file(s); they are removed when the game closes." % len(items))
            except Exception as e:
                if items or archived or journal_read(): restore_files()
                if isinstance(e, RuntimeError):
                    self.say("Stopped: %s" % e); return
                if addr: self.say("Could not ask the host for mods (%s); joining without." % e)
                items = []
            if addr and mode in ("horde", "dark"):
                zombies = any(m.get("id") == "zombie_skins" for m in man.get("mods", [])) if man else False
                self.look_on(game, zombies, dark="flash" if mode == "dark" else True)
            cmd = game_cmd(game, self.screen.get(), self.char.get(), addr, horde=bool(addr) and mode in ("horde", "dark"))
            if self.stopped():
                if self.session or journal_read(): restore_files()
                self.look_off(); self.session = False; self.say("Joining cancelled."); return
            try: me = subprocess.Popen(cmd, cwd=game); self.join_game = me
            except OSError as e:
                self.say("Could not start the game: %s" % e); restore_files(); self.session = False; return
            threading.Thread(target=self.check_game_folder, args=(game,), daemon=True).start()
            if addr and mode in ("horde", "dark"):
                self.say("Game starting: it joins the host's Survival Horde game and the Survivors team by itself.")
                threading.Thread(target=self.auto_team, args=(me.pid, "red", True), daemon=True).start()
                if self.music_path: threading.Thread(target=self.play_music, args=(self.music_path,), daemon=True).start()
                threading.Thread(target=self.hud_loop, args=(addr,), daemon=True).start()
            elif addr:
                self.say("Game starting: it joins the host's game by itself.")
            else:
                self.say("Game starting. Choose LAN > Join Game and pick the host's game from the list.")
            self.wait_and_restore(True)
            shutil.rmtree(tmp, ignore_errors=True)
        finally:
            self.busy = False
            self.joining = False; self.join_stop = False; self.join_game = None
            self.after(0, lambda: self.join_btn.configure(text="JOIN GAME", state="normal"))

    def cached_mod(self, sha):
        p = os.path.join(DATA, "cache", sha)
        return p if os.path.exists(p) else None

    def keep_mod(self, path, sha):
        """Keep a downloaded mod file (by its hash) for the next join. Files not used for 60 days are dropped."""
        try:
            d = os.path.join(DATA, "cache"); os.makedirs(d, exist_ok=True)
            shutil.copyfile(path, os.path.join(d, sha))
            for n in os.listdir(d):
                q = os.path.join(d, n)
                if time.time() - os.path.getmtime(q) > 60 * 86400: os.remove(q)
        except OSError:
            pass

    def look_modules(self):
        """The bot toolkit's Survival look (teamcolors.py with darkmode, spvpatch, dxpatch), built into this app. Their
        backups and log go to this app's folder, never next to the game."""
        import teamcolors, darkmode
        d = os.path.join(DATA, "look"); os.makedirs(d, exist_ok=True)
        teamcolors.BACKUP = os.path.join(d, "teamcolors_orig.zip"); teamcolors.LOG = os.path.join(d, "teamcolors_log.txt")
        darkmode.BACKUP = os.path.join(d, "darkmode_orig.zip")
        return teamcolors

    def look_on(self, game, zombies, dark=True):
        """Survivors purple, the Horde green, the team names and the darker world: edits in Common.kpf, made before
        the game starts and taken out again when it closes (what the host's own game gets)."""
        kpf = os.path.join(game, "Common.kpf")
        if not os.path.exists(kpf) or not self.ensure_writable(game): return
        try:
            tc = self.look_modules()
            self.cfg["look_on"] = True; save(self.cfg)
            self.say("Putting in the Survival look (Survivors purple, Horde green, a darker world)...")
            if not tc.set_theme("on", kpf=kpf, quiet=True, neutral="horde" if zombies else False, dark=dark):
                self.say("Couldn't put in the Survival look; the game starts with the normal colours.")
        except Exception as e:
            self.say("Couldn't put in the Survival look (%s)." % e)

    def look_check(self):
        """At start, with the game closed: put the game's colours back if any of the Survival look is still in Common.kpf.
        Before v0.24 the purple Survivors' tint could stay behind after a Horde game (red purple, blue untinted)."""
        try:
            kpf = os.path.join(self.game_dir(), "Common.kpf")
            if self.cfg.get("look_on") or (os.path.exists(kpf) and self.look_modules().dirty(kpf)):
                self.look_off(force=True)
        except Exception as e:
            self.note("look check: %s" % e)

    def look_off(self, force=False):
        if not self.cfg.get("look_on") and not force: return
        kpf = os.path.join(self.game_dir(), "Common.kpf")
        try:
            tc = self.look_modules()
            for _ in range(20):
                if tc.set_theme("off", kpf=kpf, quiet=True): break
                time.sleep(3)
            else:
                self.say("The game's colours couldn't be put back yet; they will be the next time this app starts."); return
            self.cfg["look_on"] = False; save(self.cfg)
        except Exception as e:
            self.say("Couldn't put the game's colours back (%s)." % e)

    # -- on-screen overlays in a joined Horde game: what the host's own screen shows (tierhud, announce, welcome)
    def overlay(self, bg, alpha=0.9, transparent=False):
        w = tk.Toplevel(self); w.withdraw(); w.overrideredirect(True); w.attributes("-topmost", True); w.configure(bg=bg)
        try:
            if transparent: w.attributes("-transparentcolor", bg)
            w.attributes("-alpha", alpha)
        except tk.TclError: pass
        return w

    def show_no_focus(self, w):
        """Show an overlay without taking the focus from the game; mouse clicks go straight through it."""
        try:
            import ctypes
            u = ctypes.windll.user32; fg = u.GetForegroundWindow()
            w.update_idletasks()
            hwnd = u.GetParent(w.winfo_id()) or w.winfo_id()
            u.SetWindowLongW(hwnd, -20, u.GetWindowLongW(hwnd, -20) | 0x08000000 | 0x80 | 0x20 | 0x80000)
            u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x40); u.ShowWindow(hwnd, 4)
            if fg and u.GetForegroundWindow() != fg: u.SetForegroundWindow(fg)
        except Exception:
            w.deiconify()

    def thunder(self, volume=1.0):
        """One of the game's own thunder sounds (read from its sound archive once), as the host's banner plays it."""
        try:
            import winsound, struct, random
            want = random.choice(["thunder1.wav", "thunder2.wav"])
            cache = os.path.join(DATA, "sounds", want)
            if not os.path.exists(cache):
                for lab in ("olpatch5.lab", "olsfx.lab", "olpatch6.lab", "olpatch4.lab", "olpatch3.lab"):
                    path = os.path.join(self.game_dir(), lab)
                    if not os.path.exists(path): continue
                    with open(path, "rb") as f:
                        head = f.read(16); n, = struct.unpack_from("<I", head, 8); ent = f.read(16 * n)
                        names = f.read(struct.unpack_from("<I", head, 12)[0])
                        for i in range(n):
                            no, off, size, _t = struct.unpack_from("<IIII", ent, 16 * i)
                            if names[no:names.index(b"\0", no)].decode("latin1").lower() == want:
                                f.seek(off); os.makedirs(os.path.dirname(cache), exist_ok=True)
                                with open(cache, "wb") as out: out.write(f.read(size))
                                break
                    if os.path.exists(cache): break
            play = cache
            if os.path.exists(cache) and volume < 0.99:            # a quieter copy (a distant storm)
                quiet = cache[:-4] + "_far.wav"
                if not os.path.exists(quiet):
                    import wave, array
                    with wave.open(cache, "rb") as src: prm, frames = src.getparams(), src.readframes(src.getnframes())
                    if prm.sampwidth == 2:
                        a = array.array("h"); a.frombytes(frames); frames = array.array("h", (int(x * volume) for x in a)).tobytes()
                    elif prm.sampwidth == 1:
                        frames = bytes(128 + int((x - 128) * volume) for x in frames)
                    with wave.open(quiet, "wb") as dst: dst.setparams(prm); dst.writeframes(frames)
                play = quiet
            if os.path.exists(play): FX.send("play", play)
        except Exception:
            pass

    def banner_window(self):
        """The big banner's window: full width, see-through, made once when the match starts and only drawn into after
        that (opening a new window in the middle of the match makes the game let go of the keys you are holding:
        you'd stop sprinting, or throw the TNT you were holding)."""
        from tkinter import font as tkfont
        KEY = "#010101"; w = self.overlay(KEY, 0.93, transparent=True)
        sw, bh = w.winfo_screenwidth(), 110
        cv = tk.Canvas(w, width=sw, height=bh, bg=KEY, highlightthickness=0); cv.pack()
        w.geometry("%dx%d+0+%d" % (sw, bh, int(w.winfo_screenheight() * 0.08))); self.show_no_focus(w)
        self._banner = (w, cv, tkfont.Font(family="Impact", size=34, weight="bold"), [None])
        return self._banner

    def banner(self, text="Horde Becomes Stronger", secs=3.5):
        self.thunder()
        b = getattr(self, "_banner", None)
        try:
            if b is None or not b[0].winfo_exists(): b = self.banner_window()
        except tk.TclError:
            b = self.banner_window()
        w, cv, font, clear = b
        cv.delete("all")
        sw, bg = w.winfo_screenwidth(), "#d9d2c3"
        tw = font.measure(text.upper()) + 72; x0 = (sw - tw) // 2; y0, y1 = 18, 92
        cv.create_rectangle(x0, y0, x0 + tw, y1, fill=bg, outline=bg)
        cv.create_text(sw // 2, (y0 + y1) // 2, text=text.upper(), font=font, fill="#7a0a0a")
        if clear[0]: w.after_cancel(clear[0])
        clear[0] = w.after(int(secs * 1000), lambda: cv.delete("all"))

    def welcome(self):
        from tkinter import font as tkfont
        KEY = "#010101"; w = self.overlay(KEY, 0.0, transparent=True)
        fams = {f.lower(): f for f in tkfont.families()}
        face = next((fams[n] for n in ("chiller", "jokerman", "old english text mt", "impact") if n in fams), "Impact")
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        cv = tk.Canvas(w, width=sw, height=int(sh * 0.40), bg=KEY, highlightthickness=0); cv.pack()
        for text, y, size, col in (("WELCOME TO HORDE MODE", int(sh * 0.11), max(40, sh // 9), "#8a0b0b"),
                                   ("GOOD LUCK", int(sh * 0.27), max(28, sh // 14), "#a31212")):
            f = (face, size, "bold")
            for dx, dy in ((-3, 3), (3, 3), (0, 5), (-3, -2), (3, -2)): cv.create_text(sw // 2 + dx, y + dy, text=text, font=f, fill="black")
            cv.create_text(sw // 2, y, text=text, font=f, fill=col)
        w.geometry("%dx%d+0+%d" % (sw, int(sh * 0.40), int(sh * 0.06))); self.show_no_focus(w)
        t0 = time.time(); FI, HOLD, FO = 1.5, 4.0, 2.0
        def tick():
            t = time.time() - t0
            if t >= FI + HOLD + FO: w.destroy(); return
            a = t / FI if t < FI else 1.0 if t < FI + HOLD else 1 - (t - FI - HOLD) / FO
            try: w.attributes("-alpha", max(0.0, min(0.95, a)))
            except tk.TclError: return
            w.after(40, tick)
        tick()

    def storm_flash(self):
        """One lightning flash over the whole screen (faint, see-through, click-through), as the host's storm.py does."""
        w = self.overlay("#c9cdd8", 0.0)
        w.geometry("%dx%d+0+0" % (w.winfo_screenwidth(), w.winfo_screenheight())); self.show_no_focus(w)
        steps = [(0.20, 60), (0.0, 80), (0.14, 60), (0.07, 70), (0.03, 80)]
        def step():
            if not steps: w.destroy(); return
            a, ms = steps.pop(0)
            try: w.attributes("-alpha", a)
            except tk.TclError: return
            w.after(ms, step)
        step()

    def storm_loop(self, running):
        """A distant storm while the Horde game runs: every 50-110 s a flash, then thunder 0.7-2.4 s later."""
        import random
        nxt = time.time() + random.uniform(25, 45)
        while running[0] and game_running():
            if time.time() >= nxt:
                self.after(0, self.storm_flash)
                time.sleep(random.uniform(0.7, 2.4))
                self.thunder(0.45)
                nxt = time.time() + random.uniform(50, 110)
            time.sleep(0.5)

    def tier_label(self):
        KEY = "#010101"; w = self.overlay(KEY, 0.85, transparent=True)
        W, H = 260, 24
        cv = tk.Canvas(w, width=W, height=H, bg=KEY, highlightthickness=0); cv.pack()
        w.geometry("%dx%d+14+%d" % (W, H, max(0, (w.winfo_screenheight() - H) // 2)))
        self.show_no_focus(w)
        def draw(tier):
            cv.delete("all")
            text = "HORDE: %s" % tier.upper()
            col = {"easiest": "#c9c2a8", "easy": "#d6c36a", "normal": "#e0993a", "hard": "#d9602b", "nightmare": "#d02a1f", "hell": "#ff2a1a"}.get(tier, "#d6c36a")
            for dx, dy in ((-1, 1), (1, 1), (0, 2), (-1, -1), (1, -1)):
                cv.create_text(6 + dx, H // 2 + dy, text=text, font=("Consolas", 11, "bold"), fill="black", anchor="w")
            cv.create_text(6, H // 2, text=text, font=("Consolas", 11, "bold"), fill=col, anchor="w")
        return w, draw

    def hud_loop(self, addr):
        """Joined Horde game: the welcome title, the tier label and the 'Horde Becomes Stronger' banner, driven by the
        host's game (its /horde.json). Ends when the match is over or your game closes."""
        t0 = time.time()
        while not game_running() and time.time() - t0 < 180: time.sleep(2)
        t0 = time.time()
        while game_running() and not getattr(self, "on_team", False) and time.time() - t0 < 300: time.sleep(0.5)
        if not game_running(): return
        time.sleep(2.5)                                          # let the map appear first
        self.after(0, self.welcome)
        self.after(0, self.banner_window)                        # (made now, drawn into later: see banner_window)
        storming = [True]
        threading.Thread(target=self.storm_loop, args=(storming,), daemon=True).start()
        hud, shown, fails = [None], [None], 0
        try:
            while game_running():
                try:
                    st = json.load(self.fetch(addr, "/horde.json", 5)); fails = 0
                except Exception:
                    fails += 1
                    if fails > 30: break                         # an old host without overlays (or it went away)
                    time.sleep(1); continue
                if st.get("over"): break
                tier = st.get("tier")
                if tier and tier != shown[0]:
                    if shown[0] is not None: self.after(0, self.banner)
                    shown[0] = tier
                    def upd(t=tier):
                        if hud[0] is None: hud[0] = self.tier_label()
                        hud[0][1](t)
                    self.after(0, upd)
                time.sleep(1)
        finally:
            storming[0] = False
            self.after(0, lambda: hud[0] and hud[0][0].destroy())
            def _drop():
                b = getattr(self, "_banner", None); self._banner = None
                if b:
                    try: b[0].destroy()
                    except tk.TclError: pass
            self.after(6000, _drop)                              # (after the last banner has had its time)

    def get_music(self, addr, entry):
        """The host's Horde music, kept in this app's folder (downloaded again only when the host's file changes)."""
        d = os.path.join(DATA, "music"); os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "horde_ambient.wav")
        if not (os.path.exists(p) and sha256(p) == entry["sha256"]):
            self.say("Downloading the Horde music...")
            with self.fetch(addr, entry["url"], 120) as r, open(p + ".part", "wb") as f: shutil.copyfileobj(r, f)
            if sha256(p + ".part") != entry["sha256"]: os.remove(p + ".part"); raise RuntimeError("download damaged")
            os.replace(p + ".part", p)
        return p

    def play_music(self, path):
        """The Horde's background loop, on this PC, for as long as your game is open (the host hears its own copy)."""
        try: import winsound
        except ImportError: return
        t0 = time.time()
        while not game_running() and time.time() - t0 < 180: time.sleep(2)
        MUSIC.send("loop", path)
        try:
            while game_running(): time.sleep(3)
        finally:
            MUSIC.send("stop")

    def agent_source(self):
        """input_agent.js (the bot toolkit's game agent; knows the Steam and the GOG game): built into the exe, or
        beside companion.py, or in the toolkit folder."""
        for d in (getattr(sys, "_MEIPASS", ""), os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__)),
                  self.toolkit()):
            p = os.path.join(d, "input_agent.js")
            if d and os.path.exists(p):
                with open(p, encoding="utf-8") as f: return f.read()
        return None

    def auto_team(self, pid, team="red", horde=False):
        self.on_team = False
        try: return self._auto_team(pid, team, horde)
        finally: self.on_team = True                             # (also when it couldn't: the overlays may start)

    def _auto_team(self, pid, team="red", horde=False):
        """Put your game on a team as soon as its lobby shows (what the launcher's autojoin.py does): the Survivors (red)
        in a Horde game, or the side with fewer bots in a team game. If anything fails: pick it by hand."""
        LOBBY_STATE, PICK, TEAM_ID = 8, {"red": 3, "blue": 4}, {"red": 1, "blue": 2}
        name = "the Survivors" if horde else "the %s team" % team.title()
        try:
            import frida
        except ImportError:
            self.say("Pick %s in the lobby (this copy can't do it for you)." % name); return
        js = self.agent_source()
        if not js:
            self.say("Pick %s in the lobby (the game agent is missing)." % name); return
        session, end = None, time.time() + 150
        while time.time() < end:                                # the game may still be starting
            try: session = frida.get_local_device().attach(pid); break
            except Exception: time.sleep(1.0)
        if session is None:
            self.say("Couldn't reach your game to pick the team: pick %s in the lobby." % name); return
        script = None
        try:
            script = session.create_script(js); script.on("message", lambda m, d: None); script.load()
            api = script.exports_sync
            # Press Join Red whenever the lobby is up without a pick (every 1 s, once it has been up for 1.5 s). Joining over
            # the network the game shows its lobby state for a moment while it connects, ignores a pick made then, and loads
            # before the real team screen: so the lobby closing proves nothing. Done only once the game says our own
            # player is on the red team (the Survivors); if it ends up on blue, say so.
            pressed_at, presses, seen, checked = 0.0, 0, None, 0.0
            deadline = time.time() + 20 * 60
            while time.time() < deadline:
                time.sleep(0.25)
                try: lob = api.lobby()
                except Exception:
                    if not game_running(): return
                    continue
                if not lob: continue
                if lob.get("state") == LOBBY_STATE:
                    if lob.get("pick") == 0:
                        seen = seen or time.time()
                        if time.time() - seen >= 1.5 and time.time() - pressed_at >= 1.0 and presses < 120:
                            api.lobby_join(PICK[team]); pressed_at, presses = time.time(), presses + 1
                            if presses <= 3 or presses % 10 == 0: self.note("auto team: pressed Join %s (%d)" % (team.title(), presses))
                    elif lob.get("pick") != PICK[team] and not presses:
                        self.say("You picked a team yourself, so I left it."); return
                    continue
                seen = None
                if time.time() - checked < 1.0: continue
                checked = time.time()
                try: w = api.world()
                except Exception: continue
                me = next((p for p in (w or {}).get("players") or [] if p.get("local")), None) if (w or {}).get("ok") else None
                if not me: continue                                 # still loading
                if me.get("team") == TEAM_ID[team]:
                    self.on_team = True
                    self.note("auto team: on the %s team after %d press(es)" % (team, presses))
                    self.say("You're on %s. Good luck!" % name); return
                if me.get("team") == TEAM_ID[OTHER_TEAM[team]]:
                    self.say("You're on the %s team, not %s: press Esc and switch teams if you meant to."
                             % ("Horde (blue)" if horde else OTHER_TEAM[team].title(), name)); return
            self.say("Gave up waiting for the lobby: pick %s by hand." % name)
        except Exception as e:
            self.say("Couldn't pick the team for you (%s): pick %s in the lobby." % (e, name))
        finally:
            try:
                if script: script.unload()
                session.detach()
            except Exception: pass

    def wait_and_restore(self, start_wait):
        t0 = time.time()
        while start_wait and not game_running() and time.time() - t0 < 180: time.sleep(2)
        while game_running(): time.sleep(3)
        time.sleep(3)
        for _ in range(20):
            if restore_files(): break
            time.sleep(3)
        self.look_off()
        if self.session:
            self.say("Game closed. Your game folder is back as it was.")
        self.session = False

    # -- HOST
    def build_host(self):
        cv = self.cv; S, F = ("host_setup",), ("host_form",)
        Txt(cv, 36, 128, (
            "Hosting needs the bot toolkit: a one-time download of about 35 MB from GitHub (the bots, zombie skins, "
            "death sounds and music), plus Python 3, which it installs for you if this PC doesn't have it."), S, width=WIN_W - 72, anchor="nw")
        self.setup_btn = ttk.Button(self, text="Set up hosting", style="Big.TButton", command=self.setup_toolkit)
        self.put(self.setup_btn, 36, 210, S, width=WIN_W - 72 - CLOSE_W - 10)
        self.close_btns.append(ttk.Button(self, text="Close All Windows", style="Close.TButton", command=self.close_all))
        self.put(self.close_btns[-1], WIN_W - 36, 210, S, anchor="e", width=CLOSE_W, height=48)
        X = 250                                                   # where the boxes start
        self.h_game = ttk.Combobox(self, values=[a for a, _ in GAMES], state="readonly", width=24); self.h_game.set(self.cfg.get("h_game", "Survival Horde"))
        Txt(cv, 36, 130, "Game", F); self.put(self.h_game, X, 130, F)
        Txt(cv, 36, 164, "Map", F)                                # Standard / Custom ticks, then the map list for that choice
        self.h_mapkind = tk.StringVar(value=self.cfg.get("h_mapkind", "Standard"))
        self.kind_ticks = {}
        for i, t in enumerate(("Standard", "Custom")):
            tick = Txt(cv, X + i * 110, 164, "", F, font=("Segoe UI Symbol", 10))
            tick.bind(lambda t=t: (self.h_mapkind.set(t), self.map_choices())); self.kind_ticks[t] = tick
        self.h_map = ttk.Combobox(self, state="readonly", width=32); self.put(self.h_map, X, 192, F)
        self.map_keys = {}
        self.h_bots = tk.IntVar(value=self.cfg.get("h_bots", 3)); self.h_users = tk.IntVar(value=self.cfg.get("h_users", 2))
        self.h_bots_spin = ttk.Spinbox(self, from_=1, to=7, width=6, textvariable=self.h_bots)
        Txt(cv, 36, 228, "# of Bots (For Normal Games)", F); self.put(self.h_bots_spin, X, 228, F)
        Txt(cv, 36, 262, "Players, you included", F); self.put(ttk.Spinbox(self, from_=1, to=10, width=6, textvariable=self.h_users), X, 262, F)
        self.h_level = ttk.Combobox(self, values=LEVELS, state="readonly", width=24); self.h_level.set(self.cfg.get("h_level", "Normal"))
        Txt(cv, 36, 296, "Bot level", F); self.put(self.h_level, X, 296, F)
        H = ("host_form", "hmode")
        HMODES = ["Normal", "Challenge", "Melee Zombies"]         # Dark Mode: held back until it's ready for the companion
                                                                  # Melee Zombies: Mary and Two Feathers, knives and fists only
        self.h_hmode = ttk.Combobox(self, values=HMODES, state="readonly", width=24)
        self.h_hmode.set(self.cfg.get("h_hmode") if self.cfg.get("h_hmode") in HMODES else "Normal")
        Txt(cv, 36, 330, "Horde mode", H); self.put(self.h_hmode, X, 330, H)
        self.h_char = ttk.Combobox(self, values=CHARS, state="readonly", width=24)            # yours, in a Horde game
        self.h_char.set(self.cfg.get("h_char") if self.cfg.get("h_char") in CHARS else CHARS[0])
        Txt(cv, 36, 364, "Your character", H); self.put(self.h_char, X, 364, H)
        N = ("host_form", "normal")                               # a normal game's limits, in the same place (blank: none set)
        self.h_kills = tk.StringVar(value=self.cfg.get("h_kills", "")); self.h_time = tk.StringVar(value=self.cfg.get("h_time", ""))
        self.h_mode = ttk.Combobox(self, state="readonly", width=24)                          # only the map's own modes
        Txt(cv, 36, 330, "Game mode", N); self.put(self.h_mode, X, 330, N)
        Txt(cv, 36, 364, "Limits (blank = no limit)", N)
        self.put(ttk.Spinbox(self, from_=1, to=200, width=6, textvariable=self.h_kills), X, 364, N)
        Txt(cv, X + 66, 364, "kills/captures", N, fill=DIM, font=("Segoe UI", 9))     # Capture the flag: captures to win
        self.put(ttk.Spinbox(self, from_=1, to=120, width=6, textvariable=self.h_time), X + 160, 364, N)
        Txt(cv, X + 226, 364, "minutes", N, fill=DIM, font=("Segoe UI", 9))
        # Team games: how many of the bots play Red, the rest play Blue (each box fills the other in from "# of Bots").
        total = max(1, int(self.cfg.get("h_bots", 3) or 3))
        self.h_red = tk.StringVar(value=str(min(total, int(self.cfg.get("h_red", (total + 1) // 2)))))
        self.h_blue = tk.StringVar(value=str(total - int(self.h_red.get())))
        self.red_spin = ttk.Spinbox(self, from_=0, to=7, width=6, textvariable=self.h_red)
        self.blue_spin = ttk.Spinbox(self, from_=0, to=7, width=6, textvariable=self.h_blue)
        TS = N + ("teamsplit",)                                   # shown only for team games (visibility)
        Txt(cv, 36, 398, "Bots on Red", TS); self.put(self.red_spin, X, 398, TS)
        Txt(cv, X + 82, 398, "Bots on Blue", TS); self.put(self.blue_spin, X + 190, 398, TS)
        self._split_busy = False
        self.h_red.trace_add("write", lambda *a: self.split_bots("red"))
        self.h_blue.trace_add("write", lambda *a: self.split_bots("blue"))
        self.h_bots.trace_add("write", lambda *a: self.split_bots("total"))
        self.h_mode.bind("<<ComboboxSelected>>", lambda e: self.map_choices())      # the maps that have this mode
        self.h_screen = ttk.Combobox(self, values=list(SCREENS), state="readonly", width=24)     # your own game's window
        self.h_screen.set(self.cfg.get("h_screen") if self.cfg.get("h_screen") in SCREENS else "Full screen")
        Txt(cv, 36, 432, "Your screen", F); self.put(self.h_screen, X, 432, F)
        self.h_private = tk.BooleanVar(value=self.cfg.get("h_private", False))   # only you and the bots: nobody sees or joins it
        self.private_tick = Txt(cv, X + 196, 432, "", F, font=("Segoe UI Symbol", 10))
        self.private_tick.bind(lambda: (self.h_private.set(not self.h_private.get()), self.draw_ticks()))
        self.h_trim = tk.BooleanVar(value=self.cfg.get("h_trim", True))
        self.trim_tick = Txt(cv, 36, 466, "", F, font=("Segoe UI Symbol", 10))
        self.trim_tick.bind(lambda: (self.h_trim.set(not self.h_trim.get()), self.draw_ticks()))
        ram, cpu = hardware(); low = (ram and ram < 16) or cpu < 8
        self.low_pc = bool(low)
        Txt(cv, 36, 486, (
            "Every bot is a full copy of the game, so each one uses a lot of RAM and CPU. Hosting too many bots can slow or "
            "crash your game. Survival Horde grows to 10 bots. This PC: %s, %d CPU threads. %s" % (
                ("%d GB RAM" % ram) if ram else "RAM unknown", cpu,
                "That is on the low side: keep normal games to 1-2 bots, and join Horde games instead of hosting them." if low
                else "If the game stutters, lower the bots.")), F, fill=WARN if low else DIM, font=("Segoe UI", 9),
            width=WIN_W - 72, anchor="nw")
        self.host_btn = ttk.Button(self, text="START HOSTING", style="Big.TButton", command=self.host_toggle)
        self.put(self.host_btn, 36, 586, F, width=WIN_W - 72 - CLOSE_W - 10)
        self.close_btns.append(ttk.Button(self, text="Close All Windows", style="Close.TButton", command=self.close_all))
        self.put(self.close_btns[-1], WIN_W - 36, 586, F, anchor="e", width=CLOSE_W, height=48)
        refresh = Txt(cv, 70, WIN_H - 28, "Refresh hosting toolkit", F, fill=DIM, font=("Segoe UI", 9, "underline"))
        refresh.bind(self.setup_toolkit)
        self.addr_lbl = Txt(cv, 36, 622, "", F, fill=GOLD, font=("Segoe UI", 11, "bold"))
        self.h_game.bind("<<ComboboxSelected>>", lambda e: self.game_choice())
        self.game_choice()

    def draw_ticks(self):
        for t, tick in self.kind_ticks.items():
            tick.configure(text=("◉ " if self.h_mapkind.get() == t else "○ ") + t)
        self.trim_tick.configure(text=("☑ " if self.h_trim.get() else "☐ ") + "Trim Idle Bot Memory (Lowers RAM Usage)")
        if hasattr(self, "private_tick"):
            self.private_tick.configure(text=("☑ " if self.h_private.get() else "☐ ") + "Private game")

    def map_choices(self):
        """Fill the Map list: the game's own maps, or the custom maps in your game folder. Only maps that have the
        chosen game's mode are offered (Survival Horde needs team deathmatch; Sanctuary is made to work for it)."""
        horde = dict(GAMES).get(self.h_game.get()) == "horde"
        self.draw_ticks()
        self.mode_choices()
        letter = "M" if horde else self.mode_letter()                # a normal game: only maps that have its mode
        if self.h_mapkind.get() == "Custom":
            found = custom_maps(self.game_dir(), self.toolkit())
            self.map_modes = {k: m for _l, k, m in found}
            pairs = [(l, k) for l, k, m in found if letter in m]
        else:
            pairs = [("Random", RANDOM_MAP)] + [(l, k) for l, k in MAPS if horde or letter in STD_MODES.get(k, "")]
        self.map_keys = dict(pairs)
        if not pairs:
            self.h_map.configure(values=[]); self.h_map.set("(no custom maps with this game mode in your game folder)"); return
        names = [l for l, _ in pairs]; self.h_map.configure(values=names)
        cur = self.h_map.get()
        want = cur if cur in names else self.cfg.get("h_map", "Dry Gulch")
        self.h_map.set(want if want in names else names[0])

    def mode_choices(self):
        """Normal game: every game mode is offered; the Map list then holds only the maps that have the chosen one
        (Bunkers has no Capture the flag, so it isn't offered for it)."""
        if not hasattr(self, "h_mode"): return
        names = [n for n, _c, _m in GAME_MODES]
        self.h_mode.configure(values=names)
        cur = self.h_mode.get() or self.cfg.get("h_mode", "Deathmatch")
        self.h_mode.set(cur if cur in names else names[0])
        self.botteam_state()

    def mode_letter(self):
        """The chosen normal game's mode, as the .rcm letter (D deathmatch, M team deathmatch, C capture the flag ...)."""
        return {n: c for n, c, _m in GAME_MODES}.get(self.h_mode.get() if hasattr(self, "h_mode") else "", "D")

    def team_game(self):
        """The chosen normal game is one with red and blue teams (Team deathmatch, Capture the flag)."""
        if not hasattr(self, "h_mode"): return False
        return {n: m for n, _c, m in GAME_MODES}.get(self.h_mode.get()) in ("team", "ctf")

    def botteam_state(self):
        for w in (getattr(self, "red_spin", None), getattr(self, "blue_spin", None)):
            if w is not None: w.state(["!disabled"] if self.team_game() else ["disabled"])
        if hasattr(self, "tab"): self.visibility()           # the Bots on Red / Blue row: team games only

    def split_bots(self, changed):
        """Keep Bots on Red + Bots on Blue = # of Bots: the box you didn't touch fills itself in."""
        if self._split_busy: return
        def num(v, default):
            try: return int(v.get())
            except (ValueError, tk.TclError): return default
        total = max(1, min(7, num(self.h_bots, 1)))
        red, blue = num(self.h_red, None), num(self.h_blue, None)
        if changed == "blue" and blue is not None: red = total - max(0, min(total, blue))
        elif red is None: return                                  # half typed: wait
        red = max(0, min(total, red))
        self._split_busy = True
        try:
            if changed != "red" or str(red) != self.h_red.get(): self.h_red.set(str(red))
            if changed != "blue" or str(total - red) != self.h_blue.get(): self.h_blue.set(str(total - red))
        finally:
            self._split_busy = False

    def bot_split(self):
        """(bots on red, bots on blue) for a team game."""
        try: red = int(self.h_red.get())
        except ValueError: red = (self.h_bots.get() + 1) // 2
        total = self.h_bots.get(); red = max(0, min(total, red))
        return red, total - red

    def game_choice(self):
        """Grey out what the chosen game ignores: Horde picks its own bot count and level; Horde mode is Horde-only."""
        horde = dict(GAMES).get(self.h_game.get()) == "horde"
        self.map_choices()
        self.h_bots_spin.state(["disabled"] if horde else ["!disabled"])
        self.h_level.configure(state="disabled" if horde else "readonly")     # Horde raises the bots' level by itself
        if hasattr(self, "tab"): self.visibility()                           # only a Horde game has a Horde mode

    def refresh_host(self):
        self.have_toolkit = os.path.exists(os.path.join(self.toolkit(), "botctl.py"))
        self.visibility()

    def auto_refresh_toolkit(self, tries=0):
        """The newer toolkit on GitHub, at start: waits while something else is busy, never while a game is hosted."""
        if self.host_proc and self.host_proc.poll() is None: return
        tk_dir = self.toolkit()
        if os.path.exists(os.path.join(tk_dir, "BotLauncher.exe")) or os.path.isdir(os.path.join(tk_dir, "Companion-Launcher")):
            return                                  # the original toolkit (it is never replaced)
        if self.busy or not hasattr(self, "setup_btn"):
            if tries < 60: self.after(5000, lambda: self.auto_refresh_toolkit(tries + 1))
            return
        self.setup_toolkit()

    def setup_toolkit(self):
        if self.busy: return
        self.busy = True; self.setup_btn.configure(state="disabled", text="Setting up...")
        threading.Thread(target=self.setup_work, daemon=True).start()

    def python_cmd(self):
        local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python")
        found = sorted((os.path.join(local, d, "python.exe") for d in (os.listdir(local) if os.path.isdir(local) else [])
                        if d.lower().startswith("python3")), reverse=True)            # a fresh install isn't on PATH yet
        for c in [["py", "-3"], ["python"]] + [[p] for p in found if os.path.exists(p)]:
            try:
                if subprocess.run(c + ["-c", "import venv,tkinter"], capture_output=True, creationflags=NO_WINDOW).returncode == 0: return c
            except OSError: pass
        return None

    def install_python(self):
        """Python 3.12 from python.org, installed for this Windows user only (no admin prompt, nothing else changed).
        Checked first: the installer must carry a valid Python Software Foundation signature. Returns python_cmd()."""
        arm = "ARM64" in (os.environ.get("PROCESSOR_ARCHITEW6432", "") + os.environ.get("PROCESSOR_ARCHITECTURE", "")).upper()
        url = PY_URL.replace("-amd64.exe", "-arm64.exe") if arm else PY_URL
        exe = os.path.join(tempfile.mkdtemp(prefix="outlaws_comp_"), url.rsplit("/", 1)[1])
        self.say("Python 3 isn't on this PC: downloading it from python.org (about 25 MB)...")
        with urllib.request.urlopen(url, timeout=600) as r, open(exe, "wb") as out: shutil.copyfileobj(r, out)
        ps = ("$s = Get-AuthenticodeSignature -LiteralPath '%s'; if ($s.Status -eq 'Valid' -and "
              "$s.SignerCertificate.Subject -match 'Python Software Foundation') { exit 0 } else { exit 1 }" % exe.replace("'", "''"))
        if subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                          creationflags=NO_WINDOW).returncode != 0:
            raise RuntimeError("the Python download isn't signed by the Python Software Foundation")
        self.say("Installing Python 3.12 for your Windows user (a minute or two, no admin needed)...")
        r = subprocess.run([exe, "/quiet", "InstallAllUsers=0", "Include_launcher=1", "InstallLauncherAllUsers=0",
                            "Include_tcltk=1", "Include_pip=1", "Include_test=0", "Include_doc=0", "PrependPath=1",
                            "Shortcuts=0", "AssociateFiles=0"], creationflags=NO_WINDOW)
        shutil.rmtree(os.path.dirname(exe), ignore_errors=True)
        if r.returncode not in (0, 3010):
            raise RuntimeError("the Python installer stopped (code %d)" % r.returncode)
        return self.python_cmd()

    @staticmethod
    def extract_toolkit(zip_path, tk_dir):
        with zipfile.ZipFile(zip_path) as z:
            for n in z.namelist():                           # scripts, plus zombie\ and sounds\ (the Horde's assets)
                parts = n.replace("\\", "/").split("/")
                if len(parts) > 2 or ".." in parts or not parts[-1] or (len(parts) == 2 and parts[0] not in ("zombie", "sounds")):
                    continue
                z.extract(n, tk_dir)

    def toolkit_current(self):
        """Before hosting: if GitHub has a newer hosting toolkit than this PC's, get it now, so nobody hosts an old one.
        GitHub out of reach: host with the toolkit that's here. Not for the original toolkit (it is never replaced)."""
        tk_dir = self.toolkit()
        if os.path.exists(os.path.join(tk_dir, "BotLauncher.exe")) or os.path.isdir(os.path.join(tk_dir, "Companion-Launcher")):
            return
        try:
            stamp, sha, size = self.gh("toolkit/version.txt", 15, root=True).read().decode().split()[:3]
        except Exception:
            self.note("toolkit check: GitHub out of reach, hosting with this PC's toolkit"); return
        if self.cfg.get("toolkit_stamp") and stamp <= self.cfg["toolkit_stamp"]: return
        self.say("A newer hosting toolkit is on GitHub: getting it before the game starts (%d MB)..." % (int(size) // 2**20))
        try:
            tmp = os.path.join(tempfile.mkdtemp(prefix="outlaws_comp_"), "t.zip")
            with self.gh("toolkit/toolkit.zip", 600, root=True) as r, open(tmp, "wb") as out: shutil.copyfileobj(r, out)
            if sha256(tmp) != sha.lower(): raise RuntimeError("the download was damaged")
            self.extract_toolkit(tmp, tk_dir)
            self.cfg["toolkit_stamp"] = stamp; save(self.cfg)
            self.say("Hosting toolkit updated.")
        except Exception as e:
            self.say("Couldn't update the hosting toolkit (%s): hosting with this PC's copy." % e)

    def setup_work(self):
        try:
            tk_dir = self.toolkit()
            if os.path.exists(os.path.join(tk_dir, "BotLauncher.exe")) or os.path.isdir(os.path.join(tk_dir, "Companion-Launcher")):
                self.say("This PC has the original bot toolkit (the one the hosting toolkit is made from): it is not "
                         "replaced. Hosting is ready."); return
            if not self.ensure_writable(self.game_dir()): self.say("Setup cancelled: the game folder is protected."); return
            os.makedirs(tk_dir, exist_ok=True)
            tmp = os.path.join(tempfile.mkdtemp(prefix="outlaws_comp_"), "t.zip")
            stamp = None
            try:                                       # the published toolkit on GitHub: works whether or not anyone is hosting
                stamp, sha, size = self.gh("toolkit/version.txt", 30, root=True).read().decode().split()[:3]
                self.say("Downloading the hosting toolkit from GitHub (%d MB)..." % (int(size) // 2**20))
                with self.gh("toolkit/toolkit.zip", 600, root=True) as r, open(tmp, "wb") as out: shutil.copyfileobj(r, out)
                if sha256(tmp) != sha.lower(): raise RuntimeError("the download was damaged")
            except Exception as e:                     # GitHub unreachable: fetch it from a friend who is hosting
                stamp = None
                self.say("Couldn't get the toolkit from GitHub (%s); trying a friend who is hosting..." % e)
                if not self.ensure_zerotier(): self.say("Setup cancelled: ZeroTier is needed first."); return
                hosts = self.discover(4)
                addr = self.choose_host(hosts) if hosts else None
                if not addr: self.say("Nobody is hosting right now either. Try again in a while."); return
                with self.fetch(addr, "/toolkit.zip", 300) as r, open(tmp, "wb") as out: shutil.copyfileobj(r, out)
            self.extract_toolkit(tmp, tk_dir)
            if stamp: self.cfg["toolkit_stamp"] = stamp; save(self.cfg)
            self.say("Toolkit installed. Setting up Python (a few minutes the first time)...")
            py = self.python_cmd()
            if not py:
                try: py = self.install_python()
                except Exception as e: self.say("Couldn't install Python by itself (%s)." % e)
            if not py:
                self.say("Python 3 (with tkinter) is still missing. Opening python.org: install it, then press Set up hosting again.")
                webbrowser.open("https://www.python.org/downloads/"); return
            venv = os.path.join(tk_dir, ".venv")
            if not os.path.exists(os.path.join(venv, "Scripts", "python.exe")):
                subprocess.run(py + ["-m", "venv", venv], check=True, creationflags=NO_WINDOW)
                r = subprocess.run([os.path.join(venv, "Scripts", "python.exe"), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                                    "-r", os.path.join(tk_dir, "requirements.txt")], capture_output=True, text=True, creationflags=NO_WINDOW)
                if r.returncode != 0:
                    shutil.rmtree(venv, ignore_errors=True); self.say("pip failed: " + (r.stderr or "")[-300:]); return
            self.say("Hosting is ready.")
        except Exception as e:
            self.say("Setup failed: %s" % e)
        finally:
            self.busy = False
            self.after(0, lambda: (self.setup_btn.configure(state="normal", text="Set up hosting"), self.refresh_host()))

    def host_warning(self, key, text):
        """The 'bots use a lot of RAM and CPU' question before hosting, with a 'Do Not Show This Again' tick box.
        Once ticked (and answered Yes), that warning is skipped from then on. True = host."""
        if self.cfg.get("skip_warning_" + key): return True
        win = tk.Toplevel(self); win.title(APP); win.configure(bg=DARK); win.resizable(False, False); win.transient(self)
        try: win.iconphoto(False, self.icon_img)
        except Exception: pass
        ans, dont = [False], tk.BooleanVar(value=False)
        tk.Label(win, text=text, bg=DARK, fg=TXT, font=("Segoe UI", 10), wraplength=420, justify="left").pack(padx=18, pady=(16, 10), anchor="w")
        tk.Checkbutton(win, text="Do Not Show This Again", variable=dont, bg=DARK, fg=TXT, selectcolor="#2a1a10",
                       activebackground=DARK, activeforeground=GOLD, font=("Segoe UI", 10)).pack(padx=14, anchor="w")
        row = tk.Frame(win, bg=DARK); row.pack(pady=(10, 14), padx=18, anchor="e")
        def done(yes):
            ans[0] = yes
            if yes and dont.get():
                self.cfg["skip_warning_" + key] = True; save(self.cfg)
            win.destroy()
        ttk.Button(row, text="Host anyway", command=lambda: done(True)).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="Cancel", command=lambda: done(False)).pack(side="left")
        win.protocol("WM_DELETE_WINDOW", lambda: done(False))
        win.bind("<Escape>", lambda e: done(False))
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry("+%d+%d" % (max(0, x), max(0, y)))
        win.grab_set(); win.focus_set(); self.wait_window(win)
        return ans[0]

    def host_toggle(self):
        # STOP: stop the hosting, never start it again. (While ZeroTier is being checked there's no host program yet,
        # which used to send STOP down the start path, with the RAM/CPU warning.)
        if (self.host_proc and self.host_proc.poll() is None) or getattr(self, "hosting", False): self.stop_host(); return
        self.host_cancel = False
        if game_running(): messagebox.showinfo(APP, "Close Outlaws first (hosting restarts the game copies)."); return
        kind = dict(GAMES)[self.h_game.get()]
        map_key = self.map_keys.get(self.h_map.get())
        if not map_key:
            messagebox.showinfo(APP, "Pick a map first. (Custom maps are the ones in your Outlaws folder: a .lab and an .rcm file each.)"); return
        if map_key == RANDOM_MAP:                         # a standard map that has the chosen game mode, not the last one picked
            letter = "" if kind == "horde" else {n: c for n, c, _m in GAME_MODES}.get(self.h_mode.get(), "D")
            maps = [(l, k) for l, k in MAPS if letter in STD_MODES.get(k, "")]
            maps = [m for m in maps if m[1] != self.cfg.get("last_random_map")] or maps
            label, map_key = random.choice(maps)
            self.cfg["last_random_map"] = map_key
            self.say("Random map: %s" % label)
        lim = {}
        if kind != "horde":
            for k, v, name in (("fraglimit", self.h_kills.get().strip(), "Kill/capture limit"), ("timelimit", self.h_time.get().strip(), "Time limit")):
                if not v: continue
                if not v.isdigit() or int(v) < 1:
                    messagebox.showinfo(APP, "%s must be a whole number (or blank)." % name); return
                lim[k] = v
        if kind == "horde":
            if not self.host_warning("horde", "Survival Horde adds bots until there are 10, and every bot is a full copy of the game.\n\n"
                                       "On a PC that isn't powerful, hosting can slow down or crash your game (and everyone else's). "
                                       "If your hardware isn't good, join a Horde game someone else hosts instead.\n\nHost it anyway?"): return
        elif self.h_bots.get() > 2 or self.low_pc:
            if not self.host_warning("bots", "Each bot uses a lot of RAM and CPU (it is a full game window).\n\n"
                                       "With %d bots on a PC that isn't powerful, the game can slow down or crash. Keep the number low if unsure.\n\nHost anyway?" % self.h_bots.get()): return
        mode = "horde" if kind == "horde" else {n: m for n, _c, m in GAME_MODES}.get(self.h_mode.get(), "dm")
        cfg = {"kind": "horde" if kind == "horde" else "bots", "mode": mode, "map": map_key,
               "count": self.h_bots.get(), "difficulty": self.h_level.get(),
               "users": 1 if self.h_private.get() else self.h_users.get(),        # a private game: just you
               "private": bool(self.h_private.get()),
               "horde": {"Challenge": "challenge", "Dark Mode": "dark"}.get(self.h_hmode.get(), "normal"), "player": "Player", "trim": bool(self.h_trim.get()), "melee": kind == "horde" and self.h_hmode.get() == "Melee Zombies",
               "tnt_only": kind != "horde" and self.h_mode.get() == TNT_ONLY,
               "melee_only": kind != "horde" and self.h_mode.get() == MELEE_ONLY,
               **lim}
        if kind != "horde" and self.team_game():
            red, blue = self.bot_split()
            cfg["red_bots"] = red                                       # the rest are blue; everyone picks their own team in the lobby
        self.cfg.update(h_game=self.h_game.get(), h_map=self.h_map.get(), h_mapkind=self.h_mapkind.get(), h_bots=self.h_bots.get(), h_users=self.h_users.get(),
                        h_level=self.h_level.get(), h_hmode=self.h_hmode.get(), h_char=self.h_char.get(), h_screen=self.h_screen.get(), h_kills=self.h_kills.get().strip(), h_time=self.h_time.get().strip(), h_trim=bool(self.h_trim.get()), h_private=bool(self.h_private.get()), h_mode=self.h_mode.get(), h_red=self.bot_split()[0]); save(self.cfg)
        tkd = self.toolkit(); jf = os.path.join(tkd, "companion_host.json")
        with open(jf, "w") as f: json.dump(cfg, f)
        self.host_btn.configure(text="STOP"); self.hosting = True
        threading.Thread(target=self.host_work, args=(jf, True, kind), daemon=True).start()

    def host_work(self, jf, play, kind):
        if not self.ensure_zerotier():
            self.say("Hosting cancelled: this PC isn't on the games' ZeroTier network yet."); self.hosting = False
            self.after(0, lambda: self.host_btn.configure(text="START HOSTING")); return
        if getattr(self, "host_cancel", False): return     # STOP was pressed while ZeroTier was checked
        self.after(0, lambda: self.addr_lbl.configure(text="Private game: only you and the bots." if self.cfg.get("h_private")
                                                      else "Friends: open the Companion and press Join Game."))
        self.toolkit_current()                         # never host an old toolkit
        if getattr(self, "host_cancel", False): return
        tkd = self.toolkit(); py = os.path.join(tkd, ".venv", "Scripts", "python.exe")
        self.say("Starting the game server (this takes a minute)...")
        self.host_proc = p = subprocess.Popen([py, "-u", os.path.join(tkd, "companionhost.py"), jf], cwd=tkd, stdout=subprocess.PIPE,
                                              stderr=subprocess.STDOUT, text=True, errors="replace", creationflags=NO_WINDOW)
        joined = False
        for line in p.stdout:
            if "lobby up" in line or ("waiting for" in line and "player" in line and "waiting for the host" not in line) or "HOSTED" in line:
                if not joined:
                    joined = True; self.say("The game is up: friends can join now.")
                    if play:
                        game = self.game_dir()
                        char = self.h_char.get() if kind == "horde" else self.char.get()
                        cmd = game_cmd(game, self.h_screen.get(), char, "127.0.0.1", horde=(kind == "horde"))
                        try:
                            me = subprocess.Popen(cmd, cwd=game)
                            threading.Thread(target=self.end_with_my_game, args=(me,), daemon=True).start()
                            if kind == "horde":
                                self.say("Your game is starting as %s and joins the Survivors team by itself." % char)
                                threading.Thread(target=self.auto_team, args=(me.pid, "red", True), daemon=True).start()
                            else:
                                self.say("Your own game is starting.")
                        except OSError as e: self.say("Could not start your game: %s" % e)
        p.wait()
        self.say("Hosting ended.")
        self.host_cleanup()
        self.hosting = False
        self.after(0, lambda: (self.host_btn.configure(text="START HOSTING"), self.addr_lbl.configure(text="")))

    def check_game_folder(self, game):
        """The mods go into the companion's game folder. If Steam starts the game from another install instead, none of
        them (nor the character or the direct join) reach the game: find that out and say so plainly."""
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='OutlawsAHOM.exe'\" | ForEach-Object { '{0}|{1}' -f $_.ExecutablePath, $_.CommandLine }")
        for _ in range(40):
            time.sleep(3)
            try:
                out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                                     text=True, timeout=20, creationflags=NO_WINDOW).stdout
            except Exception:
                return
            mine = [l.split("|", 1) for l in out.splitlines() if l.strip() and "-userpath" not in l]
            if not mine: continue
            exe, cmdline = mine[0][0].strip(), (mine[0][1] if len(mine[0]) > 1 else "")
            self.note("game running from %s with: %s" % (exe, cmdline.strip()))
            if exe and os.path.normcase(os.path.dirname(exe)) != os.path.normcase(os.path.normpath(game)):
                self.say("Your game is running from %s, but the companion put the mods in %s. Pick the game's real "
                         "folder by clicking the folder path under the title (Steam: right-click the game > Manage > Browse local files), "
                         "then join again." % (os.path.dirname(exe), game))
            elif "-join" not in cmdline:
                self.say("Your game started without the companion's settings (Steam may have restarted it), so the "
                         "mods, character and team may be missing. Start Steam first, then press Join again.")
            return

    def own_game_running(self):
        """Is your own game (not a bot's copy: those run with -userpath) still open? None if Windows can't be asked."""
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='OutlawsAHOM.exe'\" | ForEach-Object { $_.CommandLine }")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                               timeout=20, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0: return None
        return any(l.strip() and "-userpath" not in l for l in r.stdout.splitlines())

    def end_with_my_game(self, me):
        """When you close your own game, the hosted game is over: close the bots, every toolkit window and the music,
        and take the mods out (Close All Windows, without the question)."""
        me.wait()
        gone = 0
        while self.host_proc and self.host_proc.poll() is None:       # (Steam can hand the game to a new process)
            running = self.own_game_running()
            gone = gone + 1 if running is False else 0
            if gone >= 2: break
            time.sleep(3)
        if not (self.host_proc and self.host_proc.poll() is None) and not game_running():
            return                                                     # the match already ended and tidied up
        self.say("Your game closed: ending the hosted game, closing every window and taking the mods out...")
        self.after(0, lambda: [b.configure(state="disabled", text="Closing...") for b in self.close_btns])
        self.close_work(quiet=True)

    def close_all(self):
        """Close All Windows: end every Outlaws game copy and every bot (yours, the host's, the bot launcher's), then
        make sure no mod is left in the game folder."""
        if not messagebox.askyesno(APP, "Close every Outlaws window and bot on this PC (including a game you are hosting), "
                                   "and take all mods out of the game folder?"): return
        for b in self.close_btns: b.configure(state="disabled", text="Closing...")
        threading.Thread(target=self.close_work, daemon=True).start()

    def close_work(self, quiet=False):
        try:
            if self.host_proc and self.host_proc.poll() is None:
                subprocess.run(["taskkill", "/PID", str(self.host_proc.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
            # every bot-toolkit program: the bots, the music and sounds, the on-screen overlays and their console windows
            # (never this companion itself, which may be running from companion.py in that same folder)
            ps = ("Get-CimInstance Win32_Process | Where-Object { ($_.Name -match 'python|cmd|conhost') -and $_.ProcessId -ne %d -and "
                  "$_.CommandLine -notmatch 'companion\\.py|test_companion|launcher\\.py|launcher\\.cmd' -and $_.CommandLine -match "
                  "'botctl\\\\[^\\\\ ]+\\.(py|cmd)|companionhost\\.py|horde\\.py|botctl\\.py|lms\\.py|autojoin\\.py|deathwatch\\.py|"
                  "ambient\\.py|wolf\\.py|deathsound\\.py|tierhud\\.py|welcome\\.py|storm\\.py|announce\\.py' } | "
                  "ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }") % os.getpid()
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60, creationflags=NO_WINDOW)
            for _ in range(2):
                subprocess.run(["taskkill", "/IM", "OutlawsAHOM.exe", "/F"], capture_output=True, creationflags=NO_WINDOW)
                time.sleep(1.5)
            for _ in range(10):
                if not game_running(): break
                time.sleep(1)
            self.say("Closed every game window and bot.")
            ok = True
            for _ in range(10):                                  # mods a join installed (zombie skins, map files ...)
                if restore_files(): break
                time.sleep(2)
            else:
                ok = False
            self.session = False
            self.look_off()                                      # the Survival look a join put into Common.kpf
            tkd = self.toolkit(); py = os.path.join(tkd, ".venv", "Scripts", "python.exe")
            if os.path.exists(os.path.join(tkd, "companionhost.py")) and os.path.exists(py):      # mods a hosted game installed
                r = subprocess.run([py, os.path.join(tkd, "companionhost.py"), "--cleanup"], cwd=tkd, capture_output=True,
                                   text=True, timeout=180, creationflags=NO_WINDOW)
                ok = ok and r.returncode == 0
            self.say("All mods are out of your game folder." if ok else
                     "Some mod files are still in use; press Close All Windows again in a moment.")
        except Exception as e:
            self.say("Close All Windows: %s" % e)
        finally:
            self.after(0, lambda: [b.configure(state="normal", text="Close All Windows") for b in self.close_btns])
            self.hosting = False
            self.after(0, lambda: self.host_btn.configure(text="START HOSTING"))

    def stop_host(self):
        self.hosting = False; self.host_cancel = True               # (a start still checking ZeroTier stops there)
        self.host_btn.configure(text="START HOSTING")
        try:
            if self.host_proc and self.host_proc.poll() is None:   # the whole tree: horde.py and its bots too
                subprocess.run(["taskkill", "/PID", str(self.host_proc.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
                self.host_proc.kill()
        except OSError: pass
        subprocess.run(["taskkill", "/IM", "OutlawsAHOM.exe", "/F"], capture_output=True, creationflags=NO_WINDOW)
        self.say("Stopped hosting; game windows closed.")
        self.host_cleanup()

    def host_cleanup(self):
        """Take the Horde mods out of the game folder once every game window is closed (runs on its own)."""
        tkd = self.toolkit(); py = os.path.join(tkd, ".venv", "Scripts", "python.exe")
        if not os.path.exists(os.path.join(tkd, "companionhost.py")): return
        try:
            subprocess.Popen([py, os.path.join(tkd, "companionhost.py"), "--cleanup"], cwd=tkd, creationflags=NO_WINDOW)
        except OSError as e:
            self.say("Could not start the mod clean-up: %s" % e)


def sound_server(port):
    """OutlawsCompanion --sound-server <port>: a background copy of this app that plays the sounds it is told over a
    local connection ('play <wav>', 'loop <wav>', 'stop'). Windows plays one sound at a time per program, so the music
    and the effects (thunder) each get their own and never cut each other off. It ends when this app closes."""
    import winsound, socket
    c = socket.create_connection(("127.0.0.1", int(port)), timeout=30); c.settimeout(None)
    for line in c.makefile("r", encoding="utf-8"):
        cmd, _, path = line.strip().partition(" ")
        try:
            if cmd == "play": winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            elif cmd == "loop": winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
            elif cmd == "stop": winsound.PlaySound(None, 0)
        except Exception:
            pass


class SoundChannel:
    """One sound helper, started the first time it is used and then kept (one start: the packed exe unpacks once)."""
    def __init__(self): self.conn = None; self.lock = threading.Lock()
    def _start(self):
        import socket
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1); srv.settimeout(30)
        me = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__)]
        subprocess.Popen(me + ["--sound-server", str(srv.getsockname()[1])], creationflags=NO_WINDOW, env=clean_env())
        try: self.conn = srv.accept()[0]
        finally: srv.close()
    def send(self, cmd, path=""):
        with self.lock:
            for _ in range(2):
                try:
                    if self.conn is None: self._start()
                    self.conn.sendall((("%s %s" % (cmd, path)).strip() + "\n").encode("utf-8")); return
                except OSError:
                    self.conn = None


MUSIC, FX = SoundChannel(), SoundChannel()


def log_line(msg):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "companion_log.txt"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + vtext(VERSION) + " " + msg.rstrip() + "\n")
    except OSError:
        pass


def hand_over():
    """An older copy of the app was opened (an old download, a pin to the copy on a network drive): start the newest
    installed copy instead and close. The newest copy to run writes down where it is. True = this copy should exit."""
    if not getattr(sys, "frozen", False): return False
    cfg = load()
    inst = cfg.get("installed") or {}
    path, ver = inst.get("path") or "", int(inst.get("version") or 0)
    me = os.path.normcase(os.path.abspath(sys.executable))
    if ver > VERSION and path and os.path.normcase(os.path.abspath(path)) != me and os.path.isfile(path):
        log_line("this copy (%s) is older than the one at %s (%s): starting that one" % (sys.executable, path, vtext(ver)))
        try:
            subprocess.Popen(["explorer.exe", path], env=clean_env()); return True
        except OSError:
            return False
    if ver <= VERSION:
        cfg["installed"] = {"path": sys.executable, "version": VERSION}; save(cfg)
    return False


def refresh_old_copy():
    """After an update from a network drive the new version runs from this PC, while the copy on the share stays old (the
    share kept it locked). Once it is free, put this version over it too, so opening it from there is current as well."""
    if not getattr(sys, "frozen", False): return
    cfg = load(); old = cfg.get("replace_old")
    if not old or os.path.normcase(os.path.abspath(old)) == os.path.normcase(os.path.abspath(sys.executable)): return
    tries = int(cfg.get("replace_old_tries", 0)) + 1
    try:
        if not os.path.isfile(old): raise FileNotFoundError(old)
        with open(old, "rb") as f: head = f.read()
        if b"companion_bg" not in head: raise ValueError("not this app")
        if sha256(old) != sha256(sys.executable):
            shutil.copyfile(sys.executable, old + ".new")
            os.replace(old + ".new", old)
            log_line("the copy at %s is now %s too" % (old, vtext(VERSION)))
        cfg.pop("replace_old", None); cfg.pop("replace_old_tries", None)
    except (FileNotFoundError, ValueError):
        cfg.pop("replace_old", None); cfg.pop("replace_old_tries", None)
    except OSError as e:
        try: os.remove(old + ".new")
        except OSError: pass
        if tries >= 20:
            log_line("gave up updating the copy at %s (%s)" % (old, e)); cfg.pop("replace_old", None); cfg.pop("replace_old_tries", None)
        else:
            cfg["replace_old_tries"] = tries
    save(cfg)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--sound-server"]:
        try: sound_server(sys.argv[2])
        except Exception: pass
        sys.exit(0)
    if hand_over(): sys.exit(0)
    threading.Thread(target=refresh_old_copy, daemon=True).start()
    App().mainloop()
