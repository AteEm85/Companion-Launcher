"""Survival theme: Survivors (purple, the human team) vs Horde (green, the bots), or back to red/blue.
horde.py installs it when a Survival game starts and removes it when the game ends.
Usage: python teamcolors.py on | off | status [--kpf path] [--wait]  (--wait: for off, wait until the game is closed)
Edits 3 small text files inside Common.kpf (a plain zip): the player tint shader, the scoreboard
colours and the English team names. Fully reversible: 'off' puts the original text back."""
import sys, os, time, zipfile, shutil, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spvpatch                      # same for the Vulkan (SPIR-V) shaders
import dxpatch                       # recolors the team tint inside the precompiled shaders
import darkmode                      # the darker, eerier look of Survival (world and sky shaders), installed and removed together with the theme

HERE = os.path.dirname(os.path.abspath(__file__))
KPF = os.path.join(os.path.dirname(HERE), "Common.kpf")
BACKUP = os.path.join(HERE, "teamcolors_orig.zip")      # the original Vulkan shaders, so 'off' puts back exactly what was there

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

def state(z):
    """-> 'on', 'off', or 'mixed'"""
    on = off = 0
    for entry, reps in EDITS:
        t = z.read(entry).decode("utf-8")
        for a, b in reps:
            if b in t: on += 1
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
        if not os.path.exists(BACKUP): return True
        with zipfile.ZipFile(BACKUP) as bz, zipfile.ZipFile(kpf) as z:
            for n in bz.namelist():
                want = spvpatch.patch(bz.read(n), neutral)
                return want is None or z.read(n) == want          # one shader is enough to tell
    except Exception:
        return True
    return True

LOG = os.path.join(HERE, "teamcolors_log.txt")

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
        if st == want: return True
        if os.name == "nt" and running():
            say("Game is running; cannot change Common.kpf now."); return False
        tmp = kpf + ".tmp"
        backups, saved = {}, {}
        if want == "off" and os.path.exists(BACKUP):
            with zipfile.ZipFile(BACKUP) as bz:
                saved = {n: bz.read(n) for n in bz.namelist()}
        with zipfile.ZipFile(kpf) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zout:
            edits = {e: r for e, r in EDITS}
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename in edits:
                    t = data.decode("utf-8")
                    for o, n in edits[info.filename]:
                        src, dst = (o, n) if want == "on" else (n, o)
                        t = t.replace(src, dst)
                    data = t.encode("utf-8")
                elif "hlsl/OLHardwareSprite" in info.filename and info.filename.endswith("_ps_5_0.bin"):
                    if want == "off":              # Direct3D shaders are never patched on (the game rejects edited ones);
                        data = dxpatch.patch(data, want)[0]   # this only undoes an earlier patch
                elif "spirv/OLHardwareSprite" in info.filename and info.filename.endswith(".frag.spirv"):
                    if want == "on":
                        newdata = spvpatch.patch(data, neutral)
                        if newdata is not None:
                            backups[info.filename] = data
                            data = newdata
                    elif info.filename in saved:
                        orig = saved[info.filename]
                        if data in (spvpatch.patch(orig), spvpatch.patch(orig, True)):   # still our patched copy (not a newer game file)
                            data = orig
                ni = zipfile.ZipInfo(info.filename, info.date_time)
                ni.compress_type = info.compress_type; ni.external_attr = info.external_attr
                zout.writestr(ni, data)
        with zipfile.ZipFile(tmp) as z, zipfile.ZipFile(kpf) as old:
            if z.testzip() is not None or state(z) != want or len(z.namelist()) != len(old.namelist()):
                os.remove(tmp); say("Verification failed; original left untouched."); return False
        os.replace(tmp, kpf)
        if backups and not os.path.exists(BACKUP):
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
        return current(kpf) != "off" or darkmode.present(kpf)
    except Exception:
        return False

def set_theme(want, kpf=KPF, quiet=False, neutral=False, dark=False):
    """Install ('on') or remove ('off') the Survival look. dark=True ('on' only) adds the darker world and sky.
    Returns True when the file is in the wanted state."""
    if want == "off":
        gone = darkmode.off(kpf)
        return _set_theme("off", kpf, quiet, neutral) and gone
    ok = _set_theme("on", kpf, quiet, neutral)
    if ok:
        ok = darkmode.on(kpf, flash=(dark == "flash")) if dark else darkmode.off(kpf)
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
    dark = "flash" if "--flash" in sys.argv else ("--dark" in sys.argv)
    while True:
        if set_theme(cmd, kpf, neutral=neutral, dark=dark): return 0
        if not wait: return 1
        time.sleep(5)

if __name__ == "__main__":
    sys.exit(main())
