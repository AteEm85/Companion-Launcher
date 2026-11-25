"""Outlaws bot launcher: a small window to set up the bots and start a LAN game.

Put BotLauncher.exe (or run this file) in the botctl folder, next to botctl.py and bots.cmd.
  Launch Game  - starts the bots (the first one hosts) with the settings below
  Join Game    - starts your own OutlawsAHOM.exe and joins the LAN game on this PC
Only the standard library is used, so it builds into one .exe with build_exe.cmd.
"""
import json
import re
import os
import random
import subprocess
import tempfile
import sys
import threading
import time
import tkinter as tk
import datetime, hashlib, http.server, platform, shutil, socket, urllib.request, zipfile    # (for launchertools.py: a built exe only carries what this file imports)
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
try:
    import modserver                      # lets friends' Outlaws Companion fetch the mods a hosted game needs
except Exception:
    modserver = None

# (25 Nov, the user) the Outlaws Companion is this same launcher with a different face: "Companion App" at the top left,
# only the Play, Custom Games and Online tabs, Survival Horde alone in Custom Games, its own updates from GitHub
# (companion_app.py, the Companion's start: it finds the game and its bot toolkit first, then sets BOT_HOME).
COMPANION = os.environ.get("OUTLAWS_COMPANION") == "1" or "--companion" in sys.argv or \
    (getattr(sys, "frozen", False) and "companion" in Path(sys.executable).stem.lower())
# The folder with botctl.py: BOT_HOME (the Companion: <game>\botctl), else next to the .exe when frozen, else next to this file.
HERE = Path(os.environ["BOT_HOME"]).resolve() if os.environ.get("BOT_HOME") else \
    Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def ghost_script(here):
    """The ghost player's script: botctl\\ghost.py when it is the ghost player (a flat copy), else netre\\ghost.py. In the repository
    botctl\\ghost.py is the first prototype (4 Nov), which can't get past the lobby: wherever both were there it was the one
    started, and no ghost ever showed up (11 Nov). None: neither."""
    for p in (here / "ghost.py", here / "netre" / "ghost.py"):
        try:
            if p.exists() and b"class FightWalker" in p.read_bytes():
                return p
        except OSError:
            pass
    return None


def _disk(name):
    """Load a helper module (darkitems, silvergat ...) from its .py file next to the launcher, every time. A frozen BotLauncher.exe
    carries its own copy of every module it imports, made when it was last built, and that copy would win over a newer file."""
    import importlib
    import importlib.util
    p = HERE / f"{name}.py"
    if not p.exists():
        return importlib.import_module(name)
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    spec = importlib.util.spec_from_file_location(name, str(p))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m
GAME_DIR = HERE.parent
GAME_EXE = GAME_DIR / "OutlawsAHOM.exe"
SETTINGS = HERE / "launcher_settings.json"
BOT_PRESETS = HERE / "bot_presets.json"
PRESET_SLOTS = 10

# ---- what the lists offer (edit here to add maps later) -------------------------------------------------------------
MAPS = [("Buckshotville", "civpow2"), ("Dry Gulch", "drygulch"), ("The Fort", "fort"), ("The Granary", "granary"),
        ("Hargrove's Pattern Repair", "hargrove"), ("Bunkers", "lilbatl"), ("Simms At Night", "mltsimms"), ("Orleans", "orleans"),
        ("Thud Tower", "thudtowr"), ("Sanctuary", "town_2p"), ("Wilderness", "wilderns"),
        ("The Dip (custom)", "TheDip1s"), ("Stone (custom)", "Stone")]      # (label, botctl --map value)
# The game modes each map has (from its .rcm file), by botctl --mode value.
_STD = {"dm", "ctf", "chicken", "team"}
MAP_MODES = {"civpow2": _STD, "drygulch": _STD, "fort": _STD, "granary": _STD, "hargrove": _STD, "lilbatl": {"dm", "chicken", "team"}, "mltsimms": _STD,
             "orleans": _STD, "thudtowr": {"dm", "team"}, "town_2p": {"dm", "ctf", "chicken", "tag"}, "wilderns": _STD,
             "TheDip1s": {"dm", "ctf", "chicken", "team"}, "Stone": {"dm", "ctf", "team"}}
# Every other map comes from maps_data.json next to the launcher (written by mapscan.py, which reads each map's .rcm:
# its name and game modes). Re-run mapscan.cmd after adding maps; no rebuild needed.
_LETTER_MODE = {"D": "dm", "M": "team", "C": "ctf", "T": "tag", "K": "chicken", "S": "doc"}
try:
    _have = {k for _, k in MAPS}
    _extra = []
    for _m in json.loads((HERE / "maps_data.json").read_text()):
        _modes = {_LETTER_MODE[c] for c in _m.get("modes", "") if c in _LETTER_MODE}
        if _m["key"] in MAP_MODES and _m["key"] in _have:
            if _modes:
                MAP_MODES[_m["key"]] = _modes              # the .rcm is the truth for the maps listed above too
            continue
        if _m.get("skip") or not _modes:
            continue
        MAP_MODES[_m["key"]] = _modes
        _extra.append((_m["name"] + (" (custom)" if _m.get("kind") == "custom" else ""), _m["key"]))
    MAPS = MAPS + sorted(_extra, key=lambda t: t[0].lower())
except Exception:
    pass
# Standard maps (the ones that come with the game) and custom maps are kept apart: the Map box has a group box beside it.
STANDARD_KEYS = {"civpow2", "drygulch", "fort", "granary", "hargrove", "lilbatl", "mltsimms", "orleans", "thudtowr",
                 "town_2p", "wilderns"}
_std = [(l, k) for l, k in MAPS if k in STANDARD_KEYS]
_cust, _seen = [], {l for l, _ in _std}
for _l, _k in MAPS:
    if _k in STANDARD_KEYS:
        continue
    _base = _l.replace(" (custom)", "").strip() or _k
    _lab = _base if _base not in _seen else f"{_base} ({_k})"       # two maps with one name: tell them apart
    _seen.add(_lab)
    _cust.append((_lab, _k))
_cust.sort(key=lambda t: t[0].lower())
MAPS = _std + _cust
MAP_GROUPS = {"Standard maps": [l for l, _ in _std], "Custom maps": [l for l, _ in _cust]}
MAP_KEY = {l: k for l, k in MAPS}


# Maps whose .rcm lists no Team Deathmatch (Sanctuary): a Ghosts tab game in that mode puts a loose copy of the .rcm beside the
# game with the mode added (a loose .rcm overrides the archives, as horde.py does for its Horde games) and removes it when the game
# is over, so other games on the map are untouched.
TEAM_RCM_ADD = {"town_2p": (b"MODES:  DKCT", b"MODES:  DKCTM")}


def add_team_rcm(level):
    """Put the loose .rcm with Team Deathmatch in place; returns its path (to remove after the game) or None (the player's own
    loose .rcm is never touched)."""
    if level not in TEAM_RCM_ADD:
        return None
    try:
        import lvt
        path = GAME_DIR / f"{level}.rcm"
        if path.exists():
            return None
        data = lvt.read_from_labs(f"{level}.rcm")
        old, new = TEAM_RCM_ADD[level]
        if not data or old not in data:
            return None
        path.write_bytes(data.replace(old, new))
        return path
    except Exception:
        return None


def horde_maps(names):
    """Survival Horde is a Team Deathmatch game: only the maps that have that mode. Sanctuary has none in its .rcm, but
    horde.py adds it for the length of the game."""
    return [n for n in names if "team" in MAP_MODES.get(MAP_KEY.get(n), ()) or MAP_KEY.get(n) == "town_2p"]


MODES = [("Deathmatch", "dm"), ("Team deathmatch", "team"), ("Capture the flag", "ctf"),
         ("Tag", "tag"), ("Chicken", "chicken"), ("Doc", "doc")]
RANKED_KEYS = {"Repair": "hargrove", "Dry Gulch": "drygulch", "Bunkers": "lilbatl"}
RANKED_LABELS = ["Repair", "Dry Gulch", "Bunkers"]
TEAM_MODES = ("ctf", "team", "doc")                              # modes where red/blue matters
# Ghost names by character (the user's lists, 11 Nov): each ghost gets one at random, no two alike in a match. Empty: the old names.
GHOST_NAME_POOL = {
    "sanchez": ["GHOST_WaRRioR", "GHOST_Legend", "GHOST_MRBO", "GHOST_Gampolero", "GHOST_Dotard"],
    "bob": ["GHOST_Shifty", "GHOST_Dotard", "GHOST_WaRRioR"],
    "james": ["GHOST_LadyOL", "GHOST_MissVic", "GHOST_Jim"],
    "drdeath": ["GHOST_MissVic", "GHOST_Shifty", "GHOST_Dirty", "GHOST_Dingle", "GHOST_Legend"],
    "twofeathers": ["GHOST_WaRRioR", "GHOST_Dotard", "GHOST_Shifty"],
    "mary": ["GHOST_Dirty", "GHOST_Crow", "GHOST_Dingle", "GHOST_MRBO"],
}
CHARACTERS = [("Sanchez", "sanchez"), ("James Anderson", "james"), ("Two Feathers", "twofeathers"),
              ("Dr Death", "drdeath"), ("Bob", "bob"), ("Mary", "mary")]
BEHAVIOURS = ["default", "sapper", "dynamiter", "dotard", "legend", "allrounder", "rifleman", "shotgunner",
              "shotgunner_doc", "knifer", "sniper", "brawler", "pistolero"]
NIGHTMARE_CHARS = ["james", "mary", "bob", "drdeath"]     # Sanchez and Two Feathers (the chief) have no nightmare version
CHAR_RANDOM = "Random"      # with the real presets, picks from that preset's own characters
CHAR_DEFAULT = CHAR_RANDOM
BEH_DEFAULT = "Preset"
TEAMS = ["Auto", "Red", "Blue"]
SPLIT_PER_BOT = "Per-bot settings"
WINDOW_MODES = [("Hidden (off screen, no taskbar button)", "hidden"), ("Minimized", "minimized"),
                ("Visible, in the background", "visible")]

# The preset bots, in the order bot1 .. bot7 (name, behaviour, characters it picks from).
PRESETS = [
    ("Dotard", "dotard", ["james", "bob", "sanchez"]),
    ("DTG_Legend", "legend", ["sanchez"]),
    ("T4H_Dingle", "allrounder", ["drdeath", "mary", "bob", "sanchez"]),
    ("T4H_LadyOutlaw", "rifleman", ["james"]),
    ("WAC_Dirtyolero", "shotgunner", ["mary", "drdeath"]),
    ("creature9", "legend", ["sanchez"]),
    ("Noobke", "knifer", ["twofeathers"]),
    ("Bot 8", "default", ["sanchez", "james", "twofeathers", "drdeath", "bob", "mary"]),
    ("Bot 9", "default", ["sanchez", "james", "twofeathers", "drdeath", "bob", "mary"]),
    ("Bot 10", "default", ["sanchez", "james", "twofeathers", "drdeath", "bob", "mary"]),
]
PRESET_BEHAVIOUR = {("WAC_Dirtyolero", "drdeath"): "shotgunner_doc"}
CHAR_LABEL = {v: k for k, v in CHARACTERS}

# Weapons with a strength dropdown each: (label, slot). The sawed-off (6) isn't used by the bots.
WEAPONS = [("Fists", 1), ("Pistol", 2), ("Rifle", 3), ("Shotgun", 4), ("Double barrel", 5), ("TNT", 7),
           ("Knife", 8), ("Gatling", 9)]
LEVELS = ["Strong", "Average", "Weak"]
GHOST_HOST_PLAYERS = 16         # your hosted ghost game's lobby size (you + up to 15 ghosts)
MAX_GHOSTS = 15                 # ghost players on the Ghosts tab (each its own address 127.0.0.<n+1>, slot and player id)
LEANS = [("No lean", None), ("Revolver", 2), ("Rifle", 3), ("Shotgun", 4), ("Double barrel", 5), ("Sawed-off", 6)]   # per bot, next game only
LETTER = {"Strong": "s", "Average": "a", "Weak": "w"}
# Each preset's strengths as designed (see brain.py). Untouched, the preset plays exactly as designed.
_S, _A, _W = "Strong", "Average", "Weak"
#            fists pistol rifle shotgun double TNT knife gatling
BASE_LEVELS = [                         # what the behaviours in brain.py already do
    [_W, _S, _S, _A, _S, _W, _W, _A],      # Dotard: rifle, pistol, double barrel
    [_W, _S, _A, _A, _A, _S, _W, _A],      # DTG_Legend: TNT first, strong pistol
    [_W, _S, _A, _S, _S, _W, _W, _A],      # T4H_Dingle: pistol and shotguns
    [_W, _A, _S, _A, _A, _W, _W, _A],      # T4H_LadyOutlaw: rifle
    [_W, _A, _A, _S, _S, _W, _W, _A],      # WAC_Dirtyolero: shotguns
    [_W, _S, _A, _A, _A, _S, _W, _A],      # creature9: TNT first, strong pistol
    [_W, _A, _A, _A, _A, _W, _S, _A],      # Noobke: knife
    [_W, _A, _A, _A, _A, _W, _W, _A],      # Bot 8-10: plain bots
    [_W, _A, _A, _A, _A, _W, _W, _A],      # Bot 8-10: plain bots
    [_W, _A, _A, _A, _A, _W, _W, _A],      # Bot 8-10: plain bots
]
# The window's defaults: the same, with changes you want on top (a change makes botctl reshape that bot's skill).
LEVELS_VER = 5                                        # bump when PRESET_LEVELS change
PRESET_LEVELS = [list(r) for r in BASE_LEVELS]
PRESET_LEVELS[1] = [_W, _S, _A, _A, _A, _S, _A, _A]   # DTG_Legend: knife average
PRESET_LEVELS[5] = [_W, _S, _A, _A, _A, _S, _A, _A]   # creature9: knife average
PRESET_LEVELS[2] = [_W, _S, _S, _S, _A, _A, _W, _A]   # T4H_Dingle: strong pistol, rifle, shotgun; average double barrel and TNT


DIFFICULTIES = ["Easiest", "Easy", "Normal", "Hard", "Nightmare", "Hell"]   # botctl's lv_easiest .. lv_hell
SHARP = ("Nightmare", "Hell")       # strong with every weapon, one of the nightmare characters
ALL_CHARS = ["sanchez", "james", "twofeathers", "drdeath", "bob", "mary"]


def preset_row(i, real):
    """(name, behaviour, characters) of bot i (1-based): the real presets, or a plain 'Bot i'."""
    return PRESETS[i - 1] if real else (f"Bot {i}", "default", ALL_CHARS)


def base_levels(i, real):
    return BASE_LEVELS[i - 1] if real else BASE_LEVELS[7]


def default_levels(i, real):
    return PRESET_LEVELS[i - 1] if real else BASE_LEVELS[7]


def clean_name(text, fallback):
    """The game keeps names to 15 characters; no ':' or '=' (they are botctl's separators)."""
    text = "".join(c for c in text if c not in ":=\"'").strip()[:15]
    return text or fallback


class CharPicker(ttk.Menubutton):
    """A dropdown for a bot's character that can gray out single entries (a ttk Combobox can't): the same get / set /
    configure(state=) as the Combobox it replaced, plus block(labels) for the entries to gray out."""

    def __init__(self, master, values, width=19):
        self.var = tk.StringVar(value=values[0])
        super().__init__(master, textvariable=self.var, width=width, direction="below")
        self.values, self.blocked = list(values), set()
        self.menu = tk.Menu(self, tearoff=0)
        self["menu"] = self.menu
        self._build()

    def _build(self):
        self.menu.delete(0, "end")
        for v in self.values:
            self.menu.add_command(label=v, command=lambda v=v: self.var.set(v),
                                  state="disabled" if v in self.blocked else "normal")

    def get(self):
        return self.var.get()

    def set(self, v):
        self.var.set(v)

    def block(self, labels):
        """Gray out these entries (and fall back to Random if one of them is the current pick)."""
        self.blocked = set(labels)
        self._build()
        if self.var.get() in self.blocked:
            self.var.set(CHAR_RANDOM)

    def configure(self, cnf=None, **kw):
        if "state" in kw:
            kw["state"] = "disabled" if kw["state"] == "disabled" else "normal"
        return super().configure(cnf, **kw) if cnf else super().configure(**kw)


def bot_spec(i, name, char_label, beh_label, team_label, mode, levels=None, tnt_only=False, difficulty="Normal",
             real=False, lean=None):
    """The botctl argument for bot i (1-based). With the real presets and nothing changed, a bot stays 'botN' so
    botctl picks the character (and its behaviour) at random the way it always did. `levels` is the list of
    strengths in WEAPONS order. Difficulty: tags the bot (lv_easiest .. lv_hell); Nightmare and Hell also make it
    strong with every weapon and one of James, Mary, Bob or Dr Death (TNT-only wins over it)."""
    import random
    preset_name, preset_beh, preset_chars = preset_row(i, real)
    name = clean_name(name, preset_name)
    team = team_label.lower() if (mode in TEAM_MODES and team_label in ("Red", "Blue")) else None
    lv = "lv_" + (difficulty or "Normal").lower()
    if tnt_only:                                            # everyone Sanchez, TNT then knife or fists
        return ":".join([f"bot{i}", "sanchez", "tnt_only"] + ([team] if team else []) + ([lv] if lv else [])) + "=" + name
    if difficulty in SHARP:                                 # sharp, strong with everything, one of four characters
        pick = dict(CHARACTERS).get(char_label)             # the one chosen, unless it isn't a nightmare character
        char = pick if pick in NIGHTMARE_CHARS else random.choice(NIGHTMARE_CHARS)
        lean_slot = dict(LEANS).get(lean) if lean else None
        return ":".join([f"bot{i}", char, "nightmare"] + ([team] if team else []) + [lv]
                        + ["wp_" + "".join(f"{slot}s" for _, slot in WEAPONS)]
                        + ([f"lean_{lean_slot}"] if lean_slot else [])) + "=" + name
    levels = levels or default_levels(i, real)
    changed = list(levels) != base_levels(i, real)
    lean_slot = dict(LEANS).get(lean) if lean else None
    plain = (not lean_slot and real and not lv and name == preset_name and char_label == CHAR_DEFAULT and beh_label == BEH_DEFAULT
             and not changed)
    if plain:
        return f"bot{i}" + (f":{team}" if team else "")
    if char_label == CHAR_RANDOM:
        char = random.choice(preset_chars)
    else:
        char = dict(CHARACTERS)[char_label]
    beh = PRESET_BEHAVIOUR.get((preset_name, char), preset_beh) if beh_label == BEH_DEFAULT else beh_label
    tags = [f"bot{i}", char, beh] + ([team] if team else []) + ([lv] if lv else [])
    if changed:
        tags.append("wp_" + "".join(f"{slot}{LETTER[lv_]}" for (_, slot), lv_ in zip(WEAPONS, levels)))
    if lean_slot:
        tags.append(f"lean_{lean_slot}")
    return ":".join(tags) + "=" + name


def melee_spec(i, name, team_label, mode, difficulty="Normal"):
    """Melee Only Deathmatch: the bots are Mary and Two Feathers (the chief), in turn. Mary is a boxer (her fists 7 times
    in 10, now and then the knife), the chief a brawler (the knife, and fists only when he has none); neither picks up
    or uses a gun or TNT. Nightmare and Hell bots are strong with the knife and the fists."""
    name = clean_name(name, f"Bot {i}")
    team = team_label.lower() if (mode in TEAM_MODES and team_label in ("Red", "Blue")) else None
    char, beh = ("mary", "boxer") if i % 2 else ("twofeathers", "brawler")
    sharp = difficulty in SHARP                 # ("nightmare" is a behaviour too: it goes first, so the melee one wins)
    tags = [f"bot{i}", char] + (["nightmare"] if sharp else []) + [beh] + ([team] if team else []) \
        + ["lv_" + (difficulty or "Normal").lower()] + (["wp_8s1s"] if sharp else [])
    return ":".join(tags) + "=" + name


def build_args(cfg):
    """botctl's command line from the window's settings (a plain dict, so it can be tested without a window)."""
    n = max(1, min(10, int(cfg["count"])))
    mode = cfg["mode"]
    specs = [melee_spec(i, b["name"], b["team"], mode, b.get("diff") or cfg.get("difficulty", "Normal"))
             if cfg.get("melee_only") else
             bot_spec(i, b["name"], b["char"], b["beh"], b["team"], mode, b.get("levels"), bool(cfg.get("tnt_only")),
                      b.get("diff") or cfg.get("difficulty", "Normal"), bool(cfg.get("real_names")), b.get("lean"))
             for i, b in enumerate(cfg["bots"][:n], 1)]
    users = int(cfg.get("wait_users") or (1 if cfg.get("wait_for_me") else 0))
    wait = (n - 1) + users
    args = specs + ["--host", "--mode", mode, "--map", cfg["map"], "--fight",
                    "--wait-for", str(wait), "--wait-max", "900" if users > 1 else "300" if users else "120",
                    "--size", cfg.get("size") or "320x180", "--fps", str(cfg.get("fps") or 60)]
    if str(cfg.get("timelimit", "")).strip():
        args += ["--timelimit", str(int(cfg["timelimit"]))]
    if str(cfg.get("fraglimit", "")).strip():
        args += ["--fraglimit", str(int(cfg["fraglimit"]))]
    if cfg.get("friendly_fire"):
        args.append("--friendly-fire")
    args += ["--window-mode", cfg.get("window_mode") or "hidden"]
    if not cfg.get("taunts", True):
        args.append("--no-taunts")
    if not cfg.get("marshall_law", True):
        args.append("--no-marshall-law")
    if cfg.get("real_name"):
        args.append("--real-name")        # the host copy shows as the PC's own Steam account (a ghost rotation's host)
    if cfg.get("online"):
        args += ["--online", "--wait-for", "0", "--maxplayers", str(int(cfg.get("maxplayers") or 16))]   # the game's online lobbies: one host copy, your account
    elif cfg.get("private"):
        args.append("--private")          # only you and the bots: the game's own private lobby, not offered to friends
    if not cfg.get("bench", True):
        args.append("--no-bench")
    return args


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def own_game_pids():
    """Pids of OutlawsAHOM.exe copies that are yours: the bots' copies all run with -userpath. None if Windows
    can't be asked."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='OutlawsAHOM.exe'\" | "
          "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.CommandLine }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                           text=True, timeout=20, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [line.split("|", 1)[0] for line in r.stdout.splitlines()
            if line.strip() and "-userpath" not in line]


ROT_RANDOM_DIFFS = ["Easy", "Normal", "Hard", "Nightmare"]    # Map Rotation ghosts on Random: no Easiest, no Hell ...
ROT_RANDOM_CAPS = {"Nightmare": 1, "Hard": 2}                  # ... and at most this many of these in a game
ROT_RANDOM_WEIGHTS = {"Easy": 2, "Normal": 4, "Hard": 2, "Nightmare": 1}   # ... and Normal the most common
ROT_GHOST_DELAY = 10         # Map Rotation with ghosts: seconds after the next lobby is up before the ghosts join it
ROT_GHOST_DELAY_YOU = 10    # ... when you play too: your game gets in first (ghosts joining while it loads drop you to the menu)
ONLINE_HOST_WAIT = 240      # seconds for the online host's next lobby: online sign-up plus a slow map load (a PC without a GPU)
ONLINE_MAX_PLAYERS = 16     # Map Rotation's Online game: the lobby size (the host copy is one of them, sitting in the lobby)
HOLD_AFTER_END = 20          # seconds after the last round is won before the map files go back


def venv_py(wait=0.0):
    """The Python that runs botctl/rotation/ghosts: the bots' .venv (bots.cmd makes it on the first launch, with Frida).
    Never the launcher's own exe: frozen, sys.executable is BotLauncher.exe and would just open another launcher.
    wait: seconds to wait for bots.cmd to finish making the .venv."""
    v = HERE / ".venv" / "Scripts" / "python.exe"
    end = time.time() + wait
    while not v.exists() and time.time() < end:
        time.sleep(1.0)
    if v.exists():
        return str(v)
    if not getattr(sys, "frozen", False):
        return sys.executable
    import shutil
    return shutil.which("python") or shutil.which("py") or "python"


def bot_game_pids():
    """Pids of the bots' game copies (the ones with -userpath), or None if Windows can't be asked."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='OutlawsAHOM.exe'\" | "
          "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.CommandLine }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                           text=True, timeout=20, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [int(line.split("|", 1)[0]) for line in r.stdout.splitlines()
            if line.strip() and "-userpath" in line and line.split("|", 1)[0].isdigit()]


def trim_working_sets(pids):
    """Ask Windows to move each process's idle memory out of RAM (EmptyWorkingSet). What a bot needs comes
    back by itself; the rest stays in the page file. Returns how many processes were trimmed."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    psapi.EmptyWorkingSet.argtypes = (wintypes.HANDLE,)
    done = 0
    for pid in pids:
        h = k32.OpenProcess(0x0100 | 0x0400, False, pid)           # PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION
        if h:
            if psapi.EmptyWorkingSet(h):
                done += 1
            k32.CloseHandle(h)
    return done


def zerotier_ip():
    """The IPv4 address of this PC's ZeroTier adapter, or None."""
    ps = ("Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -like '*ZeroTier*' } | "
          "Select-Object -First 1 -ExpandProperty IPAddress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                           timeout=20, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    ip = (r.stdout or "").strip().splitlines()
    return ip[0].strip() if ip and ip[0].strip().count(".") == 3 else None


ZT_SERVICE = "ZeroTierOneService"
ZT_NET = "b6079f73c6535907"            # the games' ZeroTier network: every launcher and the Companion join it
ZT_MSI = "https://download.zerotier.com/dist/ZeroTier%20One.msi"
ZT_CLI = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "ZeroTier" / "One" / "zerotier-cli.bat"


def zt_state():
    """'missing' (not installed), 'on' or 'off'."""
    try:
        r = subprocess.run(["sc", "query", ZT_SERVICE], capture_output=True, text=True, timeout=15,
                           creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return "missing"
    if r.returncode != 0:
        return "missing"
    return "on" if "RUNNING" in (r.stdout or "") else "off"


def zt_joined():
    """True when this PC is joined to the games' network (ZeroTier says OK)."""
    try:
        out = subprocess.run([str(ZT_CLI), "listnetworks"], capture_output=True, text=True, timeout=15,
                             creationflags=NO_WINDOW).stdout
        return any(ZT_NET in line and "OK" in line for line in out.splitlines())
    except Exception:                                     # noqa: BLE001
        return False


def run_elevated(exe, args):
    """Run a program as administrator (Windows asks you to allow it) and wait for it to finish. `args` is one
    string, written exactly as it would be on a command line."""
    ps = (f"Start-Process -FilePath '{exe}' -ArgumentList '{args.replace(chr(39), chr(39) * 2)}' "
          "-Verb RunAs -Wait -WindowStyle Hidden")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                       creationflags=NO_WINDOW)
    return r.returncode == 0


def download(url, dest):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                             "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)


CHAR_NUMBER = {"Sanchez": 1, "James Anderson": 2, "Two Feathers": 3, "Dr Death": 4, "Bob": 5, "Mary": 6}      # mp_defaultChar (the character menu's order)


SCREEN_MODES = [("Exclusive full screen", "exclusive"), ("Full screen", "full"), ("Borderless window", "borderless"), ("Window", "window")]
SCREEN_ARGS = {                       # (flags, the game's v_windowmode: 0 windowed, 1 borderless, 2 fullscreen, 3 exclusive)
    "window": (["-window", "-width", "1280", "-height", "720"], "0"),
    "borderless": ([], "1"),
    "full": (["-fullscreen"], "2"),       # full screen at the desktop's resolution
    "exclusive": ([], "3"),               # the game: 0 Windowed, 1 Borderless, 2 Fullscreen, 3 Exclusive Fullscreen; full screen at the game's own resolution (no size given: yours, if you changed it)
}


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


GHOST_MODE_ID = {"ctf": 1, "tag": 2, "chicken": 3, "doc": 4, "team": 5, "dm": 6}      # mp_gamemode values (botctl.MODES)


def join_command(char=None, screen=None, horde=False, host=None):
    """Your own game: your normal profile and settings (no -userpath), joining the LAN game on this PC.
    char: a character name from CHARACTERS: the game starts with that character picked.
    screen: window / borderless / full: the display mode it starts in.
    horde: a Survival Horde game: the Survival look only exists in the Vulkan shaders, so the game runs on Vulkan, and
    full screen becomes borderless full screen (the banners can't be drawn over a game in exclusive full screen).
    The game reads ONE -setvar block (a second one is ignored, which lost the character before), so all go in one."""
    if horde and screen in ("full", "exclusive"):
        screen = "borderless"
    flags, winmode = SCREEN_ARGS.get(screen, ([], None))
    cmd = [str(GAME_EXE), "-netdriver", "lan", "-skipintromovies", "-join", "127.0.0.1"] + flags
    setvars = []
    if host:                                   # you host: a LAN lobby with this map and mode (the Ghosts tab's own game)
        cmd = [str(GAME_EXE), "-netdriver", "lan", "-skipintromovies", "-host", "-maxplayers", str(host["maxplayers"])] + \
              (["-private"] if host.get("private") else []) + flags
        setvars += ["mp_gamemode", str(GHOST_MODE_ID.get(host["mode"], 6)), "mp_levelName", host["map"]]
        if host.get("timelimit"): setvars += ["mp_timeToEnd", str(host["timelimit"])]
        if host.get("fraglimit"): setvars += ["mp_killsToEnd", str(host["fraglimit"])]
        if "ff" in host: setvars += ["mp_friendlyFire", "1" if host["ff"] else "0"]   # (Game rules: Friendly fire)
    if winmode is not None:
        setvars += ["v_windowmode", winmode]
        size = (1280, 720) if screen == "window" else None if screen == "exclusive" else desktop_size()   # the game keeps the last size it ran at (a window
        if size:                                                         # game saves 1280 x 720): always say the size
            setvars += ["v_width", str(size[0]), "v_height", str(size[1])]
    if char in CHAR_NUMBER:
        setvars += ["mp_defaultChar", str(CHAR_NUMBER[char])]
    if horde:
        setvars += ["r_rhirenderfamily", "vulkan"]
    return cmd + (["-setvar"] + setvars if setvars else [])


WIN_W, WIN_H = 1100, 880
HEAD_H = 78           # the title across the top, on the wood
PLAY_H = 600          # the Play tab's height (it scrolls when Advanced Options is open)
# (13 Nov: a western look, kept tidy. Wood planks behind everything, a parchment panel with the tabs, leather tabs and buttons,
#  rust for what matters, and the western Rye typeface for titles. PARCH is the panel, LEATHER the dark leather, RUST the accent,
#  EDGE the thin brown outlines, SOFT the light tan of plain buttons, FIELD the boxes you type in.)
PARCH, SAND, INK, LEATHER, RUST = "#f3e6c8", "#f6e7c1", "#33241a", "#4a2f1c", "#a8480f"
TXT, GOLD, EDGE = INK, RUST, "#b89a72"
MUTED, FIELD, SOFT = "#76583e", "#fffdf7", "#e6d2a8"
GOLDEN = "#f2c14e"                                      # the title's gold
ZOOM_MIN, ZOOM_MAX = 0.82, 2.2                          # how far the window can be shrunk / stretched (1.0 = 1100x880)
DISPLAY = "Georgia"                                     # tabs, box titles, buttons: Rockwell (a slab "wanted poster" type) if the PC has it
TITLEF = "Georgia"                                      # the big title: the game's own western lettering once western_fonts() has loaded it


def western_fonts():
    """The game's own western lettering (in its Common.kpf as fonts/Ultra-Regular.ttf; letters and digits only) for the big
    title: copied once to state\\fonts and loaded for this program only (nothing is installed in Windows)."""
    global TITLEF
    if os.name != "nt":
        return TITLEF
    try:
        import ctypes
        out = HERE / "state" / "fonts"
        want = {"Ultra-Regular.ttf": "fonts/Ultra-Regular.ttf"}
        if not all((out / n).exists() for n in want):
            out.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(GAME_DIR / "Common.kpf") as z:
                for n, inner in want.items():
                    (out / n).write_bytes(z.read(inner))
        FR_PRIVATE = 0x10
        if all(ctypes.windll.gdi32.AddFontResourceExW(str(out / n), FR_PRIVATE, 0) > 0 for n in want):
            TITLEF = "Ultra"
    except Exception:
        pass
    return TITLEF

# ---- the window -----------------------------------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        western_fonts()                                     # (before any window: the titles' typeface)
        super().__init__()
        self.title("Outlaws Companion" if COMPANION else "Outlaws Bot Launcher")
        self.resizable(True, True)
        pad = dict(padx=6, pady=3)
        saved = self.load()
        self._zfonts, self._z, self._job, self._ready, self._keep = [], None, None, False, []
        self.style_window()
        self.header()
        try:                                               # the taskbar / title-bar icon
            from icon_data import ICON_B64
            self.icon = tk.PhotoImage(data=ICON_B64)
            self.iconphoto(True, self.icon)
        except Exception:
            pass

        # ---- the Play tab: the game, who plays in it, LAUNCH (13 Nov: one place to start a game with bots, ghosts or both).
        #      Advanced Options (folded away at first) holds each bot and each ghost; the tab scrolls when it is open. ----
        body = self.play
        self.adv = tk.Frame(body, bg=PARCH)             # Advanced Options: the bots' part (self.bt) and the ghosts' (self.gh)
        self.bt = tk.Frame(self.adv, bg=PARCH)
        self.gh = tk.Frame(self.adv, bg=PARCH)
        self.bt.grid(row=0, column=0, sticky="w")
        self.gh.grid(row=1, column=0, sticky="w")
        gs0 = saved.get("ghost_game") or {}
        # (settings from before this layout: a ghost game was set up on the Ghosts tab with its own map and mode)
        old_ghost = "play_who" not in saved and bool(saved.get("ghost_on")) and bool(gs0)
        self.who = tk.StringVar(value=saved.get("play_who") if saved.get("play_who") in self.WHO else ("Ghosts" if old_ghost else "Bots"))
        self.ghost_on = tk.BooleanVar(value=self.who.get() != "Bots")
        try:
            gc0 = max(1, min(MAX_GHOSTS, int(saved.get("ghost_count", 3))))
        except (TypeError, ValueError):
            gc0 = 3
        self.ghost_count = tk.IntVar(value=gc0)
        self.ghost_diffmode_v = tk.StringVar(value=saved.get("ghost_diffmode") if saved.get("ghost_diffmode") in ["Custom"] + DIFFICULTIES else "Normal")

        top = ttk.LabelFrame(body, text="Game")
        top.grid(row=1, column=0, sticky="ew", padx=14, pady=(14, 6))
        top.columnconfigure(2, minsize=40)
        ttk.Label(top, text="Map").grid(row=0, column=0, sticky="w", **pad)
        mapf = ttk.Frame(top)
        mapf.grid(row=0, column=1, sticky="w", **pad)
        want = ((gs0.get("map") if old_ghost else saved.get("map")) or "").replace(" (custom)", "")
        group = "Custom maps" if want in MAP_GROUPS["Custom maps"] else "Standard maps"
        self.map_group = ttk.Combobox(mapf, values=list(MAP_GROUPS), state="readonly", width=14)
        self.map_group.set(group)
        self.map_group.grid(row=0, column=0, padx=(0, 4))
        self.map = ttk.Combobox(mapf, values=MAP_GROUPS[group], state="readonly", width=28)
        self.map.set(want if want in MAP_GROUPS[group] else MAP_GROUPS[group][0])
        self.map.grid(row=0, column=1)
        self.map_group.bind("<<ComboboxSelected>>", lambda e: self.pick_map_group())
        ttk.Label(top, text="Game mode").grid(row=0, column=3, sticky="w", **pad)
        self.mode = ttk.Combobox(top, values=[m for m, _ in MODES], state="readonly", width=22)
        self.mode.set((gs0.get("mode") if old_ghost else saved.get("mode")) or "Deathmatch")
        self.mode.grid(row=0, column=4, sticky="w", **pad)
        self.mode.bind("<<ComboboxSelected>>", lambda e: self.refresh_all())
        self.map.bind("<<ComboboxSelected>>", lambda e: self.update_modes())
        self.labeled(top, "Score limit", "The match ends when a player (or a team) reaches this score. Leave it empty for no limit.").grid(
            row=1, column=0, sticky="w", **pad)
        self.fraglimit = tk.StringVar(value=str((gs0.get("frag", "") if old_ghost else saved.get("fraglimit", ""))))
        ttk.Entry(top, textvariable=self.fraglimit, width=8).grid(row=1, column=1, sticky="w", **pad)
        self.labeled(top, "Time limit (min)", "The match ends after this many minutes. Leave it empty for no limit.").grid(
            row=1, column=3, sticky="w", **pad)
        self.timelimit = tk.StringVar(value=str((gs0.get("time", "") if old_ghost else saved.get("timelimit", ""))))
        ttk.Entry(top, textvariable=self.timelimit, width=8).grid(row=1, column=4, sticky="w", **pad)
        # The display mode your own game starts in (every tab that starts your game uses it).
        self.labeled(top, "Screen size", "How your own game opens. Every tab that starts your game uses this.").grid(
            row=2, column=0, sticky="w", **pad)
        self.screen = ttk.Combobox(top, values=[l for l, _ in SCREEN_MODES], state="readonly", width=20)
        self.screen.set(next((l for l, v in SCREEN_MODES if v == saved.get("screen_mode") and not (v == "full" and not saved.get("screen_v2"))), "Exclusive full screen"))
        self.screen.grid(row=2, column=1, sticky="w", **pad)
        self.private = tk.BooleanVar(value=bool(gs0.get("private", False) if old_ghost else saved.get("private", False)))     # nobody can see or join it
        self.check(top, "Private game", self.private,
                   "Only you and the bots / ghosts: nobody else can see or join the game.").grid(
            row=2, column=3, columnspan=2, sticky="w", **pad)
        # Your character (13 Nov): your game starts as it, and the game's XP goes to it (see after_my_game for a switch in the lobby)
        self.labeled(top, "Your character", "Your game starts as this character, and the XP you earn in the game goes to THIS "
                                            "character (Stats tab: XP, level and prestige).\n\nIf you switch to another character "
                                            "in the game's lobby, the launcher sees it when you leave the game and gives that "
                                            "game's XP to the character you really played.").grid(row=3, column=0, sticky="w", **pad)
        labels = [l for l, _k in CHARACTERS]
        self.my_char = ttk.Combobox(top, values=labels, state="readonly", width=20)
        self.my_char.set(saved.get("my_char") if saved.get("my_char") in labels else "Mary")
        self.my_char.grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(top, text="your XP goes to this character", style="Sum.TLabel").grid(row=3, column=3, columnspan=2, sticky="w", **pad)

        pl = self.pl = ttk.LabelFrame(body, text="Players")
        pl.grid(row=2, column=0, sticky="ew", padx=14, pady=6)
        seg = ttk.Frame(pl)
        seg.grid(row=0, column=0, sticky="w", padx=8, pady=(6, 8))
        for i, w in enumerate(self.WHO):
            ttk.Radiobutton(seg, text=w, value=w, variable=self.who, style="Seg.Toolbutton", command=self.who_changed).grid(
                row=0, column=i, padx=(0, 0 if i < len(self.WHO) - 1 else 10))
        self.hint(seg, (
            "Bots: full computer players. Each one runs its own hidden copy of the game, so they are the smartest opponents "
            "and the heaviest on your PC. The first bot hosts the game and your game joins it.\n\n"
            "Ghosts: light-weight players that join over the network, so many fit on one PC. You host the game and the "
            "ghosts join you (they appear when you start the match).\n\n"
            "Bots + Ghosts: a bots' game; the ghosts join it about a minute after your game is in.")).grid(row=0, column=len(self.WHO))
        self.bots_row = br = ttk.Frame(pl)
        br.grid(row=1, column=0, sticky="w", padx=2)
        ttk.Label(br, text="Bots", width=8).grid(row=0, column=0, sticky="w", **pad)
        self.count = tk.IntVar(value=int(saved.get("count", 6)))
        ttk.Spinbox(br, from_=1, to=10, width=5, textvariable=self.count, command=self.refresh_all).grid(row=0, column=1, sticky="w", **pad)
        self.count.trace_add("write", lambda *a: self.refresh_all())
        ttk.Label(br, text="Difficulty").grid(row=0, column=2, sticky="w", padx=(22, 6))
        self.last_base = saved.get("difficulty") if saved.get("difficulty") in DIFFICULTIES else "Normal"
        self.diff_v = tk.StringVar(value="Custom" if any((b or {}).get("diff") in DIFFICULTIES for b in (saved.get("bots") or [])) else self.last_base)
        self.diff = ttk.Combobox(br, values=["Custom"] + DIFFICULTIES, state="readonly", width=12, textvariable=self.diff_v)
        self.diff.grid(row=0, column=3, sticky="w", **pad)
        self.diff.bind("<<ComboboxSelected>>", lambda e: self.diff_changed())
        self.hint(br, "Every bot plays at this difficulty. Custom: each bot has its own (Advanced Options).").grid(row=0, column=4)
        ttk.Button(br, text="Set up each bot", command=lambda: self.adv_show(self.bt)).grid(row=0, column=5, padx=(22, 6))
        self.ghosts_row = gr = ttk.Frame(pl)
        gr.grid(row=2, column=0, sticky="w", padx=2)
        ttk.Label(gr, text="Ghosts", width=8).grid(row=0, column=0, sticky="w", **pad)
        ttk.Spinbox(gr, from_=1, to=MAX_GHOSTS, width=5, textvariable=self.ghost_count).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(gr, text="Difficulty").grid(row=0, column=2, sticky="w", padx=(22, 6))
        self.play_gh_diff = ttk.Combobox(gr, values=["Custom"] + DIFFICULTIES, state="readonly", width=12, textvariable=self.ghost_diffmode_v)
        self.play_gh_diff.grid(row=0, column=3, sticky="w", **pad)
        self.play_gh_diff.bind("<<ComboboxSelected>>", lambda e: self.ghost_diffmode_apply())
        self.hint(gr, "Every ghost plays at this difficulty. Custom: each ghost has its own (Advanced Options).").grid(row=0, column=4)
        ttk.Button(gr, text="Set up each ghost", command=lambda: self.adv_show(self.gh)).grid(row=0, column=5, padx=(22, 6))
        # Team split (team modes only): how many of the bots play on Red; the rest play on Blue, so 6 v 2 works.
        self.split_row = tk.Frame(pl, bg=PARCH)
        self.split_row.grid(row=3, column=0, sticky="w", padx=10, pady=4)
        def _num(v):
            return v if str(v).isdigit() else ""
        self.red_n = tk.StringVar(value=_num(saved.get("red_bots", "")))
        self.blue_n = tk.StringVar(value=_num(saved.get("blue_bots", "")))
        ttk.Label(self.split_row, text="Red Team").pack(side="left")
        ttk.Entry(self.split_row, textvariable=self.red_n, width=4).pack(side="left", padx=(6, 14))
        ttk.Label(self.split_row, text="Blue Team").pack(side="left")
        ttk.Entry(self.split_row, textvariable=self.blue_n, width=4).pack(side="left", padx=(6, 8))
        self.hint(self.split_row, "Team modes: how many play on Red and on Blue. Fill in one box and the other fills itself. "
                                  "Bots + Ghosts: the numbers count bots and ghosts together, and each team gets its share of both. "
                                  "Empty = the teams even out. You pick your own side in the lobby.").pack(side="left", padx=(0, 10))
        self.split_src, self.split_busy = "red", False

        def _typed(which):
            def f(*a):
                if not self.split_busy:
                    self.split_src = which
                    self.refresh_all()
            return f
        self.red_n.trace_add("write", _typed("red"))
        self.blue_n.trace_add("write", _typed("blue"))
        self.split_info = ttk.Label(self.split_row, text="", style="Muted.TLabel")
        self.split_info.pack(side="left")
        # You join automatically, so the match waits for you. Unticked, LAUNCH GAME starts only the bots.
        self.wait_me = tk.BooleanVar(value=bool(saved.get("wait_for_me", True)))
        self.me_row = self.check(pl, "I play too", self.wait_me,
                                 "Your own game starts and joins the bots' game by itself. Untick it to start only the bots "
                                 "(you can still join from the game's LAN menu).", command=self.play_summary)
        self.me_row.grid(row=5, column=0, sticky="w", padx=8, pady=(2, 6))

        btns = ttk.Frame(body)
        btns.grid(row=5, column=0, pady=(12, 2))
        self.launch_btn = ttk.Button(btns, text="LAUNCH GAME", command=self.play_launch, width=20, style="Launch.TButton")
        self.launch_btn.grid(row=0, column=0, padx=6)
        self.launch_btns = [(self.launch_btn, "LAUNCH GAME")]
        ttk.Button(btns, text="Close all game windows", command=self.kill, width=22).grid(row=0, column=1, padx=6)
        # (11 Nov: without this button a bots' game could not get ghosts at all)
        self.gh_now_btn = ttk.Button(btns, text="Join ghosts now", command=self.ghosts_now, width=16)
        self.gh_now_btn.grid(row=0, column=2, padx=6)
        self.tip(self.gh_now_btn, "Adds the ghosts to the game that is running right now, without waiting.")
        self.play_sum = tk.StringVar()
        ttk.Label(body, textvariable=self.play_sum, style="Sum.TLabel").grid(row=6, column=0, pady=(2, 6))
        # Advanced Options: click to open (each bot, each ghost) or close. Remembered between launches.
        foldf = ttk.Frame(body)
        foldf.grid(row=7, column=0, sticky="w", padx=10, pady=(4, 2))
        self.adv_btn = ttk.Button(foldf, text="Advanced Options   \u25bc", style="Fold.TButton", command=self.adv_toggle)
        self.adv_btn.pack(side="left")
        self.hint(foldf, "Each bot's name, character, behaviour, team and weapon strengths, the bot presets and the bot windows; "
                         "each ghost's character and difficulty and the ghost options. Only the part for the players picked "
                         "above shows.").pack(side="left", padx=(6, 0))
        self.adv_open = bool(saved.get("adv_open", False))
        self.log = tk.Text(body, height=6, width=104, state="disabled", bg=FIELD, fg=INK, relief="flat", highlightthickness=1,
                           highlightbackground=EDGE, highlightcolor=EDGE, padx=8, pady=6, font=self.zfont("Consolas", 9))
        self.log.grid(row=9, column=0, padx=14, pady=(8, 50))

        # ---- Advanced Options, the bots' part: each bot, the saved presets, the bots' game rules and the bot windows ----
        bt = self.bt
        # (13 Nov, the user: the bots' part looks just like the ghosts': how many, characters, difficulty, then one row per bot
        #  with its character and difficulty. The rest of a bot (name, behaviour, team, lean, weapon strengths) is still kept,
        #  never shown: its saved values keep working, and the Red / Blue boxes under Players set the teams.)
        self.about(bt, "Bots", (
            "Full computer players: each one runs its own hidden copy of the game. The first bot hosts the game.\n\n"
            "Nightmare and Hell bots are strong with every weapon.")).grid(row=0, column=0, sticky="w", padx=14, pady=(8, 0))
        box = ttk.LabelFrame(bt, text="Each bot")
        box.grid(row=1, column=0, sticky="ew", padx=14, pady=6)
        self.bind_all("<MouseWheel>", self.play_wheel)
        self.bind_class("TCombobox", "<MouseWheel>", self.play_wheel)   # the wheel scrolls the page, it never changes a dropdown
        self.bind_class("TSpinbox", "<MouseWheel>", lambda e: "break")
        rf = self.bot_rows_fr = ttk.Frame(box)            # bots 1-5 and 6-10 side by side
        rf.grid(row=0, column=0, sticky="nw")
        cmf = ttk.Frame(rf); cmf.grid(row=0, column=0, columnspan=11, sticky="w", **pad)
        ttk.Label(cmf, text="How many").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Spinbox(cmf, from_=1, to=10, width=4, textvariable=self.count, command=self.refresh_all).grid(row=0, column=1, sticky="w", padx=8, pady=2)
        ttk.Label(cmf, text="Characters").grid(row=0, column=2, sticky="w", padx=(24, 0), pady=2)
        self.bot_charmode = ttk.Combobox(cmf, values=self.GHOST_CHARMODES, state="readonly", width=10)
        self.bot_charmode.set(saved.get("bot_charmode") if saved.get("bot_charmode") in self.GHOST_CHARMODES else "Custom")
        self.bot_charmode.grid(row=0, column=3, sticky="w", padx=8, pady=2)
        self.hint(cmf, "Custom: pick each bot's character below.\nRandom: every bot gets a random character at each launch.").grid(row=0, column=4)
        ttk.Label(cmf, text="Difficulty").grid(row=0, column=5, sticky="w", padx=(24, 0), pady=2)
        self.bot_diffmode = ttk.Combobox(cmf, values=["Custom"] + DIFFICULTIES, state="readonly", width=10, textvariable=self.diff_v)
        self.bot_diffmode.grid(row=0, column=6, sticky="w", padx=8, pady=2)
        self.bot_diffmode.bind("<<ComboboxSelected>>", lambda e: self.diff_changed())
        self.hint(cmf, "Custom: pick each bot's difficulty below.\nAny other choice: every bot plays at that difficulty.").grid(row=0, column=7)
        for blk in (0, 4):
            ttk.Label(rf, text="Bot", style="Muted.TLabel").grid(row=1, column=blk, sticky="w", **pad)
            ttk.Label(rf, text="Character", style="Muted.TLabel").grid(row=1, column=blk + 1, sticky="w", **pad)
            ttk.Label(rf, text="Difficulty", style="Muted.TLabel").grid(row=1, column=blk + 2, sticky="w", **pad)
        self.bot_rows_head = [w for w in rf.grid_slaves(row=1) if int(w.grid_info()["column"]) >= 4]
        ttk.Frame(rf, width=24).grid(row=1, column=3)
        hidden = ttk.Frame(bt)                            # (never shown: each bot's other settings)
        self.real = tk.BooleanVar(value=False)         # the real-name presets are gone: bots are plain "Bot N" until saved
        self.blocks = []
        sb = saved.get("bots", [])
        chars = [CHAR_RANDOM] + [k for k, _ in CHARACTERS]
        behs = [BEH_DEFAULT] + BEHAVIOURS[1:]
        for i in range(10):
            s = sb[i] if i < len(sb) else {}
            frame = ttk.Frame(hidden)
            lab = ttk.Label(rf, text=f"Bot {i + 1}")
            name = tk.StringVar(value=s.get("name") or preset_row(i + 1, self.real.get())[0])
            char = CharPicker(rf, chars, width=14)
            char.set(s.get("char") if s.get("char") in chars else CHAR_DEFAULT)
            beh = ttk.Combobox(frame, values=behs, state="readonly", width=14)
            beh.set(s.get("beh") if s.get("beh") in behs else BEH_DEFAULT)
            team = ttk.Combobox(frame, values=TEAMS, state="readonly", width=7)
            team.set(s.get("team") if s.get("team") in TEAMS else "Auto")
            lean = ttk.Combobox(frame, values=[k for k, _ in LEANS], state="readonly", width=12)
            lean.set("No lean")                          # next game only: not remembered between launches
            bdiff = ttk.Combobox(rf, values=DIFFICULTIES, state="readonly", width=14)
            bdiff.set(s.get("diff") if s.get("diff") in DIFFICULTIES else "Same as above")
            bdiff.bind("<<ComboboxSelected>>", lambda e: self.refresh_all())
            wbox = ttk.Frame(frame)
            saved_levels = s.get("levels") if isinstance(s.get("levels"), list) and len(s.get("levels")) == len(WEAPONS) else None
            wcombos = []
            for k, (label, _slot) in enumerate(WEAPONS):
                cb = ttk.Combobox(wbox, values=LEVELS, state="readonly", width=8)
                dl = default_levels(i + 1, self.real.get())
                v = saved_levels[k] if saved_levels else dl[k]
                cb.set(v if v in LEVELS else dl[k])
                wcombos.append(cb)
            self.blocks.append({"frame": frame, "label": lab, "name": name, "char": char, "beh": beh, "team": team,
                                "weapons": wcombos, "wbox": wbox, "lean": lean, "diff": bdiff})
        cc_, cd_ = saved.get("bot_custom_chars") or [], saved.get("bot_custom_diffs") or []
        self.bot_custom_chars = [cc_[i] if i < len(cc_) and cc_[i] in chars else b["char"].get() for i, b in enumerate(self.blocks)]
        self.bot_custom_diffs = [cd_[i] if i < len(cd_) and cd_[i] in DIFFICULTIES else
                                 (b["diff"].get() if b["diff"].get() in DIFFICULTIES else self.last_base) for i, b in enumerate(self.blocks)]
        self.bot_charmode.bind("<<ComboboxSelected>>", lambda e: self.bot_charmode_apply())
        self.bot_charmode_apply(first=True)
        self.bot_diffmode_apply(first=True)
        # The saved bot presets: kept working (never shown any more).
        pbox = ttk.Frame(hidden)
        self.preset_data = self.read_presets()
        self.preset_list = tk.Listbox(pbox, height=PRESET_SLOTS, width=24, exportselection=False)
        self.load_btn = ttk.Button(pbox, text="Load", command=self.load_preset, state="disabled")
        self.del_btn = ttk.Button(pbox, text="Delete", command=self.delete_preset, state="disabled", width=7)
        self.show_presets()

        # Game rules: for the bots AND the ghosts (13 Nov, the user), so they sit under both parts of Advanced Options.
        rules = self.rules_box = ttk.LabelFrame(self.adv, text="Game rules")
        rules.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 10))
        # Weapons, for the bots and the ghosts (13 Nov, the user: a dropdown like the ghosts had, no TNT tick box)
        wv = saved.get("game_weapons")
        if wv not in self.GHOST_WEAPON_MODES:            # (settings from before: the TNT tick box, or the ghosts' own Weapons box)
            wv = "TNT only" if saved.get("tnt_only") else (saved.get("ghost_weapons") if saved.get("ghost_weapons") in self.GHOST_WEAPON_MODES else "Normal")
        self.tnt_only = tk.BooleanVar(value=wv == "TNT only")
        self.melee_only = tk.BooleanVar(value=wv == "Melee only")
        wf = self.labeled(rules, "Weapons", "Normal: each character's own weapons.\n"
                                            "Melee only: fists and knives. The bots play as Mary and Two Feathers.\n"
                                            "TNT only: dynamite, then knife or fists. Every bot plays as Sanchez.")
        wf.grid(row=0, column=0, sticky="w", **pad)
        self.weapons_rule = ttk.Combobox(rules, values=self.GHOST_WEAPON_MODES, state="readonly", width=12)
        self.weapons_rule.set(wv)
        self.weapons_rule.grid(row=0, column=1, sticky="w", **pad)
        self.weapons_rule.bind("<<ComboboxSelected>>", lambda e: self.weapons_changed())
        self.ff = tk.BooleanVar(value=bool(saved.get("friendly_fire", False)))
        self.check(rules, "Friendly fire", self.ff, "Team modes: team mates can hurt each other.").grid(row=0, column=2, sticky="w", **pad)
        self.taunts = tk.BooleanVar(value=bool(saved.get("taunts", True)))
        self.check(rules, "Taunts after kills", self.taunts, "Bots and ghosts call out a taunt after a kill.").grid(row=0, column=3, sticky="w", **pad)
        self.law = tk.BooleanVar(value=bool(saved.get("marshall_law", True)))
        self.check(rules, "Marshall Law", self.law, "The badge and the invisible cream are left alone (the ghosts never take them).").grid(
            row=1, column=0, columnspan=2, sticky="w", **pad)
        self.bench = tk.BooleanVar(value=bool(saved.get("bench", True)))
        self.check(rules, "Bench when teams are uneven", self.bench,
                   "Team modes: when one side has more players, a bot on that side sits out until the teams are even again "
                   "(the bots only, for now).").grid(row=1, column=2, sticky="w", **pad)
        self.trim = tk.BooleanVar(value=bool(saved.get("trim_memory", True)))
        self.trim_row = self.check(rules, "Trim idle bot memory", self.trim,
                                   "Hands the bots' unused memory back to Windows now and then (lower RAM use). Only with bots.")
        self.trim_row.grid(row=1, column=3, sticky="w", **pad)

        adv = ttk.LabelFrame(bt, text="Bot windows")
        adv.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 6))
        ttk.Label(adv, text="Size").grid(row=0, column=0, **pad)
        self.size = tk.StringVar(value=saved.get("size", "320x180"))
        ttk.Entry(adv, textvariable=self.size, width=10).grid(row=0, column=1, **pad)
        ttk.Label(adv, text="FPS").grid(row=0, column=2, **pad)
        self.fps = tk.StringVar(value=str(saved.get("fps", 60)))
        ttk.Entry(adv, textvariable=self.fps, width=6).grid(row=0, column=3, **pad)
        ttk.Label(adv, text="Window").grid(row=0, column=4, **pad)
        self.wmode = ttk.Combobox(adv, values=[m for m, _ in WINDOW_MODES], state="readonly", width=36)
        self.wmode.set(saved.get("window_mode_label") or WINDOW_MODES[0][0])
        self.wmode.grid(row=0, column=5, **pad)
        self.hint(adv, "Each bot runs its own copy of the game. A smaller size and a lower FPS use less memory and graphics power.").grid(row=0, column=6, padx=(4, 8))

        upd_btns = self.upd_btns = []
        for tab in (self.body, self.zt, self.gm, self.rot, self.st, self.tl):     # the same button, bottom right, on every tab
            b = ttk.Button(tab, text="Update Launcher", command=self.companion_update if COMPANION else self.update_launcher, width=16)
            b.place(relx=1.0, rely=1.0, anchor="se", x=-30 if tab is self.body else -12, y=-10)    # (clear of the Play tab's scroll bar)
            upd_btns.append(b)
        self.gm_close = ttk.Button(self.gm, text="Close all game windows", command=self.kill, width=22)
        gm_upd = upd_btns[[self.body, self.zt, self.gm].index(self.gm)]
        if COMPANION:                                  # (the Companion has no Tools tab: Remove All Mods is left of Close all game windows)
            self.gm_mods = ttk.Button(self.gm, text="Remove All Mods", command=lambda: self.tool("mods_delete"), width=18)
            upd_btns.append(self.gm_mods)
        def _beside(_e=None):                          # left of Update Launcher, whatever size the buttons are drawn at
            self.gm_close.place(relx=1.0, rely=1.0, anchor="se", x=-(gm_upd.winfo_width() + 24), y=-10)
            if COMPANION:
                self.gm_mods.place(relx=1.0, rely=1.0, anchor="se", x=-(gm_upd.winfo_width() + self.gm_close.winfo_width() + 36), y=-10)
        gm_upd.bind("<Configure>", _beside, add="+")
        self.gm_close.bind("<Configure>", _beside, add="+")
        _beside()
        upd_btns.append(self.gm_close)
        # (24 Nov, the user: the missions button moved here from the Play tab, and called Christmas Missions - not "Co-op",
        #  as you can play them solo too) top right of the Game Modes tab, whatever mode is picked; its window is launchertools'
        self.gm_missions = ttk.Button(self.gm, text="Christmas Missions...", command=lambda: self.tool("missions_window"), width=22)
        self.gm_missions.place(relx=1.0, y=14, anchor="ne", x=-12)
        upd_btns.append(self.gm_missions)
        self.build_zerotier(saved)
        self.build_stats(saved)
        self.build_ghosts(saved)
        self.build_modes(saved)
        def _flush_online():                       # ranked games that couldn't be pushed last time go out now
            try:
                if str(HERE) not in sys.path:
                    sys.path.insert(0, str(HERE))
                import onlinerank
                onlinerank.flush_outbox()
            except Exception:
                pass
        threading.Thread(target=_flush_online, daemon=True).start()
        self.build_gungame(saved)
        self.build_roles(saved)
        self.build_rotation(saved)
        # Tools tab: saved setups, health check, host status, problem report, match history. Its code is launchertools.py,
        # read from disk at start (see _disk), so a fix there needs no rebuild; without it the launcher works as before.
        try:
            self.tools = _disk('launchertools').Tools(self, saved)
        except Exception as e:
            self.tools = None
            ttk.Label(self.tl, wraplength=820, justify="left", text=f"The Tools tab could not start: {e}").grid(row=0, column=0, padx=12, pady=12, sticky="w")
        for b in upd_btns:
            b.lift()                                   # above the tab's other widgets
        self.update_modes()
        self.who_changed()
        self.after(5000, self.give_old_xp)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.start_bugshot()
        self._ready = True
        self.after(30, self.relayout)

    # -- Map Rotation tab: a list of matches the bots go through (rotation.py), one after the other in the same game copies
    def build_rotation(self, saved):
        pad = dict(padx=8, pady=4)
        g, sv = self.rot, saved.get("rotation", {})
        self.about(g, "Map rotation", (
            "A list of matches played one after the other. When a match ends, the host bot hosts the next map on the list "
            "and the other bots join it, without restarting their game copies.\n\nThe bots, their difficulty and their "
            "windows come from the Play tab.\n\nYou stay on the score screen: press Continue, then join the LAN "
            "game again from the game's multiplayer menu.")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(14, 4))
        add = ttk.LabelFrame(g, text="Add a match")
        add.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=4)
        self.rot_group = ttk.Combobox(add, values=list(MAP_GROUPS), state="readonly", width=14)
        self.rot_group.set("Standard maps")
        self.rot_group.grid(row=0, column=0, **pad)
        self.rot_map = ttk.Combobox(add, values=MAP_GROUPS["Standard maps"], state="readonly", width=26)
        self.rot_map.set(MAP_GROUPS["Standard maps"][0])
        self.rot_map.grid(row=0, column=1, **pad)
        self.rot_mode = ttk.Combobox(add, state="readonly", width=18)
        self.rot_mode.grid(row=0, column=2, **pad)
        ttk.Label(add, text="Minutes").grid(row=0, column=3, **pad)
        self.rot_min = tk.StringVar(value="15")
        ttk.Entry(add, textvariable=self.rot_min, width=5).grid(row=0, column=4, **pad)
        ttk.Label(add, text="Kills").grid(row=0, column=5, **pad)
        self.rot_kills = tk.StringVar(value="")
        ttk.Entry(add, textvariable=self.rot_kills, width=5).grid(row=0, column=6, **pad)
        ttk.Button(add, text="Add", command=self.rot_add).grid(row=0, column=7, **pad)

        def pick_group(_e=None):
            names = MAP_GROUPS.get(self.rot_group.get()) or MAP_GROUPS["Standard maps"]
            self.rot_map.configure(values=names)
            self.rot_map.set(names[0])
            pick_map()

        def pick_map(_e=None):
            have = MAP_MODES.get(dict(MAPS).get(self.rot_map.get(), ""))
            labels = [m for m, v in MODES if have is None or v in have]
            self.rot_mode.configure(values=labels)
            if self.rot_mode.get() not in labels:
                self.rot_mode.set(labels[0])
        self.rot_group.bind("<<ComboboxSelected>>", pick_group)
        self.rot_map.bind("<<ComboboxSelected>>", pick_map)
        pick_map()
        cols = ("#", "Map", "Mode", "Minutes", "Kills")
        self.rot_tree = ttk.Treeview(g, columns=cols, show="headings", height=8, selectmode="browse")
        for c, w in zip(cols, (40, 260, 160, 80, 80)):
            self.rot_tree.heading(c, text=c)
            self.rot_tree.column(c, width=w, anchor="w" if c in ("Map", "Mode") else "center")
        self.rot_tree.grid(row=2, column=0, sticky="w", padx=12, pady=4)
        side = ttk.Frame(g)
        side.grid(row=2, column=1, sticky="nw", pady=4)
        for text, cmd in (("Up", lambda: self.rot_move(-1)), ("Down", lambda: self.rot_move(1)),
                          ("Remove", self.rot_remove), ("Clear", self.rot_clear)):
            ttk.Button(side, text=text, command=cmd, width=10).pack(pady=2)
        keys = {k for _, k in MAPS}
        self.rot_entries = [e for e in sv.get("entries", []) if isinstance(e, dict) and e.get("map") in keys
                            and e.get("mode") in dict((v, m) for m, v in MODES)]
        self.rot_show()
        opts = ttk.Frame(g)
        opts.grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=2)
        self.rot_loop = tk.BooleanVar(value=bool(sv.get("loop", True)))
        ttk.Checkbutton(opts, text="Start over after the last map", variable=self.rot_loop).pack(side="left")
        self.rot_shuffle = tk.BooleanVar(value=bool(sv.get("shuffle", False)))
        self.check(opts, "Shuffle", self.rot_shuffle, "The maps are played in a random order. The first map stays first.").pack(side="left", padx=(16, 0))
        ttk.Label(opts, text="Seconds on the score screen").pack(side="left", padx=(16, 4))
        sw_ = sv.get("score_wait", 5)
        if str(sw_) == "10": sw_ = 5                     # (the old default; 11 Nov the default became 5 - a 10 someone typed is lost, rarely)
        self.rot_wait = tk.StringVar(value=str(sw_))
        ttk.Spinbox(opts, from_=3, to=120, width=5, textvariable=self.rot_wait).pack(side="left")
        self.rot_you = tk.BooleanVar(value=bool(sv.get("you", True)))
        net = ttk.Frame(g)
        net.grid(row=4, column=0, columnspan=2, sticky="w", padx=12, pady=2)
        ttk.Label(net, text="Game").pack(side="left")
        self.rot_net = ttk.Combobox(net, values=self.ROT_NETS, state="readonly", width=12)
        self.rot_net.set(sv.get("net") if sv.get("net") in self.ROT_NETS else "LAN game")
        self.rot_net.pack(side="left", padx=8)
        self.rot_you_f = self.check(net, "I play too", self.rot_you,
                                    "Your own game starts and joins the first map. Untick it to start only the bots.")
        self.rot_you_cb = self.rot_you_f.box
        self.rot_who_f = ttk.Frame(net)                      # LAN only: who plays against you, bots (game copies) or ghosts
        ttk.Label(self.rot_who_f, text="Players").pack(side="left", padx=(16, 0))
        self.rot_who = ttk.Combobox(self.rot_who_f, values=["Bots", "Ghosts"], state="readonly", width=8)
        self.rot_who.set(sv.get("who") if sv.get("who") in ("Bots", "Ghosts") else "Bots")
        self.rot_who.pack(side="left", padx=8)
        self.rot_gh_f = ttk.Frame(self.rot_who_f)            # Ghosts: how many and their difficulty (characters: random)
        ttk.Label(self.rot_gh_f, text="How many").pack(side="left")
        self.rot_gh_n = tk.IntVar(value=max(1, min(MAX_GHOSTS, int(sv.get("ghosts", 7) or 7))))
        ttk.Spinbox(self.rot_gh_f, from_=1, to=MAX_GHOSTS, textvariable=self.rot_gh_n, width=4, state="readonly").pack(side="left", padx=(6, 12))
        ttk.Label(self.rot_gh_f, text="Difficulty").pack(side="left")
        self.rot_gh_diff = ttk.Combobox(self.rot_gh_f, values=DIFFICULTIES + ["Random"], state="readonly", width=10)
        self.rot_gh_diff.set(sv.get("ghost_diff") if sv.get("ghost_diff") in DIFFICULTIES + ["Random"] else "Random")
        self.rot_gh_diff.pack(side="left", padx=6)
        self.rot_who_f.pack(side="left")
        self.rot_you_f.pack(side="left", padx=(16, 0))
        self.rot_who_hint = self.hint(self.rot_who_f, (
            "Bots: the bots from the Play tab play every map.\n\n"
            "Ghosts: one passive host copy holds each map (it sits in the lobby and never fights) and ghost players fight "
            "instead of bots, each a random character (new ones every map). They leave when a match ends and join the next "
            "map about 10 s after its lobby is up."))
        self.rot_who_hint.pack(side="left", padx=(0, 6))
        def pick_who(_e=None):
            if self.rot_net.get() != "Online game" and self.rot_who.get() == "Ghosts":
                if not self.rot_gh_f.winfo_ismapped(): self.rot_gh_f.pack(side="left", padx=(4, 0))
            else:
                self.rot_gh_f.pack_forget()
        self.rot_who.bind("<<ComboboxSelected>>", pick_who)
        self.hint(net, (
            "LAN game: the rotation is hosted on your network (and the Outlaws ZeroTier network).\n\n"
            "Online game: one host copy signs in with YOUR account (your name shows as the host) and hosts each map in the "
            "game's online list for everyone to see and join. It stays in the lobby and never plays; no bots. Standard maps "
            "only (others can't load custom maps), and every map needs Minutes so the match ends. Your account is in use while "
            "it runs, so you can't play online on it at the same time. The first time, its window may ask you to accept the "
            "online terms: accept them once in that window.")).pack(side="left", after=self.rot_net)
        def pick_net(_e=None):
            online = self.rot_net.get() == "Online game"
            self.rot_you_cb.configure(state="disabled" if online else "normal")
        self.rot_boot = tk.BooleanVar(value=STARTUP_CMD.exists() if os.environ.get("APPDATA") else False)
        def set_boot():
            try:
                startup_set(bool(self.rot_boot.get()))
                self.say("this rotation now starts when Windows starts (host only, and it restarts itself if the game closes)"
                         if self.rot_boot.get() else "the rotation no longer starts with Windows")
            except OSError as e:
                messagebox.showerror("Start with Windows", f"Could not change the Startup folder:\n{e}")
        self.rot_boot_f = self.check(g, "Host server", self.rot_boot,
                                     "This PC becomes a host-only server: the rotation starts when Windows starts and is kept "
                                     "running (it restarts itself if the game closes). Nobody plays on this PC.", command=set_boot)
        self.rot_boot_cb = self.rot_boot_f.box
        _pick_net = pick_net
        def pick_net(_e=None):
            _pick_net()
            if self.rot_net.get() == "Online game": self.rot_who_f.pack_forget()      # (ghosts can't play online)
            elif not self.rot_who_f.winfo_ismapped(): self.rot_who_f.pack(side="left", before=self.rot_you_f)
            pick_who()
            self.rot_boot_f.grid(row=7, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 48))
            if getattr(self, "server_mode", False):         # a host-only server: you never play on it
                self.rot_you.set(False); self.rot_you_cb.configure(state="disabled")
        self.rot_net.bind("<<ComboboxSelected>>", pick_net)
        pick_net()
        rb = ttk.Frame(g)
        rb.grid(row=5, column=0, columnspan=2, pady=12)
        b = ttk.Button(rb, text="LAUNCH ROTATION", command=lambda: self.launch(rotation=True), width=22,
                       style="Launch.TButton")
        b.grid(row=0, column=0, padx=6)
        ttk.Button(rb, text="Close all game windows", command=self.kill, width=22).grid(row=0, column=1, padx=6)
        self.launch_btns.append((b, "LAUNCH ROTATION"))

    ROT_NETS = ["LAN game", "Online game"]

    def auto_online(self):
        """--online-host (Windows sign-in on an always-on PC): wait for Steam, start the Map Rotation as set on its tab
        (LAN or Online, bots or ghosts; host only, 'I play too' off), and start it again whenever the host's game copy
        has been gone for a few minutes (a crash, an update)."""
        self.server_mode = True
        self.rot_you.set(False); self.rot_you_cb.configure(state="disabled")
        def note(msg):                                    # (an unattended PC: what happened goes to a file too)
            self.say("host server: " + msg)
            try:
                with (HERE / "online_host_log.txt").open("a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
            except OSError:
                pass
        self.auto_note = note
        self.tool("serve", note)                        # other PCs at home can ask this one how it is doing (Tools tab)
        note(f"started; the Map Rotation ({self.rot_net.get()}" + (f", {self.rot_gh_n.get()} ghosts, {self.rot_gh_diff.get()}"
             if self.rot_net.get() != "Online game" and self.rot_who.get() == "Ghosts" else "")
             + f") starts in {AUTO_ONLINE_DELAY} s (Steam signs in first)")
        def go():
            try:
                self.rot_you.set(False)
                note(f"launching the rotation ({len(self.rot_entries)} map(s) on the list)")
                self.launch(rotation=True)
                note("launch started" if getattr(self, "launching", False) or bot_game_pids() else
                     "the launch did not start (a message box in the launcher may say why)")
            except Exception as e:
                import traceback
                note("launch failed: " + "".join(traceback.format_exception(e)).strip())
        self.after(AUTO_ONLINE_DELAY * 1000, go)
        def watch():
            time.sleep(AUTO_ONLINE_DELAY + 300)          # (the first start has had time to come up)
            gone = stuck = 0
            while True:
                time.sleep(60)
                pids = bot_game_pids()
                if pids is None or getattr(self, "launching", False):
                    continue
                # (13 Nov, a hosting PC: the map rotation restarted the host itself at 13:03:47 and this watch, which had seen no host
                #  for 3 minutes while the rotation was winding down, started a SECOND rotation 6 s later. From 13:19 the two fought
                #  over the host and the ghosts never came back. While the rotation program is alive or has just restarted the host,
                #  it is in charge: this watch waits.)
                rp = getattr(self, "rot_proc", None)
                if (rp is not None and rp.poll() is None) or time.time() - getattr(self, "rot_restart_t", 0.0) < 300:
                    gone = stuck = 0
                    continue
                # (15 Nov, a hosting PC: at 21:12 the host's game stopped answering but stayed open; the rotation program saw no host,
                #  said "rotation over" and ended, and this watch, still seeing the game's process, waited for 20 hours. The rotation
                #  ended and a game copy still open for 3 minutes: that copy is stuck. Close it and start again.)
                if pids and rp is not None and rp.poll() is not None:
                    stuck += 1
                    if stuck >= 3:
                        stuck = gone = 0
                        self.auto_note("the rotation stopped but its game is still open (not answering?): closing it and starting the rotation again")
                        try: self.kill_all()
                        except Exception as e: self.auto_note(f"closing the stuck game failed: {e}")
                        time.sleep(10)
                        self.after(0, go)
                        time.sleep(240)
                    continue
                stuck = 0
                gone = 0 if pids else gone + 1
                if gone >= 3:                             # three minutes without the host's game copy: start it again
                    gone = 0
                    self.auto_note("the host's game is gone; starting the rotation again")
                    self.after(0, go)
                    time.sleep(240)
        threading.Thread(target=watch, daemon=True).start()

    def rot_show(self, select=None):
        labels = {k: l for l, k in MAPS}
        modes = {v: m for m, v in MODES}
        self.rot_tree.delete(*self.rot_tree.get_children())
        for i, e in enumerate(self.rot_entries):
            self.rot_tree.insert("", "end", iid=str(i), values=(i + 1, labels.get(e["map"], e["map"]), modes[e["mode"]],
                                                                e.get("timelimit") or "-", e.get("fraglimit") or "-"))
        if select is not None and 0 <= select < len(self.rot_entries):
            self.rot_tree.selection_set(str(select))
            self.rot_tree.see(str(select))

    def rot_wait_s(self):
        v = self.rot_wait.get().strip()
        return min(120, max(3, int(v))) if v.isdigit() else 5

    def rot_selected(self):
        sel = self.rot_tree.selection()
        return int(sel[0]) if sel else None

    def rot_add(self):
        tl, fl = self.rot_min.get().strip(), self.rot_kills.get().strip()
        if tl and not (tl.isdigit() and int(tl) <= 120) or fl and not (fl.isdigit() and int(fl) <= 100):
            messagebox.showerror("Check the numbers", "Minutes must be 0-120 and kills 0-100 (or left empty).")
            return
        self.rot_entries.append({"map": dict(MAPS)[self.rot_map.get()], "mode": dict(MODES)[self.rot_mode.get()],
                                 "timelimit": tl, "fraglimit": fl})
        self.rot_show(select=len(self.rot_entries) - 1)

    def rot_move(self, d):
        i = self.rot_selected()
        if i is None or not 0 <= i + d < len(self.rot_entries):
            return
        e = self.rot_entries
        e[i], e[i + d] = e[i + d], e[i]
        self.rot_show(select=i + d)

    def rot_remove(self):
        i = self.rot_selected()
        if i is not None:
            del self.rot_entries[i]
            self.rot_show(select=min(i, len(self.rot_entries) - 1))

    def rot_clear(self):
        if self.rot_entries and messagebox.askyesno("Map rotation", "Remove every map from the list?"):
            self.rot_entries = []
            self.rot_show()

    def rot_order(self):
        """The list as this launch plays it: from line rot_start on (an online host restarted on its next map), then round."""
        k = getattr(self, "rot_start", 0) % max(1, len(self.rot_entries))
        return self.rot_entries[k:] + self.rot_entries[:k]

    def rot_write(self):
        """rotation.txt for rotation.py: one 'map mode minutes kills' line per match ('-' = keep the setting)."""
        lines = ["# written by the launcher's Map Rotation tab: map mode minutes kills"]
        for e in self.rot_order():
            lines.append(f"{e['map']} {e['mode']} {e.get('timelimit') or '-'} {e.get('fraglimit') or '-'}")
        (HERE / "rotation.txt").write_text("\n".join(lines) + "\n")

    def rot_ghost_cfg(self):
        """Map Rotation's ghosts: its own How many and Difficulty, every ghost a random character (drawn again each map);
        start weapons and swim sounds as on the Ghosts tab."""
        try:
            n = max(1, min(MAX_GHOSTS, int(self.rot_gh_n.get())))
        except (tk.TclError, ValueError):
            n = 7
        pick = self.rot_gh_diff.get()
        diff = pick if pick in DIFFICULTIES else "Normal"
        keys = [k for _, k in self.GHOST_CHARS if k != "random"]
        # Random: each ghost its own difficulty, drawn again every map, from Easy to Nightmare (never Easiest or Hell),
        # with at most ROT_RANDOM_CAPS of a kind (one Nightmare, two Hard)
        # Normal is the most common (weighted, then evened out so no other difficulty outnumbers it)
        diffs = [diff] * n
        if pick == "Random":
            used, diffs = {}, []
            for _ in range(n):
                opts = [x for x in ROT_RANDOM_DIFFS if used.get(x, 0) < ROT_RANDOM_CAPS.get(x, n)]
                d = random.choices(opts, weights=[ROT_RANDOM_WEIGHTS[x] for x in opts])[0]
                used[d] = used.get(d, 0) + 1
                diffs.append(d)
            while True:
                top = max((x for x in used if x != "Normal"), key=lambda x: used[x], default=None)
                if top is None or used.get("Normal", 0) >= used[top]:
                    break
                diffs[diffs.index(top)] = "Normal"
                used[top] -= 1
                used["Normal"] = used.get("Normal", 0) + 1
            random.shuffle(diffs)
        rows = [(random.choice(keys), d) for d in diffs]
        return (True, n, diff, rows, True, True)          # (each character's own start weapon; splash sounds)

    def rot_marker(self, m):
        """rotation.py's '@match-over' / '@map <key>': ghost players (Map Rotation with ghosts) leave with the match and
        join the next map once its lobby is up."""
        if not getattr(self, "rot_ghosts", False) or self.cancel:
            return
        if m == "@match-over":
            self.stop_ghosts()
            self.say("ghost players left with the match")
        elif m.startswith("@map "):
            self.ghost_rot_level = m.split(None, 1)[1]
            self.stop_ghosts()                              # (any that were still around)
            self.ghost_cfg = self.rot_ghost_cfg()           # new random characters for the new map
            wait = ROT_GHOST_DELAY_YOU if getattr(self, "join_me", False) else ROT_GHOST_DELAY
            self.say(f"ghost players join the next map in {wait} s")
            self.start_ghosts(delay=wait)

    def run_rotation(self, args):
        """rotation.py next to the bots: it waits for the host's match, then moves everyone from map to map."""
        venv_python = HERE / ".venv" / "Scripts" / "python.exe"
        for _ in range(300):                       # bots.cmd may still be making the venv (first run)
            if venv_python.exists() or not getattr(sys, "frozen", False) or self.cancel:
                break
            time.sleep(1)
        if getattr(sys, "frozen", False) and not venv_python.exists():
            self.say("map rotation: the bots' Python (.venv) was never made, so nothing could start. Run bots.cmd in the botctl "
                     "folder once by hand and read what it says (usually: Python is not installed or not on PATH).")
            if getattr(self, "auto_note", None): self.auto_note("no .venv: run bots.cmd once by hand to see why it fails")
            return
        py = venv_py()
        try:
            old = getattr(self, "rot_proc", None)
            if old is not None and old.poll() is None:            # (never two rotations at once: the old one goes first)
                try: old.kill()
                except OSError: pass
            p = subprocess.Popen([py, "-u", str(HERE / "rotation.py")] + args, cwd=str(HERE),
                                 env=dict(os.environ, PYTHONUNBUFFERED="1"), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, errors="replace", creationflags=NO_WINDOW)
            self.rot_proc = p
            for line in p.stdout:
                if line.startswith("@"):                       # markers for the launcher, not messages
                    self.tool("marker", line.strip())
                    self.rot_marker(line.strip())
                    continue
                self.say("[rotation] " + line)
            p.wait()
        except OSError as e:
            self.say(f"could not start the map rotation: {e}")
            return
        if p.returncode == 3 and not self.cancel:          # an online host that couldn't host its next map in-game
            try:
                pos = int(json.loads((HERE / "state" / "rotation_restart.json").read_text())["pos"])
            except (OSError, ValueError, KeyError):
                pos = 1
            self.rot_start = (getattr(self, "rot_start", 0) + pos) % max(1, len(self.rot_entries))
            note = getattr(self, "auto_note", None) or (lambda m: self.say("map rotation: " + m))
            note(f"restarting the host on map {self.rot_start + 1} of the list")
            self.rot_restart_t = time.time()
            self.kill_bot_copies()                         # (the host's copy and its botctl only: a game of yours stays open)
            time.sleep(5)
            self.after(0, lambda: self.launch(rotation=True))

    # -- ZeroTier tab
    def build_zerotier(self, saved):
        pad = dict(padx=8, pady=4)
        z = self.zt
        ttk.Label(z, text="Host a LAN game that friends join over ZeroTier. Everyone joins the same ZeroTier network; "
                          "your ZeroTier IP is your game's address.", wraplength=820, justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w", **pad)
        box = ttk.LabelFrame(z, text="Your address")
        box.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=6)
        ttk.Label(box, text="ZeroTier network ID").grid(row=0, column=0, sticky="w", **pad)
        self.zt_net = tk.StringVar(value=saved.get("zt_network", ""))
        ttk.Entry(box, textvariable=self.zt_net, width=22).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(box, text="ZeroTier IP (saved)").grid(row=1, column=0, sticky="w", **pad)
        self.zt_ip = tk.StringVar(value=saved.get("zt_ip", ""))
        ttk.Entry(box, textvariable=self.zt_ip, width=22, font=self.zfont("Consolas", 12, "bold")).grid(
            row=1, column=1, sticky="w", **pad)
        row = ttk.Frame(box)
        row.grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        ttk.Button(row, text="Copy address", command=self.zt_copy_ip).grid(row=0, column=0, padx=4)
        ttk.Button(row, text="Copy join instructions", command=self.zt_copy_help).grid(row=0, column=1, padx=4)
        ttk.Button(row, text="Detect my ZeroTier IP", command=self.zt_detect).grid(row=0, column=2, padx=4)
        row2 = ttk.Frame(box)
        row2.grid(row=2, column=2, sticky="w", **pad)
        self.zt_btn = ttk.Button(row2, text="Checking ZeroTier...", command=self.zt_toggle, width=34)
        self.zt_btn.grid(row=0, column=0)
        self.zt_msg = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.zt_msg).grid(row=3, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(box, wraplength=780, justify="left", text=(
            "The IP is saved here and found again each time the launcher opens. For an address that never changes, "
            "open your network in my.zerotier.com, find your PC under Members and give it a fixed IP under "
            "'Managed IPs' (or tick 'Do not auto-assign IPs' and type one). Then click Detect.")).grid(
            row=4, column=0, columnspan=2, sticky="w", **pad)
        opt = ttk.LabelFrame(z, text="Game")
        opt.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=6)
        ttk.Label(opt, text="Bots wait to start until this many players have joined (you count as one)").grid(
            row=0, column=0, sticky="w", **pad)
        self.zt_users = tk.IntVar(value=max(2, int(saved.get("zt_users", 2) or 2)))
        ttk.Spinbox(opt, from_=2, to=10, width=5, textvariable=self.zt_users).grid(row=0, column=1, **pad)
        ttk.Label(opt, wraplength=780, justify="left", text=(
            "The bots, mode, map and all other settings come from the Play tab. If you leave the game, it stays up "
            "for your friends; use 'Close all game windows' to end it.")).grid(
            row=1, column=0, columnspan=2, sticky="w", **pad)
        b2 = ttk.Button(z, text="LAUNCH ZEROTIER GAME", command=lambda: self.launch(zerotier=True), width=26,
                        style="Launch.TButton")
        b2.grid(row=3, column=0, columnspan=2, pady=14)
        self.launch_btns.append((b2, "LAUNCH ZEROTIER GAME"))
        ttk.Label(z, text="Windows Firewall must allow OutlawsAHOM.exe on the ZeroTier network, and friends join with "
                          "the address above.", wraplength=820, justify="left").grid(
            row=4, column=0, columnspan=2, sticky="w", **pad)
        self.zt_detect(quiet=True)
        self.zt_refresh_button()

    # -- Gun-Game tab
    GG_WEAPONS = [("Double-barrel", 5), ("Shotgun", 4), ("Rifle", 3), ("Gatling gun", 9), ("Sawed-off", 6),
                  ("Pistol", 2), ("TNT", 7), ("Knife", 8), ("Fists", 1)]
    GG_ORDERS = ["Default (TNT and knife last)", "Randomized", "Custom"]   # Randomized: reshuffled every game, knife last

    def build_gungame(self, saved):
        pad = dict(padx=8, pady=4)
        g, sv = self.gg, saved.get("gungame", {})
        self.about(g, "Gun-Game", (
            "Everyone starts with the first weapon on the ladder and can only use that weapon. A kill moves you up a "
            "weapon; dying drops you back one. Kill someone with the last weapon to win.\n\nThe bots use the all-weapons "
            "cheat to get each weapon, so the host turns on 'Allow Cheats'.\n\nBot names, characters and strengths come "
            "from the Play tab (Advanced Options). Your own game joins automatically while 'I play too' is ticked on the Play tab."),
            style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(2, 0))
        box = ttk.LabelFrame(g, text="Game")
        box.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        ttk.Label(box, text="Map").grid(row=0, column=0, sticky="w", **pad)
        mf = ttk.Frame(box)
        mf.grid(row=0, column=1, sticky="w", **pad)
        want = (sv.get("map") or "").replace(" (custom)", "")
        grp = "Custom maps" if want in MAP_GROUPS["Custom maps"] else "Standard maps"
        self.gg_group = ttk.Combobox(mf, values=list(MAP_GROUPS), state="readonly", width=14)
        self.gg_group.set(grp)
        self.gg_group.grid(row=0, column=0, padx=(0, 4))
        self.gg_map = ttk.Combobox(mf, values=MAP_GROUPS[grp], state="readonly", width=26)
        self.gg_map.set(want if want in MAP_GROUPS[grp] else MAP_GROUPS[grp][0])
        self.gg_map.grid(row=0, column=1)

        def pick_group(_e=None):
            names = MAP_GROUPS.get(self.gg_group.get()) or MAP_GROUPS["Standard maps"]
            self.gg_map.configure(values=names)
            self.gg_map.set(names[0])
        self.gg_group.bind("<<ComboboxSelected>>", pick_group)
        self.gg_relay = tk.BooleanVar(value=bool(sv.get("relay", False)))
        ttk.Checkbutton(box, text="Relay: Red and Blue each share ONE ladder (a kill by anyone on a team moves the whole team up; "
                                  "dying costs nothing). Pick your team in the lobby.",
                        variable=self.gg_relay).grid(row=5, column=0, columnspan=2, sticky="w", **pad)
        self.gg_rounds = tk.BooleanVar(value=bool(sv.get("rounds", True)))
        ttk.Checkbutton(box, text="Play 3 rounds (everyone starts over after each knife win; the game ends after the third)",
                        variable=self.gg_rounds).grid(row=4, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(box, text="Bots to play against").grid(row=1, column=0, sticky="w", **pad)
        self.gg_count = tk.IntVar(value=int(sv.get("count", 3)))
        ttk.Spinbox(box, from_=1, to=10, width=5, textvariable=self.gg_count).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(box, text="Bot difficulty").grid(row=2, column=0, sticky="w", **pad)
        self.gg_diff = ttk.Combobox(box, values=DIFFICULTIES + ["Custom"], state="readonly", width=12)
        saved_pb = sv.get("bot_diffs") or []
        any_pb = any(x in DIFFICULTIES for x in saved_pb)
        self.gg_last_base = sv.get("difficulty") if sv.get("difficulty") in DIFFICULTIES else "Normal"
        self.gg_diff.set("Custom" if (any_pb or sv.get("difficulty") == "Custom") else self.gg_last_base)
        self.gg_diff.grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(box, text="Per-bot difficulty").grid(row=3, column=0, sticky="nw", **pad)
        pf = ttk.Frame(box)
        pf.grid(row=3, column=1, sticky="w", **pad)
        self.gg_bot_diffs = []
        self.gg_bot_rows = []
        for i in range(10):
            lb = ttk.Label(pf, text=f"Bot {i + 1}")
            cb = ttk.Combobox(pf, values=["Same as above"] + DIFFICULTIES, state="readonly", width=13)
            cb.set(saved_pb[i] if i < len(saved_pb) and saved_pb[i] in DIFFICULTIES else "Same as above")
            cb.bind("<<ComboboxSelected>>", lambda e, i=i: self.gg_pb_changed(i))
            self.gg_bot_diffs.append(cb)
            self.gg_bot_rows.append((lb, cb))
        self.gg_diff.bind("<<ComboboxSelected>>", lambda e: self.gg_diff_changed())
        self.gg_count.trace_add("write", lambda *_: self.gg_pb_refresh())
        self.gg_pb_refresh()
        gun = ttk.LabelFrame(g, text="Weapons")
        gun.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
        ttk.Label(gun, text="Weapon order").grid(row=0, column=0, sticky="w", **pad)
        self.gg_order = ttk.Combobox(gun, values=self.GG_ORDERS, state="readonly", width=28)
        self.gg_order.set(sv.get("order") if sv.get("order") in self.GG_ORDERS else self.GG_ORDERS[0])
        self.gg_order.grid(row=0, column=1, sticky="w", **pad)
        self.gg_order.bind("<<ComboboxSelected>>", lambda e: self.gg_refresh())
        self.gg_gat = tk.BooleanVar(value=bool(sv.get("gatling", True)))
        self.check(gun, "Include the gatling gun", self.gg_gat,
                   "Players holding the gatling gun stand still while using it (unless the box below is ticked).",
                   command=self.gg_refresh).grid(row=1, column=0, columnspan=2, sticky="w", **pad)
        self.gg_stin = tk.BooleanVar(value=bool(sv.get("stinnett", False)))
        self.check(gun, "Move while using the gatling gun", self.gg_stin,
                   "Uses the game's STINNETT cheat so everyone can move with the gatling gun. The game then flashes "
                   "characters yellow as cheaters.").grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=4)
        self.gg_custom = ttk.Frame(gun)
        self.gg_custom.grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=4)
        names = [n for n, _ in self.GG_WEAPONS]
        saved_c = sv.get("custom") if isinstance(sv.get("custom"), list) and len(sv.get("custom")) == len(names) else names[:-1] + ["Knife"]   # (Fists is in the list but not in the default order; a weapon twice counts once)
        self.gg_slots = []
        for i in range(len(names)):
            r, c = divmod(i, 4)
            ttk.Label(self.gg_custom, text=f"{i + 1}.").grid(row=r, column=c * 2, sticky="e", padx=(4, 2), pady=2)
            cb = ttk.Combobox(self.gg_custom, values=names, state="readonly", width=14)
            cb.set(saved_c[i] if saved_c[i] in names else names[i])
            cb.grid(row=r, column=c * 2 + 1, padx=(0, 8), pady=2)
            self.gg_slots.append(cb)
        b = ttk.Button(g, text="LAUNCH GUN-GAME", command=lambda: self.launch(gungame=True), width=26,
                       style="Launch.TButton")
        b.grid(row=3, column=0, pady=14)
        self.launch_btns.append((b, "LAUNCH GUN-GAME"))
        ttk.Frame(g, height=40).grid(row=4, column=0)             # (clear of the buttons bottom right)
        self.gg_refresh()

    # -- Juggernaut / Infection (roles.py): one small settings page for both
    def build_roles(self, saved):
        pad = dict(padx=8, pady=4)
        g, sv = self.rl, saved.get("roles", {})
        box = ttk.LabelFrame(g, text="Game")
        box.grid(row=0, column=0, sticky="ew", padx=12, pady=6)
        ttk.Label(box, text="Map").grid(row=0, column=0, sticky="w", **pad)
        mf = ttk.Frame(box)
        mf.grid(row=0, column=1, sticky="w", **pad)
        want = (sv.get("map") or "").replace(" (custom)", "")
        grp = "Custom maps" if want in MAP_GROUPS["Custom maps"] else "Standard maps"
        self.rl_group = ttk.Combobox(mf, values=list(MAP_GROUPS), state="readonly", width=14)
        self.rl_group.set(grp)
        self.rl_group.grid(row=0, column=0, padx=(0, 4))
        self.rl_map = ttk.Combobox(mf, values=MAP_GROUPS[grp], state="readonly", width=26)
        self.rl_map.set(want if want in MAP_GROUPS[grp] else MAP_GROUPS[grp][0])
        self.rl_map.grid(row=0, column=1)

        def pick_group(_e=None):
            names = MAP_GROUPS.get(self.rl_group.get()) or MAP_GROUPS["Standard maps"]
            self.rl_map.configure(values=names)
            self.rl_map.set(names[0])
        self.rl_group.bind("<<ComboboxSelected>>", pick_group)
        ttk.Label(box, text="Bots to play against").grid(row=1, column=0, sticky="w", **pad)
        self.rl_count = tk.IntVar(value=int(sv.get("count", 4)))
        ttk.Spinbox(box, from_=2, to=10, width=5, textvariable=self.rl_count).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(box, text="Bot difficulty").grid(row=2, column=0, sticky="w", **pad)
        self.rl_diff = ttk.Combobox(box, values=DIFFICULTIES, state="readonly", width=12)
        self.rl_diff.set(sv.get("difficulty") if sv.get("difficulty") in DIFFICULTIES else "Normal")
        self.rl_diff.grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(box, text="Minutes").grid(row=3, column=0, sticky="w", **pad)
        self.rl_minutes = tk.IntVar(value=int(sv.get("minutes", 10)))
        ttk.Spinbox(box, from_=2, to=60, width=5, textvariable=self.rl_minutes).grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(box, text="Kill limit").grid(row=4, column=0, sticky="w", **pad)
        self.rl_kills = tk.IntVar(value=int(sv.get("kills", 0)))
        kf = ttk.Frame(box)
        kf.grid(row=4, column=1, sticky="w", **pad)
        ttk.Spinbox(kf, from_=0, to=200, width=5, textvariable=self.rl_kills).grid(row=0, column=0)
        self.hint(kf, "The first player to this many kills ends the game. 0 = no kill limit. Not used in Infection.").grid(row=0, column=1, padx=(8, 0))
        self.rl_law = tk.BooleanVar(value=bool(sv.get("marshall_law", True)))
        self.check(box, "Marshall Law", self.rl_law, "The bots leave the badge and the invisible cream alone.").grid(
            row=5, column=0, columnspan=2, sticky="w", **pad)
        b = ttk.Button(g, text="LAUNCH", command=lambda: self.launch(roles=self.gm_mode.get()), width=26, style="Launch.TButton")
        b.grid(row=1, column=0, pady=14)
        self.launch_btns.append((b, "LAUNCH"))
        self.about(g, "Good to know", (
            "Bot names, characters and strengths come from the Play tab (Advanced Options). Your own game joins automatically while "
            "'I play too' is ticked on the Play tab. The host turns on 'Allow Cheats'.\n\nYour name on the Stats tab is "
            "what lets the game give YOU a role; without it you play as a plain player."),
            style="Muted.TLabel").grid(row=2, column=0, sticky="w", padx=14, pady=(4, 46))   # (clear of the buttons bottom right)

    # -- Stats tab
    GHOST_CHARMODES = ["Custom", "Random"]

    def ghost_charmode_apply(self, first=False):
        """Random: every ghost's character shows Random and can't be changed; Custom: each ghost's own pick back"""
        rnd = self.ghost_charmode.get() == "Random"
        for i, c in enumerate(self.ghost_char_w):
            if rnd:
                if not first and str(c.cget("state")) != "disabled": self.ghost_custom_chars[i] = c.get()
                c.set("Random"); c.configure(state="disabled")
            else:
                c.configure(state="readonly")
                if not first: c.set(self.ghost_custom_chars[i])

    def ghost_diffmode_apply(self, first=False):
        """one difficulty picked: every ghost shows it and can't be changed; Custom: each ghost's own difficulty back"""
        mode = self.ghost_diffmode.get()
        for i, d in enumerate(self.ghost_diff_w):
            if mode in DIFFICULTIES:
                if not first and str(d.cget("state")) != "disabled": self.ghost_custom_diffs[i] = d.get()
                d.set(mode); d.configure(state="disabled")
            else:
                d.configure(state="readonly")
                if not first: d.set(self.ghost_custom_diffs[i])

    def ghost_diff_saved(self, i):
        """what to save for ghost i's difficulty: its Custom pick (one-for-all keeps them for later)"""
        if getattr(self, "ghost_diffmode", None) is not None and self.ghost_diffmode.get() in DIFFICULTIES:
            return self.ghost_custom_diffs[i]
        return self.ghost_diff_w[i].get()

    def ghost_char_saved(self, i):
        """what to save for ghost i's character: its Custom pick (Random mode keeps them for later)"""
        if getattr(self, "ghost_charmode", None) is not None and self.ghost_charmode.get() == "Random":
            return self.ghost_custom_chars[i]
        return self.ghost_char_w[i].get()

    GHOST_CHARS = [("Mary", "mary"), ("Bob", "bob"), ("James", "james"), ("Dr Death", "drdeath"), ("Sanchez", "sanchez"),
                   ("Two Feathers", "twofeathers"), ("Random", "random")]

    # Normal: each character's own style; Melee only: fists (Chief and anyone with a knife: the knife); TNT only: dynamite, then knife or fists.
    # A bots' game that is TNT only (or Melee Only Deathmatch) makes the ghosts follow it whatever is picked here.
    GHOST_WEAPON_MODES = ["Normal", "Melee only", "TNT only"]

    def build_ghosts(self, saved):
        """The Ghosts tab: ghost players (light-weight clients, ghost.py) that join the game and fight; each has its own
        character and difficulty. How many play, and starting the game, is on the Play tab."""
        pad = dict(padx=8, pady=4)
        g = self.gh
        self.about(g, "Ghosts", (
            "Ghosts are light-weight stand-ins for bots: they join the game over the network instead of each running a full "
            "copy of the game, so many fit on one PC. They fight at the bots' difficulty levels, take their character's "
            "damage, start weapon and voice from the game's files, swim, and respawn at a start position.\n\n"
            "Pick Ghosts (or Bots + Ghosts) under Players to play against them.\n\n"
            "Only Mary has a hurt sound in the game; the other characters stay silent when hit. Health and speed are the "
            "same for every character (the game's character files don't set them).")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(14, 4))
        card = ttk.LabelFrame(g, text="Each ghost")
        card.grid(row=1, column=0, sticky="ew", padx=14, pady=6)
        old = saved.get("ghost_diff") if saved.get("ghost_diff") in DIFFICULTIES else "Normal"
        rows = saved.get("ghost_rows") or []
        self.ghost_diff_default = old
        names = [n for n, _ in self.GHOST_CHARS]
        self.ghost_char_w, self.ghost_diff_w, self.ghost_row_w = [], [], []
        self.ghost_rows_fr = rf = ttk.Frame(card)                 # ghosts 1-5, 6-10 and 11-15 side by side
        rf.grid(row=0, column=0, sticky="nw")
        cmf = ttk.Frame(rf); cmf.grid(row=0, column=0, columnspan=11, sticky="w", **pad)
        ttk.Label(cmf, text="How many").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Spinbox(cmf, from_=1, to=MAX_GHOSTS, width=4, textvariable=self.ghost_count, command=self.ghost_rows_refresh).grid(row=0, column=1, sticky="w", padx=8, pady=2)
        self.ghost_count.trace_add("write", lambda *_: self.ghost_rows_refresh())
        # Characters: Custom (each ghost's own pick below) or Random (every ghost "Random": a new character at each launch)
        ttk.Label(cmf, text="Characters").grid(row=0, column=2, sticky="w", padx=(24, 0), pady=2)
        self.ghost_charmode = ttk.Combobox(cmf, values=self.GHOST_CHARMODES, state="readonly", width=10)
        self.ghost_charmode.set(saved.get("ghost_charmode") if saved.get("ghost_charmode") in self.GHOST_CHARMODES else "Custom")
        self.ghost_charmode.grid(row=0, column=3, sticky="w", padx=8, pady=2)
        self.hint(cmf, "Custom: pick each ghost's character below.\nRandom: every ghost gets a new random character at each launch.").grid(row=0, column=4)
        # Difficulty: one difficulty for every ghost, or Custom (each ghost's own pick below)
        ttk.Label(cmf, text="Difficulty").grid(row=0, column=5, sticky="w", padx=(24, 0), pady=2)
        self.ghost_diffmode = ttk.Combobox(cmf, values=["Custom"] + DIFFICULTIES, state="readonly", width=10, textvariable=self.ghost_diffmode_v)
        self.ghost_diffmode.grid(row=0, column=6, sticky="w", padx=8, pady=2)
        self.hint(cmf, "Custom: pick each ghost's difficulty below.\nAny other choice: every ghost plays at that difficulty.").grid(row=0, column=7)
        for blk in (0, 4, 8):
            ttk.Label(rf, text="Ghost", style="Muted.TLabel").grid(row=1, column=blk, sticky="w", **pad)
            ttk.Label(rf, text="Character", style="Muted.TLabel").grid(row=1, column=blk + 1, sticky="w", **pad)
            ttk.Label(rf, text="Difficulty", style="Muted.TLabel").grid(row=1, column=blk + 2, sticky="w", **pad)
        self.ghost_rows_head = [w for w in rf.grid_slaves(row=1) if int(w.grid_info()["column"]) >= 4]
        ttk.Frame(rf, width=24).grid(row=1, column=3); ttk.Frame(rf, width=24).grid(row=1, column=7)
        for i in range(MAX_GHOSTS):
            r = rows[i] if i < len(rows) and isinstance(rows[i], dict) else {}
            lab = ttk.Label(rf, text=f"Ghost {i + 1}")
            cc = ttk.Combobox(rf, values=names, state="readonly", width=14)
            cc.set(r.get("char") if r.get("char") in names else "Mary")
            dd = ttk.Combobox(rf, values=DIFFICULTIES, state="readonly", width=14)
            dd.set(r.get("diff") if r.get("diff") in DIFFICULTIES else old)
            self.ghost_char_w.append(cc); self.ghost_diff_w.append(dd); self.ghost_row_w.append((lab, cc, dd))
        self.ghost_custom_chars = [c.get() for c in self.ghost_char_w]     # the Custom picks (kept while Random shows)
        self.ghost_charmode.bind("<<ComboboxSelected>>", lambda _=None: self.ghost_charmode_apply())
        self.ghost_charmode_apply(first=True)
        self.ghost_custom_diffs = [d.get() for d in self.ghost_diff_w]     # the Custom difficulties (kept while one for all shows)
        self.ghost_diffmode.bind("<<ComboboxSelected>>", lambda _=None: self.ghost_diffmode_apply())
        self.ghost_diffmode_apply(first=True)

        oc = self.ghost_opts = ttk.LabelFrame(g, text="Ghost options")
        oc.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 10))
        # (13 Nov, the user: no choice any more - every ghost starts with its character's own weapons and damage, and the
        #  splash / swimming sounds are always on.)
        self.ghost_wpn = tk.BooleanVar(value=True)
        self.ghost_swim_snd = tk.BooleanVar(value=True)
        # (the Weapons choice is under Game rules now, for the bots too; this hidden box follows it)
        self.ghost_weapons = ttk.Combobox(oc, values=self.GHOST_WEAPON_MODES, state="readonly", width=14)
        self.ghost_weapons.set(self.weapons_rule.get())
        gs = saved.get("ghost_game") or {}
        self.gh_delay_lab = self.labeled(oc, "Join after (seconds)", "How long after your game starts the ghosts join. Ghosts that "
                                                 "connect while your game is still loading drop you back to the main menu: raise "
                                                 "the number if that happens.\n\nBots + Ghosts: at least 30 seconds, since your game "
                                                 "first has to load and join the bots' game.")
        self.gh_delay_lab.grid(row=1, column=0, sticky="w", **pad)
        self.gh_delay = tk.StringVar(value=str(gs.get("delay", "14")))
        self.gh_delay_ent = ttk.Entry(oc, textvariable=self.gh_delay, width=6)
        self.gh_delay_ent.grid(row=1, column=1, sticky="w", **pad)

        # -- a game you host, with ghosts and no bots: its map, mode and limits are the Play tab's now. These boxes are the
        #    old Ghosts-tab game box, kept (never shown) because launch_ghost_game and the saved setups read them; play_launch
        #    fills them from the Play tab before each ghost game.
        hf = ttk.Frame(g)
        self.gh_hf = None
        mf = ttk.Frame(hf)
        want = gs.get("map") if gs.get("map") in dict(MAPS) else "Hargrove's Pattern Repair"
        grp = "Custom maps" if want in MAP_GROUPS["Custom maps"] else "Standard maps"
        self.gh_group = ttk.Combobox(mf, values=list(MAP_GROUPS), state="readonly", width=14); self.gh_group.set(grp)
        self.gh_map = ttk.Combobox(mf, values=MAP_GROUPS[grp], state="readonly", width=26)
        self.gh_map.set(want if want in MAP_GROUPS[grp] else MAP_GROUPS[grp][0])
        self.gh_mode = ttk.Combobox(hf, values=[m for m, _ in MODES], state="readonly", width=20)
        self.gh_mode.set(gs.get("mode") if gs.get("mode") in dict(MODES) else "Deathmatch")
        self.gh_frag = tk.StringVar(value=str(gs.get("frag", "")))
        self.gh_time = tk.StringVar(value=str(gs.get("time", "")))
        self.gh_private = tk.BooleanVar(value=bool(gs.get("private", False)))
        # Team split (team modes only, like the bots'): how many ghosts play on Red; the rest play on Blue. Blank = the game
        # evens them out. Shown on the Play tab, in the Players box, for a ghosts-only game.
        self.gh_split = tk.Frame(self.pl, bg=PARCH)
        self.gh_red = tk.StringVar(value=str(gs.get("red", "")) if str(gs.get("red", "")).isdigit() else "")
        self.gh_blue = tk.StringVar(value=str(gs.get("blue", "")) if str(gs.get("blue", "")).isdigit() else "")
        ttk.Label(self.gh_split, text="Red Team").pack(side="left")
        ttk.Entry(self.gh_split, textvariable=self.gh_red, width=4).pack(side="left", padx=(6, 14))
        ttk.Label(self.gh_split, text="Blue Team").pack(side="left")
        ttk.Entry(self.gh_split, textvariable=self.gh_blue, width=4).pack(side="left", padx=(6, 8))
        self.hint(self.gh_split, "Team modes: how many ghosts play on Red and on Blue. Fill in one box and the other fills "
                                 "itself. Empty = the game evens the teams out. You pick your own side in the lobby.").pack(side="left", padx=(0, 10))
        self.gh_split_info = ttk.Label(self.gh_split, text="", style="Muted.TLabel")
        self.gh_split_info.pack(side="left")
        self.gh_split_src, self.gh_split_busy = "red", False
        def _gtyped(which):
            def f(*a):
                if not self.gh_split_busy:
                    self.gh_split_src = which
                    self.gh_split_refresh()
            return f
        self.gh_red.trace_add("write", _gtyped("red")); self.gh_blue.trace_add("write", _gtyped("blue"))
        self.ghost_count.trace_add("write", lambda *_: (self.gh_split_refresh(), self.refresh_all() if self.who.get() == "Bots + Ghosts" and hasattr(self, "play_sum") else self.play_summary()))
        self.ghost_rows_refresh()
        self.gh_split_refresh()

    def gh_split_red(self):
        """A ghosts-only game's team split for this launch: how many ghosts go Red (the rest Blue), or None (the game evens them out)"""
        if self.mode_value() not in TEAM_MODES:
            return None
        try:
            n = max(1, min(MAX_GHOSTS, int(self.ghost_count.get())))
        except (tk.TclError, ValueError):
            return None
        r, b = self.gh_red.get().strip(), self.gh_blue.get().strip()
        if self.gh_split_src == "blue" and b.isdigit(): return n - min(int(b), n)
        if r.isdigit(): return min(int(r), n)
        if b.isdigit(): return n - min(int(b), n)
        return None

    def gh_split_refresh(self):
        if not hasattr(self, "gh_split"):
            return
        if self.mode_value() not in TEAM_MODES or self.who.get() != "Ghosts":
            self.gh_split.grid_remove(); return
        self.gh_split.grid(row=4, column=0, sticky="w", padx=10, pady=4)
        red = self.gh_split_red()
        if red is None:
            self.gh_split_info.configure(text="empty: the game evens the teams out"); return
        try:
            n = max(1, min(MAX_GHOSTS, int(self.ghost_count.get())))
        except (tk.TclError, ValueError):
            return
        self.gh_split_busy = True                 # (filling in the other box is not the user typing)
        try:
            src_var, other_var, other = (self.gh_blue, self.gh_red, red) if self.gh_split_src == "blue" else (self.gh_red, self.gh_blue, n - red)
            if other_var.get() != str(other): other_var.set(str(other))
        finally:
            self.gh_split_busy = False
        self.gh_split_info.configure(text=f"{red} on Red, {n - red} on Blue")

    # -- the Play tab's switch: who plays in the game
    WHO = ["Bots", "Ghosts", "Bots + Ghosts"]

    def who_changed(self):
        """Bots / Ghosts / Bots + Ghosts was picked: show that choice's rows, and tell the ghosts' on-off box."""
        w = self.who.get()
        self.ghost_on.set(w != "Bots")
        if w == "Ghosts": self.bots_row.grid_remove(); self.me_row.grid_remove()
        else: self.bots_row.grid(); self.me_row.grid()
        if w == "Bots": self.ghosts_row.grid_remove(); self.gh_now_btn.grid_remove()
        else: self.ghosts_row.grid(); self.gh_now_btn.grid()
        self.update_modes()                        # (a ghosts-only game can add Team Deathmatch to some maps; also the team rows)
        self.adv_apply()

    # -- Advanced Options on the Play tab: each bot and each ghost, folded away until clicked
    def adv_apply(self):
        """Show or hide Advanced Options, and in it only the part for who plays (bots, ghosts or both)."""
        if not hasattr(self, "adv_btn"):
            return
        w = self.who.get()
        if w == "Ghosts": self.bt.grid_remove()
        else: self.bt.grid()
        if w == "Bots": self.gh.grid_remove()
        else: self.gh.grid()
        if w == "Ghosts": self.trim_row.grid_remove()
        else: self.trim_row.grid()
        if self.adv_open: self.adv.grid(row=8, column=0, sticky="w")
        else: self.adv.grid_remove()
        self.adv_btn.configure(text="Advanced Options   " + ("\u25b2" if self.adv_open else "\u25bc"))
        self.play_fit()

    def adv_toggle(self):
        self.adv_open = not self.adv_open
        self.adv_apply()
        if self.adv_open:                          # (scroll a little so the newly opened part shows)
            self.after(60, lambda: self.adv_scroll_to(self.adv))

    def adv_show(self, part):
        """'Set up each bot / ghost': open Advanced Options and scroll to that part."""
        self.adv_open = True
        self.adv_apply()
        self.after(60, lambda: self.adv_scroll_to(part))

    def adv_scroll_to(self, w):
        try:
            self.update_idletasks()
            total = max(1, self.play.winfo_reqheight())
            y = w.winfo_rooty() - self.play.winfo_rooty()
            self.play_cv.yview_moveto(max(0.0, min(1.0, (y - 10) / total)))
        except tk.TclError:
            pass

    def play_summary(self):
        """One plain line under LAUNCH GAME: what pressing it will do."""
        if not hasattr(self, "play_sum"):
            return
        try:
            n = max(1, min(10, int(self.count.get())))
            gn = max(1, min(MAX_GHOSTS, int(self.ghost_count.get())))
        except (tk.TclError, ValueError):
            return
        bots, ghosts = f"{n} bot" + ("s" if n != 1 else ""), f"{gn} ghost" + ("s" if gn != 1 else "")
        where = f"{self.map.get()}, {self.mode.get()}"
        w = self.who.get()
        if w == "Ghosts":
            t = f"{where}: you host the game and {ghosts} join you."
        else:
            me = "your game joins it" if self.wait_me.get() else "your own game is not started"
            t = f"{where}: {bots} host the game, {me}" + (f", then {ghosts} join." if w != "Bots" else ".")
        self.play_sum.set(t)

    def play_launch(self):
        """LAUNCH GAME on the Play tab. Ghosts: you host and the ghosts join you (launch_ghost_game, with the Play tab's map,
        mode and limits). Bots, or Bots + Ghosts: the bots host and your game joins (launch)."""
        if self.who.get() == "Ghosts":
            grp = self.map_group.get()
            self.gh_group.set(grp)
            self.gh_map.configure(values=MAP_GROUPS.get(grp) or MAP_GROUPS["Standard maps"])
            self.gh_map.set(self.map.get())
            self.gh_mode.configure(values=[m for m, _ in MODES])
            self.gh_mode.set(self.mode.get())
            self.gh_frag.set(self.fraglimit.get().strip())
            self.gh_time.set(self.timelimit.get().strip())
            self.gh_private.set(bool(self.private.get()))
            self.save()
            self.launch_ghost_game()
        else:
            self.launch()

    def ghost_rows_refresh(self):
        try:
            n = max(1, min(MAX_GHOSTS, int(self.ghost_count.get())))
        except (tk.TclError, ValueError):
            n = 1
        for w in self.ghost_rows_head:                              # a column's headings only once its first ghost is there
            c_ = int(w.grid_info().get("column", 0)) if w.winfo_manager() else int(getattr(w, "_col", 4))
            w._col = c_
            w.grid() if n > (5 if c_ < 8 else 10) else w.grid_remove()
        for i, (lab, cc, dd) in enumerate(self.ghost_row_w):
            if i < n:
                r, c = 2 + i % 5, (i // 5) * 4
                lab.grid(row=r, column=c, sticky="w", padx=8, pady=4)
                cc.grid(row=r, column=c + 1, sticky="w", padx=8, pady=4)
                dd.grid(row=r, column=c + 2, sticky="w", padx=8, pady=4)
            else:
                lab.grid_remove(); cc.grid_remove(); dd.grid_remove()

    def build_stats(self, saved):
        pad = dict(padx=8, pady=4)
        g = self.st
        # (13 Nov, the user: no "Survival Horde results" heading - the numbers are from every launcher game the bots host)
        ttk.Frame(g, height=10).grid(row=0, column=0)
        ttk.Label(g, text="Your name").grid(row=1, column=0, sticky="e", **pad)
        self.stats_name = tk.StringVar(value=saved.get("stats_player") or (saved.get("modes") or {}).get("player", ""))
        ttk.Entry(g, textvariable=self.stats_name, width=22).grid(row=1, column=1, sticky="w", **pad)
        bf = ttk.Frame(g)
        bf.grid(row=1, column=2, sticky="w", **pad)
        ttk.Button(bf, text="Refresh", command=self.stats_refresh).pack(side="left")
        ttk.Button(bf, text="Reset Stats", command=self.stats_reset).pack(side="left", padx=(8, 0))
        ttk.Button(bf, text="Match history", command=lambda: self.tool("show_history")).pack(side="left", padx=(8, 0))
        self.stats_sum = tk.StringVar()
        ttk.Label(g, textvariable=self.stats_sum, justify="left", font=("TkDefaultFont", 11, "bold")).grid(
            row=2, column=0, columnspan=3, sticky="w", **pad)
        cols = ("Map", "Games", "Kills", "Deaths", "Suicides", "K/D")
        self.stats_tree = ttk.Treeview(g, columns=cols, show="headings", height=10)
        for c, w in zip(cols, (180, 70, 70, 70, 80, 70)):
            self.stats_tree.heading(c, text=c)
            self.stats_tree.column(c, width=w, anchor="center" if c != "Map" else "w")
        self.stats_tree.grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=6)
        # Characters: each of the six has its own XP, level and prestige (xp.py; the online rankings when set up, see relay.py)
        ttk.Label(g, text="Characters: XP, level and prestige", font=("TkDefaultFont", 11, "bold")).grid(row=4, column=0, columnspan=3, sticky="w", padx=8, pady=(10, 0))
        ccols = ("Character", "Prestige", "Level", "XP", "XP to next level")
        self.xp_tree = ttk.Treeview(g, columns=ccols, show="headings", height=6, selectmode="browse")
        for c, w in zip(ccols, (180, 90, 70, 90, 130)):
            self.xp_tree.heading(c, text=c)
            self.xp_tree.column(c, width=w, anchor="center" if c != "Character" else "w")
        self.xp_tree.grid(row=5, column=0, columnspan=3, sticky="w", padx=12, pady=4)
        self.xp_tree.bind("<<TreeviewSelect>>", lambda _e: self.xp_buttons())
        self.xp_tree.bind("<Double-1>", lambda _e: self.prestige_looks())
        pf = ttk.Frame(g)
        pf.grid(row=6, column=0, columnspan=3, sticky="w", padx=12)
        self.prestige_btn = ttk.Button(pf, text="Prestige this character", command=self.xp_prestige, state="disabled")
        self.prestige_btn.pack(side="left")
        looks = ttk.Button(pf, text="Prestige looks", command=self.prestige_looks)
        looks.pack(side="left", padx=(8, 0))
        self.tip(looks, "What the character looks like at each prestige: normal, bronze, silver and gold. Pick a character in the "
                        "table first (or double-click one); with none picked, all six show.")
        self.xp_note = tk.StringVar()
        ttk.Label(pf, textvariable=self.xp_note, wraplength=560, justify="left").pack(side="left", padx=10)
        self.xp_status = {}
        self.stats_refresh()

    def xp_refresh(self):
        """Fill the character table (level, prestige, XP per character)."""
        for i in self.xp_tree.get_children():
            self.xp_tree.delete(i)
        self.xp_status = {}
        name = self.stats_name.get().strip()
        self.prestige_btn.configure(state="disabled")
        if not name:
            self.xp_note.set("")
            return
        try:
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import xp
            source, st = xp.overview(name)
        except Exception as e:
            self.xp_note.set(f"XP unavailable: {e}")
            return
        self.xp_status = st
        for label, key in CHARACTERS:
            s = st.get(key)
            if not s:
                continue
            tiers = ["-", "Bronze", "Silver", "Gold"]
            self.xp_tree.insert("", "end", iid=key, values=(label, tiers[min(s["prestige"], 3)], s["level"], s["xp"],
                                                           "MAX LEVEL" if s["to_next"] is None else s["to_next"]))
        self.xp_note.set(f"From: {source}. A character at level 50 can prestige (up to 3 times: bronze, silver, gold); it starts again at level 1.")

    def prestige_looks(self):
        """A small window with the prestige looks (normal, bronze, silver, gold) of the picked character, or of all six."""
        try:
            from prestige_data import PREVIEWS
        except Exception:
            messagebox.showinfo("Prestige looks", "The pictures (prestige_data.py) are not in the botctl folder.")
            return
        sel = self.xp_tree.selection()
        keys = [sel[0]] if sel and sel[0] in PREVIEWS else [k for _l, k in CHARACTERS if k in PREVIEWS]
        names = {k: l for l, k in CHARACTERS}
        old = getattr(self, "_looks_win", None)
        if old is not None:
            try: old.destroy()
            except tk.TclError: pass
        w = self._looks_win = tk.Toplevel(self)
        w.title("Prestige looks" + (f": {names.get(keys[0], keys[0])}" if len(keys) == 1 else ""))
        BG = "#000000"                                       # (13 Nov, the user: black, so the tier outlines stand out)
        w.configure(bg=BG)
        w.transient(self)
        w._imgs = []
        tiers = [("Normal", "#e8dcc4"), ("Bronze", "#cd7f32"), ("Silver", "#c0c0c0"), ("Gold", "#ffd700")]
        for c, (t, col) in enumerate(tiers):
            tk.Label(w, text=t, bg=BG, fg=col, font=(DISPLAY, 12, "bold")).grid(row=0, column=c + 1, padx=10, pady=(10, 2))
        for r, k in enumerate(keys):
            if len(keys) > 1:
                tk.Label(w, text=names.get(k, k), bg=BG, fg="#e8dcc4", font=(DISPLAY, 11, "bold")).grid(row=r + 1, column=0, padx=(12, 4), sticky="e")
            for c, b64 in enumerate(PREVIEWS[k]):
                img = tk.PhotoImage(data=b64)
                if len(keys) > 2:
                    img = img.subsample(2, 2)                 # (all six: half size so the window fits the screen)
                w._imgs.append(img)
                tk.Label(w, image=img, bg=BG).grid(row=r + 1, column=c + 1, padx=10, pady=6)
        tk.Label(w, text="Prestige starts the character again at level 1 with this look (up to three times).", bg=BG, fg="#b8a98f",
                 font=("Segoe UI", 9, "italic")).grid(row=len(keys) + 1, column=0, columnspan=5, pady=(4, 10))

    def xp_buttons(self):
        sel = self.xp_tree.selection()
        ok = bool(sel) and bool((self.xp_status.get(sel[0]) or {}).get("can_prestige"))
        self.prestige_btn.configure(state="normal" if ok else "disabled")

    def xp_prestige(self):
        sel = self.xp_tree.selection()
        name = self.stats_name.get().strip()
        if not sel or not name:
            return
        label = dict((k, l) for l, k in CHARACTERS).get(sel[0], sel[0])
        if not messagebox.askyesno("Prestige", f"Prestige {label}?\n\n{label} goes back to level 1 and gets the next prestige look "
                                   "(bronze, silver, then gold). This cannot be undone.", icon="warning", default="no"):
            return
        try:
            import xp
            ok, msg = xp.do_prestige(name, sel[0])
        except Exception as e:
            ok, msg = False, str(e)
        (messagebox.showinfo if ok else messagebox.showerror)("Prestige", msg)
        self.xp_refresh()

    def stats_reset(self):
        """Reset Stats button: after a confirmation, delete this player's saved games (back to zero)."""
        name = self.stats_name.get().strip()
        if not name:
            messagebox.showinfo("Reset Stats", "Type your name first.")
            return
        if not messagebox.askyesno("Reset Stats", "Are You Sure You Want To Reset Your Stats?", icon="warning", default="no"):
            return
        try:
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import ranked
            ranked.reset_player(name)
        except Exception as e:
            messagebox.showerror("Reset Stats", f"Could not reset the stats:\n{e}")
            return
        self.stats_refresh()

    def stats_refresh(self):
        """Fill the Stats tab from the local database."""
        name = self.stats_name.get().strip()
        for i in self.stats_tree.get_children():
            self.stats_tree.delete(i)
        self.xp_refresh()
        if not name:
            self.stats_sum.set("Type your name above, then play Survival Horde.")
            return
        try:
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import ranked
            tot, per = ranked.player_stats(name)
        except Exception as e:
            self.stats_sum.set(f"Stats unavailable: {e}")
            return
        if not tot["games"]:
            self.stats_sum.set(f"No games saved for {name} yet.")
            return
        self.stats_sum.set(f"{name}: {tot['games']} games, {tot['kills']} kills, {tot['deaths']} deaths, "
                           f"{tot['suicides']} suicides, K/D {tot['kd']}, {tot['minutes']} min played"
                           + (f", favourite character {tot['favourite']}" if tot["favourite"] else "")
                           + ("\nDeaths by cause: " + ", ".join(f"{k} {v}" for k, v in sorted(tot.get("causes", {}).items(), key=lambda a: -a[1]))
                              if tot.get("causes") else ""))
        labels = {k: lab.replace(" (custom)", "") for lab, k in MAPS}
        for row in per:
            self.stats_tree.insert("", "end", values=(labels.get(row[0], row[0]),) + tuple(row[1:]))

    # -- Game Modes tab: Survival Horde, Last Man Standing
    GM_MODES = {
        "Survival Horde": "Test your skills in a game of survival. The further you progress the harder the challenge will become.\n\nRanked games record your game stats (kills, deaths, character and map) to the rankings.",
        "Gun-Game": "Everyone starts with the first weapon on the ladder; a kill moves you up a weapon, dying drops you back one. Kill someone with the last weapon to win.",
        "Juggernaut": "Free for all. Every Gatling gun, every ammo box and every boiler plate is taken off the map (only weapons and health items stay), except for one random gold Gatling: whoever finds and picks it up is the Juggernaut (if nobody does within 4 minutes, the first kill makes one), and from then on whoever kills the Juggernaut takes over. He is marked on screen for everyone (a marker over his head and the name and distance at the right edge), with 150 armour, every weapon, 7 TNT, 6 knives, 2 med bags and 100 Gatling rounds (no refills: his ammo runs out). Every bot hunts the Juggernaut first. Whoever kills him takes over. Set your name on the Stats tab so you can be the Juggernaut too. (Only on this PC for now.)",
        # "Hot Potato" is taken off the launcher for now (user); its code stays (roles.py, brain.py), to come back later:
        # "Hot Potato": "TNT only. One player holds the hot potato, with a 25 second fuse. Any kill between the holder and someone else hands the potato to the other one and resets the fuse. When the fuse runs out the holder blows up and somebody else gets it. Set your name on the Stats tab so you can hold it too. (Only on this PC for now.)",
        "Infection": "Free for all. One bot starts infected: knife and fists only, but faster. Anyone an infected player kills is infected too. When everybody is infected the game ends; if the time runs out first the survivors win. Set your name on the Stats tab so you can be infected too. (Only on this PC for now.)",
        # "Last Man Standing" is taken off the launcher for now (user); its code stays, to come back later:
        # "Last Man Standing": "Free for all, one life each. A bot that dies stays dead; the last one alive wins and the game ends. (You aren't held to the rule: stop playing when you die.)",
    }
    GM_LEVELS = ["Mixed (nightmare to easiest)"] + DIFFICULTIES
    GM_HMODES = ["Normal", "Challenge", "Melee Zombies", "Christmas"]     # (15 Nov: Christmas back on the launcher, user)
    GM_OPPONENTS = ["Bots", "Ghosts"]

    def _ghost_max(self):
        try: return max(4, min(25, int(self.gm_ghost_max.get())))
        except (tk.TclError, ValueError, AttributeError): return 16                          # Survival Horde's zombies: bots (botctl) or ghost players (ghost.py)       # Dark Mode and Christmas are off the launcher for now (user)

    def build_modes(self, saved):
        pad = dict(padx=8, pady=4)
        g, sv = self.gm, saved.get("modes", {})
        mode_fr = ttk.Frame(g)                        # the mode box and its "?" (what the chosen mode is)
        # (25 Nov, the user) the Companion: only Survival Horde for now, with "More To Come..." under it, greyed and not to be picked
        modes_ = ["Survival Horde", self.GM_MORE] if COMPANION else list(self.GM_MODES)
        self.gm_mode = ttk.Combobox(mode_fr, values=modes_, state="readonly", width=24,
                                    postcommand=self.gm_more_grey if COMPANION else "")
        self.gm_mode.set(sv.get("mode") if sv.get("mode") in self.GM_MODES and sv.get("mode") in modes_ else "Survival Horde")
        mode_lbl = ttk.Label(g, text="Game mode")
        mode_lbl.grid(row=0, column=0, sticky="w", padx=(14, 8), pady=(14, 4))
        mode_fr.grid(row=0, column=1, sticky="w", padx=8, pady=(14, 4))
        self.gm_mode.pack(side="left")
        self.gm_info = tk.StringVar()
        info_lbl = self.hint(mode_fr, self.gm_info)
        info_lbl.pack(side="left", padx=(8, 0))
        ttk.Label(g, text="Map").grid(row=2, column=0, sticky="w", padx=(14, 8), pady=4)
        mf = ttk.Frame(g)
        mf.grid(row=2, column=1, sticky="w", **pad)
        want = (sv.get("map") or "").replace(" (custom)", "")
        grp = "Custom maps" if want in MAP_GROUPS["Custom maps"] else "Standard maps"
        self.gm_group = ttk.Combobox(mf, values=list(MAP_GROUPS), state="readonly", width=14)
        self.gm_group.set(grp)
        self.gm_group.grid(row=0, column=0, padx=(0, 4))
        self.gm_map = ttk.Combobox(mf, values=MAP_GROUPS[grp], state="readonly", width=26)
        self.gm_map.set(want if want in MAP_GROUPS[grp] else MAP_GROUPS[grp][0])
        self.gm_map.grid(row=0, column=1)

        def pick_group(_e=None):
            names = MAP_GROUPS.get(self.gm_group.get()) or MAP_GROUPS["Standard maps"]
            if self.gm_mode.get() == "Survival Horde":
                names = horde_maps(names) or names
            self.gm_map.configure(values=names)
            self.gm_map.set(names[0])
        self.gm_group.bind("<<ComboboxSelected>>", pick_group)
        self.gm_vars = {}
        rows = [("bots", "Last Man Standing: bots", 4, 2, 7),
                ("lives", "Last Man Standing: lives each", 1, 1, 5)]
        lms_only = []                                  # Last Man Standing settings: hidden for Survival Horde
        for r, (key, label, default, lo, hi) in enumerate(rows, 3):
            lb = ttk.Label(g, text=label)
            lb.grid(row=r, column=0, sticky="w", padx=(14, 8), pady=4)
            v = tk.IntVar(value=int(sv.get(key, default)))
            sp = ttk.Spinbox(g, from_=lo, to=hi, width=6, textvariable=v)
            sp.grid(row=r, column=1, sticky="w", **pad)
            lms_only += [lb, sp]
            self.gm_vars[key] = v
        lvl_lbl = ttk.Label(g, text="Bot difficulty (Last Man Standing)")
        lvl_lbl.grid(row=7, column=0, sticky="w", padx=(14, 8), pady=4)
        lms_only.append(lvl_lbl)
        self.gm_level = ttk.Combobox(g, values=self.GM_LEVELS, state="readonly", width=24)
        lms_only.append(self.gm_level)
        self.gm_level.set(sv.get("level") if sv.get("level") in self.GM_LEVELS else self.GM_LEVELS[0])
        self.gm_level.grid(row=7, column=1, sticky="w", **pad)
        self.gm_you = tk.BooleanVar(value=bool(sv.get("you", True)))
        self.check(g, "I play too", self.gm_you, "Your own game starts and joins by itself.").grid(
            row=8, column=0, columnspan=2, sticky="w", padx=(14, 8), pady=4)
        chars = [k for k, _ in CHARACTERS]
        char_fr = ttk.Frame(g)
        char_fr.grid(row=9, column=0, columnspan=2, sticky="w", padx=(14, 8), pady=4)
        ttk.Label(char_fr, text="Your character").grid(row=0, column=0, sticky="w")
        self.gm_char = ttk.Combobox(char_fr, values=chars, state="readonly", width=16)
        self.gm_char.set(sv.get("character") if sv.get("character") in chars else chars[1])
        self.gm_char.grid(row=0, column=1, padx=8)
        self.hint(char_fr, "This character is picked for you, and you join the Survivors automatically.").grid(row=0, column=2, sticky="w")
        ttk.Label(char_fr, text="Horde mode").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.gm_hmode = ttk.Combobox(char_fr, values=self.GM_HMODES, state="readonly", width=16)
        self.gm_hmode.set(sv.get("horde_mode") if sv.get("horde_mode") in self.GM_HMODES else "Normal")
        self.gm_hmode.grid(row=1, column=1, padx=8, pady=(4, 0))
        self._gm_opp_lbl = ttk.Label(char_fr, text="Zombies"); self._gm_opp_lbl.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.gm_opp = ttk.Combobox(char_fr, values=self.GM_OPPONENTS, state="readonly", width=16)
        self.gm_opp.set(sv.get("opponents") if sv.get("opponents") in self.GM_OPPONENTS else "Bots")
        self.gm_opp.grid(row=2, column=1, padx=8, pady=(4, 0))
        gmx = ttk.Frame(char_fr); gmx.grid(row=2, column=2, sticky="w", pady=(4, 0))
        ttk.Label(gmx, text="Max zombies").pack(side="left")
        try: _gm = int(sv.get("ghost_max", 16))
        except (TypeError, ValueError): _gm = 16
        self.gm_ghost_max = tk.IntVar(value=max(4, min(25, _gm)))
        ttk.Spinbox(gmx, from_=4, to=25, width=4, textvariable=self.gm_ghost_max).pack(side="left", padx=6)
        self.hint(gmx, "Ghost zombies only: the horde's size at its peak. 16 runs well on most PCs; lower it on a slower PC. 25 is the most.").pack(side="left")
        _xl = ttk.Label(char_fr, text="Christmas final tier"); _xl.grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.gm_xtop = ttk.Combobox(char_fr, values=["Hell", "Nightmare", "Hard", "Normal"], state="readonly", width=16)
        self.gm_xtop.set(sv.get("xmas_top") if sv.get("xmas_top") in ("Hell", "Nightmare", "Hard", "Normal") else "Nightmare")
        self.gm_xtop.grid(row=3, column=1, padx=8, pady=(4, 0))
        _xh = self.hint(char_fr, "The strongest the elves get. Nightmare: Easy at 8 kills, Normal at 14, Hard at 18, "
                                 "Nightmare at 25. Hell: the same, then Hell at 30. Hard or Normal: that tier at 25, the ones below spread evenly.")
        _xh.grid(row=3, column=2, sticky="w", pady=(4, 0))
        # Christmas: no "Max zombies" (the user, 16 Nov), and the final-tier choice only shows for it
        self._gm_xmas_only = [_xl, self.gm_xtop, _xh]; self._gm_not_xmas = [gmx]
        def _hmode_apply(_e=None):
            xm = self.gm_hmode.get() == "Christmas"
            for w_ in self._gm_xmas_only: (w_.grid() if xm else w_.grid_remove())
            for w_ in self._gm_not_xmas: (w_.grid_remove() if xm else w_.grid())
            self._gm_opp_lbl.configure(text="Elves" if xm else "Zombies")
        self.gm_hmode.bind("<<ComboboxSelected>>", _hmode_apply, add="+")
        self._gm_hmode_apply = _hmode_apply
        _hmode_apply()
        self.hint(char_fr, "Normal: the horde gets stronger as you go.\nChallenge: Normal bots to start, Hard at 5 kills, Nightmare at 10, "
                           "Hell at 15.\nMelee Zombies: Mary and Two Feathers, knives and fists only.\nChristmas: elves in a snowy world with "
                           "Christmas lights, trees and music; Santa arrives at the end.").grid(
            row=1, column=2, sticky="w", pady=(4, 0))
        self.gm_classic = tk.BooleanVar(value=bool(sv.get("classic", False)))
        self.gm_hell = tk.BooleanVar(value=bool(sv.get("hell", False)))
        hell_cb = self.check(g, "Include Hell", self.gm_hell, "At 25 kills the bots become Hell bots: no aim wobble, no reaction time.")
        hell_cb.grid(row=10, column=0, columnspan=2, sticky="w", padx=(14, 8), pady=4)
        self._gm_not_xmas.append(hell_cb); self._gm_hmode_apply()          # (Christmas: its own "final tier" instead)
        self.gm_private = tk.BooleanVar(value=bool(sv.get("private", False)))
        private_cb = self.check(g, "Private game", self.gm_private, "Only you and the bots: nobody else can see or join the game.")
        private_cb.grid(row=15, column=0, columnspan=2, sticky="w", padx=(14, 8), pady=(4, 48))
        classic_cb = self.check(g, "Classic Outlaws style", self.gm_classic,
                                "No mods: no zombie skins, team colours, darkness, music or storms; the bots are named Bot 1, "
                                "Bot 2 ...; only the HORDE BECOMES STRONGER banner and the thunder stay.")
        classic_cb.grid(row=11, column=0, columnspan=2, sticky="w", padx=(14, 8), pady=4)
        rk = ttk.LabelFrame(g, text="Survival Horde: ranked")
        rk.grid(row=12, column=0, columnspan=2, sticky="ew", padx=14, pady=4)
        self.gm_ranked = tk.BooleanVar(value=False)         # always unticked when the launcher opens: only you tick it on
        self.check(rk, "Ranked game", self.gm_ranked,
                   "Saves your kills, deaths, character and map to the rankings. Ranked maps: Repair, Dry Gulch and Bunkers.",
                   command=self.gm_ranked_changed).grid(row=0, column=0, columnspan=4, sticky="w", **pad)
        self.rk_labels = [ttk.Label(rk, text="Played under the name on the Stats tab"), ttk.Label(rk, text="Ranked map")]
        self.rk_labels[0].grid(row=1, column=0, columnspan=2, sticky="w", **pad)
        self.rk_labels[1].grid(row=2, column=0, sticky="e", **pad)
        self.gm_rank_map = ttk.Combobox(rk, values=list(RANKED_LABELS), state="readonly", width=16)
        self.gm_rank_map.set(RANKED_LABELS[0])
        self.gm_rank_map.grid(row=2, column=1, sticky="w", **pad)
        self.gm_show_rank = ttk.Button(rk, text="Show rankings", command=self.show_rankings)
        self.gm_show_rank.grid(row=2, column=2, columnspan=2, sticky="w", **pad)
        self.gm_top = tk.StringVar()
        ttk.Label(rk, textvariable=self.gm_top, justify="left").grid(row=3, column=0, columnspan=4, sticky="w", **pad)
        self.gm_map.bind("<<ComboboxSelected>>", lambda e: self.gm_refresh_top(), add="+")
        self.gm_rank_map.bind("<<ComboboxSelected>>", lambda e: self.gm_refresh_top(from_box=True), add="+")
        self.gm_ranked_changed()
        self.gm_refresh_top()
        b = ttk.Button(g, text="LAUNCH GAME MODE", command=self.launch_mode, width=26, style="Launch.TButton")
        b.grid(row=13, column=0, columnspan=2, pady=14)
        self.launch_btns.append((b, "LAUNCH GAME MODE"))
        surv_note = ttk.Frame(g)                      # (the old paragraph here is in the mode's "?" now)
        surv_note.grid(row=14, column=0)
        # (13 Nov, the user: "remove the ranked game section on game modes for now". Built but never shown, so a ranked game
        #  can't be started; gm_ranked stays unticked. Put rk back in this list to bring it back.)
        rk.grid_remove()
        surv_only = [surv_note, classic_cb, char_fr, hell_cb, private_cb]                    # shown only for Survival Horde

        keep = {mode_lbl, mode_fr, self.gg, self.rl, rk, *self.upd_btns}   # (the Update Launcher button is placed, not gridded; rk hidden)

        def show(_e=None):
            self.gm_info.set(self.GM_MODES[self.gm_mode.get()])
            role = self.gm_mode.get() in ("Juggernaut", "Infection", "Hot Potato")
            gun = self.gm_mode.get() == "Gun-Game"       # Gun-Game swaps in its own settings and launch button
            for w in g.winfo_children():
                if w in keep:
                    continue
                surv = self.gm_mode.get() == "Survival Horde"
                if gun or role or (w in lms_only and surv) or (w in surv_only and not surv) \
                        or (w is hell_cb and self.gm_hmode.get() == "Christmas"):          # (Christmas: its own final tier)
                    w.grid_remove()
                else:
                    w.grid()
            if gun:
                self.gg.grid(row=2, column=0, columnspan=2, sticky="nw")
            else:
                self.gg.grid_remove()
            if role:
                self.rl.grid(row=2, column=0, columnspan=2, sticky="nw")
            else:
                self.rl.grid_remove()
        self.gm_mode.bind("<<ComboboxSelected>>", lambda e: (self.gm_more_guard(), show(), self.gm_ranked_changed()))
        show()

    GM_MORE = "More To Come..."

    def companion_tabs(self):
        """the Companion: only Play, Custom Games and Online (Map Rotation, Stats and Tools are built - the stats are still kept -
        but not shown)"""
        for f in (self.rot, self.st, self.tl):
            try: self.nb.hide(f)
            except Exception: pass
        try:
            import companion_app
            companion_app.after_start(self)
        except Exception as e:
            self.say(f"companion: {e}")

    def companion_update(self):
        try:
            import companion_app
            companion_app.update(self)
        except Exception as e:
            messagebox.showerror("Update", f"Could not check for an update: {e}")

    def gm_more_grey(self):
        """the Companion's mode list: "More To Come..." drawn greyed out (the list's own row colours)"""
        try:
            pd = self.gm_mode.tk.eval(f"ttk::combobox::PopdownWindow {self.gm_mode}")
            lb = f"{pd}.f.l"
            bg = self.gm_mode.tk.eval(f"{lb} cget -background")
            self.gm_mode.tk.eval(f"{lb} itemconfigure 1 -foreground #a39780 -selectforeground #a39780 -selectbackground {bg}")
        except Exception:
            pass

    def gm_more_guard(self):
        if self.gm_mode.get() == self.GM_MORE:          # (not a mode: back to the one there is)
            self.gm_mode.set("Survival Horde")

    def gm_refresh_top(self, from_box=False):
        """The top 3 players of the selected map (the Rankings-for box, or the map chosen for the game)."""
        from_box = from_box or bool(self.gm_ranked.get())
        label = self.gm_rank_map.get() if from_box or self.gm_map.get() not in RANKED_KEYS else self.gm_map.get()
        if not from_box and self.gm_map.get() in RANKED_KEYS:
            self.gm_rank_map.set(label)
        try:
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import ranked
            rows = ranked.best_leaderboard(RANKED_KEYS[label], limit=3)
        except Exception as e:
            self.gm_top.set(f"Top 3 on {label}: rankings unavailable ({e})")
            return
        medals = ["1st", "2nd", "3rd"]
        lines = [f"{medals[r[0] - 1]}  {r[1]}   K/D {r[4]}  ({r[2]} kills, {r[3]} deaths, {r[6] or 'no character'})" for r in rows]
        self.gm_top.set(f"Top 3 on {label} ({getattr(ranked, 'LAST_SOURCE', '')}):\n" + ("\n".join(lines) if lines else "no ranked games yet"))

    def gm_ranked_changed(self):
        """Ranked games: only Repair, Dry Gulch and Bunkers. Everything else in the ranked box stays grayed
        out until the Ranked Game box is ticked."""
        on = bool(self.gm_ranked.get())
        try:                                    # ranked games are always the normal climb
            if on:
                self.gm_hmode.set("Normal")
                getattr(self, "_gm_hmode_apply", lambda: None)()
            self.gm_hmode.configure(state="disabled" if on else "readonly")
        except (tk.TclError, AttributeError):
            pass
        for w, normal in ((self.gm_rank_map, "readonly"), (self.gm_show_rank, "normal")):
            try:
                w.configure(state=normal if on else "disabled")
            except tk.TclError:
                pass
        for lb in getattr(self, "rk_labels", []):
            try:
                lb.configure(state="normal" if on else "disabled")
            except tk.TclError:
                pass
        if on and self.gm_mode.get() == "Survival Horde":
            # A ranked game is played on the map picked in the ranked box, so the regular map lists are grayed out.
            self.gm_group.configure(state="disabled")
            self.gm_map.configure(state="disabled")
        else:
            self.gm_group.configure(state="readonly")
            names = MAP_GROUPS.get(self.gm_group.get()) or MAP_GROUPS["Standard maps"]
            if self.gm_mode.get() == "Survival Horde":
                names = horde_maps(names) or names                 # only maps with Team Deathmatch
            self.gm_map.configure(state="readonly", values=names)
            if self.gm_map.get() not in names and self.gm_map.get() not in RANKED_KEYS:
                self.gm_map.set(names[0])
            if self.gm_map.get() in RANKED_KEYS and self.gm_map.get() not in dict(MAPS):    # a ranked short name ("Repair")
                full = next((n for n, k in MAPS if k == RANKED_KEYS[self.gm_map.get()]), None)
                if full:
                    self.gm_map.set(full)
        if hasattr(self, "gm_top"):
            self.gm_refresh_top(from_box=True)

    def show_rankings(self):
        """A window with the kill/death rankings of the chosen map."""
        try:
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import ranked
            rows = ranked.best_leaderboard(RANKED_KEYS[self.gm_rank_map.get()])
        except Exception as e:
            messagebox.showerror("Rankings", f"Could not read the rankings database:\n{e}")
            return
        win = tk.Toplevel(self)
        win.title(f"Survival rankings: {self.gm_rank_map.get()} ({getattr(ranked, 'LAST_SOURCE', '')})")
        cols = ("Rank", "Player", "Kills", "Deaths", "K/D", "Games", "Favourite character")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=min(max(len(rows), 5), 20))
        for c, w in zip(cols, (50, 160, 60, 60, 60, 60, 150)):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="center" if c != "Player" else "w")
        for r in rows:
            tree.insert("", "end", values=r)
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        if not rows:
            ttk.Label(win, text="No ranked games on this map yet.").pack(pady=(0, 8))

    def mode_args(self):
        """The command line (after python) of the chosen mode's script."""
        mode = self.gm_mode.get()
        label = self.gm_map.get()
        mp = (RANKED_KEYS.get(self.gm_rank_map.get()) if self.gm_ranked.get() and mode == "Survival Horde" else None) \
            or dict(MAPS).get(label) or RANKED_KEYS.get(label) or "drygulch"      # "Repair" etc. can linger after unticking Ranked
        n = int(self.gm_vars["bots"].get())
        users = "1" if self.gm_you.get() else "0"
        lv = self.gm_level.get()
        if lv.startswith("Mixed"):
            levels = "nightmare,hard,easy,easiest"
        else:
            levels = lv.lower()
        if mode == "Survival Horde":
            extra = []
            if self.gm_ranked.get():
                extra = ["--ranked"]
            if self.gm_classic.get():
                extra += ["--classic"]
            if self.gm_hell.get() and self.gm_hmode.get() != "Christmas":
                extra += ["--hell"]
            if self.gm_hmode.get() == "Challenge" and not self.gm_ranked.get():
                extra += ["--challenge"]
            if self.gm_hmode.get() == "Dark Mode" and not self.gm_ranked.get():
                extra += ["--dark"]
            if self.gm_hmode.get() == "Melee Zombies" and not self.gm_ranked.get():
                extra += ["--melee"]                       # Mary and Two Feathers, knives and fists only
            if self.gm_hmode.get() == "Christmas" and not self.gm_ranked.get():
                extra += ["--christmas"]                   # elves, snow, trees, Christmas music; Santa at the end
                extra += ["--xmas-top", (self.gm_xtop.get() or "Nightmare").lower()]
            if self.gm_private.get():
                extra += ["--private"]
            if self.gm_opp.get() == "Ghosts":
                extra += ["--ghosts"] + ([] if self.gm_hmode.get() == "Christmas" else ["--ghost-max", str(self._ghost_max())])
            extra += ["--player", self.stats_name.get().strip() or "Player"]
            extra += ["--character", self.horde_char()]
            return ["horde.py", "--map", mp, "--minutes", "30", "--users", users] + extra
        return ["lms.py", "--map", mp, "--bots", str(max(2, n)), "--levels", levels, "--lives", str(self.gm_vars["lives"].get()),
                    "--minutes", "30", "--users", users]

    def horde_char(self):
        """Your character in a Survival Horde game. (15 Nov, user: Sanchez stays playable at Christmas - Santa is his own model,
        SANTA.NWX, which only Santa's game copy announces, so a Sanchez player is drawn as Sanchez.)"""
        return self.gm_char.get()

    def launch_mode(self, zt_ok=False):
        if not zt_ok:                          # ZeroTier first, like every other launch (friends join over it)
            self.zt_then(lambda: self.launch_mode(zt_ok=True)); return
        if not (HERE / "horde.py").exists():
            messagebox.showerror("Not found", f"The game mode scripts are not next to the launcher:\n{HERE}")
            return
        self.save()
        try:
            _a = self.mode_args()
            self.serve_mods(("dark" if "--dark" in _a else "horde") if _a[0] == "horde.py" else "lms", _a[_a.index("--map") + 1],
                            melee="--melee" in _a, christmas="--christmas" in _a, private="--private" in _a)
        except Exception:
            pass
        for b, _ in self.launch_btns:
            b.configure(text="Loading...", state="disabled")
        self.zerotier = False
        self.cancel = False
        self.launching = True
        self.gg_env = {}
        self.play_char = self.horde_char() if self.gm_mode.get() == "Survival Horde" else None   # Survival: your character and the Survivors team are picked for you
        if self.play_char and self.play_char != self.gm_char.get():
            self.say("Christmas Horde: Sanchez is Santa, so you play %s." % self.play_char)
        self.play_horde = self.gm_mode.get() == "Survival Horde" and not self.gm_classic.get()   # (Vulkan for the Survival look)
        self.play_autojoin = bool(self.play_char)          # (Survival: your game joins the Survivors by itself; horde.py sends its XP)
        self.minimize_new_consoles()
        threading.Thread(target=self.run_mode, args=(self.mode_args(), self.gm_you.get()), daemon=True).start()

    def close_new_consoles(self):
        """The game is over: close the console (cmd) windows that opened after the launch button was pressed, so none has to
        be closed by hand. The game's own window (its title says Outlaws) is left alone."""
        try:
            import ctypes
            u = ctypes.windll.user32
            for h in list(getattr(self, "new_consoles", ())):
                if not u.IsWindow(h):
                    continue
                buf = ctypes.create_unicode_buffer(256)
                u.GetWindowTextW(h, buf, 256)
                if "outlaws" in buf.value.lower():
                    continue
                u.PostMessageW(h, 0x0010, 0, 0)               # WM_CLOSE
            self.new_consoles = set()
        except Exception:
            pass

    def minimize_new_consoles(self, seconds=3 * 3600):
        """Any console (cmd) window that opens after the launch button is pressed is minimized, so it isn't left in the middle
        of the screen: the game opens one of its own. Only windows that were not there when the button was pressed."""
        def work():
            try:
                import ctypes
                from ctypes import wintypes
                u = ctypes.windll.user32
                classes = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
                proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

                def consoles():
                    found = []

                    def cb(h, _):
                        if u.IsWindowVisible(h):
                            buf = ctypes.create_unicode_buffer(64)
                            u.GetClassNameW(h, buf, 64)
                            if buf.value in classes:
                                found.append(h)
                        return True
                    u.EnumWindows(proc(cb), 0)
                    return found
                before, done = set(consoles()), set()
                self.new_consoles = done
                end = time.time() + seconds
                while time.time() < end:
                    time.sleep(0.4)
                    for h in consoles():
                        if h not in before and h not in done:
                            done.add(h)
                            u.ShowWindow(h, 7)                    # SW_SHOWMINNOACTIVE: minimized, the game keeps the focus
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def run_mode(self, args, you):
        seamless = getattr(self, "seamless_next", False)    # (the next Co-op Mission in the same game: launchertools.mission_chain)
        self.seamless_next = False
        if not seamless:
            self.say("ending any game copies left over from before...")
            self.kill_all()
            time.sleep(2)
        venv_python = HERE / ".venv" / "Scripts" / "python.exe"
        py = venv_py()
        joined = seamless                                       # (seamless: your game is already in, and its watcher still runs)
        try:
            p = subprocess.Popen([py, "-u", str(HERE / args[0])] + args[1:], cwd=str(HERE),
                                 env=dict(os.environ, PYTHONUNBUFFERED="1"), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, errors="replace", creationflags=NO_WINDOW)
            self.bots_proc = p
            for line in p.stdout:
                if self.cancel:
                    break
                self.say(line)
                if you and not joined and "lobby up" in line:
                    joined = True
                    self.say("the game is hosted; joining it...")
                    threading.Thread(target=self.join_and_watch, daemon=True).start()
            if self.cancel:
                try:
                    p.kill()
                except OSError:
                    pass
            else:
                p.wait()
                time.sleep(3)
                self.close_new_consoles()
        except OSError as e:
            self.say(f"could not start the game mode: {e}")
        if not joined:
            self.ready()

    def gg_pb_refresh(self):
        """One per-bot box for each bot to play against (the Bots to play against number)."""
        try:
            n = max(0, min(10, int(self.gg_count.get())))
        except (ValueError, tk.TclError):
            return
        for i, (lb, cb) in enumerate(self.gg_bot_rows):
            if i < n:
                lb.grid(row=i % 5, column=(i // 5) * 2, sticky="e", padx=(8, 2), pady=1)
                cb.grid(row=i % 5, column=(i // 5) * 2 + 1, pady=1)
            else:
                lb.grid_remove()
                cb.grid_remove()

    def gg_diff_changed(self):
        """Picking a general difficulty clears the per-bot choices; "Custom" keeps them."""
        d = self.gg_diff.get()
        if d in DIFFICULTIES:
            self.gg_last_base = d
            for cb in self.gg_bot_diffs:
                cb.set("Same as above")

    def gg_pb_changed(self, i):
        """A bot's own difficulty was changed: the general difficulty becomes Custom. The bots still on "Same as
        above" keep the difficulty they had, spelled out."""
        base = self.gg_diff.get()
        if base in DIFFICULTIES:
            self.gg_last_base = base
        for j, cb in enumerate(self.gg_bot_diffs):
            if j != i and cb.get() not in DIFFICULTIES:
                cb.set(self.gg_last_base)
        self.gg_diff.set("Custom")

    def gg_refresh(self):
        """Custom order boxes only in Custom mode; the gatling gun only when included."""
        custom = self.gg_order.get() == "Custom"
        for cb in self.gg_slots:
            cb.configure(state="readonly" if custom else "disabled")

    def gg_ladder(self):
        """The slot order for this game: default, shuffled (the knife stays last) or the custom boxes."""
        import random
        by_name = dict(self.GG_WEAPONS)
        mode = self.gg_order.get()
        if mode == "Custom":
            order = [by_name[cb.get()] for cb in self.gg_slots]
            order = list(dict.fromkeys(order))                  # a weapon twice counts once
        else:
            order = [5, 4, 3, 9, 6, 2, 7, 8]
            if mode == "Randomized":
                rest = order[:-1]
                random.shuffle(rest)
                order = rest + [8]
        if not self.gg_gat.get():
            order = [s for s in order if s != 9]
        return order or [8]

    def zt_say(self, text):
        self.after(0, lambda: self.zt_msg.set(text))

    def zt_refresh_button(self):
        def work():
            st = zt_state()
            label = {"missing": "Install and turn on ZeroTier", "off": "Turn ON ZeroTier",
                     "on": "Turn OFF ZeroTier"}[st]
            self.after(0, lambda: self.zt_btn.configure(text=label, state="normal"))
        threading.Thread(target=work, daemon=True).start()

    def zt_toggle(self):
        st = zt_state()
        if st == "missing" and not messagebox.askyesno(
                "Install ZeroTier", "ZeroTier One isn't installed.\n\nDownload it from download.zerotier.com and install "
                "it now? Windows will ask you to allow the installer."):
            return
        self.zt_btn.configure(state="disabled", text="Working, please wait...")
        threading.Thread(target=self.zt_work, args=(st,), daemon=True).start()

    def zt_work(self, st):
        try:
            if st == "on":
                self.zt_say("turning ZeroTier off (allow the Windows prompt)...")
                run_elevated("cmd", f"/c sc stop {ZT_SERVICE}")
                time.sleep(2)
                self.zt_say("ZeroTier is off" if zt_state() != "on" else "could not turn ZeroTier off")
                return
            if st == "missing":
                import tempfile
                msi = Path(tempfile.gettempdir()) / "ZeroTierOne.msi"
                ok = False
                try:
                    self.zt_say("downloading ZeroTier...")
                    download(ZT_MSI, msi)
                    ok = msi.stat().st_size > 1_000_000 and msi.read_bytes()[:8] == bytes.fromhex("d0cf11e0a1b11ae1")
                    if ok:
                        self.zt_say("installing ZeroTier (allow the Windows prompt)...")
                        run_elevated("msiexec", f'/i "{msi}" /qn /norestart')
                except Exception as e:                         # noqa: BLE001
                    self.zt_say(f"direct download failed ({e}); trying winget...")
                for _ in range(30):
                    if zt_state() != "missing":
                        break
                    time.sleep(1)
                if zt_state() == "missing":                    # the download or install didn't work: try winget
                    self.zt_say("installing ZeroTier with winget (allow the Windows prompt; this can take a minute)...")
                    run_elevated("cmd", "/c winget install -e --id ZeroTier.ZeroTierOne --silent "
                                        "--accept-package-agreements --accept-source-agreements")
                    for _ in range(60):
                        if zt_state() != "missing":
                            break
                        time.sleep(1)
                if zt_state() == "missing":
                    import webbrowser
                    webbrowser.open("https://www.zerotier.com/download")
                    self.zt_say("could not install it automatically: I opened zerotier.com/download. Install it there, "
                                "then click this button again.")
                    return
            if zt_state() != "on":
                self.zt_say("starting ZeroTier (allow the Windows prompt)...")
                run_elevated("cmd", f"/c sc start {ZT_SERVICE}")
                for _ in range(15):
                    if zt_state() == "on":
                        break
                    time.sleep(1)
            if zt_state() != "on":
                self.zt_say("could not start ZeroTier")
                return
            net = self.zt_net.get().strip()
            if net and ZT_CLI.exists():
                self.zt_say(f"joining network {net}...")
                run_elevated("cmd", f'/c ""{ZT_CLI}" join {net}"')
            elif not net:
                self.zt_say("ZeroTier is on; type your network ID above and turn it on again to join it")
            for _ in range(20):                                # the adapter needs a moment (and your authorisation)
                ip = zerotier_ip()
                if ip:
                    self.after(0, lambda ip=ip: self.zt_ip.set(ip))
                    self.zt_say(f"ZeroTier is on; your IP is {ip}")
                    return
                time.sleep(2)
            self.zt_say("ZeroTier is on; no IP yet: authorise this PC in my.zerotier.com (Members), then click Detect")
        except Exception as e:                                 # noqa: BLE001 - show anything to the user
            self.zt_say(f"ZeroTier: {e}")
        finally:
            self.zt_refresh_button()

    def zt_detect(self, quiet=False):
        def work():
            ip = zerotier_ip()
            def done():
                if ip:
                    self.zt_ip.set(ip)
                    self.zt_msg.set(f"found your ZeroTier IP: {ip}")
                elif not quiet:
                    self.zt_msg.set("no ZeroTier adapter found: is ZeroTier One running and joined to a network?")
            try:
                self.after(0, done)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def zt_clip(self, text, msg):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.zt_msg.set(msg)

    def zt_copy_ip(self):
        ip = self.zt_ip.get().strip()
        if not ip:
            self.zt_msg.set("no IP yet: type it or click Detect")
            return
        self.zt_clip(ip, f"copied {ip}")

    def zt_copy_help(self):
        ip = self.zt_ip.get().strip()
        if not ip:
            self.zt_msg.set("no IP yet: type it or click Detect")
            return
        net = self.zt_net.get().strip()
        text = ((f"1. Install ZeroTier One and join network {net} (I'll authorise you).\n" if net else
                 "1. Install ZeroTier One and join my network (I'll authorise you).\n")
                + f"2. Start Outlaws with: OutlawsAHOM.exe -netdriver lan -skipintromovies -join {ip}\n"
                + f"   (or in the LAN game list, join {ip})")
        self.zt_clip(text, "copied the join instructions")

    # -- look
    def zfont(self, family, size, weight="normal"):
        """A font that grows and shrinks with the window (see apply_zoom)."""
        from tkinter import font as tkfont
        f = tkfont.Font(root=self, family=family, size=size, weight=weight)
        self._zfonts.append((f, size))
        return f

    def style_window(self):
        """A tidy western look on the clam theme: a parchment panel, leather tabs and buttons, rust for what matters and the
        Rye typeface for titles (13 Nov). The explanations are in "?" hints (see hint())."""
        self.configure(bg=LEATHER)
        global DISPLAY, TITLEF
        try:
            from tkinter import font as tkfont
            fams = {f.lower() for f in tkfont.families(self)}
            DISPLAY = next((f for f in ("Rockwell", "Bookman Old Style", "Georgia") if f.lower() in fams), "Georgia")
            if TITLEF == "Georgia":                         # (the game's lettering didn't load: a heavy slab or Georgia)
                TITLEF = next((f for f in ("Rockwell Extra Bold", "Playbill", "Georgia") if f.lower() in fams), "Georgia")
        except Exception:
            pass
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            return
        GREYTXT, HOVER, OFF, LEATH_HI = "#a48f74", "#d9bf8c", "#eadcbd", "#6b4428"
        st.configure(".", background=PARCH, foreground=TXT, fieldbackground=FIELD, bordercolor=EDGE, lightcolor=PARCH,
                     darkcolor=PARCH, troughcolor=PARCH, focuscolor=PARCH)
        st.map(".", foreground=[("disabled", GREYTXT)])
        st.configure("TLabelframe", background=PARCH, bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE, padding=(4, 2))
        st.configure("TLabelframe.Label", background=PARCH, foreground=RUST, font=(DISPLAY, 11, "bold"))
        st.configure("Muted.TLabel", foreground=MUTED)
        st.configure("Head.TLabel", foreground=LEATHER, font=(DISPLAY, 13, "bold"))
        st.configure("Sum.TLabel", foreground=MUTED, font=("Segoe UI", 9, "italic"))
        for w in ("TCheckbutton", "TRadiobutton"):
            st.configure(w, background=PARCH, foreground=TXT, indicatorbackground=FIELD, indicatorforeground=FIELD,
                         upperbordercolor=EDGE, lowerbordercolor=EDGE, focuscolor=PARCH)
            st.map(w, background=[("active", PARCH)], foreground=[("disabled", GREYTXT)],
                   indicatorbackground=[("disabled", OFF), ("selected", RUST)],
                   indicatorforeground=[("disabled", GREYTXT), ("selected", "white")])
        st.configure("TSeparator", background=EDGE)
        st.configure("TButton", background=LEATHER, foreground=SAND, padding=(10, 5), borderwidth=1, bordercolor="#2e1c10",
                     lightcolor=LEATHER, darkcolor=LEATHER, focuscolor=LEATHER, font=("Segoe UI", 9, "bold"))
        st.map("TButton", background=[("disabled", "#cbb48d"), ("pressed", RUST), ("active", LEATH_HI)],
               foreground=[("disabled", "#8e7655")],
               lightcolor=[("disabled", "#cbb48d"), ("active", LEATH_HI)], darkcolor=[("disabled", "#cbb48d"), ("active", LEATH_HI)],
               bordercolor=[("disabled", "#b59c76")])
        st.configure("Launch.TButton", background=RUST, foreground="white", padding=(22, 8), bordercolor="#5e2606",
                     lightcolor=RUST, darkcolor=RUST, focuscolor=RUST, font=(DISPLAY, 14, "bold"))
        st.map("Launch.TButton", background=[("disabled", "#cbb48d"), ("pressed", "#c95e16"), ("active", "#c95e16")],
               foreground=[("disabled", "#8e7655")], bordercolor=[("disabled", "#b59c76")],
               lightcolor=[("disabled", "#cbb48d"), ("active", "#c95e16")], darkcolor=[("disabled", "#cbb48d"), ("active", "#c95e16")])
        # the "Advanced Options  v" fold on the Play tab: plain text, not a button box
        st.configure("Fold.TButton", background=PARCH, foreground=LEATHER, padding=(2, 2), borderwidth=0, bordercolor=PARCH,
                     lightcolor=PARCH, darkcolor=PARCH, focuscolor=PARCH, font=(DISPLAY, 13, "bold"))
        st.map("Fold.TButton", background=[("active", PARCH), ("pressed", PARCH)], foreground=[("active", RUST)],
               lightcolor=[("active", PARCH)], darkcolor=[("active", PARCH)])
        # the Bots / Ghosts / Bots + Ghosts switch on the Play tab: three buttons side by side, the chosen one filled
        st.configure("Seg.Toolbutton", background=SOFT, foreground=LEATHER, padding=(18, 6), bordercolor=EDGE, lightcolor=SOFT,
                     darkcolor=SOFT, focuscolor=SOFT, relief="flat", font=(DISPLAY, 12, "bold"))
        st.map("Seg.Toolbutton", background=[("selected", RUST), ("active", HOVER)], foreground=[("selected", "white")],
               lightcolor=[("selected", RUST), ("active", HOVER)], darkcolor=[("selected", RUST), ("active", HOVER)],
               bordercolor=[("selected", "#5e2606")])
        st.configure("TNotebook", background=LEATHER, borderwidth=0, bordercolor=LEATHER, lightcolor=LEATHER, darkcolor=LEATHER,
                     tabmargins=(6, 6, 6, 0))
        st.configure("TNotebook.Tab", background="#6e4a2e", foreground=SAND, padding=(20, 6), bordercolor="#2e1c10",
                     lightcolor="#6e4a2e", darkcolor="#6e4a2e", focuscolor="#6e4a2e", font=(DISPLAY, 12, "bold"))
        st.map("TNotebook.Tab", background=[("selected", PARCH), ("active", "#83593a")], foreground=[("selected", RUST)],
               lightcolor=[("selected", PARCH), ("active", "#83593a")], darkcolor=[("selected", PARCH)],
               bordercolor=[("selected", EDGE)])
        # Boxes you type or pick in are near-white with dark text; the one you are in gets the rust outline.
        for w in ("TEntry", "TSpinbox", "TCombobox"):
            st.configure(w, foreground=INK, fieldbackground=FIELD, arrowcolor=LEATHER, bordercolor=EDGE, lightcolor=FIELD,
                         darkcolor=FIELD, background=SOFT)
            st.map(w, bordercolor=[("focus", RUST)], lightcolor=[("focus", FIELD)], darkcolor=[("focus", FIELD)])
        st.map("TCombobox", fieldbackground=[("disabled", OFF), ("readonly", FIELD)],
               foreground=[("disabled", GREYTXT), ("!disabled", INK)], selectbackground=[("readonly", FIELD)],
               selectforeground=[("readonly", INK)], bordercolor=[("focus", RUST)])
        st.map("TEntry", foreground=[("disabled", GREYTXT)], fieldbackground=[("disabled", OFF)], bordercolor=[("focus", RUST)])
        st.map("TSpinbox", foreground=[("disabled", GREYTXT)], fieldbackground=[("disabled", OFF)], bordercolor=[("focus", RUST)])
        st.configure("TMenubutton", background=FIELD, foreground=INK, bordercolor=EDGE, lightcolor=FIELD, darkcolor=FIELD,
                     arrowcolor=LEATHER, padding=(6, 3))
        st.map("TMenubutton", foreground=[("disabled", GREYTXT)], background=[("disabled", OFF), ("active", SOFT)])
        st.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=INK, bordercolor=EDGE, lightcolor=EDGE,
                     darkcolor=EDGE)
        st.map("Treeview", background=[("selected", "#ecc9a0")], foreground=[("selected", INK)])
        st.configure("Treeview.Heading", background=SOFT, foreground=LEATHER, bordercolor=EDGE, lightcolor=SOFT, darkcolor=SOFT,
                     font=("Segoe UI", 9, "bold"), padding=(4, 3))
        st.configure("TScrollbar", background=SOFT, troughcolor=PARCH, bordercolor=EDGE, lightcolor=SOFT, darkcolor=SOFT,
                     arrowcolor=LEATHER)
        st.map("TScrollbar", background=[("active", HOVER)])
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", INK)
        self.option_add("*TCombobox*Listbox.selectBackground", RUST)
        self.option_add("*TCombobox*Listbox.selectForeground", "white")
        self._sbase = {"TLabelframe.Label": (DISPLAY, 11, "bold"), "TButton": ("Segoe UI", 9, "bold"),
                       "Launch.TButton": (DISPLAY, 14, "bold"), "TNotebook.Tab": (DISPLAY, 12, "bold"),
                       "Seg.Toolbutton": (DISPLAY, 11, "bold"), "Head.TLabel": (DISPLAY, 13, "bold"),
                       "Fold.TButton": (DISPLAY, 13, "bold"), "Treeview.Heading": ("Segoe UI", 9, "bold"),
                       "Sum.TLabel": ("Segoe UI", 9, "italic")}

    # -- "?" hints: the explanations that used to sit beside or under the controls. Hover (or click) the ? to read one.
    def _tip_hide(self):
        j = getattr(self, "_tip_job", None)
        if j:
            try: self.after_cancel(j)
            except Exception: pass
        self._tip_job = None
        w = getattr(self, "_tip_win", None)
        self._tip_win = None
        if w is not None:
            try: w.destroy()
            except Exception: pass

    def _tip_show(self, widget, text):
        self._tip_hide()
        try:
            t = text() if callable(text) else (text.get() if hasattr(text, "get") else text)
            t = str(t or "").strip()
            if not t or not widget.winfo_exists():
                return
            z = self._z or 1.0
            w = self._tip_win = tk.Toplevel(self)
            w.wm_overrideredirect(True)
            try: w.attributes("-topmost", True)
            except tk.TclError: pass
            w.configure(bg=GOLDEN)
            tk.Label(w, text=t, justify="left", wraplength=int(400 * z), bg=LEATHER, fg=SAND,
                     font=("Segoe UI", max(8, int(round(9 * z)))), padx=12, pady=8).pack(padx=1, pady=1)
            w.update_idletasks()
            tw, th = w.winfo_reqwidth(), w.winfo_reqheight()
            x = widget.winfo_rootx() + widget.winfo_width() + 8
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            try:                                           # keep it on the screen the launcher is on (all monitors together)
                x0, y0 = self.winfo_vrootx(), self.winfo_vrooty()
                x1, y1 = x0 + self.winfo_vrootwidth(), y0 + self.winfo_vrootheight()
                if x1 - x0 < 200 or y1 - y0 < 200:
                    x0, y0, x1, y1 = 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()
            except Exception:
                x0, y0, x1, y1 = 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()
            if x + tw > x1 - 8:
                x = max(x0 + 8, widget.winfo_rootx() - tw - 8)
            if y + th > y1 - 48:
                y = max(y0 + 8, widget.winfo_rooty() - th - 4)
            w.wm_geometry(f"+{int(x)}+{int(y)}")
        except Exception:
            self._tip_hide()

    def tip(self, widget, text, delay=450, click=False):
        """text shows in a small dark box while the mouse rests on widget. text: a string, a StringVar or a function."""
        def enter(_e=None):
            self._tip_hide()
            self._tip_job = self.after(delay, lambda: self._tip_show(widget, text))
        def leave(_e=None):
            self._tip_hide()
        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")
        widget.bind("<ButtonPress>", (lambda _e: self._tip_show(widget, text)) if click else leave, add="+")
        return widget

    def hint(self, parent, text):
        """A small round "?" that shows text when the mouse is on it (or it is clicked). Returns the widget: grid / pack it."""
        lb = tk.Label(parent, text="?", bg=SOFT, fg=LEATHER, width=2, bd=0, padx=1, pady=0, cursor="question_arrow",
                      font=self.zfont("Segoe UI", 8, "bold"))
        lb.bind("<Enter>", lambda _e: lb.configure(bg=RUST, fg="white"), add="+")
        lb.bind("<Leave>", lambda _e: lb.configure(bg=SOFT, fg=LEATHER), add="+")
        return self.tip(lb, text, delay=120, click=True)

    def about(self, parent, title, text, style="Head.TLabel"):
        """A heading with its "?" (a tab's or a box's explanation). Returns the frame: grid / pack it."""
        f = ttk.Frame(parent)
        ttk.Label(f, text=title, style=style).pack(side="left")
        self.hint(f, text).pack(side="left", padx=(8, 0))
        return f

    def labeled(self, parent, text, tip=None):
        """A label, with a "?" after it when tip is given. Returns the frame."""
        f = ttk.Frame(parent)
        ttk.Label(f, text=text).pack(side="left")
        if tip:
            self.hint(f, tip).pack(side="left", padx=(6, 0))
        return f

    def check(self, parent, text, variable, tip=None, command=None):
        """A tick box with a short label, and a "?" after it when tip is given. Returns the frame."""
        f = ttk.Frame(parent)
        kw = {"command": command} if command else {}
        cb = ttk.Checkbutton(f, text=text, variable=variable, **kw)
        cb.pack(side="left")
        f.box = cb
        if tip:
            self.hint(f, tip).pack(side="left", padx=(6, 0))
        return f

    @staticmethod
    def scaled(img, z, big=8):
        """A PhotoImage at about z times its size (zoom by a whole number, then shrink by one)."""
        if abs(z - 1) < 0.02:
            return img
        a, b = min(((a, b) for a in range(1, big + 1) for b in range(1, big + 1)), key=lambda ab: abs(ab[0] / ab[1] - z))
        out = img.zoom(a, a) if a > 1 else img
        return out.subsample(b, b) if b > 1 else out

    def header(self):
        """Wood planks fill the window; the title sits on them at the top and the tabs on a parchment panel under it
        (self.shell). The whole thing grows and shrinks with the window (relayout)."""
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        z0 = max(ZOOM_MIN, min(1.0, (sh - 110) / WIN_H, (sw - 40) / WIN_W))
        self.geometry(f"{int(WIN_W * z0)}x{int(WIN_H * z0)}")
        self.minsize(int(WIN_W * ZOOM_MIN), int(WIN_H * ZOOM_MIN))
        self.bg0 = None
        try:
            from wood_data import WOOD_B64
            self.bg0 = tk.PhotoImage(data=WOOD_B64)
        except Exception:
            pass
        cv = self.cv = tk.Canvas(self, highlightthickness=0, bg="#22150b")
        cv.pack(fill="both", expand=True)
        shell = self.shell = tk.Frame(cv, bg=PARCH, highlightbackground="#2e1c10", highlightcolor="#2e1c10", highlightthickness=3)
        self.nb = ttk.Notebook(shell)
        self.nb.pack(fill="both", expand=True)
        # the "Play" tab: the game, who plays in it, LAUNCH, and (Advanced Options) each bot and each ghost. It scrolls.
        self.body = tk.Frame(self.nb, bg=PARCH)
        self.nb.add(self.body, text="  Play  ")
        pc = self.play_cv = tk.Canvas(self.body, bg=PARCH, highlightthickness=0, width=860, height=PLAY_H)
        self.play_bar = ttk.Scrollbar(self.body, orient="vertical", command=pc.yview)
        pc.configure(yscrollcommand=self.play_bar.set)
        pc.grid(row=0, column=0, sticky="nsew")
        self.play_bar.grid(row=0, column=1, sticky="ns")
        self.body.rowconfigure(0, weight=1)                 # (as tall as the tallest tab; PLAY_H is only its least)
        self.body.columnconfigure(0, weight=1)
        self.play = tk.Frame(pc, bg=PARCH)
        self.play_win = pc.create_window((0, 0), window=self.play, anchor="nw")
        self.play.bind("<Configure>", lambda e: self.play_fit())
        pc.bind("<Configure>", lambda e: self.play_center())
        self.zt = tk.Frame(self.nb, bg=PARCH)              # (the old "ZeroTier LAN" tab: built, never shown)
        # (no ZeroTier tab any more: launching a game installs/starts ZeroTier and joins the games' network, see zt_ensure)
        self.gm = tk.Frame(self.nb, bg=PARCH)              # the "Game Modes" tab
        self.nb.add(self.gm, text="  Custom Games  ")          # (25 Nov, the user: "Custom Games", was "Game Modes")
        self.rl = tk.Frame(self.gm, bg=PARCH)              # Juggernaut / Infection settings
        self.gg = tk.Frame(self.gm, bg=PARCH)              # Gun-Game: shown inside that tab when its mode is chosen
        self.rot = tk.Frame(self.nb, bg=PARCH)             # the "Map Rotation" tab
        self.nb.add(self.rot, text="  Map Rotation  ")
        self.st = tk.Frame(self.nb, bg=PARCH)              # the "Stats" tab
        self.nb.add(self.st, text="  Stats  ")
        self.tl = tk.Frame(self.nb, bg=PARCH)              # the "Tools" tab (launchertools.py)
        self.nb.add(self.tl, text="  Tools  ")
        cv.bind("<Configure>", lambda e: self.relayout_soon())

    def play_fit(self):
        """The Play tab's scrolling area follows what is in it (Advanced Options open or closed)."""
        # (13 Nov: this runs on every <Configure> of the content, which also fires when the content only MOVES (centring,
        #  the window being redrawn). Doing anything then made a loop that redrew the window non-stop: only a real change of
        #  the content's size counts, and only a change of its width asks for a new layout.)
        try:
            pc = self.play_cv
            rw, rh = self.play.winfo_reqwidth(), self.play.winfo_reqheight()
            last = getattr(self, "_play_req", None)
            if last == (rw, rh):
                return
            self._play_req = (rw, rh)
            pc.configure(scrollregion=(0, 0, rw, rh))
            if last is None or last[0] != rw:
                pc.configure(width=rw)
            if rh <= pc.winfo_height():
                pc.yview_moveto(0)
            self.play_center()
            if last is not None and last[0] != rw:
                self.relayout_soon()
        except tk.TclError:
            pass

    def play_center(self):
        """The Play tab's content stays in the middle when the tab is wider than it."""
        try:
            x = max(0, (self.play_cv.winfo_width() - self.play.winfo_reqwidth()) // 2)
            if getattr(self, "_play_x", None) == x:
                return
            self._play_x = x
            self.play_cv.coords(self.play_win, x, 0)
        except (tk.TclError, AttributeError):
            pass

    def play_wheel(self, e):
        """The mouse wheel scrolls the Play tab (anywhere on it); never changes a dropdown or a number box."""
        try:
            if str(self.nb.select()) != str(self.body):
                return
            if self.play.winfo_reqheight() <= self.play_cv.winfo_height():
                return "break"
            self.play_cv.yview_scroll(-1 * (e.delta // 120 or (1 if e.delta > 0 else -1)) * 2, "units")
        except tk.TclError:
            pass
        return "break"

    # -- the window can be any size: the layout is drawn for WIN_W x WIN_H and zoomed (fonts and the header)
    def relayout_soon(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(140, self.relayout)

    def relayout(self):
        self._job = None
        if not self._ready:
            return
        try:
            W, H = self.cv.winfo_width(), self.cv.winfo_height()
            if W < 100 or H < 100:
                return
            key = (W, H, getattr(self, "_play_req", None))
            if key == getattr(self, "_last_layout", None):
                return                                      # (nothing changed: no redraw, no flicker)
            # (a safety net against any other loop: at most 4 layouts in 3 s for the same window size)
            now = time.time()
            runs = [t for t in getattr(self, "_lay_runs", []) if now - t < 3.0 and getattr(self, "_lay_size", None) == (W, H)]
            if len(runs) >= 4:
                if not getattr(self, "_lay_retry", False):     # (one more try once things have settled)
                    self._lay_retry = True
                    def again():
                        self._lay_retry = False
                        self._lay_runs = []
                        self.relayout()
                    self.after(3100, again)
                return
            self._lay_runs, self._lay_size = runs + [now], (W, H)
            z = max(ZOOM_MIN, min(ZOOM_MAX, W / WIN_W, H / WIN_H))
            for _ in range(4):                              # the panel must fit: shrink the zoom a little if it doesn't
                self.apply_zoom(z)
                self.update_idletasks()
                pw, ph = self.shell.winfo_reqwidth(), self.shell.winfo_reqheight()
                room_h = H - (HEAD_H + 6) * z - 10
                if pw <= W - 6 and ph <= room_h:
                    break
                z = max(ZOOM_MIN * 0.6, z * min((W - 6) / pw, room_h / ph) * 0.98)
            z = getattr(self, "_zapplied", z)
            self._z = z
            self.draw_header(z, W, H)
            self.place_shell(force=True)
            self.update_idletasks()
            self._last_layout = (self.cv.winfo_width(), self.cv.winfo_height(), getattr(self, "_play_req", None))
        except tk.TclError:
            pass

    def apply_zoom(self, z):
        if self._z is not None and abs(z - getattr(self, "_zapplied", -1)) < 0.004:
            return
        from tkinter import font as tkfont
        if not hasattr(self, "_nbase"):
            self._nbase = {}
            for n in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont",
                      "TkIconFont", "TkTooltipFont", "TkFixedFont"):
                try:
                    self._nbase[n] = tkfont.nametofont(n).cget("size")
                except tk.TclError:
                    pass
        size = lambda n: (-1 if n < 0 else 1) * max(7, int(round(abs(n) * z)))
        for n, base in self._nbase.items():
            try:
                tkfont.nametofont(n).configure(size=size(base))
            except tk.TclError:
                pass
        for f, base in self._zfonts:
            f.configure(size=size(base))
        st = ttk.Style(self)
        for name, (fam, base, weight) in getattr(self, "_sbase", {}).items():
            st.configure(name, font=(fam, size(base), weight))
        st.configure("Treeview", rowheight=max(14, int(round(20 * z))))
        pc = getattr(self, "play_cv", None)
        if pc is not None:
            pc.configure(height=int(PLAY_H * z))
        self._zapplied = z

    def draw_header(self, z, W, H):
        """The wood, and on it the title, what it is for and who made it."""
        cv = self.cv
        cv.delete("all")
        self._keep = []
        img = self.wood_for(W, H)
        if img is not None:
            cv.create_image(W / 2, H, image=img, anchor="s")        # (the floor and the boots stay at the bottom)
        ox = max(0.0, (W - WIN_W * z) / 2)
        hh = HEAD_H * z
        tf = (TITLEF, max(12, int(round((34 if TITLEF == "Ultra" else 28) * z))))
        sf = (DISPLAY, max(8, int(round(11 * z))), "bold")
        def shadowed(x, y, text, font, fill, anchor, d=2):
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1), (d + 1, d + 1)):      # a dark outline and a drop shadow
                cv.create_text(x + dx * max(1, z), y + dy * max(1, z), text=text, anchor=anchor, fill="#1a0f07", font=font)
            cv.create_text(x, y, text=text, anchor=anchor, fill=fill, font=font)
        title = "BOT LAUNCHER COMPANION" if COMPANION else "BOT LAUNCHER"    # (25 Nov, the user: "Bot Launcher Companion")
        if COMPANION:                                  # (a longer title: smaller if it would reach the credits on the right)
            try:
                from tkinter import font as tkfont
                while tf[1] > 12 and tkfont.Font(family=tf[0], size=tf[1]).measure(title) > (WIN_W - 300) * z:
                    tf = (tf[0], tf[1] - 1)
            except Exception:
                pass
        shadowed(ox + 26 * z, hh * 0.44, title, tf, GOLDEN, "w", 3)
        shadowed(ox + 30 * z, hh * 0.86, "Outlaws + A Handful of Missions: Remastered", sf, SAND, "w", 1)
        shadowed(ox + (WIN_W - 26) * z, hh * 0.5, "By WaRRioR & Dotard", sf, SAND, "e", 1)
        if COMPANION:                                  # (25 Nov, the user: the Companion's version stays on show, as on the old one)
            ca = sys.modules.get("companion_app")
            if ca is None:
                try:
                    import companion_app as ca
                except Exception:
                    ca = None
            if ca is not None and getattr(ca, "VERSION", None):
                n = int(ca.VERSION)
                shadowed(ox + (WIN_W - 26) * z, hh * 0.86, "v%d.%02d" % divmod(n, 100), sf, SAND, "e", 1)

    def wood_for(self, W, H):
        """The wood picture that covers a W x H window: the small one, the big one (2560 x 1600, made the first time it is
        needed), or the big one doubled for even larger screens (made once, kept)."""
        if self.bg0 is None:
            return None
        if W <= self.bg0.width() and H <= self.bg0.height():
            return self.bg0
        big = getattr(self, "bg_big", None)
        if big is None:
            try:
                from wood_data import WOOD_BIG_B64
                big = self.bg_big = tk.PhotoImage(data=WOOD_BIG_B64)
            except Exception:
                big = self.bg_big = False
        if not big:
            return self.bg0
        if W <= big.width() and H <= big.height():
            return big
        dbl = getattr(self, "bg_dbl", None)
        if dbl is None:
            try:
                dbl = self.bg_dbl = big.zoom(2, 2)
            except Exception:
                dbl = self.bg_dbl = big
        return dbl

    def place_shell(self, force=False):
        """The tabs' panel sits on the wood under the title (re-made after the header is redrawn)."""
        if not self._ready or self._z is None:
            return
        try:
            z = self._z
            W = self.cv.winfo_width()
            self.cv.create_window(W / 2, (HEAD_H + 6) * z, window=self.shell, anchor="n")
        except tk.TclError:
            pass

    def pick_map_group(self):
        """Show the standard or the custom maps in the Map box."""
        names = MAP_GROUPS.get(self.map_group.get()) or MAP_GROUPS["Standard maps"]
        self.map.configure(values=names)
        self.map.set(names[0])
        self.update_modes()

    def update_modes(self):
        """Offer only the game modes the chosen map has; a mode it lacks falls back to Deathmatch."""
        key = dict(MAPS).get(self.map.get(), "hargrove")
        have = MAP_MODES.get(key)
        if have is not None and key in TEAM_RCM_ADD and getattr(self, "who", None) is not None and self.who.get() == "Ghosts":
            have = set(have) | {"team"}             # (a game you host: Team Deathmatch is added for it, see TEAM_RCM_ADD)
        labels = [m for m, v in MODES if have is None or v in have]
        self.mode.configure(values=labels)
        if self.mode.get() not in labels:
            self.mode.set(labels[0])
        self.refresh_all()

    # -- rows
    @property
    def screen_value(self):
        return dict(SCREEN_MODES).get(self.screen.get(), "exclusive")

    def base_diff(self):
        """The general difficulty; while the box says Custom, the one it had before."""
        d = self.diff.get()
        return d if d in DIFFICULTIES else self.last_base

    def diff_changed(self):
        """One difficulty for every bot (each bot's row shows it, greyed out), or Custom (each bot's own pick back)."""
        d = self.diff.get()
        if d in DIFFICULTIES:
            self.last_base = d
        self.bot_diffmode_apply()
        self.refresh_all()

    def weapons_changed(self):
        """Game rules' Weapons: the bots' TNT-only / melee-only game and the ghosts' weapons follow it."""
        v = self.weapons_rule.get()
        self.tnt_only.set(v == "TNT only")
        self.melee_only.set(v == "Melee only")
        if hasattr(self, "ghost_weapons"):
            self.ghost_weapons.set(v)
        self.refresh_all()

    def bot_diffmode_apply(self, first=False):
        mode = self.diff_v.get()
        for i, b in enumerate(self.blocks):
            d = b["diff"]
            if mode in DIFFICULTIES:
                if not first and str(d.cget("state")) != "disabled": self.bot_custom_diffs[i] = d.get()
                d.set(mode); d.configure(state="disabled")
            else:
                d.configure(state="readonly")
                d.set(self.bot_custom_diffs[i] if self.bot_custom_diffs[i] in DIFFICULTIES else self.last_base)

    def bot_charmode_apply(self, first=False):
        """Random: every bot's character shows Random and can't be changed; Custom: each bot's own pick back."""
        rnd = self.bot_charmode.get() == "Random"
        for i, b in enumerate(self.blocks):
            c = b["char"]
            if rnd:
                if not first and str(c.cget("state")) != "disabled": self.bot_custom_chars[i] = c.get()
                c.set(CHAR_RANDOM); c.configure(state="disabled")
            else:
                c.configure(state="normal")
                if not first: c.set(self.bot_custom_chars[i])
        if not first:
            self.refresh_all()

    def bot_diff_changed(self, changed):
        """One bot got its own difficulty: the general box says Custom. The other bots keep the difficulty they had,
        spelled out."""
        d = self.diff.get()
        if d in DIFFICULTIES:
            self.last_base = d
            for b in self.blocks:
                if b["diff"] is not changed and b["diff"].get() not in DIFFICULTIES:
                    b["diff"].set(self.last_base)
        self.diff.set("Custom")
        self.refresh_all()

    def refresh_all(self):
        try:
            n = max(1, min(10, int(self.count.get())))
        except (tk.TclError, ValueError):
            n = 1
        team_mode = self.mode_value() in TEAM_MODES
        self.apply_split(n, team_mode)
        base = self.base_diff()
        only = self.tnt_only.get() or base in SHARP
        for w in getattr(self, "bot_rows_head", []):           # the second column's headings only once bot 6 is there
            w.grid() if n > 5 else w.grid_remove()
        for i, b in enumerate(self.blocks):
            if i < n:
                r, c = 2 + i % 5, (i // 5) * 4
                b["label"].grid(row=r, column=c, sticky="w", padx=8, pady=4)
                b["char"].grid(row=r, column=c + 1, sticky="w", padx=8, pady=4)
                b["diff"].grid(row=r, column=c + 2, sticky="w", padx=8, pady=4)
            else:
                b["label"].grid_remove(); b["char"].grid_remove(); b["diff"].grid_remove()
            b["team"].configure(state="readonly" if team_mode else "disabled")
            own = b["diff"].get()
            nightmare = (own if own in DIFFICULTIES else base) in SHARP and not self.tnt_only.get()
            b["char"].block([k for k, v in CHARACTERS if v not in NIGHTMARE_CHARS] if nightmare else [])
            rnd = getattr(self, "bot_charmode", None) is not None and self.bot_charmode.get() == "Random"
            b["char"].configure(state="disabled" if self.tnt_only.get() or self.melee_only.get() or rnd else "normal")
            b["beh"].configure(state="disabled" if only else "readonly")
            for cb in b["weapons"]:
                cb.configure(state="disabled" if only else "readonly")
        if hasattr(self, "gh_split"):
            self.gh_split_refresh()
        self.play_summary()

    def apply_split(self, n, team_mode):
        """Team modes: with a number chosen, the first that many bots go Red and the rest Blue (6 v 2 and so on).
        Bots + Ghosts: the numbers count bots AND ghosts together (13 Nov, the user: 7 bots + 7 ghosts showed only 7); each
        team gets its share of bots and of ghosts, and the ghosts' part is kept in self.both_red_ghosts for start_ghosts."""
        row = getattr(self, "split_row", None)
        self.both_red_ghosts = None
        if row is None:
            return
        both = getattr(self, "who", None) is not None and self.who.get() == "Bots + Ghosts"
        if both and team_mode:
            try:
                gn = max(1, min(MAX_GHOSTS, int(self.ghost_count.get())))
            except (tk.TclError, ValueError):
                gn = 0
            row.grid()
            total = n + gn
            r, bl = self.red_n.get().strip(), self.blue_n.get().strip()
            src = getattr(self, "split_src", "red")
            typed = bl if src == "blue" else r
            if not typed.isdigit():
                if not (r.isdigit() or bl.isdigit()):
                    self.split_info.configure(text=f"{total} players: empty = the teams even out")
                    for b in self.blocks: b["team"].set("Auto")    # (each bot's Team box is no longer shown)
                return
            red = total - min(int(bl), total) if src == "blue" else min(int(r), total)
            blue = total - red
            other, other_var = (red, self.red_n) if src == "blue" else (blue, self.blue_n)
            self.split_busy = True
            try:
                if other_var.get() != str(other):
                    other_var.set(str(other))
            finally:
                self.split_busy = False
            rb = round(red * n / total) if total else 0           # each team: bots and ghosts in the same mix as the whole game
            rg = red - rb
            if rg > gn: rb, rg = rb + (rg - gn), gn
            if rb > n: rg, rb = rg + (rb - n), n
            for i, b in enumerate(self.blocks):
                if i < n:
                    b["team"].set("Red" if i < rb else "Blue")
            self.both_red_ghosts = rg
            self.split_info.configure(text=f"Red {red} ({rb} bots, {rg} ghosts), Blue {blue} ({n - rb} bots, {gn - rg} ghosts)")
            return
        if not team_mode or (getattr(self, "who", None) is not None and self.who.get() == "Ghosts"):
            row.grid_remove()                # (a ghosts-only game has its own Red / Blue row, gh_split)
            return
        row.grid()
        r, bl = self.red_n.get().strip(), self.blue_n.get().strip()
        src = getattr(self, "split_src", "red")
        typed = bl if src == "blue" else r
        if not typed.isdigit():
            if not (r.isdigit() or bl.isdigit()):
                self.split_info.configure(text="empty: the teams even out")
                for b in self.blocks: b["team"].set("Auto")        # (each bot's Team box is no longer shown)
            return                          # (never rewrite the box being typed in, it fights the keyboard)
        if src == "blue":
            blue = min(int(bl), n)
            red = n - blue
            other, other_var = red, self.red_n
        else:
            red = min(int(r), n)
            blue = n - red
            other, other_var = blue, self.blue_n
        self.split_busy = True              # (setting the other box must not count as the user typing)
        try:
            if other_var.get() != str(other):
                other_var.set(str(other))
        finally:
            self.split_busy = False
        for i, b in enumerate(self.blocks):
            if i < n:
                b["team"].set("Red" if i < red else "Blue")
        self.split_info.configure(text=f"{red} on Red, {blue} on Blue")

    # -- saved bot presets
    def read_presets(self):
        try:
            data = json.loads(BOT_PRESETS.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return (data + [None] * PRESET_SLOTS)[:PRESET_SLOTS]
        except (OSError, ValueError):
            pass
        return [None] * PRESET_SLOTS

    def write_presets(self):
        try:
            BOT_PRESETS.write_text(json.dumps(self.preset_data, indent=1), encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Could not save", f"The presets file could not be written:\n{e}")

    def show_presets(self, select=None):
        self.preset_list.delete(0, "end")
        for k, p in enumerate(self.preset_data):
            self.preset_list.insert("end", f"{k + 1}.  " + (p.get("name", "Preset") if p else "(empty)"))
        if select is not None:
            self.preset_list.selection_set(select)
        self.preset_buttons()

    def chosen_slot(self):
        sel = self.preset_list.curselection()
        return sel[0] if sel else None

    def preset_buttons(self):
        k = self.chosen_slot()
        full = k is not None and self.preset_data[k] is not None
        self.load_btn.configure(state="normal" if full else "disabled")
        self.del_btn.configure(state="normal" if full else "disabled")

    def save_preset(self):
        k = self.chosen_slot()
        if k is None:
            k = next((i for i, p in enumerate(self.preset_data) if p is None), None)
        if k is None:
            messagebox.showinfo("Presets full", "All 10 slots are used. Click a slot in the list to replace it, "
                                "or delete one first.")
            return
        old = self.preset_data[k]
        if old and not messagebox.askyesno("Replace preset", f"Replace \"{old.get('name', 'Preset')}\" in slot {k + 1}?"):
            return
        name = simpledialog.askstring("Save Bot Preset", f"Name for slot {k + 1}:",
                                      initialvalue=(old or {}).get("name", f"Preset {k + 1}"), parent=self)
        if not name or not name.strip():
            return
        cfg = self.settings()
        self.preset_data[k] = {"name": name.strip()[:24], "count": cfg["count"], "bots": cfg["bots"]}
        self.write_presets()
        self.show_presets(k)
        self.say(f"saved bot preset {k + 1}: {name.strip()}")

    def load_preset(self):
        k = self.chosen_slot()
        p = k is not None and self.preset_data[k]
        if not p:
            return
        chars = [CHAR_RANDOM] + [kk for kk, _ in CHARACTERS]
        behs = [BEH_DEFAULT] + BEHAVIOURS[1:]
        for i, b in enumerate(self.blocks):
            s = p["bots"][i] if i < len(p.get("bots", [])) else {}
            b["name"].set(s.get("name") or f"Bot {i + 1}")
            b["char"].set(s.get("char") if s.get("char") in chars else CHAR_DEFAULT)
            b["beh"].set(s.get("beh") if s.get("beh") in behs else BEH_DEFAULT)
            b["team"].set(s.get("team") if s.get("team") in TEAMS else "Auto")
            lv = s.get("levels")
            dl = default_levels(i + 1, False)
            for j, cb in enumerate(b["weapons"]):
                v = lv[j] if isinstance(lv, list) and j < len(lv) else dl[j]
                cb.set(v if v in LEVELS else dl[j])
        try:
            self.count.set(max(1, min(10, int(p.get("count", 6)))))
        except (tk.TclError, ValueError):
            pass
        self.refresh_all()
        self.say(f"loaded bot preset {k + 1}: {p.get('name')}")

    def delete_preset(self):
        k = self.chosen_slot()
        if k is None or not self.preset_data[k]:
            return
        if messagebox.askyesno("Delete preset", f"Delete \"{self.preset_data[k].get('name')}\"?"):
            self.preset_data[k] = None
            self.write_presets()
            self.show_presets()

    def reset(self):
        for i, b in enumerate(self.blocks):
            b["name"].set(preset_row(i + 1, self.real.get())[0])
            b["char"].set(CHAR_DEFAULT)
            b["beh"].set(BEH_DEFAULT)
            b["team"].set("Auto")
            b["lean"].set("No lean")
            for cb, v in zip(b["weapons"], default_levels(i + 1, self.real.get())):
                cb.set(v)

    # -- settings
    def mode_value(self):
        return dict(MODES).get(self.mode.get(), "dm")

    def settings(self):
        return {
            "map": dict(MAPS).get(self.map.get(), "hargrove"), "mode": self.mode_value(),
            "count": self.count.get(), "timelimit": self.timelimit.get(), "fraglimit": self.fraglimit.get(),
            "friendly_fire": self.ff.get(), "wait_for_me": self.wait_me.get(), "screen_mode": self.screen_value, "screen_v2": True, "private": self.private.get(), "tnt_only": self.tnt_only.get(), "melee_only": self.melee_only.get(), "game_weapons": self.weapons_rule.get(), "difficulty": self.base_diff(),
            "zt_network": self.zt_net.get().strip(), "zt_ip": self.zt_ip.get().strip(),
            "zt_users": self.zt_users.get(), "red_bots": self.red_n.get(), "blue_bots": self.blue_n.get(),
            "taunts": self.taunts.get(), "marshall_law": self.law.get(), "bench": self.bench.get(), "ghost_on": self.ghost_on.get(), "play_who": self.who.get(), "my_char": self.my_char.get(), "adv_open": bool(getattr(self, "adv_open", False)), "ghost_count": self.ghost_count.get(), "ghost_diff": self.ghost_diff_default, "ghost_rows": [{"char": self.ghost_char_saved(i), "diff": self.ghost_diff_saved(i)} for i in range(len(self.ghost_diff_w))], "ghost_charmode": self.ghost_charmode.get(), "ghost_diffmode": self.ghost_diffmode.get(), "ghost_wpn": self.ghost_wpn.get(), "ghost_game": {"map": self.gh_map.get(), "mode": self.gh_mode.get(), "frag": self.gh_frag.get(), "time": self.gh_time.get(), "delay": self.gh_delay.get(), "private": self.gh_private.get(), "red": self.gh_red.get(), "blue": self.gh_blue.get()}, "ghost_swim_snd": self.ghost_swim_snd.get(), "ghost_weapons": self.ghost_weapons.get(), "trim_memory": self.trim.get(), "window_mode": dict(WINDOW_MODES).get(self.wmode.get(), "hidden"),
            "size": self.size.get().strip(), "fps": self.fps.get().strip(),
            "bots": [{"name": b["name"].get(), "char": b["char"].get(), "beh": b["beh"].get(),
                      "team": b["team"].get(), "levels": [cb.get() for cb in b["weapons"]],
                      "lean": b["lean"].get(),
                      "diff": b["diff"].get() if b["diff"].get() in DIFFICULTIES and self.diff_v.get() == "Custom" else None}
                     for b in self.blocks],
            # (the Advanced Options picks kept while Random / one difficulty for all shows instead)
            "bot_charmode": self.bot_charmode.get(), "bot_custom_chars": [
                b["char"].get() if self.bot_charmode.get() != "Random" else self.bot_custom_chars[i] for i, b in enumerate(self.blocks)],
            "bot_custom_diffs": [b["diff"].get() if self.diff_v.get() == "Custom" else self.bot_custom_diffs[i] for i, b in enumerate(self.blocks)],
        }

    def load(self):
        try:
            saved = json.loads(SETTINGS.read_text())
        except (OSError, ValueError):
            try:                                         # (a broken file: the copy from the last good save)
                saved = json.loads(SETTINGS.with_suffix(".json.bak").read_text())
            except (OSError, ValueError):
                return {}
        if saved.get("levels_ver") != LEVELS_VER:          # the preset strengths changed: start from the new ones
            for b in saved.get("bots", []):
                for k in ("name", "char", "levels", "beh"):
                    b.pop(k, None)
        return saved

    def snapshot(self):
        """Everything the launcher remembers between runs (what save() writes; the Tools tab's saved setups keep copies)."""
        s = self.settings()
        s["map"], s["mode"] = self.map.get(), self.mode.get()
        s["window_mode_label"] = self.wmode.get()
        s["levels_ver"] = LEVELS_VER
        s["stats_player"] = self.stats_name.get()
        s["modes"] = {"mode": self.gm_mode.get(), "map": self.gm_map.get(), "level": self.gm_level.get(), "you": self.gm_you.get(),
                      "ranked": self.gm_ranked.get(), "character": self.gm_char.get(), "classic": self.gm_classic.get(), "hell": self.gm_hell.get(), "horde_mode": self.gm_hmode.get(), "opponents": self.gm_opp.get(), "ghost_max": self._ghost_max(), "xmas_top": self.gm_xtop.get(),
                      **{k: v.get() for k, v in self.gm_vars.items()}}
        s["gungame"] = {"map": self.gg_map.get(), "count": self.gg_count.get(), "difficulty": self.gg_diff.get(),
                        "order": self.gg_order.get(), "rounds": self.gg_rounds.get(), "relay": self.gg_relay.get(), "gatling": self.gg_gat.get(), "stinnett": self.gg_stin.get(),
                        "bot_diffs": [cb.get() for cb in self.gg_bot_diffs],
                        "custom": [cb.get() for cb in self.gg_slots]}
        s["roles"] = {"map": self.rl_map.get(), "count": self.rl_count.get(), "difficulty": self.rl_diff.get(),
                      "minutes": self.rl_minutes.get(), "kills": self.rl_kills.get(), "marshall_law": self.rl_law.get()}
        s["rotation"] = {"entries": self.rot_entries, "loop": self.rot_loop.get(), "shuffle": self.rot_shuffle.get(),
                         "score_wait": self.rot_wait_s(), "you": self.rot_you.get(), "net": self.rot_net.get(), "who": self.rot_who.get(),
                         "ghosts": self.rot_gh_n.get(), "ghost_diff": self.rot_gh_diff.get()}
        return s

    def save(self):
        """(17 Nov: the server VM's disk filled up, a save there left launcher_settings.json empty and its map rotation was
        lost. Now written to a temporary file first and swapped in only when it's whole; the last good one is kept as .bak.)"""
        s = self.snapshot()
        tmp = SETTINGS.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(s, indent=1))
            json.loads(tmp.read_text())                  # (all of it reached the disk)
            if SETTINGS.exists():
                try:
                    json.loads(SETTINGS.read_text())       # (only a good one becomes the backup)
                    shutil.copy2(SETTINGS, SETTINGS.with_suffix(".json.bak"))
                except (OSError, ValueError): pass
            os.replace(tmp, SETTINGS)
        except (OSError, ValueError):
            try: tmp.unlink()
            except OSError: pass

    def tool(self, name, *a, **kw):
        """Call the Tools tab's code (launchertools.py) if it loaded; never lets a problem there stop a game."""
        t = getattr(self, "tools", None)
        if t is None:
            return None
        try:
            return getattr(t, name)(*a, **kw)
        except Exception as e:
            try: self.say(f"tools: {name} failed: {e}")
            except Exception: pass
            return None

    def update_launcher(self):
        """Rebuild BotLauncher.exe from the newest launcher.py (update_launcher.cmd), then start it again. This window
        closes: the old exe can't be replaced while it runs."""
        from tkinter import messagebox
        script = HERE / "update_launcher.cmd"
        if not script.exists():
            messagebox.showerror("Update Launcher", f"{script.name} is missing from {HERE}.")
            return
        import hashlib
        stamp = HERE / ".build" / "built.sha"
        try:
            # What the build covers: launcher.py and the build script itself.
            now_hashes = [hashlib.sha256((HERE / n).read_bytes()).hexdigest() for n in ("launcher.py", "update_launcher.cmd")]
        except OSError:
            messagebox.showerror("Update Launcher", f"launcher.py or update_launcher.cmd is missing from {HERE}.")
            return
        try:
            built = [ln.strip().lower() for ln in stamp.read_text().splitlines() if ln.strip()]
        except OSError:
            built = []                      # never built with a stamp: can't tell, so offer it
        if built == now_hashes:
            messagebox.showinfo("Update Launcher", "This launcher is already up to date. Nothing has changed since "
                                "it was last built.")
            return
        if not messagebox.askyesno("Update Launcher",
                                   "A newer launcher.py is waiting.\n\nBuild it now?\n\nThis window closes, a build "
                                   "window runs for a minute or two, and the new launcher opens by itself."):
            return
        # The frozen exe points TCL_LIBRARY/TK_LIBRARY into its own temp folder, which is deleted when it closes; the
        # build must not inherit them or PyInstaller sees no Tcl/Tk and leaves tkinter out of the new exe.
        env = {k: v for k, v in os.environ.items() if k not in ("TCL_LIBRARY", "TK_LIBRARY")}
        try:
            subprocess.Popen(["cmd", "/c", "start", "Updating the launcher", "/D", str(HERE), str(script)],
                             cwd=str(HERE), env=env, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        except OSError as e:
            messagebox.showerror("Update Launcher", f"Could not start the build: {e}")
            return
        self.close()

    def start_bugshot(self):
        """Ctrl+Alt+PgDn anywhere saves a screenshot + system report into the screenshots folder."""
        self.recent = []
        try:
            import bugshot
            self.bugshot = bugshot.Listener(
                extra_log=lambda: self.recent[-120:],
                on_done=lambda png, txt: self.say(f"bug capture saved: screenshots\\{png.name} (+ report)"),
                on_error=self.say)
            if self.bugshot.start():
                self.say(f"Bug capture ready: press {bugshot.HOTKEY} in the game to save a screenshot + report to "
                         "the screenshots folder.")
            else:
                self.say(f"Bug capture off: {self.bugshot.error}")
        except Exception as e:
            self.bugshot = None
            self.say(f"Bug capture unavailable: {e}")

    def close(self):
        self.save()
        if getattr(self, "bugshot", None):
            self.bugshot.stop()
        self.destroy()

    # -- log
    def say(self, text):
        if hasattr(self, "recent"):
            self.recent.append(text.rstrip("\n"))
        def add():
            self.log.configure(state="normal")
            self.log.insert("end", text.rstrip("\n") + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(0, add)

    # -- actions
    def zt_ensure(self):
        """ZeroTier installed, running and joined to the games' network, so friends can reach this game. Runs in a
        worker thread. Returns True when this PC has a ZeroTier address. Never blocks a game: if the user says no or
        it can't be done, the game starts anyway (only people on this PC's own network can join it)."""
        try:
            if zt_state() == "missing":
                ev, ans = threading.Event(), []
                def ask():
                    ans.append(messagebox.askyesno("ZeroTier", "Must install ZeroTier to host LAN games for friends. Do you want to continue?\n\n"
                                                   "(It is downloaded and installed, then this PC joins the games' network. "
                                                   "Windows will ask you to allow it. Choose No to play without friends.)"))
                    ev.set()
                self.after(0, ask)
                ev.wait()
                if not ans[0]:
                    self.say("ZeroTier skipped: friends can't join this game")
                    return False
                msi = Path(tempfile.gettempdir()) / "ZeroTierOne.msi"
                self.say("downloading ZeroTier...")
                try:
                    download(ZT_MSI, msi)
                    self.say("installing ZeroTier (allow the Windows prompt)...")
                    run_elevated("msiexec", f'/i "{msi}" /qn /norestart')
                except Exception as e:                           # noqa: BLE001
                    self.say(f"direct download failed ({e}); trying winget...")
                for _ in range(40):
                    if zt_state() != "missing":
                        break
                    time.sleep(1)
                if zt_state() == "missing":
                    self.say("installing ZeroTier with winget (allow the Windows prompt; this can take a minute)...")
                    run_elevated("cmd", "/c winget install -e --id ZeroTier.ZeroTierOne --silent "
                                        "--accept-package-agreements --accept-source-agreements")
                    for _ in range(60):
                        if zt_state() != "missing":
                            break
                        time.sleep(2)
                if zt_state() == "missing":
                    self.say("could not install ZeroTier automatically (install it from zerotier.com/download); starting without it")
                    return False
            if zt_state() != "on":
                self.say("turning ZeroTier on (allow the Windows prompt)...")
                run_elevated("cmd", f"/c sc start {ZT_SERVICE}")
                for _ in range(20):
                    if zt_state() == "on":
                        break
                    time.sleep(1)
                if zt_state() != "on":
                    self.say("could not turn ZeroTier on; starting without it")
                    return False
            if not zt_joined():
                self.say("joining the games' ZeroTier network...")
                run_elevated("cmd", f'/c ""{ZT_CLI}" join {ZT_NET}"')
            ip = None
            for i in range(30):
                ip = zerotier_ip()
                if ip:
                    break
                if i == 3:
                    self.say("waiting for the network to give this PC an address (the network owner may need to authorise it)...")
                time.sleep(2)
            self.say(f"ZeroTier is on; friends join at {ip}" if ip else
                     "ZeroTier is on, but this PC isn't authorised on the network yet: friends can't join until it is")
            return bool(ip)
        except Exception as e:                                   # noqa: BLE001
            self.say(f"ZeroTier: {e}")
            return False

    def launch(self, zerotier=False, gungame=False, rotation=False, zt_ok=False, roles=None):
        if rotation and (self.rot_net.get() == "Online game" or getattr(self, "server_mode", False)):
            zt_ok = True                       # (an online game needs no ZeroTier; an unattended host server asks nothing)
        if not zt_ok:                          # every launcher game first makes sure ZeroTier is ready (the Companion does the same)
            for b, _ in self.launch_btns:
                b.configure(state="disabled")
            def prep():
                try:
                    self.zt_ensure()
                finally:
                    def go():
                        for b, t in self.launch_btns:
                            b.configure(text=t, state="normal")
                        self.launch(zerotier=zerotier, gungame=gungame, rotation=rotation, zt_ok=True, roles=roles)
                    self.after(0, go)
            threading.Thread(target=prep, daemon=True).start()
            return
        if not (HERE / "botctl.py").exists():
            messagebox.showerror("Not found", f"botctl.py is not next to the launcher:\n{HERE}")
            return
        self.rotation_args = None
        self.play_char, self.play_horde = None, False    # (a Survival game before this one picked them)
        self.play_autojoin = False
        self.play_char = self.my_char.get()                # (your game starts as it; the XP goes to it, see after_my_game)
        # Start your own copy and join it, or only the bots: the Bots Setup and Map Rotation tabs each have an
        # "I play too" box; ZeroTier and Gun-Game games always start yours.
        self.join_me = (self.rot_you.get() and not getattr(self, "server_mode", False)) if rotation else self.wait_me.get() if not (zerotier or gungame) else True
        if rotation:
            if not self.rot_entries:
                messagebox.showinfo("Map rotation", "Add at least one map to the list first.")
                return
            if not (HERE / "rotation.py").exists():
                messagebox.showerror("Not found", f"rotation.py is not next to the launcher:\n{HERE}")
                return
            try:
                self.rot_write()
            except OSError as e:
                messagebox.showerror("Map rotation", f"Could not write rotation.txt:\n{e}")
                return
            if self.rot_net.get() == "Online game":
                std = {dict(MAPS).get(l) for l in MAP_GROUPS.get("Standard maps", [])}
                labels = {k: l for l, k in MAPS}
                bad = [labels.get(e["map"], e["map"]) for e in self.rot_entries if e["map"] not in std]
                if bad:
                    messagebox.showerror("Online game", "Online games can only use the standard maps (other players can't load "
                                         "custom maps). Remove these from the list:\n" + "\n".join(dict.fromkeys(bad)))
                    return
                if any(not str(e.get("timelimit") or "").strip() or str(e.get("timelimit")).strip() == "0" for e in self.rot_entries):
                    messagebox.showerror("Online game", "Every map needs Minutes for an online game: the host never plays, "
                                         "so a match with no time limit would never end.")
                    return
                self.join_me = False
            online = self.rot_net.get() == "Online game"
            self.rotation_args = (["rotation.txt", "--score-wait", str(self.rot_wait_s())]
                                  + (["--restart-each-map"] if online else
                                     ["--host-wait", str(ONLINE_HOST_WAIT), "--restart-on-fail"] if getattr(self, "server_mode", False) else [])
                                  + ([] if self.rot_loop.get() else ["--once"])
                                  + (["--shuffle"] if self.rot_shuffle.get() else []))
        try:
            cfg = self.settings()
            if rotation:                           # the bots start on the first map of the list
                first = self.rot_order()[0]
                cfg.update(map=first["map"], mode=first["mode"], timelimit=first.get("timelimit") or "",
                           fraglimit=first.get("fraglimit") or "", wait_for_me=self.join_me)
                self.rot_ghosts = self.rot_net.get() != "Online game" and self.rot_who.get() == "Ghosts"
                if self.rot_ghosts:                        # one passive host copy; ghost players fight instead of bots
                    # the host shows as this PC's own Steam name when nobody plays here (a host-only PC); when you play
                    # too, your game already has that name, so the host keeps its bot name
                    cfg.update(count=1, bots=cfg["bots"][:1], tnt_only=False, melee_only=False, real_name=not self.join_me)
                    self.ghost_hosted = False              # (not a Ghosts-tab game: the host is a bot copy)
                    self.ghost_rot_level = first["map"]
                if self.rot_net.get() == "Online game":    # one host copy (your account), nobody else from here
                    cfg.update(online=True, count=1, bots=cfg["bots"][:1], wait_for_me=False, tnt_only=False, melee_only=False,
                               window_mode="visible", maxplayers=ONLINE_MAX_PLAYERS,
                               size="960x540", fps=30)          # (big enough to read and click the online sign-in screens)
            self.gg_env = {}
            if self.stats_name.get().strip():
                self.gg_env = {"BOT_STATS_PLAYER": self.stats_name.get().strip(),      # the Stats tab: every launcher game
                               "BOT_STATS_CHAR": self.my_char.get()}                       # (and the character the XP goes to)
            if not rotation:
                self.rot_ghosts = False
            if rotation and getattr(self, "rot_ghosts", False):
                self.gg_env["BOT_PASSIVE"] = "1"           # the host copy holds the lobby and never fights: the ghosts do
            if gungame:
                cfg.update(map=dict(MAPS).get(self.gg_map.get(), "hargrove"), mode="dm", count=self.gg_count.get(), wait_for_me=True,
                           difficulty=(self.gg_diff.get() if self.gg_diff.get() in DIFFICULTIES else self.gg_last_base), tnt_only=False, melee_only=False, timelimit="", fraglimit="")
                pb = [cb.get() for cb in self.gg_bot_diffs]
                cfg["bots"] = [dict(b, diff=(pb[i] if i < len(pb) and pb[i] in DIFFICULTIES else None)) for i, b in enumerate(cfg["bots"])]
                order = self.gg_ladder()
                self.gg_env.update({"BOT_GUNGAME": "1", "BOT_GG_ORDER": ",".join(map(str, order)),
                                    "BOT_GG_LAUNCH": str(int(time.time() * 1000))})       # (a round's end resets every bot)
                if self.gg_relay.get():                          # teams: the bots split Red / Blue, a team shares its rung
                    key = dict(MAPS).get(self.gg_map.get(), "hargrove")
                    if "team" not in MAP_MODES.get(key, {"team"}):
                        messagebox.showerror("Relay", "This map has no team game. Pick another map for Relay.")
                        return
                    cfg["mode"] = "team"
                    for i, b in enumerate(cfg["bots"]):
                        b["team"] = "Red" if i % 2 == 0 else "Blue"
                    self.gg_env["BOT_GG_RELAY"] = "1"
                if self.gg_rounds.get():
                    self.gg_env["BOT_GG_ROUNDS"] = "3"       # the game ends after the third knife win; same map, nobody leaves
                if self.gg_order.get() == "Randomized":         # a new order for every round, the same for everyone
                    self.gg_env["BOT_GG_RANDOM"] = "1"
                if 9 in order and self.gg_stin.get():
                    self.gg_env["BOT_GG_STINNETT"] = "1"
            if roles:                                       # Juggernaut / Infection: a free for all with a timer
                rkey = dict(MAPS).get(self.rl_map.get(), "hargrove")
                cfg.update(map=rkey, mode="dm", count=self.rl_count.get(), wait_for_me=True,
                           difficulty=self.rl_diff.get(), tnt_only=(roles == "Hot Potato"), melee_only=False, timelimit=str(max(1, int(self.rl_minutes.get()))),
                           fraglimit=(str(int(self.rl_kills.get())) if roles != "Infection" and int(self.rl_kills.get() or 0) > 0 else ""),
                           marshall_law=bool(self.rl_law.get()))
                self.jug_map_key = rkey if roles == "Juggernaut" else None      # the gold Gatling (darkitems.patch_juggernaut)
                self.gg_env.update({"BOT_ROLE": {"Juggernaut": "jug", "Hot Potato": "pot"}.get(roles, "inf"),
                                    "BOT_ROLE_LAUNCH": str(int(time.time() * 1000))})
            if zerotier:                                    # the count includes you, unless you don't play
                cfg["wait_users"] = max(2, int(self.zt_users.get())) - (0 if cfg["wait_for_me"] else 1)
            cfg["private"] = bool(self.private.get()) and not zerotier and not cfg.get("online")     # (a ZeroTier game is for friends)
            if cfg["private"]:
                cfg["wait_users"] = 1 if cfg["wait_for_me"] else 0
            args = build_args(cfg)
        except ValueError:
            messagebox.showerror("Check the numbers", "Time limit, score limit and FPS must be whole numbers.")
            return
        self.save()
        self.gg_map_key = cfg["map"] if gungame else None
        if not cfg.get("online"):                  # (online players can't fetch mods from here)
            self.serve_mods("gungame" if gungame else cfg["mode"], cfg["map"], private=cfg["private"])
        if gungame:
            self.say("gun game ladder (slots): " + self.gg_env["BOT_GG_ORDER"] + ("  (Relay: teams share a ladder)" if self.gg_env.get("BOT_GG_RELAY") else ""))
        for b in self.blocks:
            b["lean"].set("No lean")            # a lean is for this game only
        for b, _ in self.launch_btns:
            b.configure(text="Loading, Please Wait", state="disabled")
        self.say("starting: " + " ".join(args))
        self.zerotier = zerotier
        self.join_me = cfg["wait_for_me"]      # start your own game once the bots' game is up
        try:
            botdiff = cfg.get("difficulty") if cfg.get("difficulty") in DIFFICULTIES else "Normal"
            chars = dict(self.GHOST_CHARS)
            rows = []
            for c, d in zip(self.ghost_char_w, self.ghost_diff_w):
                key = chars.get(c.get(), "mary")
                if key == "random":
                    key = random.choice([k for _, k in self.GHOST_CHARS if k != "random"])
                rows.append((key, d.get() if d.get() in DIFFICULTIES else botdiff))
            # Ghosts join a bots' game by themselves only when the Play tab says "Bots + Ghosts" (and never a Gun-Game, a role
            # game or a ZeroTier game). The settings are kept either way, so 'Join ghosts now' can still add them by hand.
            both = self.who.get() == "Bots + Ghosts" and not (rotation or gungame or roles or zerotier)
            self.ghost_cfg = (both, max(1, min(MAX_GHOSTS, int(self.ghost_count.get()))), botdiff, rows,
                              bool(self.ghost_wpn.get()), bool(self.ghost_swim_snd.get()))
            self.ghost_force = "tnt" if cfg.get("tnt_only") else ("melee" if cfg.get("melee_only") else None)     # the bots' game decides
            if rotation and getattr(self, "rot_ghosts", False):   # Map Rotation with ghosts: they join every map by themselves
                self.ghost_cfg = self.rot_ghost_cfg()
                self.ghost_force = None
        except Exception:
            self.ghost_cfg = (False, 1, "Normal", [], True, True)
        self.cancel = False                   # set by "Close all game windows" while loading
        self.launching = True
        threading.Thread(target=self.run_bots, args=(args,), daemon=True).start()
        if self.trim.get():
            threading.Thread(target=self.trim_loop, daemon=True).start()

    def serve_mods(self, mode, map_key, **kw):
        """Friends using the Outlaws Companion fetch this game's mods (zombie skins, custom maps) from here.
        kw: melee / christmas / private, as modserver.start takes them."""
        try:
            if modserver and modserver.start(mode, map_key, **kw):
                self.say("mod server up (port %d): friends' companion apps can fetch this game's mods" % modserver.PORT)
        except Exception as e:
            self.say("mod server not started: %s" % e)

    def ready(self):
        """Put the Launch button back (called from worker threads)."""
        self.launching = False
        try:
            self.after(0, lambda: [b.configure(text=t, state="normal") for b, t in self.launch_btns])
        except Exception:
            pass

    def trim_loop(self):
        """Every 30 seconds, trim the bots' idle memory, until their game copies are gone."""
        seen, missing, first = False, 0, True
        started = time.time()
        for _ in range(60 * 60 * 24):
            time.sleep(30)
            pids = bot_game_pids()
            if pids is None:
                continue
            if pids:
                seen, missing = True, 0
                if len(pids) == 1:
                    continue                      # (14 Nov: a lone copy is the game that hosts the ghosts; trimming it made the
                                                  #  whole game hitch every time Windows loaded its memory back)
                try:
                    n = trim_working_sets(pids)
                except OSError:
                    n = 0
                if first:
                    self.say(f"trimming idle memory of {n} of {len(pids)} bot windows (every 30 s)")
                    first = False
            else:
                missing += 1
                if (seen and missing >= 3) or (not seen and time.time() - started > 600):
                    return

    def jug_restore_when_over(self):
        """Every game window of the Juggernaut game is gone: the map and the archive with the gold Gatling go."""
        t0, seen, missing = time.time(), False, 0
        while time.time() - t0 < 6 * 3600:
            time.sleep(10)
            pids = bot_game_pids()
            if pids:
                seen, missing = True, 0
            elif seen and pids is not None:
                missing += 1
                if missing >= 3:
                    break
        try:
            darkitems = _disk('darkitems')
            silvergat = _disk('silvergat')
            darkitems.restore()
            fd = _disk('fistdmg')
            for _ in range(6):
                if silvergat.off() and fd.off():
                    break
                time.sleep(5)
        except Exception as e:
            self.say(f"could not put the map back: {e}")

    def gg_restore_when_over(self):
        """The gun game is over (the last round won, or every game window gone): the map goes back to how it was.
        (Closing the games from the launcher does the same through kill_all.)"""
        import json
        t0, seen = time.time(), False
        while time.time() - t0 < 6 * 3600:
            time.sleep(10)
            try:
                d = json.loads((HERE / "state" / "gg_ladder.json").read_text())
                if d.get("n", 0) >= int(os.environ.get("BOT_GG_ROUNDS") or self.gg_env.get("BOT_GG_ROUNDS") or 10 ** 6) \
                        and str(d.get("launch")) == self.gg_env.get("BOT_GG_LAUNCH"):
                    time.sleep(HOLD_AFTER_END)
                    break
            except (OSError, ValueError):
                pass
            pids = bot_game_pids()
            if pids:
                seen = True
            elif seen and pids is not None:
                break
        try:
            darkitems = _disk('darkitems')
            darkitems.restore()
            self.say("gun game over: the map is back to how it was")
        except Exception as e:
            self.say(f"could not put the map back: {e}")

    def run_bots(self, args):
        env = dict(os.environ, PYTHONUNBUFFERED="1", **getattr(self, "gg_env", {}))
        joined = False
        self.say("ending any game copies left over from before...")
        self.kill_all()                       # leftovers from an earlier game make bots fail to join
        self.ghosts_started = False
        time.sleep(2)
        self.clear_bot_logs()                 # this run's logs start empty, not mixed with older games
        if self.cancel:
            self.ready()
            return
        if getattr(self, "rotation_args", None):   # after kill_all, which would end it too
            threading.Thread(target=self.run_rotation, args=(self.rotation_args,), daemon=True).start()
        if env.get("BOT_GUNGAME") and getattr(self, "gg_map_key", None):      # no weapons or ammo on the map, bait in the middle
            try:
                if 1 in [int(x) for x in env.get("BOT_GG_ORDER", "").split(",") if x.strip()]:      # fists on the ladder: everyone punches like Mary
                    try:
                        _disk('fistdmg').on()
                        self.say("gun game: everyone's fists hit as hard as Mary's")
                    except Exception as e:
                        self.say(f"gun game: could not give everyone Mary's fists ({e})")
                darkitems = _disk('darkitems')
                n = darkitems.patch_gungame(self.gg_map_key, log=self.say)
                self.say(f"gun game: {n} weapons, ammo and TNT taken off {self.gg_map_key}" if n else "gun game: the map's items were left as they are")
                threading.Thread(target=self.gg_restore_when_over, daemon=True).start()
            except Exception as e:
                self.say(f"gun game: could not change the map's items: {e}")
        if env.get("BOT_ROLE") == "jug" and getattr(self, "jug_map_key", None):   # one silver Gatling on the map: its finder is the Juggernaut
            try:
                darkitems = _disk('darkitems')
                silvergat = _disk('silvergat')
                found = darkitems.patch_juggernaut(self.jug_map_key, log=self.say)
                if found >= 0 and silvergat.on(self.jug_map_key):
                    env["BOT_JUG_GATLING"] = self.gg_env["BOT_JUG_GATLING"] = "1"
                    silvergat._note(f"map {self.jug_map_key}: {found} Gatling guns found, gold one in place")
                    self.say(f"Juggernaut: {found} Gatling guns taken off the map, one gold one left: whoever picks it up is the Juggernaut"
                             if found else "Juggernaut: a gold Gatling gun is in the middle of the map")
                    threading.Thread(target=self.jug_restore_when_over, daemon=True).start()
                else:
                    darkitems.restore()
                    self.say("Juggernaut: could not set up the gold Gatling; the first kill makes the Juggernaut")
            except Exception as e:
                self.say(f"Juggernaut: no silver Gatling ({e}); the first kill makes the Juggernaut")
                try:
                    darkitems = _disk('darkitems')
                    silvergat = _disk('silvergat')
                    darkitems.restore(); silvergat.off()
                except Exception:
                    pass
        if env.get("BOT_GUNGAME") and (HERE / "ggplayer.py").exists():        # you play the ladder too: your weapon is chosen for you
            try:
                (HERE / "state" / "gg_over.json").unlink()
            except OSError:
                pass
            try:
                gp = HERE / ".venv" / "Scripts" / "python.exe"
                subprocess.Popen([venv_py(), str(HERE / "ggplayer.py"), str(os.getpid())],
                                 cwd=str(HERE), env=env, creationflags=NO_WINDOW)
            except OSError as e:
                self.say(f"could not start the player's ladder: {e}")
        if env.get("BOT_ROLE"):                                            # the referee's file, the banner and your own role
            for stale in ("roles.json", "gg_banner.json", "gg_freeze.json", "jug_claim.json"):
                try:
                    (HERE / "state" / stale).unlink()
                except OSError:
                    pass
            for old_f in list((HERE / "state").glob("name_*.txt")) + list((HERE / "state").glob("pos_*.txt")):
                try:                                               # names of bots from an earlier game would count as players
                    old_f.unlink()
                except OSError:
                    pass
            gp = HERE / ".venv" / "Scripts" / "python.exe"
            for script in ("rolesplayer.py", "ggbanner.py"):
                if (HERE / script).exists():
                    try:
                        subprocess.Popen([venv_py(), str(HERE / script), str(os.getpid())],
                                         cwd=str(HERE), env=env, creationflags=NO_WINDOW)
                    except OSError as e:
                        self.say(f"could not start {script}: {e}")
        if env.get("BOT_GUNGAME") and (HERE / "ggbanner.py").exists():     # ROUND n COMPLETE / winner / ROUND n+1 BEGIN on screen
            for stale in ("gg_banner.json", "gg_freeze.json", "gg_ladder.json"):
                try:
                    (HERE / "state" / stale).unlink()
                except OSError:
                    pass
            try:
                gp = HERE / ".venv" / "Scripts" / "python.exe"
                subprocess.Popen([venv_py(), str(HERE / "ggbanner.py"), str(os.getpid())],
                                 cwd=str(HERE), creationflags=NO_WINDOW)
            except OSError as e:
                self.say(f"could not start the round banner: {e}")
        try:
            venv_python = HERE / ".venv" / "Scripts" / "python.exe"
            if venv_python.exists():
                cmd = [str(venv_python), "-u", str(HERE / "botctl.py")] + args
            else:                                  # first run: bots.cmd makes the venv (quotes: cmd /c strips the outer pair)
                cmd = f'cmd /c ""{HERE / "bots.cmd"}" {subprocess.list2cmdline(args)}"'
            p = subprocess.Popen(cmd, cwd=str(HERE), env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                                 creationflags=NO_WINDOW)
            self.bots_proc = p
            for line in p.stdout:
                if self.cancel:
                    break
                self.say(line)
                # With a single bot this process is the host itself; its "waiting for N player(s)" line says
                # the lobby is up. With several, this process only starts them and exits (below).
                if not joined and not self.cancel and "waiting for" in line and "player" in line and "waiting for the host" not in line:
                    joined = True
                    self.start_ghosts_when_in()
                    if self.join_me:
                        self.say("the game is hosted; joining it...")
                        threading.Thread(target=self.join_and_watch, daemon=True).start()
                    else:
                        self.hosted_no_join()
            if self.cancel:
                try:
                    p.kill()
                except OSError:
                    pass
                self.ready()
                return
            code = p.wait()
        except OSError as e:
            self.say(f"could not start the bots: {e}")
            self.ready()
            return
        if self.cancel:
            self.ready()
            return
        if joined:
            return
        if code != 0:
            self.say(f"the bots did not start (code {code}); not joining")
            self.ready()
            return
        self.start_ghosts_when_in()
        if not getattr(self, "join_me", True):
            self.hosted_no_join()
            return
        self.say("the bots are in and the game is hosted; joining it...")
        self.join_and_watch()

    def start_ghosts(self, delay=4, manual=False):
        # (12 Nov, a hosted game: 22 ghosts in a 7-ghost game - a new map's ghosts were started while the last map's start was still
        #  going, 2 s a ghost, and stopping them didn't stop that loop. Each start has a number; a start that isn't the latest,
        #  or that a stop came after, starts no more ghosts.)
        self.ghost_gen = getattr(self, "ghost_gen", 0) + 1; gen = self.ghost_gen
        """'Join ghost player(s)': light-weight clients (ghost.py) join the bots' LAN game on this PC and fight at the bots' difficulty."""
        cfg = getattr(self, "ghost_cfg", (False, 1, "Normal", [], True, True))
        if not cfg[0] or getattr(self, "ghosts_started", False) or self.cancel:
            return
        self.ghosts_started = True
        script = ghost_script(HERE)
        if script is None:
            self.say("ghost players: ghost.py was not found")
            return
        venv_python = HERE / ".venv" / "Scripts" / "python.exe"
        py = venv_py()
        self.ghost_procs = getattr(self, "ghost_procs", [])
        wmode = getattr(self, "ghost_force", None) or {"Melee only": "melee", "TNT only": "tnt"}.get(self.ghost_weapons.get(), "normal")
        if not getattr(self, "ghost_force", None):
            wmode = {"Melee only": "melee", "TNT only": "tnt"}.get(self.weapons_rule.get(), "normal")   # (Game rules: Weapons)
        if getattr(self, "rot_ghosts", False):
            wmode = "normal"                                       # Map Rotation's ghosts: plain combat, whatever the Ghosts tab says
        # Map Rotation's ghosts are named after their difficulty: Nightmare 1, Hard 1, Hard 2, Normal 1 ...
        names = {}
        if getattr(self, "rot_ghosts", False):
            seen = {}
            for i in range(cfg[1]):
                d = cfg[3][i][1] if i < len(cfg[3]) else cfg[2]
                seen[d] = seen.get(d, 0) + 1
                names[i + 1] = f"{d} {seen[d]}"
        # (11 Nov, the user's names) every ghost gets a random name from its character's list, never one another ghost in the match
        # already has (a name on two lists goes to one of them only); its own list used up: another character's unused name; all used: GHOST 1, 2, ...
        if GHOST_NAME_POOL:
            used = set()
            for i in random.sample(range(cfg[1]), cfg[1]):
                ch = cfg[3][i][0] if i < len(cfg[3]) else "mary"
                free = [nm for nm in GHOST_NAME_POOL.get(ch, []) if nm not in used]
                if not free:                                       # its own list is used up: any other character's unused name
                    free = sorted({nm for lst in GHOST_NAME_POOL.values() for nm in lst} - used)
                if free:
                    nm = random.choice(free)
                else:                                              # every list used up: GHOST 1, GHOST 2, ...
                    k = 1
                    while f"GHOST {k}" in used: k += 1
                    nm = f"GHOST {k}"
                used.add(nm); names[i + 1] = nm
        self.tool("match_begin", [names.get(n, f"GhostBot_{n:02d}") for n in range(1, cfg[1] + 1)])
        def go():
            time.sleep(delay)                                      # the lobby is up; let it settle
            for n in range(1, cfg[1] + 1):
                if self.cancel:
                    return
                char, diff = cfg[3][n - 1] if n - 1 < len(cfg[3]) else ("mary", cfg[2])
                env = dict(os.environ, PYTHONUNBUFFERED="1", GHOST_WAIT_HOST="1" if getattr(self, "ghost_hosted", False) else "0", GHOST_STAGGER=str((n - 1) * 4), GHOST_LEVEL=(getattr(self, "ghost_game", None) or {}).get("map", "") if getattr(self, "ghost_hosted", False) else (getattr(self, "ghost_rot_level", "") if getattr(self, "rot_ghosts", False) else ""), GHOST_CHAR=char, GHOST_STARTWEAPON="1" if cfg[4] else "0",
                           GHOST_CHARSTATS="1" if cfg[4] else "0", GHOST_SWIMSND="1" if cfg[5] else "0", GHOST_NAME=names.get(n, f"GhostBot_{n:02d}"), GHOST_CONN=f"GhostLan{n}",
                           GHOST_SLOT=format(0x1802 + (n - 1) * 0x100, "x"), GHOST_ID=format(0x4e + n - 1, "x"), GHOST_LOG=f"ghost_lan{n}",
                           GHOST_DIFFICULTY=diff.lower(), GHOST_WEAPONS=wmode, GHOST_INDEX=str(n), GHOST_STAY="43200", GHOST_STATE_GAP="0.08", GHOST_BIND=f"127.0.0.{1 + n}" if n > 1 else "0.0.0.0")
                # (14 Nov: GHOST_STATE_GAP 0.08 - real players' moves reach the host about 10 times a second, the ghosts' only 5.6, so
                #  next to a real player the ghosts looked choppy, "laggy". About 10 a second now, like a player.)
                # the other ghosts' names (any game: their "players in the game" log leaves them out; Map Rotation's wait for a player too)
                env["GHOST_PEERS"] = "|".join(re.escape(v) for v in names.values()) + r"|GhostBot_\d+|GhostLan\d+|GHOST[_ ].*"
                env["GHOST_TAUNTS"] = "1" if self.taunts.get() else "0"   # (Game rules: Taunts after kills)
                try:                                          # (prestige looks test: prestige_test_on.cmd; ghost 1 gold, 2 silver, 3 bronze ...)
                    tiers = json.loads((HERE / "state" / "prestige_test.json").read_text(encoding="utf-8")).get("tiers") or []
                    if tiers: env["GHOST_TIER"] = str(tiers[(n - 1) % len(tiers)])
                except (OSError, ValueError, AttributeError):
                    pass
                if getattr(self, "rot_ghosts", False):        # Map Rotation: stand still until a real player (not the host, not a ghost) is in
                    env["GHOST_WAIT_PLAYER"] = "1"
                red_n = getattr(self, "ghost_red", None) if getattr(self, "ghost_hosted", False) else getattr(self, "both_red_ghosts", None)
                if red_n is not None:
                    env["GHOST_TEAM"] = "red" if n <= red_n else "blue"
                if gen != getattr(self, "ghost_gen", gen) or self.cancel:
                    return                                     # stopped, or a newer start took over
                if n == 1:
                    self.kill_stray_ghosts()
                try:
                    (HERE / "GhostLogs").mkdir(exist_ok=True)
                    out = open(HERE / "GhostLogs" / f"ghost_{n}.txt", "w", encoding="utf-8")
                    self.ghost_procs.append(subprocess.Popen([py, "-u", str(script), "127.0.0.1", "5069"], cwd=str(script.parent), env=env,
                                                             stdout=out, stderr=subprocess.STDOUT, creationflags=NO_WINDOW))
                    self.say(f"ghost player {n} ({char}, {diff}" + ("" if wmode == "normal" else f", {wmode} only") + f") is joining the game; its log: GhostLogs\\ghost_{n}.txt")
                except OSError as e:
                    self.say(f"could not start ghost player {n}: {e}")
                time.sleep(2)
        threading.Thread(target=go, daemon=True).start()

    def capture_ghost_cfg(self, botdiff):
        chars = dict(self.GHOST_CHARS)
        rows = []
        for c, d in zip(self.ghost_char_w, self.ghost_diff_w):
            key = chars.get(c.get(), "mary")
            if key == "random":
                key = random.choice([k for _, k in self.GHOST_CHARS if k != "random"])
            rows.append((key, d.get() if d.get() in DIFFICULTIES else botdiff))
        self.ghost_force = None                                     # (your own hosted game: the Weapons choice on the Ghosts tab)
        return (bool(self.ghost_on.get()), max(1, min(MAX_GHOSTS, int(self.ghost_count.get()))), botdiff, rows, bool(self.ghost_wpn.get()), bool(self.ghost_swim_snd.get()))

    def zt_then(self, fn):
        """Every launch first makes sure ZeroTier is installed, on and joined (zt_ensure, like LAUNCH GAME on Bots Setup), then runs fn."""
        for b, _ in self.launch_btns:
            b.configure(state="disabled")
        def prep():
            try:
                self.zt_ensure()
            finally:
                def go():
                    for b, t in self.launch_btns:
                        b.configure(text=t, state="normal")
                    fn()
                self.after(0, go)
        threading.Thread(target=prep, daemon=True).start()

    def launch_ghost_game(self, zt_ok=False):
        """Ghosts tab: your own game hosts the map and mode chosen there; the ghosts join it. No bots."""
        if not zt_ok and not self.gh_private.get():                 # (a private game is only you and the ghosts: no ZeroTier needed)
            self.zt_then(lambda: self.launch_ghost_game(zt_ok=True)); return
        if not GAME_EXE.exists():
            messagebox.showerror("Ghost game", f"OutlawsAHOM.exe not found: {GAME_EXE}")
            return
        if own_game_pids():
            messagebox.showinfo("Ghost game", "A game of yours is already running. Close it first (Close all game windows).")
            return
        try:
            frag = int(self.gh_frag.get()) if str(self.gh_frag.get()).strip() else None
            tlim = int(self.gh_time.get()) if str(self.gh_time.get()).strip() else None
            join_after = max(5, int(self.gh_delay.get())) if str(self.gh_delay.get()).strip() else 14
        except ValueError:
            messagebox.showerror("Ghost game", "Score limit, time limit and the join delay must be whole numbers (the first two may be empty).")
            return
        key = dict(MAPS).get(self.gh_map.get(), "hargrove")
        private = bool(self.gh_private.get())
        host = {"map": key, "mode": dict(MODES).get(self.gh_mode.get(), "dm"), "maxplayers": 8, "timelimit": tlim, "fraglimit": frag, "private": private,
                "ff": bool(self.ff.get())}
        # (the game's -maxplayers start-up option stops at 8: lobbysize.py raises your lobby to GHOST_HOST_PLAYERS once it exists,
        #  so a player alone can have up to 15 ghosts)
        self.ghost_cfg = self.capture_ghost_cfg(self.base_diff())
        self.ghost_red = self.gh_split_red()        # team modes: the first this many ghosts pick Red, the rest Blue (None: the game evens out)
        # a private game: the game's own -private lobby (not listed), sized to you plus your ghosts so nobody else fits in
        lobby = max(2, 1 + (self.ghost_cfg[1] if self.ghost_cfg[0] else 0)) if private else GHOST_HOST_PLAYERS
        if private: host["maxplayers"] = min(8, lobby)
        self.ghost_hosted, self.ghosts_started, self.cancel = True, False, False
        self.ghost_game = host
        rcm = add_team_rcm(key) if host["mode"] == "team" else None
        if rcm: self.say(f"added Team Deathmatch to {key}.rcm for this game (removed again when it ends)")
        try:
            cmd = join_command(self.my_char.get(), self.screen_value, False, host=host)
            game = subprocess.Popen(cmd, cwd=str(GAME_DIR))
        except OSError as e:
            self.say(f"could not start your game: {e}")
            if rcm:
                try: rcm.unlink()
                except OSError: pass
            return
        self.say(f"hosting {self.gh_map.get()} ({self.gh_mode.get()})" + (f", private: {lobby} players (you and the ghosts), nobody else can see or join it" if private else "") + "; " + (f"the ghosts join {join_after} s after the game starts (the box next to Time limit). Start the match yourself."
                 if self.ghost_on.get() else "no ghosts will join ('Join ghosts now' adds them by hand)."))
        try:
            venv_python = HERE / ".venv" / "Scripts" / "python.exe"
            py = venv_py()
            subprocess.Popen([py, str(HERE / "lobbysize.py"), str(lobby), "--pid", str(game.pid)], cwd=str(HERE), creationflags=NO_WINDOW)
        except OSError as e:
            self.say(f"could not raise the lobby size (it stays at 8 players): {e}")
        t_game = time.time(); g_map = (getattr(self, "ghost_game", None) or {}).get("map", ""); g_char = self.my_char.get()
        def watch():
            time.sleep(join_after)                          # the game loads and creates the lobby (ghosts that connect while it is still loading drop you to the main menu: raise the number if that happens)
            if not self.cancel:
                self.start_ghosts(delay=0)
            while game.poll() is None and not self.cancel:
                time.sleep(2)
            self.stop_ghosts()
            threading.Thread(target=self.after_ghost_game, args=(t_game, g_map, g_char), daemon=True).start()
            self.ghost_hosted = False
            if rcm:
                try: rcm.unlink()
                except OSError: pass
            self.say("your ghost game ended")
        threading.Thread(target=watch, daemon=True).start()

    def start_ghosts_when_in(self):
        """The ghosts join after YOUR game has joined: ghosts that connect while your game is still loading make the host
        drop you back to the main menu. 'Join ghosts now' on the Play tab starts them by hand instead."""
        cfg = getattr(self, "ghost_cfg", None)
        if not cfg or not cfg[0] or getattr(self, "ghosts_started", False):
            return
        if getattr(self, "rot_ghosts", False):        # Map Rotation: rotation.py says when each map's lobby is up (rot_marker)
            return
        if not getattr(self, "join_me", True):
            self.start_ghosts()
            return
        try:                                          # (Join after, under Ghost options; at least 30 s: your game loads and joins first)
            wait_s = max(30, int(str(self.gh_delay.get()).strip()))
        except (ValueError, tk.TclError, AttributeError):
            wait_s = 60
        self.say(f"ghost players will join about {wait_s} seconds after your game starts (or press 'Join ghosts now' once you are in)")
        def wait():
            up = None
            for _ in range(180):
                time.sleep(2)
                if self.cancel or getattr(self, "ghosts_started", False):
                    return
                if own_game_pids():
                    up = up or time.time()
                    if time.time() - up >= wait_s:
                        break
            if not self.cancel and not getattr(self, "ghosts_started", False):
                self.start_ghosts(delay=0)
        threading.Thread(target=wait, daemon=True).start()

    def ghosts_now(self):
        """The 'Join ghosts now' button: start the ghosts right away in the game that is running."""
        if getattr(self, "ghosts_started", False):
            self.say("the ghosts are already joining")
            return
        cfg = getattr(self, "ghost_cfg", None)
        if not cfg or not (getattr(self, "launching", False) or getattr(self, "bots_proc", None) or getattr(self, "ghost_hosted", False)):
            messagebox.showinfo("Ghosts", "Launch a game first (LAUNCH GAME), get into it, then press this.")
            return
        try:                                          # the Ghosts tab as it is NOW (how many, characters, difficulty)
            force = getattr(self, "ghost_force", None)
            cfg = self.capture_ghost_cfg(self.base_diff()); self.ghost_force = force      # (a Bots Setup game's TNT / melee rule stays)
        except Exception:
            pass
        cfg = (True,) + tuple(cfg[1:])
        self.ghost_cfg = cfg
        self.start_ghosts(delay=0, manual=True)

    def stop_ghosts(self):
        self.ghost_gen = getattr(self, "ghost_gen", 0) + 1          # (a start still going stops starting more)
        for p in getattr(self, "ghost_procs", []):
            try:
                p.kill()
            except OSError:
                pass
        if getattr(self, "ghost_procs", None):
            self.tool("match_end")
        self.ghost_procs = []
        self.ghosts_started = False

    def hosted_no_join(self):
        """'I play too' is unticked: the bots play on their own; nothing ends the game when you leave."""
        self.say("the game is hosted; your own game isn't started ('I play too' is off). Join it yourself from the "
                 "game's LAN menu if you like; 'Close all game windows' ends it.")
        self.ready()

    def join_and_watch(self):
        try:
            self._join_and_watch()
        finally:
            self.ready()

    def _join_and_watch(self):
        if self.cancel:
            return
        """Start your own game (your normal profile), which joins the LAN game on this PC. When you leave, i.e.
        when no copy of the game without -userpath (the bots' mark) is left, end every OutlawsAHOM.exe."""
        if not GAME_EXE.exists():
            self.say(f"OutlawsAHOM.exe not found: {GAME_EXE}")
            return
        char = getattr(self, "play_char", None)
        try:
            game = subprocess.Popen(join_command(char, getattr(self, "screen_value", None), getattr(self, "play_horde", False)),
                                    cwd=str(GAME_DIR))
        except OSError as e:
            self.say(f"could not start your game: {e}")
            return
        t_start = time.time()
        if char and getattr(self, "play_autojoin", False) and (HERE / "autojoin.py").exists():   # Survival: the Survivors team is picked for you
            venv_python = HERE / ".venv" / "Scripts" / "python.exe"
            py = venv_py()
            try:
                subprocess.Popen([py, "-u", str(HERE / "autojoin.py"), str(game.pid), "red", str(HERE / "horde_logs" / "autojoin_log.txt")],
                                 cwd=str(HERE), creationflags=NO_WINDOW)
                if (HERE / "follow.py").exists():              # (the next Co-op Mission: your game goes there by itself)
                    subprocess.Popen([py, "-u", str(HERE / "follow.py"), str(game.pid), "red", str(HERE / "horde_logs" / "follow_log.txt")],
                                     cwd=str(HERE), creationflags=NO_WINDOW)
                self.say(f"your game is starting as {char} and joins the Survivors team by itself")
            except OSError:
                self.say("your game is starting and joins the LAN game (pick your team in the lobby)")
        else:
            self.say("your game is starting and joins the LAN game (pick your team in the lobby)")
        seen, gone = False, 0
        for _ in range(60 * 60 * 12):                          # up to 12 hours, a look every 2 seconds
            time.sleep(2)
            if self.cancel:
                return
            mine = own_game_pids()
            if mine is None:
                continue                                        # couldn't ask Windows; look again
            if mine:
                seen, gone = True, 0
                self.launching = False             # your game is up: loading is over
            else:
                gone += 1
            if seen and gone >= 2:                              # gone for 4 seconds
                if char and not getattr(self, "play_autojoin", False):
                    threading.Thread(target=self.after_my_game, args=(t_start, char), daemon=True).start()
                if getattr(self, "zerotier", False):
                    self.say("you left the game; it stays up for your friends (Close all game windows ends it)")
                    return
                self.say("you left the game: closing everything")
                self.kill_all()
                return
            if not seen and gone >= 60:                         # never showed up in two minutes
                self.say("your game never showed up; leaving the bots running")
                return

    def give_old_xp(self):
        """Once (state\\give_old_xp.json, {"player": ..., "character": ...}): the kills and games saved before the launcher knew
        your character go to the character you chose, and are sent to the online rankings (relay_backfill.py). Then the
        file is removed."""
        f = HERE / "state" / "give_old_xp.json"
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        def work():
            try:
                if str(HERE) not in sys.path:
                    sys.path.insert(0, str(HERE))
                import xp
                k, g = xp.assign_unclaimed(job["player"], job["character"])
                self.say(f"earlier games: {k} kills in {g} games now count for {job['character']} ({job['player']})")
                try:
                    import io, contextlib, relay_backfill
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        relay_backfill.main(job["player"])
                    self.say("online rankings: " + " ".join(out.getvalue().split())[:200])
                except Exception as e:
                    self.say(f"online rankings: not sent yet ({e})")
                f.unlink()
                self.after(0, self.stats_refresh)
            except Exception as e:
                self.say(f"could not give the earlier XP: {e}")
        threading.Thread(target=work, daemon=True).start()

    def played_char(self, since):
        """The character your game was last set to, from its own settings file (written when the game closes): the one you
        really played, even if you switched in the lobby. None if the file wasn't written after `since`."""
        cfg = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Saved Games" / "Nightdive Studios" / "Outlaws EX" / "kexengine.cfg"
        try:
            if cfg.stat().st_mtime < since:
                return None
            m = re.search(r'seta\s+mp_defaultChar\s+"(\d+)"', cfg.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None
        n = int(m.group(1)) if m else None
        return next((l for l, v in CHAR_NUMBER.items() if v == n), None)

    def after_ghost_game(self, t0, map_key, chosen):
        """A ghost game ended (13 Nov: they gave no XP online - no bot was there to keep the game's record). The ghosts recorded your
        kills of them (xp kill_events) and the first ghost your deaths (state\\ghost_deaths.jsonl): this makes the game's record from
        them, then after_my_game gives it to the character you really played and sends it to the online rankings."""
        player = self.stats_name.get().strip()
        if not player:
            return
        try:
            time.sleep(3)
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import ranked, xp
            t1 = time.time()
            with ranked.connect() as c:
                k = c.execute("SELECT COUNT(*) FROM kill_events WHERE player=? AND t>=? AND t<=?", (player, t0, t1)).fetchone()[0]
            d = su = 0; causes = {}
            try:
                for line in (HERE / "state" / "ghost_deaths.jsonl").read_text(encoding="utf-8").splitlines():
                    try: e = json.loads(line)
                    except ValueError: continue
                    if t0 <= float(e.get("t", 0)) <= t1 and str(e.get("player", "")).lower() == player.lower():
                        d += 1; su += bool(e.get("suicide")); causes[e.get("cause", "killed by a player")] = causes.get(e.get("cause", "killed by a player"), 0) + 1
            except OSError:
                pass
            if not k and not d:
                return
            with ranked.connect() as c:
                c.execute("INSERT OR IGNORE INTO sessions (id, player, character, map, kills, deaths, started, ended, mode, suicides, ranked, causes) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (f"ghosts-{int(t0)}", player, chosen, map_key or "?", k, d, t0, t1, "ghosts", su, 0, json.dumps(causes)))
            self.say(f"ghost game: {k} kills, {d} deaths saved")
            self.after_my_game(t0, chosen)
        except Exception as e:
            self.say(f"XP: could not save the ghost game: {e}")

    def after_my_game(self, t0, chosen):
        """You left a launcher game: give its XP to the character you really played, and send it to the online rankings
        (Stats tab) like a Survival game. A switch in the lobby is caught here: the game saves the character you used."""
        time.sleep(6)                                          # (the game writes its settings file as it closes)
        player = self.stats_name.get().strip()
        if not player:
            return
        try:
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import ranked, xp
            t1 = time.time()
            real = self.played_char(t0)
            if real and real != chosen:
                n = xp.reassign(player, t0 - 5, t1, real)
                self.say(f"you played {real}, not {chosen}: this game's XP ({n} kills) went to {real}")
                chosen = real
            ck = xp.char_key(chosen)
            with ranked.connect() as c:
                rows = c.execute("SELECT id, map, kills, deaths, started, ended, suicides, ranked FROM sessions "
                                 "WHERE player=? COLLATE NOCASE AND started>=?", (player, t0 - 5)).fetchall()
            import relay
            for sid, mp, k, d, st_, en, su, rk in rows:
                if relay.configured() and ck:
                    kl = xp.kills_between(player, ck, st_, (en or t1) + 2)[:k]
                    relay.submit_game(str(sid), player, ck, mp, st_, en or t1, k, d, su or 0, bool(rk), kl, max(0, k - len(kl)), mode="bots")
            if rows:
                self.say(f"this game's XP went to {chosen} ({sum(r[2] for r in rows)} kills)")
            self.after(0, self.stats_refresh)
        except Exception as e:
            self.say(f"XP: could not finish this game's XP: {e}")

    def kill_stray_ghosts(self):
        """End ghost players this launcher doesn't know about (an earlier launcher's, or a start that overlapped): they stay 12 hours
        and join every game the host starts - 22 ghosts in a 7-ghost game on 12 Nov."""
        keep = {p.pid for p in getattr(self, "ghost_procs", []) if p.poll() is None}
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'ghost\\.py' } | "
              "ForEach-Object { $_.ProcessId }")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60,
                               creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return 0
        n = 0
        for l in r.stdout.split():
            if l.isdigit() and int(l) not in keep and int(l) != os.getpid():
                subprocess.run(["taskkill", "/PID", l, "/T", "/F"], capture_output=True, creationflags=NO_WINDOW); n += 1
        if n:
            self.say(f"closed {n} leftover ghost player(s)")
        return n

    def kill_bot_copies(self):
        """End the botctl programs and the bots' own game copies (-userpath), not your game."""
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'botctl\\.py' } | "
              "ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }")
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60,
                           creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
        for pid in bot_game_pids() or []:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)

    def kill_all(self):
        """Close the bots' consoles (and the botctl programs in them), then end every OutlawsAHOM.exe. Two passes, a
        second apart, so a console or game copy that was just being started doesn't escape."""
        self.archive_bot_logs()               # keep this game's bot logs (botreports\\) before the next game overwrites them
        self.stop_ghosts()
        self.kill_stray_ghosts()
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'botctl\\.py|horde\\.py|lms\\.py|rotation\\.py|ggbanner\\.py|ggplayer\\.py|rolesplayer\\.py' } | "
              "ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }")
        out = ""
        for k in range(2):
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                               timeout=60, creationflags=NO_WINDOW)
            except (OSError, subprocess.SubprocessError):
                pass
            r = subprocess.run(["taskkill", "/IM", "OutlawsAHOM.exe", "/F"], capture_output=True, text=True,
                               creationflags=NO_WINDOW)
            out = (r.stdout or r.stderr).strip() or "done"
            if k == 0:
                time.sleep(1)
        self.say("bot consoles closed; " + out)
        self.restore_team_colors()

    def restore_team_colors(self):
        """Take out every mod an earlier game left (Survival colours, zombie skins or elves, map files): no-op when none."""
        if not (HERE / "teamcolors.py").exists():
            return
        venv_python = HERE / ".venv" / "Scripts" / "python.exe"
        py = venv_py()
        if (HERE / "companionhost.py").exists():
            # Everything an earlier Horde / Christmas game put in (colours and the winter look, zombie skins or elves,
            # its map files), finished before the next game starts (it used to run in the background, and a game
            # started a moment later kept them: elves in a normal game).
            try:
                time.sleep(2)
                r = subprocess.run([py, str(HERE / "companionhost.py"), "--cleanup"], cwd=str(HERE), capture_output=True,
                                   text=True, timeout=180, creationflags=NO_WINDOW)
                if "game folder restored" not in (r.stdout or ""):
                    self.say("some mod files from an earlier game could not be removed yet")
                return
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            time.sleep(2)
            subprocess.run([py, str(HERE / "teamcolors.py"), "off", "--wait"], cwd=str(HERE), capture_output=True, timeout=120,
                           creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            darkitems = _disk('darkitems')
            darkitems.restore()                          # the Juggernaut / gun game map files
            silvergat = _disk('silvergat')
            silvergat.off()                              # and the gold Gatling's archive
            _disk('fistdmg').off()                       # and the gun game's fists
        except Exception:
            pass
        if (HERE / "zombieskins.py").exists():           # the zombie skins go too (waits for the game to close if needed)
            try:
                subprocess.Popen([py, str(HERE / "zombieskins.py"), "off", "--wait"], cwd=str(HERE), creationflags=NO_WINDOW)
            except OSError:
                pass

    def clear_bot_logs(self):
        """Start of a run: delete every folder in botreports\\ and every log in botlogs\\, so reports and bug
        reports only hold this run's bots."""
        try:
            import shutil
            for d in (HERE / "botreports").glob("*"):
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
            for f in (HERE / "botlogs").glob("*.log"):
                try:
                    f.unlink()
                except OSError:
                    pass
        except Exception as e:
            self.say(f"could not clear the old bot logs: {e}")

    def archive_bot_logs(self):
        """Copy every bot's log from botlogs\\ into botreports\\<date>_<map>\\ (testing aid; see botreport.py)."""
        try:
            import botreport
            out, self.report_sig = botreport.archive(HERE / "botlogs", HERE / "botreports",
                                                    list(getattr(self, "recent", []))[-200:],
                                                    getattr(self, "report_sig", None))
            if out is not None:
                self.say(f"bot logs saved: botreports\\{out.name}")
        except Exception as e:
            self.say(f"could not save the bot logs: {e}")

    def kill(self):
        """Close all game windows. While a game is loading this is immediate (no question): it stops the bots from
        joining, closes the host and runs taskkill /IM OutlawsAHOM.exe /F."""
        if getattr(self, "launching", False):
            self.cancel = True
            self.say("stopping: no more bots will join; closing the host and every game copy...")
            def work():
                self.kill_all()
                self.say("stopped")
                self.ready()
            threading.Thread(target=work, daemon=True).start()
            return
        if messagebox.askyesno("Close games", "End every OutlawsAHOM.exe, the bots' and yours?"):
            threading.Thread(target=self.kill_all, daemon=True).start()


AUTO_ONLINE_ARG = "--online-host"      # started by Windows at sign-in: host the Map Rotation online, and keep it up
AUTO_ONLINE_DELAY = 90                 # seconds after start-up: Steam signs in first
STARTUP_CMD = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "OutlawsOnlineHost.cmd"


def startup_set(on):
    """Windows' Startup folder: a small .cmd that starts this launcher with --online-host at sign-in (on), or none (off)."""
    if not on:
        try: STARTUP_CMD.unlink()
        except FileNotFoundError: pass
        return
    if getattr(sys, "frozen", False):
        run = subprocess.list2cmdline([sys.executable, AUTO_ONLINE_ARG])
    else:
        venv = HERE / ".venv" / "Scripts" / "pythonw.exe"
        run = subprocess.list2cmdline([str(venv if venv.exists() else Path(sys.executable).with_name("pythonw.exe")),
                                       str(Path(__file__).resolve()), AUTO_ONLINE_ARG])
    STARTUP_CMD.parent.mkdir(parents=True, exist_ok=True)
    STARTUP_CMD.write_text("@echo off\r\nrem Outlaws Bot Launcher: host the Map Rotation online at sign-in (the Map Rotation tab's box turns this off)\r\n"
                           f'cd /d "{HERE}"\r\nstart "" {run}\r\n')


def main():
    try:
        app = App()
    except Exception:                                    # (a built launcher has no console: say why, and keep it in a file)
        import traceback
        _err = traceback.format_exc()
        try:
            (HERE / "launcher_start_error.txt").write_text(_err, encoding="utf-8")
        except OSError:
            pass
        try:
            messagebox.showerror("Bot Launcher", "The launcher could not start. The reason is saved in "
                                 "launcher_start_error.txt in the botctl folder.\n\n" + _err[-700:])
        except Exception:
            pass
        raise SystemExit(1)
    if AUTO_ONLINE_ARG in sys.argv:
        def _cb_error(exc, val, tb):                     # (no console on an unattended PC: Tk errors go to the log file)
            import traceback
            try:
                with (HERE / "online_host_log.txt").open("a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "error: " + "".join(traceback.format_exception(exc, val, tb)))
            except OSError:
                pass
        app.report_callback_exception = _cb_error
        app.after(1000, app.auto_online)
    if COMPANION:
        app.after(400, app.companion_tabs)
    app.mainloop()


if __name__ == "__main__":
    main()

# build marker 1793630243 (forces a rebuild)
