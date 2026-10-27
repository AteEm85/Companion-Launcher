"""Darker, eerier look for Survival: the world's pixel shaders multiply their colour output by a cold blue-grey, the sky by a lot
more (night). The characters, items and the HUD are left alone, so enemies stay readable. Part of the Survival theme:
teamcolors.set_theme("on", dark=True) installs it and set_theme("off") removes it (so do 'team_colors_off.cmd' and the launcher).
Only the Vulkan (SPIR-V) shaders in Common.kpf are edited. The originals are kept in darkmode_orig.zip, together with the
edited copies we wrote, so 'off' only ever puts back files that are still our own edits.

Tune the look here: each entry is the (red, green, blue) multiplier, 1.0 = unchanged."""
import os, re, struct, subprocess, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
KPF = os.path.join(os.path.dirname(HERE), "Common.kpf")
BACKUP = os.path.join(HERE, "darkmode_orig.zip")        # orig/<name> = the game's file, mine/<name> = what we wrote

WORLD = (0.43, 0.46, 0.57)           # dim, cold blue-grey
FAMILIES = {                     # shader family -> multiplier
    "OLHardwareWall": WORLD,
    "OLHardwarePlane": WORLD,
    "OLHardware3DTextured": (0.49, 0.52, 0.62),
    "OLHardwareSky": (0.24, 0.27, 0.38),
    "OLHardwareHorizon": (0.24, 0.27, 0.38),
}


FLASH = {                         # Dark Mode (flashlight.py): shader family -> light left outside the cone (0 = black)
    "OLHardwareWall": 0.03, "OLHardwarePlane": 0.03, "OLHardware3DTextured": 0.04,
    "OLHardwareSky": 0.02, "OLHardwareHorizon": 0.02, "OLHardwareSprite": 0.20,
}


def family(name, table=FAMILIES):
    m = re.match(r"progs/compiled/spirv/(OLHardware[A-Za-z0-9]+?)(?:-[0-9a-f]{40}|-default)?\.frag\.spirv$", name)
    return m.group(1) if m and m.group(1) in table else None


def patch(b, mult):
    """The shader with its colour output (the vec4 at Location 0) multiplied by (r, g, b, 1). None when it isn't built the way we expect."""
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
    loc0 = {w[i + 1] for i, op, wc in ins if op == 71 and wc >= 4 and w[i + 2] == 30 and w[i + 3] == 0}      # OpDecorate Location 0
    outs = [(w[i + 2], w[i + 1]) for i, op, wc in ins if op == 59 and w[i + 3] == 3 and w[i + 2] in loc0]    # OpVariable Output
    if len(outs) != 1:
        return None
    var, ptype = outs[0]
    pointee = next((w[i + 3] for i, op, wc in ins if op == 32 and w[i + 1] == ptype), None)
    vec = next(((w[i + 2], w[i + 3]) for i, op, wc in ins if op == 23 and w[i + 1] == pointee), None)           # OpTypeVector
    if not vec or vec[1] != 4:
        return None
    flt = next((w[i + 2] for i, op, wc in ins if op == 22 and w[i + 1] == vec[0]), None)                         # OpTypeFloat width
    if flt != 32:
        return None
    for i, op, wc in ins:
        if op in (65, 66) and w[i + 3] == var:                    # a component-wise write through an access chain: not handled
            return None
    stores = [i for i, op, wc in ins if op == 62 and w[i + 1] == var]
    if not stores:
        return None
    first_fn = next((i for i, op, wc in ins if op == 54), None)
    if first_fn is None:
        return None
    bound = w[3]
    one = next((w[i + 2] for i, op, wc in ins if op == 43 and w[i + 1] == vec[0] and w[i + 3] == struct.unpack("<I", struct.pack("<f", 1.0))[0]), None)
    new_consts, ids = [], []
    for v in mult:
        cid = bound
        bound += 1
        new_consts += [(4 << 16) | 43, vec[0], cid, struct.unpack("<I", struct.pack("<f", float(v)))[0]]
        ids.append(cid)
    if one is None:
        one = bound
        bound += 1
        new_consts += [(4 << 16) | 43, vec[0], one, struct.unpack("<I", struct.pack("<f", 1.0))[0]]
    comp = bound
    bound += 1
    new_consts += [(7 << 16) | 44, pointee, comp, ids[0], ids[1], ids[2], one]
    out = w[:5]
    for i, op, wc in ins:
        if i == first_fn:
            out += new_consts
        if i in stores:
            res = bound
            bound += 1
            out += [(5 << 16) | 133, pointee, res, w[i + 2], comp]          # OpFMul  %res = %value * %multiplier
            out += [w[i], w[i + 1], res] + w[i + 3:i + wc]                  # OpStore %var %res (same memory operands)
        else:
            out += w[i:i + wc]
    out[3] = bound
    return struct.pack("<%dI" % len(out), *out)


def check(orig, new):
    """Cheap self-check of an edited shader: both parse cleanly, the bound grew, the new ids are unique and below it."""
    def walk(b):
        w = struct.unpack("<%dI" % (len(b) // 4), b)
        i, res = 5, []
        while i < len(w):
            if w[i] >> 16 == 0:
                return w, res, False
            res.append((i, w[i] & 0xFFFF, w[i] >> 16)); i += w[i] >> 16
        return w, res, i == len(w)
    w0, i0, ok0 = walk(orig)
    w1, i1, ok1 = walk(new)
    if not (ok0 and ok1) or w1[3] <= w0[3]:
        return False
    ids = [w1[i + 2] for i, op, wc in i1 if op in (43, 44, 133) and w1[i + 2] >= w0[3]]
    return len(ids) == w1[3] - w0[3] and len(ids) == len(set(ids)) and len(i1) > len(i0)


def running():
    try:
        out = subprocess.run(["tasklist"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
        return "outlawsahom.exe" in out
    except Exception:
        return False


def _rewrite(kpf, change):
    """Write a copy of Common.kpf with change(name, data) -> data applied, check it, and swap it in."""
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
            try:
                os.remove(tmp)
            except OSError:
                pass


def _saved():
    if not os.path.exists(BACKUP):
        return {}, {}
    with zipfile.ZipFile(BACKUP) as bz:
        names = bz.namelist()
        return ({n[5:]: bz.read(n) for n in names if n.startswith("orig/")},
                {n[5:]: bz.read(n) for n in names if n.startswith("mine/")})


def present(kpf=KPF):
    """True when some of our edited shaders are in Common.kpf."""
    orig, mine = _saved()
    if not mine:
        return False
    try:
        with zipfile.ZipFile(kpf) as z:
            for n, d in mine.items():
                if n in z.namelist() and z.read(n) == d:
                    return True
    except Exception:
        return False
    return False


def on(kpf=KPF, flash=False):
    """Install the darker look (flash=True: the flashlight look of Dark Mode instead). True when it is in place."""
    try:
        if running():
            return False
        orig, mine = _saved()
        plan, orig_new, mine_new = {}, dict(orig), dict(mine)
        with zipfile.ZipFile(kpf) as z:
            for n in z.namelist():
                fam = family(n, FLASH if flash else FAMILIES)
                if not fam:
                    continue
                cur = z.read(n)
                base = orig.get(n)
                if base is None or (cur != base and cur != mine.get(n)):    # no copy of the game's file yet, or the game updated it
                    base = cur
                if flash:
                    import flashlight
                    new = flashlight.patch(base, FLASH[fam])
                    if new is None or not flashlight.check(base, new):
                        continue
                else:
                    new = patch(base, FAMILIES[fam])
                    if new is None or not check(base, new):
                        continue
                orig_new[n], mine_new[n] = base, new
                if cur != new:
                    plan[n] = new
        if not mine_new:
            return False
        if not plan:
            return True
        with zipfile.ZipFile(BACKUP, "w", zipfile.ZIP_DEFLATED) as bz:
            for n, d in orig_new.items():
                bz.writestr("orig/" + n, d)
            for n, d in mine_new.items():
                bz.writestr("mine/" + n, d)
        return _rewrite(kpf, lambda n, d: plan.get(n, d))
    except Exception:
        return False


def off(kpf=KPF):
    """Put the game's shaders back. True when none of our edits are left."""
    try:
        orig, mine = _saved()
        if not mine:
            return True
        with zipfile.ZipFile(kpf) as z:
            names = set(z.namelist())
            back = {n: orig[n] for n, d in mine.items() if n in names and n in orig and z.read(n) == d}
        if not back:
            return True
        if running():
            return False
        return _rewrite(kpf, lambda n, d: back.get(n, d))
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "on":
        print("dark look installed" if on() else "could not install it (is the game closed?)")
    elif cmd == "off":
        print("normal look back" if off() else "could not restore it (is the game closed?)")
    else:
        print("dark look is", "ON" if present() else "off")
