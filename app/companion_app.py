"""Outlaws Companion (25 Nov, the user): the Bot Launcher's own window with the Companion's face - "Companion App" at the
top left, only the Play, Custom Games and Online tabs, Survival Horde alone in Custom Games (launcher.py, COMPANION).
This is its start (what OutlawsCompanion.exe runs, build_companion.cmd):
  1. the game folder: the one the Companion knew, else Steam's or GOG's, else ask;
  2. the bot toolkit in <game>\\botctl: from GitHub (Companion-Launcher's toolkit\\toolkit.zip), else from a friend who is
     hosting (their launcher's /toolkit.zip); a PC with the original toolkit (BotLauncher.exe there) keeps its own;
  3. Python for the bots (installed for this Windows user when missing) and their .venv;
  4. then the launcher, with OUTLAWS_COMPANION=1 and BOT_HOME=<game>\\botctl.
Also here: the Update button (a new OutlawsCompanion.exe from GitHub, swapped in, restarted) and, once the window is up,
taking out any mods a closed game left behind. Its settings stay where the old Companion kept them
(%APPDATA%\\OutlawsCompanion\\config.json)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

APP = "Outlaws Companion"
VERSION = 40                              # (the old Companion was 30; the launcher-faced one starts at 40)
DATA = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "OutlawsCompanion")
CONFIG = os.path.join(DATA, "config.json")
REPO, BRANCH = "AteEm85/Companion-Launcher", "main"
PY_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
GOG_ID = "1207659142"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TOOLKIT_DIRS = ("zombie", "sounds", "christmas", "christmas/music", "netre")


def load():
    try:
        with open(CONFIG) as f: return json.load(f)
    except Exception: return {}


def save(cfg):
    os.makedirs(DATA, exist_ok=True)
    with open(CONFIG, "w") as f: json.dump(cfg, f, indent=1)


def log(msg):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "companion_log.txt"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + f"v{VERSION} " + str(msg).rstrip() + "\n")
    except OSError:
        pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def clean_env():
    """this app's environment without PyInstaller's own variables (a new copy started with them looks for Python in THIS
    copy's temporary folder, deleted when it quits)"""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("_MEIPASS", "_PYI_", "PYINSTALLER_"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for k in ("TCL_LIBRARY", "TK_LIBRARY"):
        if getattr(sys, "frozen", False) and k in env and getattr(sys, "_MEIPASS", "\0") in env[k]: env.pop(k)
    return env


def find_games():
    """folders that hold OutlawsAHOM.exe: the GOG install and every Steam library"""
    found, cand = [], []
    try:
        import re
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


def run_elevated(exe, args):
    ps = "Start-Process -FilePath '%s' -ArgumentList '%s' -Verb RunAs -Wait -WindowStyle Hidden" % (exe, args.replace("'", "''"))
    return subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, creationflags=NO_WINDOW).returncode == 0


def gh_commit(cfg):
    """the commit main is at (raw links pinned to it never serve an old copy), or None"""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}",
                                 headers={"User-Agent": "OutlawsCompanion", "Accept": "application/vnd.github.sha"})
    if cfg.get("github_token"): req.add_header("Authorization", "token " + cfg["github_token"])
    try:
        sha = urllib.request.urlopen(req, timeout=15).read().decode().strip()
        return sha if len(sha) == 40 else None
    except Exception:
        return None


def gh(cfg, path, timeout=30, commit=None):
    ref = commit or gh_commit(cfg)
    url = f"https://raw.githubusercontent.com/{REPO}/{ref or BRANCH}/{path}"
    req = urllib.request.Request(url if ref else url + "?t=%d" % time.time(), headers={"User-Agent": "OutlawsCompanion", "Cache-Control": "no-cache"})
    if cfg.get("github_token"): req.add_header("Authorization", "token " + cfg["github_token"])
    return urllib.request.urlopen(req, timeout=timeout)


def original_toolkit(tk_dir):
    """the PC the toolkit is made from (BotLauncher.exe or a git checkout there): never replaced"""
    return os.path.exists(os.path.join(tk_dir, "BotLauncher.exe")) or os.path.isdir(os.path.join(tk_dir, ".git")) \
        or os.path.isdir(os.path.join(tk_dir, "Companion-Launcher"))


def toolkit_ready(tk_dir):
    return all(os.path.exists(os.path.join(tk_dir, n)) for n in ("botctl.py", "horde.py", "launchertools.py", "brain.py"))


def extract_toolkit(zip_path, tk_dir):
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            parts = n.replace("\\", "/").split("/")
            if ".." in parts or not parts[-1]:
                continue
            if len(parts) > 1 and "/".join(parts[:-1]) not in TOOLKIT_DIRS:
                continue
            z.extract(n, tk_dir)


def python_cmd():
    local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python")
    found = sorted((os.path.join(local, d, "python.exe") for d in (os.listdir(local) if os.path.isdir(local) else [])
                    if d.lower().startswith("python3")), reverse=True)
    for c in [["py", "-3"], ["python"]] + [[p] for p in found if os.path.exists(p)]:
        try:
            if subprocess.run(c + ["-c", "import venv,tkinter"], capture_output=True, creationflags=NO_WINDOW).returncode == 0: return c
        except OSError: pass
    return None


def install_python(say):
    """Python 3.12 from python.org for this Windows user only (no admin), its signature checked first"""
    arm = "ARM64" in (os.environ.get("PROCESSOR_ARCHITEW6432", "") + os.environ.get("PROCESSOR_ARCHITECTURE", "")).upper()
    url = PY_URL.replace("-amd64.exe", "-arm64.exe") if arm else PY_URL
    exe = os.path.join(tempfile.mkdtemp(prefix="outlaws_comp_"), url.rsplit("/", 1)[1])
    say("Downloading Python 3 from python.org (about 25 MB)...")
    with urllib.request.urlopen(url, timeout=600) as r, open(exe, "wb") as out: shutil.copyfileobj(r, out)
    ps = ("$s = Get-AuthenticodeSignature -LiteralPath '%s'; if ($s.Status -eq 'Valid' -and "
          "$s.SignerCertificate.Subject -match 'Python Software Foundation') { exit 0 } else { exit 1 }" % exe.replace("'", "''"))
    if subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, creationflags=NO_WINDOW).returncode != 0:
        raise RuntimeError("the Python download isn't signed by the Python Software Foundation")
    say("Installing Python 3.12 for your Windows user (a minute or two)...")
    r = subprocess.run([exe, "/quiet", "InstallAllUsers=0", "Include_launcher=1", "InstallLauncherAllUsers=0", "Include_tcltk=1",
                        "Include_pip=1", "Include_test=0", "Include_doc=0", "PrependPath=1", "Shortcuts=0", "AssociateFiles=0"], creationflags=NO_WINDOW)
    shutil.rmtree(os.path.dirname(exe), ignore_errors=True)
    if r.returncode not in (0, 3010):
        raise RuntimeError("the Python installer stopped (code %d)" % r.returncode)
    return python_cmd()


def friend_toolkit(dest, say):
    """a friend who is hosting: their launcher hands out its toolkit (/toolkit.zip). Over ZeroTier or the home network."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1); s.settimeout(1.0)
        s.sendto(b"OUTLAWS?", ("255.255.255.255", 8766))
        end = time.time() + 4
        while time.time() < end:
            try:
                _d, a = s.recvfrom(4096)
            except OSError:
                continue
            say(f"Getting the toolkit from {a[0]}...")
            with urllib.request.urlopen(f"http://{a[0]}:8765/toolkit.zip", timeout=300) as r, open(dest, "wb") as out:
                shutil.copyfileobj(r, out)
            return True
    except OSError:
        pass
    return False


class Setup:
    """The first start on a PC: a small window that says what is happening while the game folder, the toolkit and Python
    are sorted out. ok -> the toolkit folder to run the launcher on."""
    def __init__(self, cfg):
        import tkinter as tk
        from tkinter import ttk
        self.cfg, self.tk_dir, self.err = cfg, None, None
        self.root = tk.Tk(); self.root.title(APP); self.root.resizable(False, False)
        self.root.configure(bg="#f3e6c8")
        tk.Label(self.root, text="BOT LAUNCHER COMPANION", font=("Georgia", 20, "bold"), fg="#a8480f", bg="#f3e6c8").pack(padx=30, pady=(18, 4))
        self.msg = tk.StringVar(value="Getting ready...")
        tk.Label(self.root, textvariable=self.msg, wraplength=440, justify="left", bg="#f3e6c8", fg="#33241a").pack(padx=30, pady=(4, 10))
        self.bar = ttk.Progressbar(self.root, mode="indeterminate", length=440); self.bar.pack(padx=30, pady=(0, 22)); self.bar.start(12)
        threading.Thread(target=self.work, daemon=True).start()
        self.root.mainloop()

    def say(self, text):
        log(text)
        try: self.root.after(0, lambda: self.msg.set(text))
        except Exception: pass

    def ask(self, fn):
        """run fn (a dialog) on the window's own thread and wait for its answer"""
        box, ev = [], threading.Event()
        self.root.after(0, lambda: (box.append(fn()), ev.set()))
        ev.wait()
        return box[0]

    def work(self):
        from tkinter import filedialog, messagebox, simpledialog
        try:
            game = self.cfg.get("game", "")
            if not os.path.exists(os.path.join(game, "OutlawsAHOM.exe")):
                self.say("Looking for Outlaws on this PC...")
                found = find_games()
                if len(found) == 1:
                    game = found[0]
                elif len(found) > 1:
                    pick = self.ask(lambda: simpledialog.askinteger(APP, "Outlaws is in more than one place:\n\n" +
                                    "\n".join(f"{i + 1}. {f}" for i, f in enumerate(found)) + "\n\nWhich one (number)?", minvalue=1, maxvalue=len(found)))
                    game = found[(pick or 1) - 1]
                else:
                    game = self.ask(lambda: filedialog.askdirectory(title="Where is Outlaws installed? (the folder with OutlawsAHOM.exe)")) or ""
                if not os.path.exists(os.path.join(game, "OutlawsAHOM.exe")):
                    raise RuntimeError("Outlaws wasn't found (no OutlawsAHOM.exe in that folder)")
                self.cfg["game"] = game; save(self.cfg)
            if not writable(game):
                if not self.ask(lambda: messagebox.askyesno(APP, f"Windows protects your game folder:\n{game}\n\nThe bots and the mods have to go "
                                                                 "into it. Allow your Windows user to change this folder? Windows will ask you once.")):
                    raise RuntimeError("the game folder is protected")
                run_elevated("icacls", '"%s" /grant *S-1-5-32-545:(OI)(CI)M /T /Q' % game)
                if not writable(game): raise RuntimeError("the game folder couldn't be unlocked: run the Companion as administrator once")
            tk_dir = os.path.join(game, "botctl")
            os.makedirs(tk_dir, exist_ok=True)
            ver = None                                     # GitHub's toolkit: newer than this PC's? (the old Companion's
            if not original_toolkit(tk_dir):               #  toolkit, too, is replaced by the one the new app needs)
                try: ver = gh(self.cfg, "toolkit/version.txt", 30).read().decode().split()[:3]
                except Exception: ver = None
            if not original_toolkit(tk_dir) and (not toolkit_ready(tk_dir) or not os.path.exists(os.path.join(tk_dir, "launcher.py"))
                                                 or (ver and ver[0] != self.cfg.get("toolkit_stamp"))):
                tmp = os.path.join(tempfile.mkdtemp(prefix="outlaws_comp_"), "t.zip")
                try:
                    if not ver: raise RuntimeError("GitHub out of reach")
                    stamp, sha, size = ver
                    self.say(f"Downloading the bot toolkit from GitHub ({int(size) // 2**20} MB)...")
                    with gh(self.cfg, "toolkit/toolkit.zip", 600) as r, open(tmp, "wb") as out: shutil.copyfileobj(r, out)
                    if sha256(tmp) != sha.lower(): raise RuntimeError("the download was damaged")
                    self.cfg["toolkit_stamp"] = stamp
                except Exception as e:
                    self.say(f"GitHub didn't hand out the toolkit ({e}); asking a friend who is hosting...")
                    if not friend_toolkit(tmp, self.say):
                        raise RuntimeError("the bot toolkit couldn't be downloaded (GitHub out of reach and nobody hosting)")
                extract_toolkit(tmp, tk_dir); save(self.cfg)
                shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
            venv = os.path.join(tk_dir, ".venv", "Scripts", "python.exe")
            if not os.path.exists(venv):
                self.say("Setting up Python for the bots (a few minutes the first time)...")
                py = python_cmd()
                if not py:
                    try: py = install_python(self.say)
                    except Exception as e: self.say(f"Couldn't install Python by itself ({e}).")
                if py:
                    subprocess.run(py + ["-m", "venv", os.path.join(tk_dir, ".venv")], check=True, creationflags=NO_WINDOW)
                    req = os.path.join(tk_dir, "requirements.txt")
                    if os.path.exists(req):
                        r = subprocess.run([venv, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", req],
                                           capture_output=True, text=True, creationflags=NO_WINDOW)
                        if r.returncode != 0: log("pip: " + (r.stderr or "")[-400:])
                else:
                    self.say("Python 3 is still missing: the Companion opens, but bots and ghosts need Python (python.org).")
                    time.sleep(3)
            self.tk_dir = tk_dir
        except Exception as e:
            self.err = str(e); log(f"setup: {e}")
        finally:
            self.root.after(0, self.root.destroy)


def game_running():
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True, creationflags=NO_WINDOW).stdout.lower()
        return "outlawsahom.exe" in out
    except Exception:
        return True


def update(app):
    """the Update button: a newer OutlawsCompanion.exe on GitHub (companion\\version.txt: "<version> <sha256>") is downloaded,
    checked, swapped in once this copy has closed, and started"""
    from tkinter import messagebox
    cfg = load()
    def work():
        try:
            commit = gh_commit(cfg)
            num, sha = gh(cfg, "companion/version.txt", 30, commit=commit).read().decode().split()[:2]
            if int(num) <= VERSION:
                app.after(0, lambda: messagebox.showinfo("Update", f"The Companion is up to date (version {VERSION}).")); return
            if game_running():
                app.after(0, lambda: messagebox.showinfo("Update", "A new version is ready: close the game first, then press Update again.")); return
            if not getattr(sys, "frozen", False):
                app.after(0, lambda: messagebox.showinfo("Update", f"Version {num} is on GitHub (this copy runs from source).")); return
            exe = sys.executable; new = exe + ".new"
            app.after(0, lambda: app.say(f"downloading Companion version {num}..."))
            with gh(cfg, "companion/OutlawsCompanion.exe", 300, commit=commit) as r, open(new, "wb") as f: shutil.copyfileobj(r, f)
            if sha256(new) != sha.lower():
                os.remove(new); app.after(0, lambda: messagebox.showerror("Update", "The download was damaged; nothing changed. Try again.")); return
            bat = os.path.join(tempfile.gettempdir(), "outlaws_companion_update.cmd")
            lg = os.path.join(DATA, "update_log.txt")
            lines = ["@echo off", "set PYINSTALLER_RESET_ENVIRONMENT=1", "set n=0", ":wait"]
            for pid in {os.getpid(), os.getppid()}:
                lines.append('tasklist /FI "PID eq %d" /NH 2>nul | find " %d " >nul && (ping -n 2 127.0.0.1 >nul & goto wait)' % (pid, pid))
            lines += [":again", 'move /y "%s" "%s" >nul 2>&1 && goto done' % (new, exe), "set /a n+=1", "if %n% geq 90 goto failed",
                      "ping -n 3 127.0.0.1 >nul", "goto again", ":done", 'echo %%date%% %%time%% updated to %s>> "%s"' % (num, lg),
                      'explorer.exe "%s"' % exe, "goto end", ":failed", 'echo %%date%% %%time%% could not replace the app file>> "%s"' % lg,
                      'explorer.exe "%s"' % exe, ":end", 'del "%~f0"']
            with open(bat, "w") as f: f.write("\r\n".join(lines) + "\r\n")
            subprocess.Popen(["cmd", "/c", bat], creationflags=NO_WINDOW, env=clean_env())
            app.after(500, app.destroy)
        except Exception as e:
            app.after(0, lambda: messagebox.showerror("Update", f"Update failed: {e}"))
    threading.Thread(target=work, daemon=True).start()


def after_start(app):
    """once the window is up: mods a game left behind come out (the Companion's own journal, and the Horde / Christmas look),
    and the Update buttons say so when a new version is out"""
    def work():
        here = os.environ.get("BOT_HOME") or ""
        running = game_running()
        if not running and here:
            try:
                if here not in sys.path: sys.path.insert(0, here)
                import companion
                companion.restore_files()
            except Exception as e:
                log(f"leftover mods: {e}")
            py = os.path.join(here, ".venv", "Scripts", "python.exe")
            if os.path.exists(py) and os.path.exists(os.path.join(here, "companionhost.py")):
                subprocess.Popen([py, os.path.join(here, "companionhost.py"), "--cleanup"], cwd=here, creationflags=NO_WINDOW)
        try:
            num = int(gh(load(), "companion/version.txt", 20).read().decode().split()[0])
            if num > VERSION:
                app.after(0, lambda: [b.configure(text="Update available") for b in getattr(app, "upd_btns", [])
                                      if str(b.cget("text")) == "Update Launcher"])
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()


def main():
    cfg = load()
    s = Setup(cfg)
    if not s.tk_dir:
        try:
            from tkinter import messagebox
            messagebox.showerror(APP, f"The Companion couldn't get ready: {s.err or 'stopped'}.\n\nSee {os.path.join(DATA, 'companion_log.txt')}")
        except Exception:
            pass
        return 1
    sys.modules.setdefault("companion_app", sys.modules[__name__])   # (launcher.py calls back: after_start, update)
    os.environ["OUTLAWS_COMPANION"] = "1"
    os.environ["BOT_HOME"] = s.tk_dir
    log(f"starting on {s.tk_dir}")
    import launcher
    return launcher.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
