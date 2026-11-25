"""Survival theme: Survivors (purple, the human team) vs Horde (green, the bots), or back to red/blue.
horde.py installs it when a Survival game starts and removes it when the game ends.
Usage: python teamcolors.py on | off | status [--kpf path] [--wait]  (--wait: for off, wait until the game is closed)
Edits 3 small text files inside Common.kpf (a plain zip): the player tint shader, the scoreboard
colours and the English team names. Fully reversible: 'off' puts the original text back."""
import sys, os, time, zipfile, shutil, subprocess, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spvpatch                      # same for the Vulkan (SPIR-V) shaders
import dxpatch                       # recolors the team tint inside the precompiled shaders
import darkmode                      # the darker, eerier look of Survival (world and sky shaders), installed and removed together with the theme
import winter                        # the Christmas Horde's snowy look instead (same shaders, washed white-grey)

HERE = os.path.dirname(os.path.abspath(__file__))
KPF = os.path.join(os.path.dirname(HERE), "Common.kpf")
BACKUP = os.path.join(HERE, "teamcolors_orig.zip")      # the original Vulkan shaders, so 'off' puts back exactly what was there
GAME_ORIG = os.path.join(HERE, "teamcolors_game.zip")   # shipped with the app and the toolkit: the game's own sprite shaders, for a
                                                        # PC whose backup was made from an already-tinted file (before v0.24)

# (entry, [(original, replacement), ...]).  Team 1 (red, the human player) -> purple "Survivors";
# team 2 (blue, the bots) -> green "Horde".
EDITS = [
    ("progs/OLHardwareSprite.shader", [
        ("lerp(vec3(1.0, 0.0, 0.0)", "lerp(vec3(0.6, 0.0, 0.9)"),
        ("lerp(vec3(0.0, 0.0, 1.0)", "lerp(vec3(0.0, 0.8, 0.0)"),
    ]),
    ("menu/fragtable.rcss", [
        ("rgb(224, 108, 117)", "rgb(198, 120, 221)"),
        ("rgb(97, 175, 239)", "rgb(152, 195, 121)"),
    ]),
    ("localization/loc_english.txt", [
        ('m_local_316 = "Red Flag"', 'm_local_316 = "Survivors Flag"'),
        ('m_local_317 = "Blue Flag"', 'm_local_317 = "Horde Flag"'),
        ('m_local_1096 = "Red Team"', 'm_local_1096 = "Survivors"'),
        ('m_local_1097 = "Blue Team"', 'm_local_1097 = "Horde"'),
        ('m_local_5120 = "The blue team captured the flag!"', 'm_local_5120 = "The Horde captured the flag!"'),
        ('m_local_5121 = "The red team captured the flag!!"', 'm_local_5121 = "The Survivors captured the flag!!"'),
        ('m_local_5129 = "You got the red team\'s flag!"', 'm_local_5129 = "You got the Survivors\' flag!"'),
        ('m_local_5130 = "You got the blue team\'s flag!"', 'm_local_5130 = "You got the Horde\'s flag!"'),
        ('m_local_7405 = "Join Red"', 'm_local_7405 = "Join Survivors"'),
        ('m_local_7406 = "Join Blue"', 'm_local_7406 = "Join Horde"'),
        ('m_local_25117 = "%s joined the red team!"', 'm_local_25117 = "%s joined the Survivors!"'),
        ('m_local_25118 = "%s joined the blue team!"', 'm_local_25118 = "%s joined the Horde!"'),
    ]),
]


# (25 Nov, the user: in the Horde, Christmas Horde and Co-op Missions lobbies, grey out the bots' team so nobody joins it -
#  Zombies / Elves / Christmas) the lobby's "Join Blue" button: dimmed, and nothing happens on it (no click, not in the
#  keyboard's list). The blue team is the bots' in all three; the players' red button is left as it is.
EDITS.append(("menu/lobby_main.rml", [
    ('<menu_link data-if="is_team_game" class="options_burn ol-join-team" onclick="ev_lobby_exit_join_blue" kex-menu-nav>',
     '<menu_link data-if="is_team_game" class="options_burn ol-join-team" style="opacity: 0.3;">'),
]))


# The Christmas Horde (set_theme(neutral="grinch")): the players' team (red) is the Grinches, in green; the bots' team
# (blue) is the Elves, in red. Same entries as EDITS, other replacements (each must differ from the game's own text).
def _xmas_text(t):
    t = t.replace("Survivors", "Grinches").replace("Horde's", "Elves'").replace("Horde", "Elves")
    return t.replace("rgb(198, 120, 221)", "rgb(110, 214, 96)").replace("rgb(152, 195, 121)", "rgb(232, 72, 64)")
EDITS_XMAS = [(e, [(a, _xmas_text(b) if e != "progs/OLHardwareSprite.shader" else
                    b.replace("0.6, 0.0, 0.9", "0.25, 0.85, 0.2").replace("0.0, 0.8, 0.0", "0.0, 0.79, 0.0"))
                   for a, b in reps]) for e, reps in EDITS]
# (21 Nov, the user) the Co-op Missions (neutral="grinch_plain"): the players' team is "Players", the enemies' "Christmas";
# the colours as the Christmas Horde's
def _mission_text(t):
    t = t.replace("The Horde", "Christmas").replace("the Horde", "Christmas")        # ("Christmas captured the flag!")
    t = t.replace("Survivors", "Players").replace("Horde's", "Christmas's").replace("Horde", "Christmas")
    return t.replace("rgb(198, 120, 221)", "rgb(110, 214, 96)").replace("rgb(152, 195, 121)", "rgb(232, 72, 64)")
EDITS_MISSION = [(e, [(a, _mission_text(b) if e != "progs/OLHardwareSprite.shader" else
                       b.replace("0.6, 0.0, 0.9", "0.25, 0.85, 0.2").replace("0.0, 0.8, 0.0", "0.0, 0.79, 0.0"))
                      for a, b in reps]) for e, reps in EDITS]
ALL_EDITS = EDITS + EDITS_XMAS + EDITS_MISSION


def _names(z):
    """which team names Common.kpf has: 'mission' (Players / Christmas), 'xmas' (Grinches / Elves) or 'other'"""
    t = z.read("localization/loc_english.txt")
    return "mission" if b'm_local_1096 = "Players"' in t else "xmas" if b"Grinches" in t else "other"

def state(z):
    """-> 'on', 'off', or 'mixed' ('on': the Survival or the Christmas names)"""
    on = off = 0
    for (entry, reps), (_e, xreps), (_m, mreps) in zip(EDITS, EDITS_XMAS, EDITS_MISSION):
        t = z.read(entry).decode("utf-8")
        for (a, b), (_a, xb), (_b, mb) in zip(reps, xreps, mreps):
            if b in t or xb in t or mb in t: on += 1
            elif a in t: off += 1
            else: return "unknown"
    return "on" if off == 0 else "off" if on == 0 else "mixed"

def running():
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
        return "outlawsahom.exe" in out
    except Exception:
        return False

def current(kpf=KPF):
    with zipfile.ZipFile(kpf) as z:
        return state(z)

def _mode_ok(kpf, neutral):
    """True when the patched shaders already match the wanted tint mode (coloured or neutral)."""
    try:
        orig = originals()
        if not orig: return True
        with zipfile.ZipFile(kpf) as z:
            have = set(z.namelist())
            try:                                                      # (the team names have to match too: Players / Grinches / Survivors)
                if _names(z) != {"grinch": "xmas", "grinch_plain": "mission"}.get(neutral, "other"): return False
            except KeyError:
                pass
            for n, o in orig.items():
                if n not in have: continue
                want = spvpatch.patch(o, neutral)
                return want is None or z.read(n) == want          # one shader is enough to tell
    except Exception:
        return True
    return True

LOG = os.path.join(HERE, "teamcolors_log.txt")

TINT_MODES = (False, True, "horde", "grinch", "grinch_plain")      # every way spvpatch can tint the sprites: 'off' must recognise all of them


def _patched_copies(orig):
    """Our patched versions of an original sprite shader (any tint mode)."""
    out = []
    for mode in TINT_MODES:
        try:
            d = spvpatch.patch(orig, mode)
        except Exception:
            d = None
        if d is not None and d != orig: out.append(d)
    return out


_PURPLE = (struct.pack("<f", 0.6), struct.pack("<f", 0.9))     # the Survivors' purple (spvpatch): never in the game's own shaders
_GRINCH = (struct.pack("<f", 0.25), struct.pack("<f", 0.85))   # the Grinches' green (the Christmas Horde)


def _ours(cur, orig):
    """A sprite shader we changed: one of our tinted copies, or one holding our purple that the original doesn't
    (left by older versions of the tint and darkness patches, layered on each other)."""
    if cur == orig: return False
    if cur in _patched_copies(orig): return True
    return all(c in cur and c not in orig for c in _PURPLE) or all(c in cur and c not in orig for c in _GRINCH)


def genuine(b):
    """True for a game-original sprite shader: plain red and blue team tints, neither switched off. Our tinted copies
    (any mode, any version) fail this, so a backup can only ever hold the game's own files."""
    try:
        r = spvpatch._parse(b)
        if not r: return False
        w, ins = r
        targets, _ft, _consts, val = spvpatch._targets(w, ins)
        if len(targets) != 2: return False
        cols = {team: tuple(val(x) for x in w[i + 3:i + 6]) for i, team in targets}
        if not (spvpatch._close(cols.get("S", (None,) * 3), (1.0, 0.0, 0.0))
                and spvpatch._close(cols.get("H", (None,) * 3), (0.0, 0.0, 1.0))): return False
        tid = {w[i + 2] for i, _ in targets}
        ones = {w[i + 2] for i, op, wc in ins if op == 44 and wc == 6 and all(val(x) == 1.0 for x in w[i + 3:i + 6])}
        return not any(op == 12 and wc == 8 and w[i + 4] == 46 and w[i + 5] in tid and w[i + 7] in ones for i, op, wc in ins)
    except Exception:
        return False


def originals():
    """{shader name: the game's own bytes}: this PC's backup where it is genuine, else the copy shipped with the app."""
    out = {}
    for path in (GAME_ORIG, BACKUP):                 # this PC's own backup wins (it matches this PC's game files)
        try:
            with zipfile.ZipFile(path) as z:
                for n in z.namelist():
                    d = z.read(n)
                    if genuine(d): out[n] = d
        except Exception:
            pass
    return out


def shaders_patched(kpf=KPF):
    """True when a sprite shader in Common.kpf is still one of our tinted copies, even with the team names and the
    scoreboard back to normal (an 'off' before v0.24 left the purple-Survivors shaders in)."""
    try:
        orig = originals()
        if not orig: return False
        with zipfile.ZipFile(kpf) as z:
            have = set(z.namelist())
            try:                                                      # (the team names have to match too: Players / Grinches / Survivors)
                if _names(z) != {"grinch": "xmas", "grinch_plain": "mission"}.get(neutral, "other"): return False
            except KeyError:
                pass
            for n, o in orig.items():
                if n in have and _ours(z.read(n), o): return True
    except Exception:
        return False
    return False

def _set_theme(want, kpf=KPF, quiet=False, neutral=False):
    """Install ('on') or remove ('off') the theme. Returns True when the file is in the wanted state.
    neutral=True ('on' only): no team tint at all, so custom character sprites (zombie skins) keep their own colours."""
    def say(*x):
        msg = " ".join(str(i) for i in x)
        if not quiet: print(msg)
        try:
            with open(LOG, "a") as f: f.write(time.strftime("%H:%M:%S ") + want + ": " + msg + "\n")
        except OSError: pass
    try:
        st = current(kpf)
        if st == want and want == "on" and not _mode_ok(kpf, neutral):
            if not _set_theme("off", kpf, quiet): return False
            st = "off"
        if st == want and not (want == "off" and shaders_patched(kpf)): return True
        if os.name == "nt" and running():
            say("Game is running; cannot change Common.kpf now."); return False
        tmp = kpf + ".tmp"
        backups, saved = {}, {}
        origs = originals()
        if want == "off":
            saved = origs
        with zipfile.ZipFile(kpf) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zout:
            edits = {e: r for e, r in (EDITS_MISSION if neutral == "grinch_plain" else EDITS_XMAS if neutral == "grinch" else EDITS)}
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename in edits:
                    t = data.decode("utf-8")
                    if want == "on":
                        for o, n in edits[info.filename]: t = t.replace(o, n)
                    else:                                # back from either set of names
                        for e2, reps in ALL_EDITS:
                            if e2 == info.filename:
                                for o, n in reps: t = t.replace(n, o)
                    data = t.encode("utf-8")
                elif "hlsl/OLHardwareSprite" in info.filename and info.filename.endswith("_ps_5_0.bin"):
                    if want == "off":              # Direct3D shaders are never patched on (the game rejects edited ones);
                        data = dxpatch.patch(data, want)[0]   # this only undoes an earlier patch
                elif "spirv/OLHardwareSprite" in info.filename and info.filename.endswith(".frag.spirv"):
                    if want == "on":
                        if not genuine(data):                # still tinted from an earlier game: start from the original
                            data = origs.get(info.filename, data)
                        newdata = spvpatch.patch(data, neutral)
                        if newdata is not None:
                            if genuine(data): backups[info.filename] = data
                            data = newdata
                    elif info.filename in saved:
                        orig = saved[info.filename]
                        if _ours(data, orig):              # still our patched copy (not a newer game file)
                            data = orig
                ni = zipfile.ZipInfo(info.filename, info.date_time)
                ni.compress_type = info.compress_type; ni.external_attr = info.external_attr
                zout.writestr(ni, data)
        with zipfile.ZipFile(tmp) as z, zipfile.ZipFile(kpf) as old:
            if z.testzip() is not None or state(z) != want or len(z.namelist()) != len(old.namelist()):
                os.remove(tmp); say("Verification failed; original left untouched."); return False
        os.replace(tmp, kpf)
        if backups:                                   # genuine originals only; a backup with stuck copies (pre-v0.24) is replaced
            try:
                with zipfile.ZipFile(BACKUP) as bz:
                    good = all(genuine(bz.read(n)) for n in bz.namelist()) and set(backups) <= set(bz.namelist())
            except Exception:
                good = False
            if not good:
                with zipfile.ZipFile(BACKUP, "w", zipfile.ZIP_DEFLATED) as bz:
                    for n, d in backups.items():
                        bz.writestr(n, d)
        say("Team colors:", "Survival theme installed" if want == "on" else "back to original red/blue")
        return True
    except Exception as e:
        say("Could not change Common.kpf:", e)
        try: os.remove(kpf + ".tmp")
        except OSError: pass
        return False

def dirty(kpf=KPF):
    """True when anything of the Survival look (colours, team names or the darker shaders) is installed."""
    try:
        return current(kpf) != "off" or darkmode.present(kpf) or winter.present(kpf) or shaders_patched(kpf)
    except Exception:
        return False

def set_theme(want, kpf=KPF, quiet=False, neutral=False, dark=False):
    """Install ('on') or remove ('off') the Survival look. dark=True ('on' only) adds the darker world and sky,
    dark="winter" the Christmas Horde's snowy world instead. Returns True when the file is in the wanted state."""
    if want == "off":
        gone = darkmode.off(kpf)
        gone = winter.off(kpf) and gone
        return _set_theme("off", kpf, quiet, neutral) and gone
    ok = _set_theme("on", kpf, quiet, neutral)
    if ok and dark in ("winter", "winter_night", "winter_ice"):   # (winter_night: the Co-op Missions' darker misty night; _ice: Mission 3's)
        ok = darkmode.off(kpf) and winter.on(kpf, night={"winter_night": True, "winter_ice": "ice"}.get(dark, False))
        if not ok:
            try:
                with open(LOG, "a") as f: f.write(time.strftime("%H:%M:%S ") + "could not install the winter shaders\n")
            except OSError: pass
            ok = True
        return ok
    if ok:
        ok = winter.off(kpf)
        ok = (darkmode.on(kpf, flash=(dark == "flash")) if dark else darkmode.off(kpf)) and ok
        if dark and not ok:                                  # the colours are in; the darkening isn't: carry on without it
            try:
                with open(LOG, "a") as f: f.write(time.strftime("%H:%M:%S ") + "could not install the darker shaders\n")
            except OSError: pass
            ok = True
    return ok

def main():
    a = list(sys.argv[1:])
    kpf = KPF
    if "--kpf" in a:
        i = a.index("--kpf"); kpf = a[i + 1]; del a[i:i + 2]
    wait = "--wait" in a
    neutral = "--neutral" in a
    a = [x for x in a if x not in ("--wait", "--neutral")]
    cmd = a[0] if a else "status"
    if cmd == "status":
        print("Common.kpf:", {"on": "survival theme", "off": "original red/blue"}.get(current(kpf), current(kpf)), "| dark shaders:", "ON" if darkmode.present(kpf) else "off"); return 0
    if cmd not in ("on", "off"): print("use: on | off | status"); return 2
    dark = "flash" if "--flash" in sys.argv else "winter" if "--winter" in sys.argv else ("--dark" in sys.argv)
    while True:
        if set_theme(cmd, kpf, neutral=neutral, dark=dark): return 0
        if not wait: return 1
        time.sleep(5)

if __name__ == "__main__":
    sys.exit(main())
