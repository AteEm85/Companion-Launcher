"""Mod server: lets the Outlaws Companion app (friends' PCs) fetch the files a hosted game needs.
The launcher (and companionhost.py) call start(mode, map_key) when a game is hosted; the companion asks
  GET /manifest.json   which mods this game needs (name, size, sha256, url of each file)
  GET /f/<path>        one of those files
  GET /toolkit.zip     the bot toolkit (scripts only), for friends who want to host games themselves
Only files named in the manifest (and the toolkit zip) are ever served; nothing else on this PC is reachable.
Usage: python modserver.py [mode] [map]   (runs until closed; for testing)"""
import os, sys, json, hashlib, threading, zipfile, io, socket, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
GAME = os.path.dirname(HERE)
PORT = 8765
DISCOVER_PORT = 8766
BUILTIN_MAPS = {"civpow2", "drygulch", "fort", "granary", "hargrove", "lilbatl", "mltsimms", "orleans",
                "thudtowr", "town_2p", "wilderns"}
MAP_EXT = (".lab", ".rcm")      # (Dark Mode adds .obt / .obb above)
TOOLKIT_EXT = (".py", ".js", ".cmd", ".txt")
TOOLKIT_JSON = ("maps_data.json", "maps_extra.json", "mapbatch_assets.json", "teamcolors_game.zip")   # + the game's own sprite shaders (teamcolors.py)
TOOLKIT_SKIP = ("online_settings", "launcher_settings", "bot_presets")
TOOLKIT_DIRS = {"zombie": (".spx", ".wav", ".nwx", ".itm", ".json"), "sounds": (".wav",),     # the Horde's zombie skins and its music / sound effects,
                "christmas": (".spx", ".wav", ".itm", ".nwx", ".pcx", ".hdx", ".atx", ".json"), "christmas/music": (".wav",)}   # the Christmas Horde's
MUSIC = ("sounds", "horde_ambient.wav")                         # joiners play this themselves while in a Horde game
XMAS_MUSIC = ("christmas", "music", "christmas_ambient.wav")    # ... and this in a Christmas Horde game
SANTA_STEP = ("christmas", "music", "santa_step.wav")           # Santa's footstep (joiners play it from where he is)
HOHOHO = ("christmas", "music", "hohoho.wav")                   # Santa's laugh with the "Elves Grow Restless" banner
XMAS_EXT = (".SPX", ".WAV", ".ITM", ".NWX", ".PCX", ".HDX", ".ATX", ".3DO")     # the Christmas files that go into the joiner's archive
INFO = {"mode": "", "map": "", "melee": False, "humans_team": None, "christmas": False}
STARTED = [0.0]                                                 # when this game was hosted (older state files are a past game's)
_srv = None
_cache = {}



def _xmas_skip(n):
    """Files in christmas\\ that are no longer used: the lit copies for outside walls only, the lights overlay, the bulb objects."""
    import re as _re
    u = n.upper()
    if _re.match(r"XL[HV]\d\d", u):                            # log-wall light copies: only the ones the maps use
        return u[:5] not in _xmas_used_lights()
    if u.endswith(".HDX") and u[:-4] + ".PCX" in _xmas_plain():  # old lit copies under the walls' own names: the
        return True                                              # walls flash (XFnn) now, so the plain name stays plain
    if u.startswith("XTT") or _re.match(r"XGR0[12][A-D]?\.", u):  # retired light copies (Thud Tower is XTU now, Granary XGS)
        return True
    return bool(u.startswith(("XLT", "XLITE", "XMASLITE")) or _re.match(r"XMASL\d", u)
                or u in ("SANCHEZ.NWX", "SANCHEZ.SPX"))      # (Santa is SANTA.NWX/.SPX now: Sanchez stays Sanchez)


def _xmas_plain():
    try:
        import xmaslights as _xl
        return set(_xl.FLASH)
    except Exception:
        return set()


def _xmas_used_lights():
    """XLH01, XLV02, ...: the light copies christmas\\walls_extra.json points walls at."""
    import json as _json
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "christmas", "walls_extra.json")) as f:
            data = _json.load(f)
    except (OSError, ValueError):
        return set()
    return {str(e[2]).upper()[:5] for items in data.values() for e in items if e[2]}

def _sha(path):
    st = os.stat(path); key = (path, st.st_size, st.st_mtime)
    if key not in _cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for blk in iter(lambda: f.read(1 << 20), b""): h.update(blk)
        _cache[key] = h.hexdigest()
    return _cache[key]


def _file(path, dest, url):
    return {"dest": dest, "size": os.path.getsize(path), "sha256": _sha(path), "url": url}


def mods():
    """[{id, title, files:[{dest,size,sha256,url}], paths:{url: local path}}] for the game being hosted."""
    mode, mp = INFO["mode"], INFO["map"].lower()
    out = []
    xmas = mode == "horde" and INFO.get("christmas")
    if mode in ("horde", "dark"):
        zdir = os.path.join(HERE, "christmas" if xmas else "zombie")
        fl = sorted(n for n in (os.listdir(zdir) if os.path.isdir(zdir) else [])
                    if n.upper().endswith(XMAS_EXT if xmas else (".SPX", ".WAV", ".NWX", ".ITM")) and os.path.isfile(os.path.join(zdir, n))
                    and not (xmas and _xmas_skip(n)))
        if fl:                                       # (id kept: a joiner packs this set into one archive either way)
            out.append({"id": "zombie_skins", "title": "Christmas elves, Santa, their sounds, trees and lights" if xmas else "Zombie skins and death sounds",
                        "items": [(os.path.join(zdir, n), n, ("/f/christmas/" if xmas else "/f/zombie/") + n) for n in fl]})
    hallow = mode == "horde" and not xmas and not INFO.get("melee")      # the Horde's Halloween props (darkitems mode="halloween")
    maps_ = {mp}
    if xmas and mission_now():                       # (25 Nov: a mission's joiner needs every later mission's map too - the game only
        try:                                         #  reads loose map files that were there when it started; horde.py wrote them all)
            sys.path.insert(0, HERE); import missions as _ms
            maps_ |= {str(m_["map"]).lower() for m_ in _ms.MISSIONS}
        except Exception:
            pass
    if (mode == "dark" or INFO.get("melee") or xmas or hallow) and mp:   # Dark Mode / Melee Zombies: the map's swapped item lists (darkitems.py wrote them)
        items = [(os.path.join(GAME, n), n, "/f/map/" + n) for n in sorted(os.listdir(GAME))
                 if os.path.splitext(n)[0].lower() in maps_ and os.path.splitext(n)[1].lower() in ((".obt", ".obb", ".lvt", ".lvb") if xmas else (".obt", ".obb"))]
        if items:
            out.append({"id": "items_" + mp, "title": ("Christmas trees and lights (%s)" if xmas else "Halloween props (%s)" if hallow else "Melee Zombies map items (%s)" if INFO.get("melee") else "Dark Mode map items (%s)") % INFO["map"], "items": items})
    if mp and mp not in BUILTIN_MAPS or (mode in ("horde", "dark", "team") and mp == "town_2p"):   # Sanctuary: its Team Play .rcm
        items = []
        try:
            for n in sorted(os.listdir(GAME)):
                b, e = os.path.splitext(n)
                if b.lower() == mp and e.lower() in MAP_EXT:
                    items.append((os.path.join(GAME, n), n, "/f/map/" + n))
        except OSError:
            pass
        if items:
            out.append({"id": "map_" + mp, "title": "Map files (%s)" % INFO["map"], "items": items})
    res = []
    for m in out:
        res.append({"id": m["id"], "title": m["title"], "_items": m["items"],
                    "files": [_file(p, d, u) for p, d, u in m["items"]]})
    return res


def music():
    """(local path, entry) of the Horde music for joiners, or (None, None)."""
    parts = XMAS_MUSIC if INFO.get("christmas") else MUSIC
    p = os.path.join(HERE, *parts)
    if INFO["mode"] not in ("horde", "dark") or not os.path.isfile(p): return None, None
    return p, _file(p, parts[-1], "/f/" + "/".join(parts))


def horde_state():
    """The running Survival game, for joiners' on-screen overlays: its current tier and whether the match is over."""
    st = os.path.join(HERE, "state")
    best = None
    try:
        for n in os.listdir(st):
            if (n.startswith("tier_") or n.startswith("obj_")) and n.endswith(".txt"):     # (a Christmas Mission writes obj_ only)
                p = os.path.join(st, n); mt = os.path.getmtime(p)
                if mt >= STARTED[0] - 5 and (best is None or mt > best[0]):
                    r_ = n[5:-4] if n.startswith("tier_") else n[4:-4]
                    best = (mt, r_, os.path.join(st, "tier_%s.txt" % r_))
    except OSError:
        pass
    if not best: return {"mode": INFO["mode"], "run": None, "tier": None, "over": False}
    _mt, run, p = best
    try: tier = open(p).read().strip().lower()
    except OSError: tier = None
    over = any(os.path.exists(os.path.join(st, f % run)) for f in ("stop_%s", "music_stop_%s"))
    santa = None
    try:                                                        # the Christmas Horde: where Santa is (his booming footsteps)
        p2 = os.path.join(st, "santa_%s.json" % run)
        if os.path.getmtime(p2) >= STARTED[0] - 5:
            with open(p2) as f: santa = json.load(f)
            santa["age"] = time.time() - santa.get("t", 0)       # (the PCs' clocks differ: the joiner goes by the age)
    except (OSError, ValueError):
        santa = None
    # (25 Nov: joiners from the launcher's Online tab see a mission's steps and banners and follow it to the next mission)
    def rd(name):
        try:
            with open(os.path.join(st, name), encoding="utf-8", errors="replace") as f: return f.read()
        except OSError: return ""
    lobby = chain = None
    try: lobby = json.loads(rd("next_lobby.json") or "null")
    except ValueError: pass
    try: chain = json.loads(rd("mission_chain.json") or "null")
    except ValueError: pass
    return {"mode": INFO["mode"], "run": run, "tier": tier, "over": over, "santa": santa, "now": time.time(),
            "obj": rd("obj_%s.txt" % run).strip(), "banner": rd("banner_%s.txt" % run).strip(), "bosshp": rd("bosshp_%s.txt" % run).strip(),
            "lobby": lobby, "next": chain, "mission": mission_now()}


def manifest():
    ms = mods()
    m = {"v": 1, "name": socket.gethostname(), "mode": INFO["mode"], "map": INFO["map"], "melee": bool(INFO.get("melee")),
         "christmas": bool(INFO.get("christmas")),
         "humans_team": INFO.get("humans_team"),
         "halloween": 1,                       # (this mod server sends the Halloween props, Bob Graham's head and the skeletons)
         "mods": [{k: v for k, v in m.items() if k != "_items"} for m in ms],
         "toolkit": {"url": "/toolkit.zip"}}
    _p, mu = music()
    if mu: m["music"] = mu
    sp = os.path.join(HERE, *SANTA_STEP)
    if INFO.get("christmas") and os.path.isfile(sp):
        m["santa_step"] = _file(sp, SANTA_STEP[-1], "/f/" + "/".join(SANTA_STEP))
    hp = os.path.join(HERE, *HOHOHO)
    if INFO.get("christmas") and os.path.isfile(hp):
        m["hohoho"] = _file(hp, HOHOHO[-1], "/f/" + "/".join(HOHOHO))
    return m


_toolkit_cache = {}


def toolkit_zip():
    """The bot toolkit for a friend's companion: scripts, map data and the Horde's assets (~80 MB, built once per game)."""
    if "zip" in _toolkit_cache: return _toolkit_cache["zip"]
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        for n in sorted(os.listdir(HERE)):
            p = os.path.join(HERE, n)
            if not os.path.isfile(p) or any(s in n for s in TOOLKIT_SKIP): continue
            if n.lower().endswith(TOOLKIT_EXT) or n in TOOLKIT_JSON:
                z.write(p, n)
        for d, exts in TOOLKIT_DIRS.items():                 # the Horde's assets: a friend's own hosted game needs them too
            dp = os.path.join(HERE, *d.split("/"))
            for n in sorted(os.listdir(dp)) if os.path.isdir(dp) else []:
                if n.lower().endswith(exts) and os.path.isfile(os.path.join(dp, n)):
                    z.write(os.path.join(dp, n), d + "/" + n)
    _toolkit_cache["zip"] = bio.getvalue()
    return _toolkit_cache["zip"]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, data, ctype):
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers()
        try: self.wfile.write(data)
        except OSError: pass

    def do_GET(self):
        try:
            import ipaddress
            if not ipaddress.ip_address(self.client_address[0]).is_private and not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return self.send_error(403)         # LAN / ZeroTier only
            p = self.path.split("?")[0]
            if p == "/manifest.json":
                return self._send(json.dumps(manifest()).encode(), "application/json")
            if p == "/horde.json":
                return self._send(json.dumps(horde_state()).encode(), "application/json")
            if p == "/toolkit.zip":
                return self._send(toolkit_zip(), "application/zip")
            if p.startswith("/f/"):
                mp, mu = music()
                if mu and p == mu["url"]:
                    with open(mp, "rb") as f: return self._send(f.read(), "audio/wav")
                sp = os.path.join(HERE, *SANTA_STEP)
                if INFO.get("christmas") and p == "/f/" + "/".join(SANTA_STEP) and os.path.isfile(sp):
                    with open(sp, "rb") as f: return self._send(f.read(), "audio/wav")
                for m in mods():
                    for path, _d, url in m["_items"]:
                        if url == p:
                            with open(path, "rb") as f: return self._send(f.read(), "application/octet-stream")
            self.send_error(404)
        except Exception:
            try: self.send_error(500)
            except OSError: pass


def bots_now():
    """(25 Nov: the launcher's Online tab counts only real players) the players of this PC's game that are bots or ghosts:
    a ghost touches state/alive_<pid>.txt every few seconds, a bot (brain.py, the Host Bot too) writes state/pos_<pid>.txt"""
    st, n, now = os.path.join(HERE, "state"), 0, time.time()
    try:
        for f in os.listdir(st):
            if (f.startswith("alive_") or (f.startswith("pos_") and f[4:-4].isdigit())) and f.endswith(".txt"):
                try:
                    if now - os.path.getmtime(os.path.join(st, f)) < 15: n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n


def mission_now():
    """the Christmas Mission being played here (horde.py: state/mission_live.json), or None"""
    try:
        with open(os.path.join(HERE, "state", "mission_live.json")) as f:
            m = json.load(f)
        return m if INFO.get("mode") == "horde" and time.time() - float(m.get("t", 0)) < 6 * 3600 else None
    except (OSError, ValueError):
        return None


def _discovery():
    """Answer the companion's broadcast ("OUTLAWS?") with this game's name, mode and map (private addresses only)."""
    import ipaddress
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", DISCOVER_PORT))
    except OSError:
        return
    while True:
        try:
            data, a = s.recvfrom(256)
            ip = ipaddress.ip_address(a[0])
            if data.startswith(b"OUTLAWS?") and (ip.is_private or ip.is_loopback) and not INFO.get("private"):   # (a private game: no answer)
                s.sendto(json.dumps({"name": socket.gethostname(), "mode": INFO["mode"], "map": INFO["map"], "port": PORT,
                                     "christmas": bool(INFO.get("christmas")), "melee": bool(INFO.get("melee")),
                                     "bots": bots_now(), "mission": mission_now()}).encode(), a)
        except OSError:
            time.sleep(1)


def _warm():
    try: mods()
    except Exception: pass


def start(mode="", map_key="", melee=False, humans_team=None, christmas=False, private=False):
    """Start (or update) the server; safe to call for every game. humans_team: the team friends' apps join (team games
    with the bots split unevenly), or None."""
    global _srv
    INFO["mode"], INFO["map"], INFO["melee"] = mode or "", map_key or "", bool(melee)
    INFO["humans_team"] = humans_team if humans_team in ("red", "blue") else None
    INFO["christmas"] = bool(christmas) and INFO["mode"] == "horde"
    INFO["private"] = bool(private)                     # a private game: friends' apps aren't told it's here
    STARTED[0] = time.time()
    try:
        os.makedirs(os.path.join(HERE, "state"), exist_ok=True)
        with open(os.path.join(HERE, "state", "hostinfo.json"), "w") as f: json.dump(INFO, f)
    except OSError:
        pass
    # Hash every mod file now, in the background: the zombie skins alone are ~70 MB, and hashing them on the first
    # /manifest.json request made friends' companions time out and join without mods.
    threading.Thread(target=_warm, daemon=True).start()
    _toolkit_cache.clear()
    if _srv: return True
    threading.Thread(target=_discovery, daemon=True).start()
    try:
        _srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    except OSError:
        return False                       # port busy: another copy is already serving
    threading.Thread(target=_srv.serve_forever, daemon=True).start()
    return True


if __name__ == "__main__":
    start(*(sys.argv[1:3]))
    print("serving on port", PORT, INFO)
    while True: time.sleep(3600)
