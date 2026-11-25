"""Frozen water for the Christmas Horde: a map's pools become ice you walk on, with a hole cut in each one so the water
below can still be reached. Used by xmaslights.flash_lvt / flash_lvb (darkitems.patch(mode="christmas")).

A pool is a surface sector whose VADJOIN leads down into the water sector below it. On the ice, that link goes (the floor
is solid) and the floor sounds like rock; the hole is a new small square sector inside the pool, joined to it by four
walls (an inner loop of the pool's own walls). The water below gets the same square (a shaft sector): its ceiling is ice
everywhere else (15 Nov, the user swam up through it into the void), and the hole and the shaft are each other's VADJOIN.
HOLES: {map: [(pool sector index, centre x, centre z, radius)]}, worked out beforehand (the most open spot of each pool,
clear of its walls and inside the water below: 34-44 units of room around each centre). The holes are round: a ring of
SIDES walls (16 Nov: were 8-unit squares; the user asked for bigger, round ones)."""
import re
import struct

HOLES = {"orleans": [(144, 67.0, 287.0, 12.0), (146, -124.0, 161.0, 12.0), (149, -13.0, 127.0, 12.0)]}          # (16 Nov: radius 7 -> 12, Santa kept missing them)
SIDES = 16                          # walls round each hole (a ring of this many straight walls)
HOLE_TEX = "XICEHOLE.PCX"          # dark open water at the hole (christmas\XICEHOLE.PCX / .HDX)
UNDER_TEX = "XICEUNDR.PCX"         # the ice seen from below, the underwater sector's ceiling (christmas\XICEUNDR.PCX / .HDX)
ICE_SOUND = "STEPROCK"
WALL_ID0 = 0x7F1000                 # the new walls' ids
JOIN_FLAGS = 131072                 # as the walls between the map's own pool sectors


def _square(cx, cz, r, clockwise):
    """the hole's outline: SIDES points on a circle of radius r (the name is from when the holes were squares)"""
    import math
    q = [(round(cx + r * math.cos(2 * math.pi * j / SIDES), 2), round(cz + r * math.sin(2 * math.pi * j / SIDES), 2)) for j in range(SIDES)]
    n = len(q)
    area = sum(q[i][0] * q[(i + 1) % n][1] - q[(i + 1) % n][0] * q[i][1] for i in range(n))
    if (area < 0) != clockwise:
        q = q[::-1]
    return q


def _wid(k, part):
    """wall ids for hole k: part 0 the pool's ring, 1 the hole's own ring, 2 the water's ring, 3 the shaft's own ring"""
    return WALL_ID0 + 0x100 * k + 0x40 * part


def _orient(verts, walls):
    """True when the sector's walls run with a negative signed area (as every sector of Orleans)."""
    return sum(verts[a][0] * verts[b][1] - verts[b][0] * verts[a][1] for a, b in walls) < 0


# ---------------------------------------------------------------- text (.LVT)
def _wall_line(wid, v1, v2, adj, mir, nl):
    return ("  WALL: %X V1: %d V2: %d MID: 2 0.00 0.00 TOP: 2 0.00 0.00 BOT: 2 0.00 0.00 OVERLAY: -1 0.00 0.00 "
            "ADJOIN: %d MIRROR: %d DADJOIN: -1 DMIRROR: -1 FLAGS: %d 0 LIGHT: 0%s" % (wid, v1, v2, adj, mir, JOIN_FLAGS, nl))


def _cut_lvt(block, sq, inner_idx, wid0, nl, vadjoin=None, sound=None, ceil_tex=None):
    """A sector with a square cut out of it (an inner loop of 4 walls joined to sector inner_idx): (new block, the square
    the right way round for the inner sector, how many walls the outer one had)."""
    vs = [(float(a), float(b)) for a, b in re.findall(r"X:\s*(-?[\d.]+)\s+Z:\s*(-?[\d.]+)", block)]
    ws = [(int(a), int(b)) for a, b in re.findall(r"WALL:\s*\S+\s+V1:\s*(\d+)\s+V2:\s*(\d+)", block)]
    q = _square(*sq, _orient(vs, ws))
    nv, nw, n = len(vs), len(ws), len(q)
    if vadjoin is not None: block = re.sub(r"VADJOIN\s+-?\d+", "VADJOIN %d" % vadjoin, block, count=1)
    if sound: block = re.sub(r"FLOOR SOUND\s*\S*", "FLOOR SOUND " + sound, block, count=1)
    if ceil_tex is not None: block = re.sub(r"(CEILING Y\s+-?[\d.]+\s+)(-?\d+)", lambda mm: mm.group(1) + str(ceil_tex), block, count=1)
    vline = "".join("  X: %.2f Z: %.2f%s" % (x, z, nl) for x, z in q)
    block = re.sub(r"VERTICES\s+\d+[ \t]*\r?\n((?:\s*X:.*\r?\n)+)", lambda mm: "VERTICES %d%s%s%s" % (nv + n, nl, mm.group(1), vline), block, count=1)
    wl = "".join(_wall_line(wid0 + j, nv + (j + 1) % n, nv + j, inner_idx, j, nl) for j in range(n))
    block = re.sub(r"WALLS\s+\d+[ \t]*\r?\n((?:\s*WALL:.*\r?\n)+)", lambda mm: "WALLS %d%s%s%s" % (nw + n, nl, mm.group(1), wl), block, count=1)
    return block, q, nw


def _inner_lvt(orig, sid, idx, q, outer_idx, outer_nw, wid0, nl, vadjoin, floor_tex=None):
    """The square sector itself: the outer sector's header (as it was), its own 4 vertices and 4 walls."""
    hdr = orig[:re.search(r"^\s*VERTICES", orig, re.M).start()]
    hdr = re.sub(r"^SECTOR\s+[0-9A-Fa-f]+[^\r\n]*", "SECTOR %X # %d" % (sid, idx), hdr, count=1)
    hdr = re.sub(r"VADJOIN\s+-?\d+", "VADJOIN %d" % vadjoin, hdr, count=1)
    if floor_tex is not None: hdr = re.sub(r"(FLOOR Y\s+-?[\d.]+\s+)(-?\d+)", lambda mm: mm.group(1) + str(floor_tex), hdr, count=1)
    n = len(q)
    hb = hdr + " VERTICES %d" % n + nl + "".join("  X: %.2f Z: %.2f%s" % (x, z, nl) for x, z in q) + nl + " WALLS %d" % n + nl
    hb += "".join(_wall_line(wid0 + j, j, (j + 1) % n, outer_idx, outer_nw + j, nl) for j in range(n))
    return hb + nl


def patch_lvt(text, map_key):
    holes = HOLES.get(map_key.lower())
    if not holes:
        return text, 0
    nl = "\r\n" if "\r\n" in text else "\n"
    m = re.search(r"^TEXTURES\s+(\d+)[ \t]*\r?\n((?:[ \t]*TEXTURE:.*\r?\n)+)", text, re.M)
    if not m:
        return text, 0
    ntex = int(m.group(1)); hole_tex, under_tex = ntex, ntex + 1
    text = text[:m.start()] + "TEXTURES %d" % (ntex + 2) + nl + m.group(2) + " TEXTURE: %s%s TEXTURE: %s%s" % (HOLE_TEX, nl, UNDER_TEX, nl) + text[m.end():]
    starts = [mm.start() for mm in re.finditer(r"^SECTOR\s+[0-9A-Fa-f]+", text, re.M)]
    ends = starts[1:] + [len(text)]
    blocks = [text[a:b] for a, b in zip(starts, ends)]
    orig = list(blocks)
    head, nsec, nh = text[:starts[0]], len(blocks), len(holes)
    new = []
    for k, (p, cx, cz, h) in enumerate(holes):
        under = int(re.search(r"VADJOIN\s+(-?\d+)", orig[p]).group(1))
        hidx, sidx = nsec + k, nsec + nh + k                   # the hole (in the ice) and the shaft (in the water below it)
        blocks[p], qp, nwp = _cut_lvt(blocks[p], (cx, cz, h), hidx, _wid(k, 0), nl, vadjoin=-1, sound=ICE_SOUND)
        blocks[under], qu, nwu = _cut_lvt(blocks[under], (cx, cz, h), sidx, _wid(k, 2), nl, vadjoin=-1, ceil_tex=under_tex)
        new.append(("h", k, _inner_lvt(orig[p], 0x7F2000 + k, hidx, qp, p, nwp, _wid(k, 1), nl, sidx, floor_tex=hole_tex)))
        new.append(("s", k, _inner_lvt(orig[under], 0x7F2100 + k, sidx, qu, under, nwu, _wid(k, 3), nl, hidx)))
    order = [b for t, k, b in sorted(new, key=lambda e: (e[0] == "s", e[1]))]   # holes first (nsec..), then shafts
    head = re.sub(r"^(NUM)?SECTORS\s+\d+", lambda mm: "%sSECTORS %d" % (mm.group(1) or "", nsec + 2 * nh), head, count=1, flags=re.M)   # (Orleans: NUMSECTORS)
    body = "".join(blocks)
    return head + body + ("" if body.endswith(nl + nl) else nl) + "".join(order), nh


# ---------------------------------------------------------------- compiled (.LVB): records of tag byte, big-endian count, fields
_STR = {1, 4, 6, 7, 0x0D, 0x10, 0x19}


def _tokens(b):
    pos, out = 21, []
    while pos < len(b):
        st = pos; tag = b[pos]; n = struct.unpack(">I", b[pos + 1:pos + 5])[0]; pos += 5
        if tag in _STR or tag == 0x0B:
            for k in range(n):
                if tag == 0x0B and k < 5: pos += 4; continue
                pos = b.index(b"\0", pos) + 1
        else:
            pos += 4 * n
        out.append([tag, bytes(b[st:pos])])
    return out


def _vals(raw, fmt):
    n = struct.unpack(">I", raw[1:5])[0]
    return list(struct.unpack("<" + fmt * n if len(fmt) == 1 else "<" + fmt, raw[5:5 + 4 * n]))


def _rec(tag, vals, fmt):
    return bytes([tag]) + struct.pack(">I", len(vals)) + struct.pack("<" + fmt, *vals)


def _srec(tag, s_):
    return bytes([tag]) + struct.pack(">I", 1) + s_.encode("latin-1") + b"\0"


_WALL_FMT = "iii" + "iff" * 3 + "iff" + "iiii" + "iii"           # id v1 v2, mid/top/bot/overlay (tex x y), adjoin mirror dadjoin dmirror, flags flags light


def _wall(wid, v1, v2, adj, mir):
    return _rec(0x27, [wid, v1, v2, 2, 0.0, 0.0, 2, 0.0, 0.0, 2, 0.0, 0.0, -1, 0.0, 0.0, adj, mir, -1, -1, JOIN_FLAGS, 0, 0], _WALL_FMT)


def _idx(bl, tag):
    return next(i for i, (t, _) in enumerate(bl) if t == tag)


def _cut_lvb(bl, sq, inner_idx, wid0, vadjoin=None, sound=None, ceil_tex=None):
    verts = [tuple(_vals(r, "ff")) for t, r in bl if t == 0x25]
    walls = [_vals(r, _WALL_FMT) for t, r in bl if t == 0x27]
    q = _square(*sq, _orient(verts, [(w[1], w[2]) for w in walls]))
    nv, nw, n = len(verts), len(walls), len(q)
    if vadjoin is not None: bl[_idx(bl, 0x18)][1] = _rec(0x18, [vadjoin], "i")
    if sound: bl[_idx(bl, 0x19)][1] = _srec(0x19, sound)
    if ceil_tex is not None:
        i = _idx(bl, 0x1B); cv = _vals(bl[i][1], "fifff"); cv[1] = ceil_tex; bl[i][1] = _rec(0x1B, cv, "fifff")
    bl[_idx(bl, 0x24)][1] = _rec(0x24, [nv + n], "i")
    lv = max(i for i, (t, _) in enumerate(bl) if t == 0x25)
    for j, (x, z) in enumerate(q):
        bl.insert(lv + 1 + j, [0x25, _rec(0x25, [x, z], "ff")])
    bl[_idx(bl, 0x26)][1] = _rec(0x26, [nw + n], "i")
    for j in range(n):
        bl.append([0x27, _wall(wid0 + j, nv + (j + 1) % n, nv + j, inner_idx, j)])
    return q, nw


def _inner_lvb(orig, sid, q, outer_idx, outer_nw, wid0, vadjoin, floor_tex=None):
    hb = [list(x) for x in orig[:_idx(orig, 0x24)]]
    hb[0][1] = _rec(0x0F, [sid], "i")
    hb[_idx(hb, 0x18)][1] = _rec(0x18, [vadjoin], "i")
    if floor_tex is not None:
        i = _idx(hb, 0x1A); fv = _vals(hb[i][1], "fifff"); fv[1] = floor_tex; hb[i][1] = _rec(0x1A, fv, "fifff")
    n = len(q)
    hb += [[0x24, _rec(0x24, [n], "i")]] + [[0x25, _rec(0x25, [x, z], "ff")] for x, z in q] + [[0x26, _rec(0x26, [n], "i")]]
    hb += [[0x27, _wall(wid0 + j, j, (j + 1) % n, outer_idx, outer_nw + j)] for j in range(n)]
    return hb


def patch_lvb(data, map_key):
    holes = HOLES.get(map_key.lower())
    if not holes:
        return data, 0
    T = _tokens(data)
    texc = _idx(T, 0x0C)
    ntex = _vals(T[texc][1], "i")[0]; hole_tex, under_tex = ntex, ntex + 1
    T[texc][1] = _rec(0x0C, [ntex + 2], "i")
    last_tex = max(i for i, (t, _) in enumerate(T) if t == 0x0D)
    T[last_tex + 1:last_tex + 1] = [[0x0D, _srec(0x0D, HOLE_TEX)], [0x0D, _srec(0x0D, UNDER_TEX)]]
    seci = [i for i, (t, _) in enumerate(T) if t == 0x0F]
    cnt = _idx(T, 0x0E)
    nsec, nh = _vals(T[cnt][1], "i")[0], len(holes)
    T[cnt][1] = _rec(0x0E, [nsec + 2 * nh], "i")
    blocks = [T[a:b] for a, b in zip(seci, seci[1:] + [len(T)])]
    orig = [[list(x) for x in bl] for bl in blocks]
    pre = T[:seci[0]]
    hs, ss = [], []
    for k, (p, cx, cz, h) in enumerate(holes):
        under = _vals(orig[p][_idx(orig[p], 0x18)][1], "i")[0]
        hidx, sidx = nsec + k, nsec + nh + k
        qp, nwp = _cut_lvb(blocks[p], (cx, cz, h), hidx, _wid(k, 0), vadjoin=-1, sound=ICE_SOUND)
        qu, nwu = _cut_lvb(blocks[under], (cx, cz, h), sidx, _wid(k, 2), vadjoin=-1, ceil_tex=under_tex)
        hs.append(_inner_lvb(orig[p], 0x7F2000 + k, qp, p, nwp, _wid(k, 1), sidx, floor_tex=hole_tex))
        ss.append(_inner_lvb(orig[under], 0x7F2100 + k, qu, under, nwu, _wid(k, 3), hidx))
    out = data[:21] + b"".join(r for _, r in pre) + b"".join(r for bl in blocks + hs + ss for _, r in bl)
    return out, nh
