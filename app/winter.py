"""Winter look for the Christmas Horde: the world at normal brightness, washed toward a cool white-grey (snow and frost),
the floors and ceilings paler still (snowy ground), and a pale overcast sky. The characters, items and the HUD are left
alone. Like darkmode.py it edits only the Vulkan (SPIR-V) shaders in Common.kpf; the originals are kept in
winter_orig.zip with the copies we wrote, so 'off' only puts back files that are still our own edits.

Each family: (k, tint, lift): colour = (1-k) * colour + k * luminance * tint + lift, except that strongly coloured
pixels (Christmas lights, the tree's ornaments) are left nearly as they are: the change is scaled by
1 / (1 + SAT_KEEP * chroma^4), chroma^2 being the squared distance of the colour from its own grey."""
import os, re, struct, subprocess, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
KPF = os.path.join(os.path.dirname(HERE), "Common.kpf")
BACKUP = os.path.join(HERE, "winter_orig.zip")          # orig/<name> = the game's file, mine/<name> = what we wrote

FAMILIES = {
    "OLHardwareWall":       (0.50, (1.04, 1.06, 1.12), 0.08),
    "OLHardwarePlane":      (0.80, (1.12, 1.15, 1.22), 0.27),   # floors and ceilings: snow, the texture still showing through
    "OLHardware3DTextured": (0.45, (1.02, 1.04, 1.10), 0.07),
    "OLHardwareSky":        (0.90, (0.97, 0.99, 1.04), 0.16, 0.0),   # grey overcast: a blue sky (Wilderness) washed out
    "OLHardwareHorizon":    (0.90, (0.97, 0.99, 1.04), 0.16, 0.0),   # too (no colour spared: 4th value, SAT_KEEP)
}
LUMA = (0.30, 0.59, 0.11)
SAT_KEEP = 40.0                 # how strongly bright colours are spared (0: everything washed alike)


def family(name):
    m = re.match(r"progs/compiled/spirv/(OLHardware[A-Za-z0-9]+?)(?:-[0-9a-f]{40}|-default)?\.frag\.spirv$", name)
    return m.group(1) if m and m.group(1) in FAMILIES else None


def F(v):
    return struct.unpack("<I", struct.pack("<f", float(v)))[0]


def patch(b, params):
    """The shader with every store to its colour output (vec4, Location 0) going through the winter blend. None when it
    isn't built the way we expect."""
    k, tint, lift = params[:3]
    keep = params[3] if len(params) > 3 else SAT_KEEP
    if len(b) % 4 or len(b) < 24 or struct.unpack_from("<I", b)[0] != 0x07230203:
        return None
    w = list(struct.unpack("<%dI" % (len(b) // 4), b))
    ins, i = [], 5
    while i < len(w):
        wc = w[i] >> 16
        if wc == 0 or i + wc > len(w):
            return None
        ins.append((i, w[i] & 0xFFFF, wc))
        i += wc
    loc0 = {w[i + 1] for i, op, wc in ins if op == 71 and wc >= 4 and w[i + 2] == 30 and w[i + 3] == 0}
    outs = [(w[i + 2], w[i + 1]) for i, op, wc in ins if op == 59 and w[i + 3] == 3 and w[i + 2] in loc0]
    if len(outs) != 1:
        return None
    var, ptype = outs[0]
    pointee = next((w[i + 3] for i, op, wc in ins if op == 32 and w[i + 1] == ptype), None)
    vec = next(((w[i + 2], w[i + 3]) for i, op, wc in ins if op == 23 and w[i + 1] == pointee), None)
    if not vec or vec[1] != 4:
        return None
    fl = vec[0]
    if next((w[i + 2] for i, op, wc in ins if op == 22 and w[i + 1] == fl), None) != 32:
        return None
    for i, op, wc in ins:
        if op in (65, 66) and w[i + 3] == var:
            return None
    stores = [i for i, op, wc in ins if op == 62 and w[i + 1] == var]
    first_fn = next((i for i, op, wc in ins if op == 54), None)
    if not stores or first_fn is None:
        return None
    bound = w[3]
    consts = []

    def scalar(v):
        nonlocal bound
        cid = bound; bound += 1
        consts.extend([(4 << 16) | 43, fl, cid, F(v)])
        return cid

    def vec4(vals):
        nonlocal bound
        ids = [scalar(v) for v in vals]
        cid = bound; bound += 1
        consts.extend([(7 << 16) | 44, pointee, cid] + ids)
        return cid

    KT = vec4([k * tint[0], k * tint[1], k * tint[2], 0.0])  # the white-grey share
    KM = vec4([k, k, k, 0.0])                                # the colour's own share taken away (alpha kept)
    C = vec4([lift, lift, lift, 0.0])                        # the haze
    WL = vec4([LUMA[0], LUMA[1], LUMA[2], 0.0])              # luminance weights
    ONE3 = vec4([1.0, 1.0, 1.0, 0.0])
    KS = scalar(keep)
    ONE = scalar(1.0)
    out = w[:5]
    for i, op, wc in ins:
        if i == first_fn:
            out += consts
        if i in stores:
            v = w[i + 2]
            ids = {}
            for nm in ("lum", "lv", "dl", "dd", "c2", "c4", "kc", "den", "sc", "sv", "t1", "t2", "t3", "t4", "t5", "res"):
                ids[nm] = bound; bound += 1
            g = ids.__getitem__
            out += [(5 << 16) | 148, fl, g("lum"), v, WL]                            # lum = dot(v, WL)
            out += [(7 << 16) | 80, pointee, g("lv"), g("lum"), g("lum"), g("lum"), g("lum")]
            out += [(5 << 16) | 131, pointee, g("dl"), v, g("lv")]                   # how far from grey
            out += [(5 << 16) | 133, pointee, g("dd"), g("dl"), g("dl")]
            out += [(5 << 16) | 148, fl, g("c2"), g("dd"), ONE3]                     # chroma squared
            out += [(5 << 16) | 133, fl, g("c4"), g("c2"), g("c2")]
            out += [(5 << 16) | 133, fl, g("kc"), g("c4"), KS]
            out += [(5 << 16) | 129, fl, g("den"), g("kc"), ONE]
            out += [(5 << 16) | 136, fl, g("sc"), ONE, g("den")]                     # s = 1 / (1 + K c^4)
            out += [(7 << 16) | 80, pointee, g("sv"), g("sc"), g("sc"), g("sc"), g("sc")]
            out += [(5 << 16) | 133, pointee, g("t1"), g("lv"), KT]
            out += [(5 << 16) | 133, pointee, g("t2"), v, KM]
            out += [(5 << 16) | 131, pointee, g("t3"), g("t1"), g("t2")]
            out += [(5 << 16) | 129, pointee, g("t4"), g("t3"), C]
            out += [(5 << 16) | 133, pointee, g("t5"), g("sv"), g("t4")]
            out += [(5 << 16) | 129, pointee, g("res"), v, g("t5")]                  # res = v + s (k (lum T - v) + lift)
            out += [w[i], w[i + 1], g("res")] + w[i + 3:i + wc]                      # OpStore var res
        else:
            out += w[i:i + wc]
    out[3] = bound
    return struct.pack("<%dI" % len(out), *out)


def check(orig, new):
    """Cheap self-check: both parse cleanly and the bound grew (spirv-val is run on them when building)."""
    def walk(b):
        w = struct.unpack("<%dI" % (len(b) // 4), b)
        i = 5
        while i < len(w):
            if w[i] >> 16 == 0:
                return w, False
            i += w[i] >> 16
        return w, i == len(w)
    w0, ok0 = walk(orig); w1, ok1 = walk(new)
    return ok0 and ok1 and w1[3] > w0[3]


def running():
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
        return "outlawsahom.exe" in out
    except Exception:
        return False


def _rewrite(kpf, change):
    tmp = kpf + ".tmp"
    try:
        with zipfile.ZipFile(kpf) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zout:
            for info in zin.infolist():
                data = change(info.filename, zin.read(info.filename))
                ni = zipfile.ZipInfo(info.filename, info.date_time)
                ni.compress_type = info.compress_type
                ni.external_attr = info.external_attr
                zout.writestr(ni, data)
        with zipfile.ZipFile(tmp) as z, zipfile.ZipFile(kpf) as old:
            if z.testzip() is not None or len(z.namelist()) != len(old.namelist()):
                raise RuntimeError("verification failed")
        os.replace(tmp, kpf)
        return True
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass


def _saved():
    if not os.path.exists(BACKUP):
        return {}, {}
    with zipfile.ZipFile(BACKUP) as bz:
        names = bz.namelist()
        return ({n[5:]: bz.read(n) for n in names if n.startswith("orig/")},
                {n[5:]: bz.read(n) for n in names if n.startswith("mine/")})


def present(kpf=KPF):
    orig, mine = _saved()
    if not mine: return False
    try:
        with zipfile.ZipFile(kpf) as z:
            have = set(z.namelist())
            return any(n in have and z.read(n) == d for n, d in mine.items())
    except Exception:
        return False


NIGHT = {                       # the Co-op Missions' darker winter night (night=True): shader family -> extra multiplier
    "OLHardwareWall": (0.52, 0.52, 0.62), "OLHardwarePlane": (0.52, 0.52, 0.62), "OLHardware3DTextured": (0.55, 0.55, 0.65),
    "OLHardwareSky": (0.30, 0.30, 0.40), "OLHardwareHorizon": (0.30, 0.30, 0.40),
}
NIGHT_FOG = ((0.07, 0.08, 0.12), 60.0, 480.0, 0.5)   # a dark mist: colour, near, far, most (fogfx.py)
ICE = {                         # night="ice" (Mission 3): the same dark, but cold and blue, with an icy blue mist
    "OLHardwareWall": (0.46, 0.56, 0.80), "OLHardwarePlane": (0.50, 0.62, 0.88), "OLHardware3DTextured": (0.50, 0.60, 0.84),
    "OLHardwareSky": (0.28, 0.34, 0.50), "OLHardwareHorizon": (0.28, 0.34, 0.50),
}
ICE_FOG = ((0.16, 0.22, 0.32), 50.0, 420.0, 0.55)


def on(kpf=KPF, night=False):
    """Install the winter look (night=True: the Co-op Missions' darker, misty night, the decorations kept). True when it is in place."""
    try:
        if running(): return False
        orig, mine = _saved()
        plan, orig_new, mine_new = {}, dict(orig), dict(mine)
        with zipfile.ZipFile(kpf) as z:
            for n in z.namelist():
                fam = family(n)
                if not fam: continue
                cur = z.read(n)
                base = orig.get(n)
                if base is None or (cur != base and cur != mine.get(n)):
                    base = cur
                new = patch(base, FAMILIES[fam])
                if new is None or not check(base, new): continue
                if night:
                    try:
                        import darkmode, fogfx
                        nf = darkmode.family(n)
                        table, fog_ = (ICE, ICE_FOG) if night == "ice" else (NIGHT, NIGHT_FOG)
                        dk = darkmode.patch(new, table[nf]) if nf in table else None
                        if dk is not None:
                            new = dk
                            if nf not in ("OLHardwareSky", "OLHardwareHorizon"):    # (no mist on the sky: its distance jumps
                                fg = fogfx.patch(new, *fog_)                          #  at the sky box's seams, drawing lines)
                                if fg is not None: new = fg
                    except Exception:
                        pass
                orig_new[n], mine_new[n] = base, new
                if cur != new: plan[n] = new
        if not mine_new: return False
        if not plan: return True
        with zipfile.ZipFile(BACKUP, "w", zipfile.ZIP_DEFLATED) as bz:
            for n, d in orig_new.items(): bz.writestr("orig/" + n, d)
            for n, d in mine_new.items(): bz.writestr("mine/" + n, d)
        return _rewrite(kpf, lambda n, d: plan.get(n, d))
    except Exception:
        return False


def off(kpf=KPF):
    """Put the game's shaders back. True when none of our edits are left."""
    try:
        orig, mine = _saved()
        if not mine: return True
        with zipfile.ZipFile(kpf) as z:
            names = set(z.namelist())
            back = {n: orig[n] for n, d in mine.items() if n in names and n in orig and z.read(n) == d}
        if not back: return True
        if running(): return False
        return _rewrite(kpf, lambda n, d: back.get(n, d))
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "on": print("winter look installed" if on() else "could not install it (is the game closed?)")
    elif cmd == "off": print("normal look back" if off() else "could not restore it (is the game closed?)")
    else: print("winter look is", "ON" if present() else "off")
