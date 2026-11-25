"""Christmas lights on the outside walls of buildings (the Christmas Horde). The lights are painted along the top of
lit copies of the building wall textures (XLT01..XLT26: .PCX the game's own 8-bit texture under a new name, .HDX the
HD texture with the string of lights), and only the outside face of each building wall (its record in the outdoor
sector) is switched to the lit copy: the inside of every room keeps the plain texture.
christmas\\lights.json: {map: [[wall id, "MID" or "BOT", lit texture]]} (worked out beforehand from each map's sectors).
This writes the map's geometry with that change, as the text .LVT and the compiled .LVB.
Used by darkitems.patch(mode="christmas")."""
import re
import struct


def _names(walls):
    return sorted({t.upper() for _w, _p, t in walls})


def patch_lvt(text, walls):
    """The LVT with the lit textures on these walls, or None."""
    m = re.search(r"^TEXTURES\s+(\d+)[ \t]*\r?\n((?:[ \t]*TEXTURE:.*\r?\n)+)", text, re.M)
    if not m:
        return None
    n = int(m.group(1))
    nl = "\r\n" if "\r\n" in text else "\n"
    names = _names(walls)
    index = {t: n + k for k, t in enumerate(names)}
    block = m.group(2) + "".join(" TEXTURE: %s%s" % (t, nl) for t in names)
    text = text[:m.start()] + "TEXTURES %d" % (n + len(names)) + nl + block + text[m.end():]
    seen = {}
    for i in re.findall(r"WALL:\s*([0-9A-Fa-f]+)\b", text):
        seen[i.upper()] = seen.get(i.upper(), 0) + 1
    want = {w.upper(): (p, index[t.upper()]) for w, p, t in walls if seen.get(w.upper()) == 1}   # (an id used twice is left alone)
    done = [0]

    def sub(mm):
        wid = mm.group(1).upper()
        if wid not in want:
            return mm.group(0)
        part, tex = want[wid]
        done[0] += 1
        line = mm.group(0)
        return re.sub(r"(%s:\s*)(-?\d+)" % part, lambda x: x.group(1) + str(tex), line, count=1)
    text = re.sub(r"WALL:\s*([0-9A-Fa-f]+)\b[^\n]*", sub, text)
    return text if done[0] else None


def patch_lvb(data, walls):
    """The compiled LVB with the same change, or None. The file is a stream of (tag u32, count u8, values) tokens:
    TEXTURES is tag 0x0C (one int), each TEXTURE tag 0x0D (a string), each WALL tag 0x27 with 22 values: id, v1, v2,
    mid tex, x, y, top tex, x, y, bottom tex, x, y, overlay tex, x, y, adjoin, mirror, dadjoin, dmirror, flags, flags, light."""
    out = bytearray(data)
    t = out.find(b"\x0c\x00\x00\x00\x01")
    if t < 0:
        return None
    n = struct.unpack_from("<i", out, t + 5)[0]
    pos = t + 9
    for _ in range(n):                                            # past the texture names
        if out[pos:pos + 5] != b"\x0d\x00\x00\x00\x01":
            return None
        pos = out.index(b"\x00", pos + 5) + 1
    names = _names(walls)
    index = {nm: n + k for k, nm in enumerate(names)}
    struct.pack_into("<i", out, t + 5, n + len(names))
    out[pos:pos] = b"".join(b"\x0d\x00\x00\x00\x01" + nm.encode() + b"\x00" for nm in names)
    done = 0
    for wid, part, tex in walls:
        key = b"\x27\x00\x00\x00\x16" + struct.pack("<I", int(wid, 16) & 0xFFFFFFFF)
        i = out.find(key)
        if i < 0 or out.find(key, i + 1) >= 0:
            continue
        v = i + 5                                                # the 22 values
        struct.pack_into("<i", out, v + (3 if part == "MID" else 9) * 4, index[tex.upper()])
        done += 1
    return bytes(out) if done else None


# ---- flashing: the lit wall textures swapped for their animated copies (XFnn.ATX) in the map's texture list, so every
# wall using one (inside and out) gets the chasing lights. christmas\XFnn.ATX / XFnnA..D.PCX / .HDX.
WALLS = ["FWware3", "FWware6", "FWwareH2", "GC_SHACK", "GWADOBE2", "GWBANK01", "GWBARN3", "GWBRWN2", "GWCHAP3",
         "GWCOWPK2", "GWDRYG03", "GWMISS02", "GWMISS04", "GWTELEG1", "GWTELEG2", "GWWOODBK", "GWWOODTN", "fWbrnX04",
         "fWbwar1", "fWfence1", "fWmplnk2", "fWredwd2", "fWwarf", "mwall1", "mwall2", "mwall3"]
FLASH = {n.upper() + ".PCX": "XF%02d.ATX" % (k + 1) for k, n in enumerate(WALLS)}


# ---- one-off wall changes for a map: [wall id, part, texture, offset x or None, offset y or None] (None: kept).
# christmas\walls_extra.json, worked out beforehand: the MERRY CHRISTMAS banners over gates (xmasbanner.py,
# banners2.py) and the lights up high on Wilderness's log forts (loglights.py).
_PART = {"MID": 3, "TOP": 6, "BOT": 9, "OVERLAY": 12}   # (OVERLAY: a sign drawn over a wall)


def extra_for(map_key):
    import json
    from pathlib import Path
    try:
        data = json.loads((Path(__file__).resolve().parent / "christmas" / "walls_extra.json").read_text())
    except (OSError, ValueError):
        return []
    return [tuple(e) for e in data.get(map_key.lower(), [])]


def _side(e):
    """The sector a 6th field number names (only the wall copy facing it), or None (a missing field, true or false)."""
    v = e[5] if len(e) > 5 else None
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _extra_lvt(text, items):
    m = re.search(r"^TEXTURES\s+(\d+)[ \t]*\r?\n((?:[ \t]*TEXTURE:.*\r?\n)+)", text, re.M)
    if not m or not items:
        return text, 0
    n = int(m.group(1)); nl = "\r\n" if "\r\n" in text else "\n"
    names = sorted({e[2].upper() for e in items if e[2]})
    index = {t: n + k for k, t in enumerate(names)}
    text = text[:m.start()] + "TEXTURES %d" % (n + len(names)) + nl + m.group(2) + "".join(" TEXTURE: %s%s" % (t, nl) for t in names) + text[m.end():]
    lines = text.split("\n")
    where = {}
    for i, ln in enumerate(lines):                       # each wall's lines, by its id (a wall id twice: left alone unless asked)
        m = re.match(r"\s*WALL:\s*([0-9A-Fa-f]+)\b", ln)
        if m:
            where.setdefault(m.group(1).upper(), []).append(i)
    done = 0
    for e in items:
        wid, part, tex, ox, oy = e[:5]
        rows = where.get(wid.upper(), [])
        side = _side(e)
        if side is not None:                             # only the copy of the wall that faces this sector (its ADJOIN)
            rows = [i for i in rows if re.search(r"\bADJOIN:\s*%d\b" % side, lines[i])]
        elif len(rows) > 1 and not (len(e) > 5 and e[5]):
            continue
        if not rows:
            continue
        pat = re.compile(r"(%s:\s*)(-?\d+)\s+(-?[\d.]+)\s+(-?[\d.]+)" % part)
        def sub(x, tex=tex, ox=ox, oy=oy):
            return x.group(1) + "%s %s %s" % (x.group(2) if not tex else index[tex.upper()], x.group(3) if ox is None else "%.2f" % ox,
                                             x.group(4) if oy is None else "%.2f" % oy)
        hit = 0
        for i in rows:
            new, n = pat.subn(sub, lines[i], count=1)
            if n:
                lines[i] = new; hit = 1
        done += hit
    return "\n".join(lines), done


def _extra_lvb(out, items):
    t = out.find(b"\x0c\x00\x00\x00\x01")
    if t < 0 or not items:
        return out, 0
    n = struct.unpack_from("<i", out, t + 5)[0]
    pos = t + 9
    for _ in range(n):
        if out[pos:pos + 5] != b"\x0d\x00\x00\x00\x01":
            return out, 0
        pos = out.index(b"\x00", pos + 5) + 1
    names = sorted({e[2].upper() for e in items if e[2]})
    index = {nm: n + k for k, nm in enumerate(names)}
    struct.pack_into("<i", out, t + 5, n + len(names))
    out[pos:pos] = b"".join(b"\x0d\x00\x00\x00\x01" + nm.encode() + b"\x00" for nm in names)
    done = 0
    for e in items:
        wid, part, tex, ox, oy = e[:5]
        every = len(e) > 5 and e[5]                      # (a 6th field, true: every wall with this id - Bunkers' walls share
        key = b"\x27\x00\x00\x00\x16" + struct.pack("<I", int(wid, 16))   #  ids with the wall on their other side;
        hits, i = [], out.find(key)                      #  a number: only the copy whose ADJOIN is that sector)
        while i >= 0:
            hits.append(i); i = out.find(key, i + 1)
        side = _side(e)
        if side is not None:
            hits = [i for i in hits if struct.unpack_from("<i", out, i + 5 + 15 * 4)[0] == side]
        elif len(hits) > 1 and not every:
            continue
        if not hits:
            continue
        for i in hits:
            v = i + 5 + _PART[part] * 4
            if tex: struct.pack_into("<i", out, v, index[tex.upper()])
            if ox is not None: struct.pack_into("<f", out, v + 4, ox)
            if oy is not None: struct.pack_into("<f", out, v + 8, oy)
        done += 1
    return out, done


def flash_lvt(text, map_key=""):
    """(the LVT with the walls' lit textures swapped for their flashing copies, how many changes) or (None, 0).
    The copies are added as new textures and only walls (MID/TOP/BOT) are pointed at them: a floor or ceiling that
    uses the same texture (a plank floor) keeps the plain one."""
    m = re.search(r"^TEXTURES\s+(\d+)[ \t]*\r?\n((?:[ \t]*TEXTURE:.*\r?\n)+)", text, re.M)
    if not m:
        return None, 0
    n = int(m.group(1)); nl = "\r\n" if "\r\n" in text else "\n"
    names = re.findall(r"^[ \t]*TEXTURE:[ \t]*(\S+)", m.group(2), re.M)
    remap, add = {}, []
    for i, nm in enumerate(names):
        new = FLASH.get(nm.upper())
        if new:
            remap[i] = n + len(add); add.append(new)
    done = 0
    if add:
        text = text[:m.start()] + "TEXTURES %d" % (n + len(add)) + nl + m.group(2) + "".join(" TEXTURE: %s%s" % (t, nl) for t in add) + text[m.end():]

        def sub(mm):
            i = int(mm.group(2))
            return mm.group(1) + str(remap[i]) if i in remap else mm.group(0)
        text, done = re.subn(r"((?:MID|TOP|BOT):\s*)(-?\d+)(?=\s)", lambda mm: sub(mm), text)
        done = len(add)
    text, extra = _extra_lvt(text, extra_for(map_key))
    try:                                                     # frozen pools with a hole in each (icewater.HOLES: Orleans)
        import icewater
        text, ice = icewater.patch_lvt(text, map_key)
        extra += ice
    except Exception:
        pass
    return (text, done + extra) if done + extra else (None, 0)


def flash_lvb(data, map_key=""):
    """The compiled LVB with the same change, or None (TEXTURE tokens: tag 0x0D, count 1, a string; WALL tag 0x27, 22
    values: the MID, TOP and BOT texture indexes are values 3, 6 and 9)."""
    out = bytearray(data)
    t = out.find(b"\x0c\x00\x00\x00\x01")
    if t < 0:
        return None
    n = struct.unpack_from("<i", out, t + 5)[0]
    pos, names = t + 9, []
    for _ in range(n):
        if out[pos:pos + 5] != b"\x0d\x00\x00\x00\x01":
            return None
        end = out.index(b"\x00", pos + 5)
        names.append(out[pos + 5:end].decode("latin-1"))
        pos = end + 1
    remap, add = {}, []
    for i, nm in enumerate(names):
        new = FLASH.get(nm.upper())
        if new:
            remap[i] = n + len(add); add.append(new)
    if add:
        struct.pack_into("<i", out, t + 5, n + len(add))
        out[pos:pos] = b"".join(b"\x0d\x00\x00\x00\x01" + nm.encode() + b"\x00" for nm in add)
        i = 0
        key = b"\x27\x00\x00\x00\x16"
        while True:
            i = out.find(key, i)
            if i < 0: break
            for k in (3, 6, 9):
                v = struct.unpack_from("<i", out, i + 5 + 4 * k)[0]
                if v in remap: struct.pack_into("<i", out, i + 5 + 4 * k, remap[v])
            i += 5 + 88
    out, extra = _extra_lvb(out, extra_for(map_key))
    try:
        import icewater
        out, ice = icewater.patch_lvb(bytes(out), map_key)
        out = bytearray(out); extra += ice
    except Exception:
        pass
    return bytes(out) if add or extra else None
